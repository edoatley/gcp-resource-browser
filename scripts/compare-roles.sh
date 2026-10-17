#!/usr/bin/env bash
# Diff the tool's risky-role classification against an independent jq one.
#
# Unit tests prove the classifier does what it intends against a fake
# catalogue. Only this proves it classifies the REAL catalogue the same way a
# second implementation does -- and, with --scope, that custom roles are read
# from the right collection.
#
# Exits non-zero if the two disagree.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: compare-roles.sh [--risk high|medium] [--scope organizations/ID|projects/ID]

examples:
  ./scripts/compare-roles.sh
  ./scripts/compare-roles.sh --risk medium
  ./scripts/compare-roles.sh --scope projects/my-project
USAGE
  exit 2
}

[[ "${1:-}" != "-h" && "${1:-}" != "--help" ]] || usage

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== gcloud ==" >&2
"$HERE/gcloud-list-roles.sh" "$@" > "$tmp/gcloud.tsv"

echo "== gcpe ==" >&2
uv run --project "$HERE/.." python -m scripts._explorer_roles "$@" > "$tmp/explorer.tsv"

if diff -u "$tmp/gcloud.tsv" "$tmp/explorer.tsv"; then
  echo "MATCH: $(wc -l < "$tmp/gcloud.tsv" | tr -d ' ') roles agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
