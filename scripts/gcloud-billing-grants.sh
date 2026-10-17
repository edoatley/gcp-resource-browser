#!/usr/bin/env bash
# gcloud equivalent of `gcpe billing-grants --show-all`.
#
# Deliberately INDEPENDENT of the tool: it classifies roles with jq against
# role_risk_rules.json and the role catalogue straight from `gcloud iam roles
# describe`, rather than reusing the tool's Python lookup. Only the rules file
# is shared -- it is the rule being checked, not the logic.
#
# Billing account IAM is NOT in Cloud Asset Inventory, so there is no
# `gcloud asset` equivalent; this is the Cloud Billing API, same as the tool.
#
# Service agents are NOT filtered: compare against the tool with --show-all.
# Emits sorted "resource<TAB>role<TAB>member" lines for diffing.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: gcloud-billing-grants.sh [--role-risk high|medium] [--member-type TYPE]...
USAGE
  exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="$HERE/../app/role_risk_rules.json"
RISK="high" member_types=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role-risk) RISK="$2"; shift 2 ;;
    --member-type) member_types+=("$2"); shift 2 ;;
    *) usage ;;
  esac
done

case "$RISK" in
  high) levels='["high"]' ;;
  medium) levels='["high","medium"]' ;;
  *) echo "unknown risk $RISK" >&2; exit 2 ;;
esac
permissions=$(jq -c --argjson l "$levels" \
  '[.rules[] | select(.risk as $r | $l | index($r)) | .permission]' "$RULES")
types_json=$(printf '%s\n' "${member_types[@]+"${member_types[@]}"}" | jq -R . | jq -s 'map(select(. != ""))')

accounts=$(gcloud billing accounts list --format='value(name)')
[[ -n "$accounts" ]] || { echo "no billing accounts visible" >&2; exit 0; }
echo "gcloud: $(wc -l <<< "$accounts" | tr -d ' ') billing account(s)" >&2

# Role -> whether it holds a risky permission, resolved once per distinct role
# with `gcloud iam roles describe`. Cached in a temp dir so repeated roles
# across accounts cost one call, matching the tool's single catalogue read.
cache="$(mktemp -d)"; trap 'rm -rf "$cache"' EXIT

risky_role() {
  local role="$1" file="$cache/${1//\//_}"
  if [[ ! -f "$file" ]]; then
    gcloud iam roles describe "$role" --format=json 2>/dev/null \
      | jq -c --argjson want "$permissions" \
          '[(.includedPermissions // [])[] | select(. as $p | $want | index($p))] | sort' \
      > "$file" || echo '[]' > "$file"
    [[ -s "$file" ]] || echo '[]' > "$file"
  fi
  [[ "$(cat "$file")" != "[]" ]]
}

for account in $accounts; do
  policy=$(gcloud billing accounts get-iam-policy "$account" --format=json)
  while IFS=$'\t' read -r role member; do
    [[ -n "$role" ]] || continue
    risky_role "$role" || continue
    printf '//cloudbilling.googleapis.com/billingAccounts/%s\t%s\t%s\n' "$account" "$role" "$member"
  done < <(jq -r --argjson types "$types_json" '
      .bindings[]? | .role as $role | .members[]
      | select(($types | length) == 0 or (split(":")[0] as $t | $types | index($t)))
      | [$role, .] | @tsv' <<< "$policy")
done | sort -u
