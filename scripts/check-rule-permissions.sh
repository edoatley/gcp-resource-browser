#!/usr/bin/env bash
# Check every role risk rule names a permission that exists, against the live
# IAM catalogue.
#
# Rules match exact permission strings, so a misspelled or retired permission
# matches nothing and fails silently: it loads, classifies no role, and adds a
# dead alternative to every grants query. This catches it.
#
# Two independent sources, so a miss can be told apart:
#   1. every predefined role's permissions (IAM roles, FULL view) -- is the
#      permission granted by any standard role?
#   2. the permissions testable on a project (queryTestablePermissions, the API
#      behind `gcloud iam list-testable-permissions`) -- does it exist at all,
#      and at what launch stage?
# A permission that exists but sits in no predefined role (e.g. the old
# serviceusage.apiKeys.create) only reaches people through custom roles; one
# that is not testable at all is a typo or retired; DEPRECATED is on its way out.
#
# Independent of the tool: curl and jq. Pass a site config to check its
# role_risk_rules too, and --project to choose the project the testable list is
# read for (default: gcloud's configured project).
#
# Exits non-zero if any rule's permission is missing from either source.
set -euo pipefail

usage() { echo "usage: check-rule-permissions.sh [--project ID] [SITE_CONFIG.json]" >&2; exit 2; }
PROJECT=""
if [[ "${1:-}" == "--project" ]]; then PROJECT="${2:-}"; shift 2 || usage; fi
[[ $# -le 1 && "${1:-}" != "-h" ]] || usage
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT" ]] || { echo "no project: pass --project ID" >&2; exit 2; }

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

token=""
while :; do
  body=$(jq -nc --arg r "//cloudresourcemanager.googleapis.com/projects/$PROJECT" --arg t "$token" \
    '{fullResourceName: $r, pageSize: 1000} + (if $t == "" then {} else {pageToken: $t} end)')
  page=$(curl -sSf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "$body" "https://iam.googleapis.com/v1/permissions:queryTestablePermissions")
  jq -r '(.permissions // [])[] | [.name, (.stage // "GA")] | @tsv' <<< "$page" >> "$tmp/testable"
  token=$(jq -r '.nextPageToken // empty' <<< "$page")
  [[ -n "$token" ]] || break
done
sort -u "$tmp/testable" -o "$tmp/testable"
echo "testable on projects/$PROJECT: $(wc -l < "$tmp/testable" | tr -d ' ') permissions" >&2

{
  jq -r '.rules[].permission' "$RULES"
  if [[ $# -eq 1 ]]; then jq -r '(.role_risk_rules // [])[].permission' "$1"; fi
} | sort -u > "$tmp/rules"

failed=0
while read -r permission; do
  # Testability is per resource type: organization, folder and billing-account
  # permissions are real but not testable on a project. So the testable list
  # only decides what the role catalogue cannot -- does a permission that no
  # role grants exist at all, and is anything we rely on DEPRECATED?
  stage=$(awk -F'\t' -v p="$permission" '$1 == p {print $2}' "$tmp/testable")
  if grep -qx "$permission" "$tmp/all"; then
    if [[ "$stage" == "DEPRECATED" ]]; then
      printf 'WARN\t%s\tDEPRECATED: plan its replacement\n' "$permission"
    fi
  elif [[ -n "$stage" ]]; then
    printf 'NO-ROLE\t%s\texists (%s) but in no predefined role: only custom roles carry it\n' \
      "$permission" "$stage"
    failed=1
  else
    printf 'UNKNOWN\t%s\tin no predefined role and not testable on a project: a typo, or retired\n' \
      "$permission"
    failed=1
  fi
done < "$tmp/rules"

if [[ "$failed" -ne 0 ]]; then exit 1; fi
echo "OK: all $(wc -l < "$tmp/rules" | tr -d ' ') rule permissions exist and are granted by a predefined role." >&2
