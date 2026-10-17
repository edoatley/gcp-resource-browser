"""Emit the projects the `gcpe docs` scope picker lists, as sorted TSV, for diffing.

A module rather than inline Python in bash, so ruff and pytest can see it --
see _explorer_search.py for why that matters.
"""

from __future__ import annotations

import sys

from app import core


def main() -> int:
    try:
        projects = core.list_visible_projects()
    except core.ResourceExplorerError as exc:
        print(f"explorer error: {exc}", file=sys.stderr)
        return 1

    print(f"explorer: {len(projects)} projects", file=sys.stderr)
    for line in sorted(f"{p.project_id}\t{p.number}" for p in projects):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
