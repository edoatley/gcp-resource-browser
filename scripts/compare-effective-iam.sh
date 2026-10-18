#!/usr/bin/env bash
# Diff the tool's inherited IAM bindings against gcloud's for the same scope.
#
# The tool batches names by 20 and splits a batch that fails on one unknown
# name. Unit tests prove it does what it intends; only this proves the result
# matches what Cloud Asset Inventory really returns.
#
# Inheritance stops at the scope: on a project scope this compares what each
# resource inherits from the project. Folder and organization grants only
# appear with a folder or organization scope.
#
# Exits non-zero if the two disagree.
set -euo pipefail

[[ $# -gt 0 ]] || {
  cat >&2 <<'USAGE'
usage: compare-effective-iam.sh --scope SCOPE [--type TYPE]...

examples:
  ./scripts/compare-effective-iam.sh --scope projects/my-project --type bucket
  ./scripts/compare-effective-iam.sh --scope organizations/123 --type project
USAGE
  exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== gcloud ==" >&2
"$HERE/gcloud-effective-iam.sh" "$@" > "$tmp/gcloud.tsv"

echo "== gcpe ==" >&2
uv run --project "$HERE/.." python -m scripts._explorer_effective_iam "$@" > "$tmp/explorer.tsv"

if diff -u "$tmp/gcloud.tsv" "$tmp/explorer.tsv"; then
  echo "MATCH: $(wc -l < "$tmp/gcloud.tsv" | tr -d ' ') inherited bindings agree." >&2
else
  echo "MISMATCH: '-' lines are gcloud-only, '+' lines are explorer-only." >&2
  exit 1
fi
