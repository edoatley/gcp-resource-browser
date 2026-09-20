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

    unknown = set(raw) - {"asset_types", "noise_rules", "unsuppress", "_comment"}
    if unknown:
        # Silently ignoring a typo'd key would leave someone convinced their
        # config was applied when it was not.
        raise ConfigError(
            f"{source}: unknown key(s) {', '.join(sorted(unknown))}. "
            "Expected: asset_types, noise_rules, unsuppress."
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

    return Config(
        source=source,
        extra_asset_types=dict(asset_types),
        extra_noise_rules=list(noise_rules),
        unsuppressed_types=frozenset(unsuppress),
    )


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load site configuration, or an empty Config when none is present."""
    explicit = os.environ.get(CONFIG_ENV_VAR)

    for path in config_paths():
        if not path.is_file():
            if explicit and str(path) == explicit:
                raise ConfigError(
                    f"{CONFIG_ENV_VAR} points at {path}, which does not exist."
                )
            continue
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: invalid JSON ({exc}).") from exc
        return _validate(raw, path)

    return Config()
