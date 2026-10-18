#!/usr/bin/env bash
# Check every role risk rule names a permission that exists, against the live
# IAM catalogue.
#
# Rules match exact permission strings, so a misspelled or retired permission
# matches nothing and fails silently: it loads, classifies no role, and adds a
# dead alternative to every grants query. This catches it.
#
# Independent of the tool: the catalogue comes from the IAM REST API with curl
# (gcloud cannot ask for the FULL view) and the check is jq's. Pass a site
# config to check its role_risk_rules too.
#
# Exits non-zero if any rule's permission is in no predefined role.
set -euo pipefail

usage() { echo "usage: check-rule-permissions.sh [SITE_CONFIG.json]" >&2; exit 2; }
[[ $# -le 1 && "${1:-}" != "-h" ]] || usage

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="$HERE/../app/role_risk_rules.json"
TOKEN="$(gcloud auth print-access-token)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

token=""
while :; do
  page=$(curl -sSf -H "Authorization: Bearer $TOKEN" \
    "https://iam.googleapis.com/v1/roles?view=FULL&pageSize=1000${token}")
  jq -r '(.roles // [])[] | (.includedPermissions // [])[]' <<< "$page" >> "$tmp/all"
  next=$(jq -r '.nextPageToken // empty' <<< "$page")
  [[ -n "$next" ]] || break
  token="&pageToken=$next"
done
sort -u "$tmp/all" -o "$tmp/all"
echo "catalogue: $(wc -l < "$tmp/all" | tr -d ' ') distinct permissions in predefined roles" >&2

{
  jq -r '.rules[].permission' "$RULES"
  if [[ $# -eq 1 ]]; then jq -r '(.role_risk_rules // [])[].permission' "$1"; fi
} | sort -u > "$tmp/rules"

missing=$(comm -23 "$tmp/rules" "$tmp/all")
if [[ -n "$missing" ]]; then
  echo "MISSING: these rule permissions are in no predefined role (typo, or retired):" >&2
  printf '%s\n' "$missing"
  exit 1
fi
echo "OK: all $(wc -l < "$tmp/rules" | tr -d ' ') rule permissions exist." >&2
