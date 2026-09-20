#!/usr/bin/env bash
# gcloud equivalent of `gcp-explorer search`.
#
# Deliberately an INDEPENDENT implementation: it builds the CAI query from the
# same user-level flags using its own logic, rather than reusing the tool's
# compiler. Comparing a query against itself would prove nothing.
#
# Emits normalised, sorted JSON lines for diffing.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: gcloud-search-resources.sh --scope SCOPE [options]

  --scope SCOPE       organizations/<id>, folders/<id>, or projects/<id>
  --type TYPE         friendly name or raw CAI type; repeatable.
                      Omit to search every type.
  --term TEXT         free-text term
  --label key=value   repeatable; bare `key` matches any value
  --location LOC      repeatable; several are ORed
USAGE
  exit 2
}

SCOPE="" ; TERM="" ; TYPES=() ; LABELS=() ; LOCATIONS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)    SCOPE="$2"; shift 2 ;;
    --type)     TYPES+=("$2"); shift 2 ;;
    --term)     TERM="$2"; shift 2 ;;
    --label)    LABELS+=("$2"); shift 2 ;;
    --location) LOCATIONS+=("$2"); shift 2 ;;
    -h|--help)  usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done
[[ -n "$SCOPE" ]] || usage

# Friendly name -> CAI asset type, read from the same file app/core.py loads,
# so the mapping cannot drift between the tool and this check. Anything absent
# is passed through as a raw CAI type or RE2 pattern.
TYPES_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/app/asset_types.json"
[[ -f "$TYPES_FILE" ]] || { echo "Missing $TYPES_FILE" >&2; exit 1; }

resolve_type() {
  local resolved
  resolved="$(jq -r --arg n "$1" '.types[$n] // empty' "$TYPES_FILE")"
  echo "${resolved:-$1}"
}

# No --type means every type: gcloud omits --asset-types entirely, matching the
# tool's behaviour when asset_types is empty.
asset_types=""
for t in "${TYPES[@]+"${TYPES[@]}"}"; do
  resolved="$(resolve_type "$t")"
  asset_types="${asset_types:+$asset_types,}$resolved"
done

# Build the query independently of the tool's compiler.
terms=()
[[ -n "$TERM" ]] && terms+=("$TERM")
# CAI requires a namespaced label key to be quoted -- `cloud.googleapis.com/location`
# unquoted is a 400. Plain keys are left bare.
label_field() {
  if [[ "$1" =~ ^[a-z][a-z0-9_-]*$ ]]; then
    echo "labels.$1"
  else
    echo "labels.\"$1\""
  fi
}

for spec in "${LABELS[@]+"${LABELS[@]}"}"; do
  if [[ "$spec" == *=* ]]; then
    terms+=("$(label_field "${spec%%=*}"):${spec#*=}")
  else
    terms+=("$(label_field "$spec"):*")
  fi
done
if [[ ${#LOCATIONS[@]} -eq 1 ]]; then
  terms+=("location:${LOCATIONS[0]}")
elif [[ ${#LOCATIONS[@]} -gt 1 ]]; then
  joined="$(printf ' OR %s' "${LOCATIONS[@]}")"
  terms+=("location:(${joined:4})")
fi

QUERY=""
[[ ${#terms[@]} -gt 0 ]] && QUERY="$(printf '%s ' "${terms[@]}")" && QUERY="${QUERY% }"

# gcloud bills to its own core/project, while the tool bills to the ADC quota
# project. Left to differ, the two sides of the comparison hit different
# projects and one fails with SERVICE_DISABLED while the other works. Default
# to ADC's quota project so both bill to the same place.
BILLING_PROJECT="${BILLING_PROJECT:-$(
  python3 -c "
import json, os, sys
path = os.path.expanduser('~/.config/gcloud/application_default_credentials.json')
try:
    print(json.load(open(path)).get('quota_project_id', ''))
except OSError:
    sys.exit(0)
" 2>/dev/null
)}"

args=(--scope="$SCOPE" --format=json)
[[ -n "$asset_types" ]] && args+=(--asset-types="$asset_types")
[[ -n "$QUERY" ]] && args+=(--query="$QUERY")
[[ -n "$BILLING_PROJECT" ]] && args+=(--billing-project="$BILLING_PROJECT")

echo "gcloud query: ${QUERY:-<none>}" >&2

gcloud asset search-all-resources "${args[@]}" \
  | jq -S -c '.[] | {full_name: .name, asset_type: .assetType, display_name: .displayName,
                     location: .location, labels: (.labels // {})}' \
  | sort
