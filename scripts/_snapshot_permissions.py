"""Refresh tests/fixtures/iam_permissions.txt.gz from the live IAM catalogue.

The snapshot lets a unit test check, offline, that every role risk rule names a
real permission. Refresh it when adding a rule for a permission newer than the
snapshot; `scripts/check-rule-permissions.sh` is the live check.

    uv run python -m scripts._snapshot_permissions
"""

from __future__ import annotations

import datetime
import gzip
import sys
from pathlib import Path

from app import core

SNAPSHOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "iam_permissions.txt.gz"


def main() -> int:
    roles = core.fetch_role_catalogue()
    permissions = sorted({p for role in roles for p in role.get("includedPermissions", [])})
    header = (
        f"# Every permission in a predefined IAM role, {datetime.date.today().isoformat()}: "
        f"{len(permissions)} from {len(roles)} roles. Refresh: "
        "uv run python -m scripts._snapshot_permissions\n"
    )
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the file byte-identical when the catalogue has not changed.
    with gzip.GzipFile(SNAPSHOT, "wb", mtime=0) as out:
        out.write((header + "\n".join(permissions) + "\n").encode())
    print(f"wrote {SNAPSHOT} ({len(permissions)} permissions)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
