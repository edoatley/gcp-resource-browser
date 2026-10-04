"""Which IAM permissions make a role high risk, and why.

Risk is a property of PERMISSIONS, not of role names. A role's risk is the
highest risk of any permission it contains. That one rule drives both
surfaces: `/v1/roles` evaluates it against the IAM role catalogue, and
`/v1/grants` compiles the same permissions into a CAI query. They cannot
disagree, and a custom role or an obscure predefined one is classified without
anyone having listed it -- `roles/firebase.managementServiceAgent` holds
`resourcemanager.projects.setIamPolicy`, which no role-name list would catch.

Every rule cites sources in `role_risk_rules.json`, each quoting the passage it
relies on, so the classification can be audited rather than trusted.

Pure: no GCP calls here. `app/core.py` does the I/O.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

RULES_FILE = Path(__file__).with_name("role_risk_rules.json")

# CAI expands a query into every combination of its alternatives and rejects
# more than 32 -- verified against the live API: 32 permissions alone pass and
# 33 fail; 16 permissions x 2 member types pass and 17 x 2 fail; 10 x 3 pass
# and 11 x 3 fail ("Query has too many alternations"). Permissions are
# therefore searched in batches sized to stay under this.
MAX_QUERY_ALTERNATIONS = 32

# Principal prefixes CAI understands in `memberTypes:`, as they appear before
# the colon in a binding member.
MEMBER_TYPES = (
    "user",
    "group",
    "serviceAccount",
    "domain",
    "allUsers",
    "allAuthenticatedUsers",
    "principal",
    "principalSet",
)

# Google-managed service agents (see the gcp-sa-types source). Their grants
# are made by Google for a service to work and are not the customer's to
# review, but they hold many risky permissions: 58 of the 88 predefined roles
# matched by the default rules are service-agent roles. Default service
# accounts (PROJECT_NUMBER-compute@developer..., PROJECT@appspot...) are NOT
# agents -- they are customer-managed and a classic over-grant, so they stay.
_AGENT_MEMBER = re.compile(
    r"^serviceAccount:("
    r"service-\d+@.*"  # service-PROJECT_NUMBER@gcp-sa-*.iam..., @compute-system...
    r"|\d+@cloudservices\.gserviceaccount\.com"  # Google APIs service agent
    r"|.*@gcp-sa-[a-z0-9-]+\.iam\.gserviceaccount\.com"
    r")$"
)


class Risk(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {Risk.MEDIUM: 1, Risk.HIGH: 2}


class RiskRuleError(ValueError):
    """A risk rule, level or member type is not usable."""


@dataclass(frozen=True)
class RiskRule:
    permission: str
    risk: Risk
    reason: str
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    url: str
    quote: str


def parse_risk(value: str) -> Risk:
    try:
        return Risk(value.lower())
    except ValueError:
        raise RiskRuleError(
            f"Unknown risk level {value!r}. Choose from: {', '.join(r.value for r in Risk)}."
        ) from None


def _parse_rule(raw: dict) -> RiskRule:
    return RiskRule(
        permission=raw["permission"],
        risk=parse_risk(raw["risk"]),
        reason=raw["reason"],
        sources=tuple(raw.get("sources", ())),
    )


@lru_cache(maxsize=1)
def load_rules() -> tuple[RiskRule, ...]:
    """Default rules plus site rules. A site rule replaces a default for the same permission."""
    with RULES_FILE.open() as handle:
        data = json.load(handle)
    rules = {r["permission"]: _parse_rule(r) for r in data["rules"]}

    from app.config import load_config  # imported here to avoid a cycle

    for raw in load_config().extra_role_risk_rules:
        rules[raw["permission"]] = _parse_rule(raw)
    return tuple(rules.values())


@lru_cache(maxsize=1)
def load_sources() -> dict[str, Source]:
    with RULES_FILE.open() as handle:
        data = json.load(handle)
    return {key: Source(id=key, **value) for key, value in data["sources"].items()}


def rules_at_least(minimum: Risk) -> list[RiskRule]:
    """Rules at or above `minimum`: `high` is high only, `medium` is medium and high."""
    return [r for r in load_rules() if r.risk.rank >= minimum.rank]


def classify(permissions: Iterable[str], minimum: Risk = Risk.MEDIUM) -> list[RiskRule]:
    """The rules a set of permissions trips, highest risk first."""
    held = set(permissions)
    matched = [r for r in rules_at_least(minimum) if r.permission in held]
    return sorted(matched, key=lambda r: (-r.risk.rank, r.permission))


def highest(rules: Iterable[RiskRule]) -> Risk | None:
    return max((r.risk for r in rules), key=lambda r: r.rank, default=None)


def member_type(member: str) -> str:
    """`user:a@b.com` -> `user`; `allUsers` -> `allUsers`."""
    return member.split(":", 1)[0]


def validate_member_types(types: Sequence[str]) -> list[str]:
    unknown = [t for t in types if t not in MEMBER_TYPES]
    if unknown:
        raise RiskRuleError(
            f"Unknown member type(s) {', '.join(unknown)}. Choose from: {', '.join(MEMBER_TYPES)}."
        )
    return list(dict.fromkeys(types))


def is_service_agent_role(role: str) -> bool:
    """Google names service-agent roles `roles/<service>.serviceAgent` or `...ServiceAgent`."""
    return role.lower().endswith("serviceagent")


def is_service_agent_grant(member: str, role: str) -> bool:
    return bool(_AGENT_MEMBER.match(member)) or is_service_agent_role(role)


def permission_batches(permissions: Sequence[str], member_type_count: int) -> list[list[str]]:
    """Split permissions so each query stays within CAI's alternation limit."""
    size = max(1, MAX_QUERY_ALTERNATIONS // max(1, member_type_count))
    return [list(permissions[i : i + size]) for i in range(0, len(permissions), size)]
