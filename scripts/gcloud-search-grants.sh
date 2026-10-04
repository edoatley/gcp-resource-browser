#!/usr/bin/env bash
# gcloud equivalent of `gcpe grants --show-all`.
#
# Deliberately INDEPENDENT of the tool: it runs ONE search per permission
# rather than reusing the tool's batching, and narrows bindings with its own
# jq rather than the tool's Python. A batching or narrowing bug in the tool
# then shows up as a diff instead of being reproduced on both sides. Only the
# permission list is shared -- it is the rule being checked, not the logic.
#
# Service agents are NOT filtered: compare against the tool with --show-all.
# Emits sorted "resource<TAB>role<TAB>member" lines for diffing.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: gcloud-search-grants.sh --scope SCOPE [--role-risk high|medium] [--member-type TYPE]...
USAGE
  exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="$HERE/../app/role_risk_rules.json"
SCOPE="" RISK="high" member_types=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) SCOPE="$2"; shift 2 ;;
    --role-risk) RISK="$2"; shift 2 ;;
    --member-type) member_types+=("$2"); shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$SCOPE" ]] || usage

case "$RISK" in
  high) levels='["high"]' ;;
  medium) levels='["high","medium"]' ;;
  *) echo "unknown risk $RISK" >&2; exit 2 ;;
esac
permissions=$(jq -r --argjson l "$levels" '.rules[] | select(.risk as $r | $l | index($r)) | .permission' "$RULES")

member_term=""
if [[ ${#member_types[@]} -gt 0 ]]; then
  joined=$(printf ' OR %s' "${member_types[@]}"); joined="${joined# OR }"
  member_term=" memberTypes:(${joined})"
fi
types_json=$(printf '%s\n' "${member_types[@]+"${member_types[@]}"}" | jq -R . | jq -s 'map(select(. != ""))')

# Same rationale as gcloud-search-resources.sh: bill where the tool bills.
BILLING_PROJECT="${BILLING_PROJECT:-$(python3 -c "
import json, os
try: print(json.load(open(os.path.expanduser('~/.config/gcloud/application_default_credentials.json'))).get('quota_project_id', ''))
except OSError: pass" 2>/dev/null)}"

echo "gcloud: $(wc -l <<< "$permissions" | tr -d ' ') searches, one per permission" >&2
for permission in $permissions; do
  args=(--scope="$SCOPE" --query="policy.role.permissions:${permission}${member_term}" --format=json)
  [[ -n "$BILLING_PROJECT" ]] && args+=(--billing-project="$BILLING_PROJECT")
  gcloud asset search-all-iam-policies "${args[@]}" \
    | jq -r --argjson types "$types_json" '
        .[] | .resource as $res | (.explanation.matchedPermissions // {} | keys) as $matched
        | .policy.bindings[] | select(.role as $r | $matched | index($r))
        | .role as $role | .members[]
        | select(($types | length) == 0 or (split(":")[0] as $t | $types | index($t)))
        | [$res, $role, .] | @tsv'
done | sort -u
