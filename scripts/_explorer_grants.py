"""Emit the tool's risky grants as sorted TSV lines, for diffing against gcloud.

A module rather than inline Python in bash, so ruff and pytest can see it --
see _explorer_search.py for why that matters.
"""

from __future__ import annotations

import argparse
import sys

from app import core
from app.params import GrantFilters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--role-risk", default="high")
    parser.add_argument("--member-type", dest="member_types", action="append", default=[])
    args = parser.parse_args()

    try:
        # show_all: gcloud cannot tell a service agent apart, so compare everything.
        result = core.search_grants(
            GrantFilters(
                scope=args.scope,
                min_risk=args.role_risk,
                member_types=args.member_types,
                show_all=True,
                limit=None,
            )
        )
    except core.ResourceExplorerError as exc:
        print(f"explorer error: {exc}", file=sys.stderr)
        return 1

    print(f"explorer: {len(result.queries)} batched queries", file=sys.stderr)
    for line in sorted({f"{g.resource}\t{g.role}\t{g.member}" for g in result.grants}):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
