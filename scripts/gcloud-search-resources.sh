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

# Friendly name -> CAI asset type. Kept in step with app/core.py ASSET_TYPES.
resolve_type() {
  case "$1" in
    bucket)         echo "storage.googleapis.com/Bucket" ;;
    cloudrun)       echo "run.googleapis.com/Service" ;;
    vm)             echo "compute.googleapis.com/Instance" ;;
    disk)           echo "compute.googleapis.com/Disk" ;;
    network)        echo "compute.googleapis.com/Network" ;;
    subnet)         echo "compute.googleapis.com/Subnetwork" ;;
    firewall)       echo "compute.googleapis.com/Firewall" ;;
    address)        echo "compute.googleapis.com/Address" ;;
    forwardingrule) echo "compute.googleapis.com/ForwardingRule" ;;
    gke)            echo "container.googleapis.com/Cluster" ;;
    function)       echo "cloudfunctions.googleapis.com/CloudFunction" ;;
    sql)            echo "sqladmin.googleapis.com/Instance" ;;
    spanner)        echo "spanner.googleapis.com/Instance" ;;
    topic)          echo "pubsub.googleapis.com/Topic" ;;
    subscription)   echo "pubsub.googleapis.com/Subscription" ;;
    dataset)        echo "bigquery.googleapis.com/Dataset" ;;
    table)          echo "bigquery.googleapis.com/Table" ;;
    secret)         echo "secretmanager.googleapis.com/Secret" ;;
    serviceaccount) echo "iam.googleapis.com/ServiceAccount" ;;
    project)        echo "cloudresourcemanager.googleapis.com/Project" ;;
    *)              echo "$1" ;;   # raw CAI type or RE2 pattern
  esac
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
