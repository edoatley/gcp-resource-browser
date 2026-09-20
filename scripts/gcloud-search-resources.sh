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
usage: gcloud-search-resources.sh --scope SCOPE --type TYPE [options]

  --scope SCOPE       organizations/<id>, folders/<id>, or projects/<id>
  --type TYPE         friendly name or raw CAI type; repeatable
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
[[ -n "$SCOPE" && ${#TYPES[@]} -gt 0 ]] || usage

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

asset_types=""
for t in "${TYPES[@]}"; do
  resolved="$(resolve_type "$t")"
  asset_types="${asset_types:+$asset_types,}$resolved"
done

# Build the query independently of the tool's compiler.
terms=()
[[ -n "$TERM" ]] && terms+=("$TERM")
for spec in "${LABELS[@]+"${LABELS[@]}"}"; do
  if [[ "$spec" == *=* ]]; then
    terms+=("labels.${spec%%=*}:${spec#*=}")
  else
    terms+=("labels.${spec}:*")
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

args=(--scope="$SCOPE" --asset-types="$asset_types" --format=json)
[[ -n "$QUERY" ]] && args+=(--query="$QUERY")

echo "gcloud query: ${QUERY:-<none>}" >&2

gcloud asset search-all-resources "${args[@]}" \
  | jq -S -c '.[] | {full_name: .name, asset_type: .assetType, display_name: .displayName,
                     location: .location, labels: (.labels // {})}' \
  | sort
