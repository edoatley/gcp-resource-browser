"""Emit the tool's inherited IAM bindings as sorted TSV lines, for diffing against gcloud.

A module rather than inline Python in bash, so ruff and pytest can see it --
see _explorer_search.py for why that matters.
"""

from __future__ import annotations

import argparse
import sys

from app import core
from app.params import SearchFilters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--type", dest="types", action="append", default=[])
    args = parser.parse_args()

    try:
        # show_all: gcloud has no noise reduction, so compare every resource.
        result = core.search_resources(
            SearchFilters(
                scope=args.scope,
                resource_types=args.types,
                show_all=True,
                limit=None,
                include_iam=True,
                effective_iam=True,
            )
        )
    except core.ResourceExplorerError as exc:
        print(f"explorer error: {exc}", file=sys.stderr)
        return 1

    print(f"explorer: {len(result.resources)} resources", file=sys.stderr)
    lines = {f"{name}\tUNREADABLE" for name in result.iam_errors or {}}
    lines |= {
        f"{resource.full_name}\t{binding.inherited_from}\t{binding.role}\t{member}"
        for resource in result.resources
        for binding in resource.iam_bindings or []
        if binding.inherited_from
        for member in binding.members
    }
    for line in sorted(lines):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
