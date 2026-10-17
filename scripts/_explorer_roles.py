"""Emit the tool's risky roles as sorted TSV lines, for diffing against gcloud.

A module rather than inline Python in bash, so ruff and pytest can see it --
see _explorer_search.py for why that matters.
"""

from __future__ import annotations

import argparse
import sys

from app import core


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--risk", default="high")
    parser.add_argument("--scope", default=None)
    args = parser.parse_args()

    try:
        # show_all: the gcloud side cannot tell a service-agent role apart,
        # so both sides compare the whole catalogue.
        result = core.list_risky_roles(args.risk, show_all=True, scope=args.scope)
    except core.ResourceExplorerError as exc:
        print(f"explorer error: {exc}", file=sys.stderr)
        return 1

    print(f"explorer: {result.count} roles at {result.min_risk} or above", file=sys.stderr)
    for line in sorted({f"{r.risk}\t{r.name}" for r in result.data}):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
