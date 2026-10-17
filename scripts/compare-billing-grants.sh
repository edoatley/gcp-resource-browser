#!/usr/bin/env bash
# Diff the tool's risky billing-account grants against gcloud's.
#
# This path classifies roles locally, from the predefined catalogue, because
# the Cloud Billing API returns role names and nothing else -- there is no CAI
# `matched_permissions` to lean on. That makes an independent check worth more
# here than on the CAI path, not less.
#
# Exits non-zero if the two disagree.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: compare-billing-grants.sh [--role-risk high|medium] [--member-type TYPE]...

examples:
  ./scripts/compare-billing-grants.sh
  ./scripts/compare-billing-grants.sh --role-risk medium --member-type user --member-type group
USAGE
  exit 2
}

[[ "${1:-}" != "-h" && "${1:-}" != "--help" ]] || usage

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== gcloud ==" >&2
"$HERE/gcloud-billing-grants.sh" "$@" > "$tmp/gcloud.tsv"

echo "== gcpe ==" >&2
uv run --project "$HERE/.." python -m scripts._explorer_billing_grants "$@" > "$tmp/explorer.tsv"

if diff -u "$tmp/gcloud.tsv" "$tmp/explorer.tsv"; then
  echo "MATCH: $(wc -l < "$tmp/gcloud.tsv" | tr -d ' ') grants agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
