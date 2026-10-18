#!/usr/bin/env bash
# Diff the tool's --expand-members against gcloud for one scope.
#
# The tool picks the members (the grant search is already checked by
# compare-grants.sh); this checks who it says is behind each of them.
#
# Exits non-zero if the two disagree, or if the tool could not expand one.
set -euo pipefail

usage() { echo "usage: compare-member-expansion.sh --scope projects/ID|folders/ID|organizations/ID" >&2; exit 2; }
[[ "${1:-}" == "--scope" && -n "${2:-}" ]] || usage
SCOPE="$2"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== gcpe ==" >&2
uv run --project "$HERE/.." python -m scripts._explorer_expansions --scope "$SCOPE" > "$tmp/explorer.tsv"

members=$(cut -f1 "$tmp/explorer.tsv" | sort -u)
if [[ -z "$members" ]]; then
  echo "NOTHING TO COMPARE: no expandable members in $SCOPE." >&2
  exit 0
fi

echo "== gcloud ==" >&2
# shellcheck disable=SC2086  # one member per word, by design
"$HERE/gcloud-expand-members.sh" $members > "$tmp/gcloud.tsv"

if diff -u "$tmp/gcloud.tsv" "$tmp/explorer.tsv"; then
  echo "MATCH: $(cut -f1 "$tmp/gcloud.tsv" | sort -u | wc -l | tr -d ' ') member(s) agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
