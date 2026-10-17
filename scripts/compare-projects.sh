#!/usr/bin/env bash
# Diff the `gcpe docs` scope picker's project list against gcloud's.
#
# The picker is the one place the tool lists projects rather than searching
# within a scope, so it gets its own differential check: a picker missing a
# project steers someone away from part of their estate without saying so.
#
# Exits non-zero if the two disagree.
set -euo pipefail

[[ $# -eq 0 ]] || { echo "usage: compare-projects.sh" >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== gcloud ==" >&2
"$HERE/gcloud-list-projects.sh" > "$tmp/gcloud.tsv"

echo "== gcpe ==" >&2
uv run --project "$HERE/.." python -m scripts._explorer_projects > "$tmp/explorer.tsv"

if diff -u "$tmp/gcloud.tsv" "$tmp/explorer.tsv"; then
  echo "MATCH: $(wc -l < "$tmp/gcloud.tsv" | tr -d ' ') projects agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
