#!/usr/bin/env bash
# gcloud equivalent of: gcp-explorer list-resources <scope> <type>
#
# Emits the same fields the tool emits, as sorted JSON lines, so the output can
# be diffed directly against scripts/explorer-search-resources.sh.
set -euo pipefail

SCOPE="${1:?usage: $0 <scope> <resource-type> [query]}"
RESOURCE_TYPE="${2:?usage: $0 <scope> <resource-type> [query]}"
QUERY="${3:-}"

case "$RESOURCE_TYPE" in
  bucket)   ASSET_TYPE="storage.googleapis.com/Bucket" ;;
  cloudrun) ASSET_TYPE="run.googleapis.com/Service" ;;
  *) echo "Unknown resource type: $RESOURCE_TYPE (expected bucket or cloudrun)" >&2; exit 2 ;;
esac

args=(
  --scope="$SCOPE"
  --asset-types="$ASSET_TYPE"
  --format=json
)
[[ -n "$QUERY" ]] && args+=(--query="$QUERY")

gcloud asset search-all-resources "${args[@]}" \
  | jq -S -c '.[] | {full_name: .name, asset_type: .assetType, display_name: .displayName,
                     location: .location, labels: (.labels // {})}' \
  | sort
