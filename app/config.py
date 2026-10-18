"""Site-specific configuration.

One override mechanism shared by the asset-type mapping and the noise ruleset,
rather than two. Both needed the same things -- discovery paths, merge
semantics, validation -- and building it once was the reason the asset-type
mapping was made data without being made configurable back in Phase 1.

Discovery, first match wins:

1. `$GCP_EXPLORER_CONFIG` (an explicit path; a missing file here is an error,
   since naming a path and having it ignored is worse than failing)
2. `./gcp-explorer.json` in the working directory
3. `~/.config/gcp-explorer/config.json`

Merge rather than replace. A site adds the resource types and noise rules it
cares about; it does not restate the defaults to keep them. Replacement would
mean every upgrade silently drops new defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CONFIG_ENV_VAR = "GCP_EXPLORER_CONFIG"
CONFIG_FILENAME = "gcp-explorer.json"


class ConfigError(Exception):
    """The configuration file exists but could not be used."""


@dataclass(frozen=True)
class Config:
    """Site overrides, empty when no config file is present."""

    source: Path | None = None
    extra_asset_types: dict[str, str] = field(default_factory=dict)
    extra_noise_rules: list[dict] = field(default_factory=list)
    unsuppressed_types: frozenset[str] = frozenset()
    extra_role_risk_rules: list[dict] = field(default_factory=list)


def config_paths() -> list[Path]:
    """Candidate locations, in precedence order."""
    paths = []
    if explicit := os.environ.get(CONFIG_ENV_VAR):
        paths.append(Path(explicit))
    paths.append(Path.cwd() / CONFIG_FILENAME)
    paths.append(Path.home() / ".config" / "gcp-explorer" / "config.json")
    return paths


def _validate(raw: dict, source: Path) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: expected a JSON object at the top level.")

    unknown = set(raw) - {"asset_types", "noise_rules", "unsuppress", "role_risk_rules", "_comment"}
    if unknown:
        # Silently ignoring a typo'd key would leave someone convinced their
        # config was applied when it was not.
        raise ConfigError(
            f"{source}: unknown key(s) {', '.join(sorted(unknown))}. "
            "Expected: asset_types, noise_rules, unsuppress, role_risk_rules."
        )

    asset_types = raw.get("asset_types", {})
    if not isinstance(asset_types, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in asset_types.items()
    ):
        raise ConfigError(f"{source}: 'asset_types' must be a mapping of name to CAI asset type.")

    noise_rules = raw.get("noise_rules", [])
    if not isinstance(noise_rules, list):
        raise ConfigError(f"{source}: 'noise_rules' must be a list.")
    for rule in noise_rules:
        if not isinstance(rule, dict) or "asset_type" not in rule or "reason" not in rule:
            raise ConfigError(
                f"{source}: each noise rule needs 'asset_type' and 'reason' "
                "(and may add a 'name' regex)."
            )

    unsuppress = raw.get("unsuppress", [])
    if not isinstance(unsuppress, list) or not all(isinstance(t, str) for t in unsuppress):
        raise ConfigError(f"{source}: 'unsuppress' must be a list of CAI asset types.")

    role_risk_rules = raw.get("role_risk_rules", [])
    if not isinstance(role_risk_rules, list):
        raise ConfigError(f"{source}: 'role_risk_rules' must be a list.")
    for rule in role_risk_rules:
        _validate_risk_rule(rule, source)

    return Config(
        source=source,
        extra_asset_types=dict(asset_types),
        extra_noise_rules=list(noise_rules),
        unsuppressed_types=frozenset(unsuppress),
        extra_role_risk_rules=list(role_risk_rules),
    )


_RISK_RULE_KEYS = {"permission", "risk", "reason", "sources", "downgrade"}
_RANK = {"medium": 1, "high": 2}


def _builtin_risk_rules() -> dict:
    # Read directly rather than through role_risk, which merges this config in.
    from app.role_risk import RULES_FILE

    return json.loads(RULES_FILE.read_text())


def _validate_risk_rule(rule, source: Path) -> None:
    """A site rule must be well-formed, cite real sources, and lower nothing by accident.

    Weakening the audit is the one change here that cannot announce itself:
    a lowered rule just stops reporting grants. So a downgrade must be
    confirmed with `"downgrade": true`, and is then named in every coverage
    note (`role_risk.downgrade_note`).
    """
    if (
        not isinstance(rule, dict)
        or not {"permission", "risk", "reason"} <= set(rule)
        or rule["risk"] not in _RANK
    ):
        raise ConfigError(
            f"{source}: each role risk rule needs 'permission', 'reason' and a 'risk' of "
            "'high' or 'medium' (and may add 'sources' and 'downgrade')."
        )
    permission = rule["permission"]
    if unknown := set(rule) - _RISK_RULE_KEYS:
        raise ConfigError(
            f"{source}: role risk rule for {permission} has unknown key(s) "
            f"{', '.join(sorted(unknown))}. Expected: {', '.join(sorted(_RISK_RULE_KEYS))}."
        )

    builtin = _builtin_risk_rules()
    cited = rule.get("sources", [])
    if not isinstance(cited, list) or (missing := sorted(set(cited) - set(builtin["sources"]))):
        raise ConfigError(
            f"{source}: role risk rule for {permission} cites unknown source(s) "
            f"{', '.join(missing) if isinstance(cited, list) else repr(cited)}. "
            f"Known: {', '.join(sorted(builtin['sources']))}."
        )

    was = {r["permission"]: r["risk"] for r in builtin["rules"]}.get(permission)
    lowers = was is not None and _RANK[rule["risk"]] < _RANK[was]
    confirmed = rule.get("downgrade", False)
    if not isinstance(confirmed, bool):
        raise ConfigError(f"{source}: 'downgrade' on {permission} must be true or false.")
    if lowers and not confirmed:
        raise ConfigError(
            f"{source}: the rule for {permission} lowers it from {was} to {rule['risk']}, which "
            'stops some grants being reported. If that is intended, add "downgrade": true; '
            "it will then be named in every coverage note."
        )
    if confirmed and not lowers:
        raise ConfigError(
            f'{source}: the rule for {permission} sets "downgrade": true but does not lower '
            "a built-in rule. Remove it, so it cannot hide a real downgrade later."
        )


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load site configuration, or an empty Config when none is present."""
    explicit = os.environ.get(CONFIG_ENV_VAR)

    for path in config_paths():
        if not path.is_file():
            if explicit and str(path) == explicit:
                raise ConfigError(f"{CONFIG_ENV_VAR} points at {path}, which does not exist.")
            continue
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: invalid JSON ({exc}).") from exc
        return _validate(raw, path)

    return Config()
