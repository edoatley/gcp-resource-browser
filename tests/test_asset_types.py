"""The asset-type mapping is data, and several things read it.

`app/core.py` loads it and `scripts/gcloud-search-resources.sh` reads it with
jq. These tests guard the contract between them, and the shape of the file
itself -- a typo here would silently search for an asset type that does not
exist, which CAI answers with an empty result rather than an error.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from app import core

REPO_ROOT = Path(__file__).resolve().parent.parent
GCLOUD_SCRIPT = REPO_ROOT / "scripts" / "gcloud-search-resources.sh"

# CAI asset types look like "service.googleapis.com/ResourceKind".
ASSET_TYPE = re.compile(r"^[a-z][a-z0-9.-]*\.googleapis\.com/[A-Za-z]+$")


def test_file_parses_and_is_not_empty() -> None:
    data = json.loads(core.ASSET_TYPES_FILE.read_text())
    assert data["types"]
    assert data["types"] == core.ASSET_TYPES


@pytest.mark.parametrize("name", sorted(core.ASSET_TYPES))
def test_friendly_names_are_lowercase_words(name: str) -> None:
    """Names are typed at a shell prompt; keep them boring."""
    assert re.match(r"^[a-z][a-z0-9]*$", name), name


@pytest.mark.parametrize(("name", "asset_type"), sorted(core.ASSET_TYPES.items()))
def test_asset_types_are_well_formed(name: str, asset_type: str) -> None:
    """A malformed type returns an empty result, not an error -- so check shape."""
    assert ASSET_TYPE.match(asset_type), f"{name} -> {asset_type}"


def test_no_duplicate_asset_types() -> None:
    """Two names for one type would make `types` output confusing."""
    seen = sorted(core.ASSET_TYPES.values())
    assert len(seen) == len(set(seen))


def test_friendly_names_do_not_look_like_raw_types() -> None:
    """A name containing `.` or `/` would be treated as a raw type and never resolve."""
    for name in core.ASSET_TYPES:
        assert "." not in name and "/" not in name, name


@pytest.mark.skipif(not GCLOUD_SCRIPT.exists(), reason="differential script missing")
def test_bash_resolves_types_from_the_same_file() -> None:
    """The mapping used to exist twice, in Python and in bash. It must not again."""
    script = f"""
    set -euo pipefail
    TYPES_FILE="{REPO_ROOT}/app/asset_types.json"
    for name in $(jq -r '.types | keys[]' "$TYPES_FILE"); do
      printf '%s=%s\\n' "$name" "$(jq -r --arg n "$name" '.types[$n]' "$TYPES_FILE")"
    done
    """
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout

    from_bash = dict(line.split("=", 1) for line in out.strip().splitlines())
    assert from_bash == core.ASSET_TYPES


def test_gcloud_script_has_no_hardcoded_mapping() -> None:
    """Guard against the case statement creeping back in."""
    body = GCLOUD_SCRIPT.read_text()
    hardcoded = [
        line
        for line in body.splitlines()
        if "googleapis.com/" in line and not line.strip().startswith("#")
    ]
    assert hardcoded == [], f"mapping duplicated in bash: {hardcoded}"
