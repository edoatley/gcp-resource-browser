"""Emit the tool's risky billing-account grants as sorted TSV, for diffing.

A module rather than inline Python in bash, so ruff and pytest can see it --
see _explorer_search.py for why that matters.
"""

from __future__ import annotations

import argparse
import sys

from app import core
from app.params import BillingGrantFilters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-risk", default="high")
    parser.add_argument("--member-type", dest="member_types", action="append", default=[])
    args = parser.parse_args()

    try:
        # show_all: gcloud cannot tell a service agent apart, so compare everything.
        result = core.search_billing_grants(
            BillingGrantFilters(
                min_risk=args.role_risk,
                member_types=args.member_types,
                show_all=True,
                limit=None,
            )
        )
    except core.ResourceExplorerError as exc:
        print(f"explorer error: {exc}", file=sys.stderr)
        return 1

    print(f"explorer: {len(result.accounts)} billing account(s)", file=sys.stderr)
    if result.unclassified_roles:
        # Not a diff failure in itself, but the gcloud side resolves every role
        # it sees, so it WILL report any of these that turn out to be risky.
        print(
            f"explorer: {len(result.unclassified_roles)} role(s) not in the predefined "
            f"catalogue and therefore unclassified: {', '.join(result.unclassified_roles)}",
            file=sys.stderr,
        )
    for line in sorted({f"{g.resource}\t{g.role}\t{g.member}" for g in result.grants}):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
