"""Emit who the tool finds behind each expandable member, as sorted TSV, for diffing.

A module rather than inline Python in bash, so ruff and pytest can see it --
see _explorer_search.py for why that matters.

Lines are `member<TAB>principal`, or `member<TAB>-` for a member that stands
for nobody. Members that could not be read are reported on stderr and exit
non-zero, so the comparison cannot pass by omission.
"""

from __future__ import annotations

import argparse
import sys

from app import core
from app.params import GrantFilters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True)
    args = parser.parse_args()

    try:
        # medium and show_all: the widest grant set, so the most members to check.
        result = core.search_grants(
            GrantFilters(scope=args.scope, min_risk="medium", show_all=True, expand_members=True)
        )
    except core.ResourceExplorerError as exc:
        print(f"explorer error: {exc}", file=sys.stderr)
        return 1

    lines, failed = set(), 0
    for member, found in (result.expansions or {}).items():
        if found.error:
            print(f"explorer could not expand {member}: {found.error}", file=sys.stderr)
            failed += 1
        elif found.members is not None:
            lines |= {f"{member}\t{p}" for p in found.members} or {f"{member}\t-"}
    print(f"explorer: {len(result.expansions or {})} expandable member(s)", file=sys.stderr)
    for line in sorted(lines):
        print(line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
