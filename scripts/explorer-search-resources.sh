#!/usr/bin/env bash
# The tool's own output, normalised to match gcloud-search-resources.sh.
set -euo pipefail

SCOPE="${1:?usage: $0 <scope> <resource-type> [query]}"
RESOURCE_TYPE="${2:?usage: $0 <scope> <resource-type> [query]}"
QUERY="${3:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

uv run --project "$REPO_ROOT" python -c '
import json, sys
from app import core
scope, resource_type, query = sys.argv[1], sys.argv[2], sys.argv[3]
resources, _ = core.search_resources(scope, resource_type, query=query, limit=None)
for r in resources:
    print(json.dumps({
        "full_name": r.full_name,
        "asset_type": r.asset_type,
        "display_name": r.display_name,
        "location": r.location,
        "labels": r.labels,
    }, sort_keys=True))
' "$SCOPE" "$RESOURCE_TYPE" "$QUERY" | sort
