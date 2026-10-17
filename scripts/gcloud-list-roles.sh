#!/usr/bin/env bash
# Independent equivalent of `gcpe roles`.
#
# Deliberately INDEPENDENT of the tool: it classifies roles with jq against
# role_risk_rules.json rather than reusing the tool's Python classifier. Only
# the rules file is shared -- it is the rule being checked, not the logic.
#
# It calls the IAM REST API with curl rather than `gcloud iam roles list`,
# because the gcloud command has no way to ask for the FULL view and so never
# returns `includedPermissions` -- without which there is nothing to classify.
# `gcloud auth print-access-token` still supplies the credentials, and the
# classification, which is what is under test, is entirely jq's.
#
# Pass --scope to add the custom roles defined on an organization or a project.
# IAM has no folder-level custom roles, so a folder scope is refused here
# exactly as the tool refuses it.
#
# Emits sorted "risk<TAB>role" lines for diffing.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: gcloud-list-roles.sh [--risk high|medium] [--scope organizations/ID|projects/ID]
USAGE
  exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="$HERE/../app/role_risk_rules.json"
RISK="high" SCOPE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --risk) RISK="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    *) usage ;;
  esac
done

case "$RISK" in
  high) levels='["high"]' ;;
  medium) levels='["high","medium"]' ;;
  *) echo "unknown risk $RISK" >&2; exit 2 ;;
esac
rules=$(jq -c --argjson l "$levels" \
  '[.rules[] | select(.risk as $r | $l | index($r)) | {permission, risk}]' "$RULES")

TOKEN="$(gcloud auth print-access-token)"

# A role's risk is the highest risk of any permission it holds -- the tool's
# definition, re-expressed in jq.
# shellcheck disable=SC2016  # $rules and $role are jq variables, not shell ones.
CLASSIFY='
  def rank: if . == "high" then 2 else 1 end;
  (.roles // [])[] | . as $role
  | [(.includedPermissions // [])[] as $p | $rules[] | select(.permission == $p) | .risk]
  | select(length > 0) | (max_by(rank)) as $risk
  | [$risk, $role.name] | @tsv'

list_roles() {  # $1: the collection, "" for predefined
  local prefix="$1" token="" page next
  while :; do
    page=$(curl -sSf -H "Authorization: Bearer $TOKEN" \
      "https://iam.googleapis.com/v1/${prefix}roles?view=FULL&pageSize=1000${token}")
    jq -r --argjson rules "$rules" "$CLASSIFY" <<< "$page"
    next=$(jq -r '.nextPageToken // empty' <<< "$page")
    [[ -n "$next" ]] || break
    token="&pageToken=$next"
  done
}

{
  list_roles ""
  if [[ -n "$SCOPE" ]]; then
    case "${SCOPE%%/*}" in
      organizations|projects) list_roles "${SCOPE}/" ;;
      *) echo "custom roles cannot be defined on ${SCOPE%%/*}; use organizations/ID or projects/ID" >&2
         exit 2 ;;
    esac
  fi
} | sort -u
