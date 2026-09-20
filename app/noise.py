"""Noise reduction for unfiltered searches.

An organisation-wide search with no asset type given returns mostly rows nobody
is auditing: enabled API services, image layers, per-deploy revisions,
auto-created default routes. In a census of a real estate these were 42%, 10%,
5% and 8% of all resources respectively -- two thirds of the output before any
resource of interest appears.

**Suppression is necessarily client-side.** Verified against the live API:
`assetType` is not a queryable CAI field (`400 Unsupported field: 'assetType'`),
and `asset_types` is an include list with no way to express exclusion -- RE2 has
no negative lookahead. So unlike user filters, which are always compiled into
the query and evaluated upstream, noise rules are applied to results as they
stream back. The distinction is deliberate: a user filter narrows what is
*fetched*, while a noise rule hides low-value rows from a result the user asked
for broadly.

Two invariants, because hiding data from an audit tool is dangerous:

1. `--show-all` / `?show_all=true` disables suppression entirely.
2. The number suppressed is always reported, never silently dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models import Resource

NOISE_RULES_FILE = Path(__file__).with_name("noise_rules.json")


@dataclass(frozen=True)
class NoiseRule:
    """One reason a resource is hidden by default."""

    asset_type: str
    reason: str
    name_pattern: re.Pattern[str] | None = None

    def matches(self, resource: Resource) -> bool:
        if resource.asset_type != self.asset_type:
            return False
        if self.name_pattern is None:
            return True
        # Match the short name, which is what the pattern is written against.
        name = resource.display_name or resource.full_name.rsplit("/", 1)[-1]
        return bool(self.name_pattern.search(name))


def _parse(raw: dict) -> list[NoiseRule]:
    rules = []
    for entry in raw["rules"]:
        pattern = entry.get("name")
        rules.append(
            NoiseRule(
                asset_type=entry["asset_type"],
                reason=entry["reason"],
                name_pattern=re.compile(pattern) if pattern else None,
            )
        )
    return rules


@lru_cache(maxsize=1)
def load_rules() -> tuple[NoiseRule, ...]:
    """Load the default ruleset, plus any site-specific overrides."""
    with NOISE_RULES_FILE.open() as handle:
        rules = _parse(json.load(handle))

    from app.config import load_config  # imported here to avoid a cycle

    config = load_config()
    rules = [r for r in rules if r.asset_type not in config.unsuppressed_types]
    rules.extend(_parse({"rules": config.extra_noise_rules}) if config.extra_noise_rules else [])
    return tuple(rules)


class NoiseFilter:
    """Applies the ruleset to a stream of resources, counting what it hides."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.rules = load_rules() if enabled else ()
        self.suppressed = 0
        # Reason -> count, so the CLI can explain *what* was hidden, not just
        # how much. An unexplained "38 hidden" invites distrust of the tool.
        self.by_reason: dict[str, int] = {}

    def keep(self, resource: Resource) -> bool:
        """True if the resource should be shown."""
        if not self.enabled:
            return True
        for rule in self.rules:
            if rule.matches(resource):
                self.suppressed += 1
                self.by_reason[rule.reason] = self.by_reason.get(rule.reason, 0) + 1
                return False
        return True

    def summary(self) -> str:
        """One line explaining what was hidden, or empty if nothing was."""
        if not self.suppressed:
            return ""
        top = sorted(self.by_reason.items(), key=lambda kv: -kv[1])[:3]
        detail = ", ".join(f"{count} {reason}" for reason, count in top)
        more = "" if len(self.by_reason) <= 3 else f", and {len(self.by_reason) - 3} more"
        return f"{self.suppressed} hidden: {detail}{more}. Use --show-all to include them."
