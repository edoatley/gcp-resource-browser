#!/usr/bin/env bash
# Diff the tool's risky grants against gcloud's for the same risk level.
#
# The grant search batches permissions to fit CAI's alternation limit and then
# narrows whole policies down to matched bindings. Unit tests prove it does
# what it intends; only this proves the result matches what CAI really holds.
#
# Exits non-zero if the two disagree.
set -euo pipefail

[[ $# -gt 0 ]] || {
  cat >&2 <<'USAGE'
usage: compare-grants.sh --scope SCOPE [--role-risk high|medium] [--member-type TYPE]...

examples:
  ./scripts/compare-grants.sh --scope projects/my-project
  ./scripts/compare-grants.sh --scope organizations/123 --role-risk medium \
      --member-type user --member-type group
USAGE
  exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== gcloud ==" >&2
"$HERE/gcloud-search-grants.sh" "$@" > "$tmp/gcloud.tsv"

echo "== gcpe ==" >&2
uv run --project "$HERE/.." python -m scripts._explorer_grants "$@" > "$tmp/explorer.tsv"

if diff -u "$tmp/gcloud.tsv" "$tmp/explorer.tsv"; then
  echo "MATCH: $(wc -l < "$tmp/gcloud.tsv" | tr -d ' ') grants agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
