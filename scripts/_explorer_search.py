"""Emit the tool's search results as normalised JSON lines, for diffing.

A real module rather than Python inlined into a bash heredoc: inlined code is
invisible to ruff and pytest, and this script silently broke when
`search_resources` changed signature. Nothing caught it until the differential
check was run by hand.
"""

from __future__ import annotations

import argparse
import json
import sys

from app import core
from app.params import SearchFilters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--type", dest="types", action="append", default=[])
    parser.add_argument("--term", default="")
    parser.add_argument("--label", dest="labels", action="append", default=[])
    parser.add_argument("--location", dest="locations", action="append", default=[])
    parser.add_argument("--project", dest="projects", action="append", default=[])
    parser.add_argument(
        "--suppress-noise",
        action="store_true",
        help=(
            "Apply noise reduction. Off by default: gcloud has no equivalent, so the "
            "comparison must be of what CAI returned, not of what we chose to display."
        ),
    )
    args = parser.parse_args()

    try:
        result = core.search_resources(
            SearchFilters(
                scope=args.scope,
                resource_types=args.types,
                free_text=args.term,
                labels=args.labels,
                locations=args.locations,
                projects=args.projects,
                limit=None,
                show_all=not args.suppress_noise,
            )
        )
    except core.ResourceExplorerError as exc:
        print(f"explorer error: {exc}", file=sys.stderr)
        return 1

    print(f"explorer query: {result.query or '<none>'}", file=sys.stderr)
    if result.suppressed:
        print(f"explorer suppressed: {result.suppressed}", file=sys.stderr)
    for resource in result.resources:
        print(
            json.dumps(
                {
                    "full_name": resource.full_name,
                    "asset_type": resource.asset_type,
                    "display_name": resource.display_name,
                    "location": resource.location,
                    "labels": resource.labels,
                },
                sort_keys=True,
                # Match `jq -c` exactly, so the diff shows real disagreements
                # rather than whitespace.
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
