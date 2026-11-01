#!/usr/bin/env bash
# gcloud equivalent of `gcpe search --include-iam --effective --show-all`,
# reduced to the INHERITED bindings.
#
# Deliberately INDEPENDENT of the tool: it lists the names with its own gcloud
# search and reads them ONE PER CALL, rather than in the tool's batches of 20
# with its retry-on-NotFound split. A batching or splitting bug in the tool
# then shows up as a diff rather than being reproduced on both sides.
#
# A name gcloud cannot read is emitted as "resource<TAB>UNREADABLE", which is
# how the tool's `iam_errors` are emitted too, so the two can agree on gaps.
# Emits sorted "resource<TAB>inherited_from<TAB>role<TAB>member" lines.
set -euo pipefail

usage() {
  echo "usage: gcloud-effective-iam.sh --scope SCOPE [--type TYPE]..." >&2
  exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TYPES_FILE="$HERE/../app/asset_types.json"
SCOPE="" types=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) SCOPE="$2"; shift 2 ;;
    --type) types+=("$(jq -r --arg t "$2" '.types[$t] // $t' "$TYPES_FILE")"); shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$SCOPE" ]] || usage

# Same rationale as gcloud-search-resources.sh: bill where the tool bills.
BILLING_PROJECT="${BILLING_PROJECT:-$(python3 -c "
import json, os
try: print(json.load(open(os.path.expanduser('~/.config/gcloud/application_default_credentials.json'))).get('quota_project_id', ''))
except OSError: pass" 2>/dev/null)}"
billing=()
[[ -n "$BILLING_PROJECT" ]] && billing=(--billing-project="$BILLING_PROJECT")

search=(--scope="$SCOPE" --format='value(name)')
if [[ ${#types[@]} -gt 0 ]]; then
  search+=(--asset-types="$(IFS=,; echo "${types[*]}")")
fi
names=$(gcloud asset search-all-resources "${search[@]}" "${billing[@]+"${billing[@]}"}")

echo "gcloud: $(grep -c . <<< "$names" || true) resources, one get-effective-iam-policy each" >&2
for name in $names; do
  if ! policy=$(gcloud asset get-effective-iam-policy --scope="$SCOPE" --names="$name" \
      --format=json "${billing[@]+"${billing[@]}"}" 2>/dev/null); then
    printf '%s\tUNREADABLE\n' "$name"
    continue
  fi
  jq -r '
    .policyResults[] | .fullResourceName as $res
    | .policies[] | select(.attachedResource != $res) | .attachedResource as $from
    | (.policy.bindings // [])[] | .role as $role | .members[]
    | [$res, $from, $role, .] | @tsv' <<< "$policy"
done | sort -u
