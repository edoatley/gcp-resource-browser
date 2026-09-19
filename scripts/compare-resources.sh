#!/usr/bin/env bash
# Diff the tool's results against gcloud's for the same query.
# Exits non-zero if they disagree, so it can be wired into CI against a
# known fixture project.
set -euo pipefail

SCOPE="${1:?usage: $0 <scope> <resource-type> [query]}"
RESOURCE_TYPE="${2:?usage: $0 <scope> <resource-type> [query]}"
QUERY="${3:-}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Querying via gcloud..." >&2
"$HERE/gcloud-search-resources.sh" "$SCOPE" "$RESOURCE_TYPE" "$QUERY" > "$tmp/gcloud.jsonl"

echo "Querying via gcp-explorer..." >&2
"$HERE/explorer-search-resources.sh" "$SCOPE" "$RESOURCE_TYPE" "$QUERY" > "$tmp/explorer.jsonl"

if diff -u "$tmp/gcloud.jsonl" "$tmp/explorer.jsonl"; then
  echo "MATCH: $(wc -l < "$tmp/gcloud.jsonl" | tr -d ' ') resources agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
