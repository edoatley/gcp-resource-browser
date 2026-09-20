#!/usr/bin/env bash
# The tool's own output, normalised to match gcloud-search-resources.sh.
set -euo pipefail

SCOPE="" ; TERM="" ; TYPES=() ; LABELS=() ; LOCATIONS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)    SCOPE="$2"; shift 2 ;;
    --type)     TYPES+=("$2"); shift 2 ;;
    --term)     TERM="$2"; shift 2 ;;
    --label)    LABELS+=("$2"); shift 2 ;;
    --location) LOCATIONS+=("$2"); shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TERM="$TERM" \
TYPES="$(printf '%s\n' "${TYPES[@]+"${TYPES[@]}"}")" \
LABELS="$(printf '%s\n' "${LABELS[@]+"${LABELS[@]}"}")" \
LOCATIONS="$(printf '%s\n' "${LOCATIONS[@]+"${LOCATIONS[@]}"}")" \
SCOPE="$SCOPE" \
uv run --project "$REPO_ROOT" python -c '
import json, os, sys
from app import core

def lines(name):
    return [v for v in os.environ.get(name, "").splitlines() if v]

result = core.search_resources(
    scope=os.environ["SCOPE"],
    resource_types=lines("TYPES"),
    free_text=os.environ.get("TERM", ""),
    labels=lines("LABELS"),
    locations=lines("LOCATIONS"),
    limit=None,
)
print(f"explorer query: {result.query or \"<none>\"}", file=sys.stderr)
for r in result.resources:
    print(json.dumps({
        "full_name": r.full_name,
        "asset_type": r.asset_type,
        "display_name": r.display_name,
        "location": r.location,
        "labels": r.labels,
    }, sort_keys=True))
' | sort
