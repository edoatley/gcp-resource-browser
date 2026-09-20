#!/usr/bin/env bash
# Diff the tool's results against gcloud's for the same filters.
#
# Unit tests fake the CAI client, so they can only prove the tool sends the
# query it intended to send -- never that the query is the RIGHT one. This
# catches a filter that compiles to valid-but-wrong syntax, which returns a
# plausible result set that is quietly missing rows.
#
# Exits non-zero if the two disagree.
set -euo pipefail

[[ $# -gt 0 ]] || {
  cat >&2 <<'USAGE'
usage: compare-resources.sh --scope SCOPE --type TYPE [--term TEXT] [--label k=v] [--location LOC]

examples:
  ./scripts/compare-resources.sh --scope projects/my-project --type bucket
  ./scripts/compare-resources.sh --scope organizations/123 --type bucket --type vm \
      --label env=prod --location europe-west2 --location europe-west1
USAGE
  exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== gcloud ==" >&2
"$HERE/gcloud-search-resources.sh" "$@" > "$tmp/gcloud.jsonl"

echo "== gcp-explorer ==" >&2
"$HERE/explorer-search-resources.sh" "$@" > "$tmp/explorer.jsonl"

if diff -u "$tmp/gcloud.jsonl" "$tmp/explorer.jsonl"; then
  echo "MATCH: $(wc -l < "$tmp/gcloud.jsonl" | tr -d ' ') resources agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
