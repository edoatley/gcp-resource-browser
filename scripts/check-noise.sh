#!/usr/bin/env bash
# Verify noise reduction against real data.
#
# The differential check compares what CAI returned, with suppression off --
# gcloud has no equivalent notion, so including it would compare two different
# questions. This script covers the remaining property, which is the one that
# matters for an audit tool:
#
#   the suppressed result must be a strict SUBSET of the full result.
#
# Noise reduction may only ever remove rows. If it ever added, reordered into
# existence, or altered one, that would be a silent corruption of an audit.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  cat >&2 <<'USAGE'
usage: check-noise.sh <scope>

  Verifies that noise reduction only ever removes rows: the suppressed result
  must be a strict subset of the full one.

example:
  ./scripts/check-noise.sh projects/my-project
USAGE
  exit 2
fi
SCOPE="$1"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== full result (suppression off) ==" >&2
"$HERE/explorer-search-resources.sh" --scope "$SCOPE" > "$tmp/full.jsonl"

echo "== suppressed result ==" >&2
uv run --project "$HERE/.." python -m scripts._explorer_search \
  --scope "$SCOPE" --suppress-noise 2>/dev/null | sort > "$tmp/shown.jsonl"

full=$(wc -l < "$tmp/full.jsonl" | tr -d ' ')
shown=$(wc -l < "$tmp/shown.jsonl" | tr -d ' ')
hidden=$((full - shown))

# Anything in the shown set that is not in the full set is a corruption.
if invented=$(comm -13 "$tmp/full.jsonl" "$tmp/shown.jsonl") && [[ -z "$invented" ]]; then
  pct=$(( full > 0 ? 100 * hidden / full : 0 ))
  echo "OK: $shown of $full shown, $hidden hidden (${pct}%). Shown is a strict subset." >&2
else
  echo "CORRUPTION: noise reduction produced rows absent from the full result:" >&2
  echo "$invented" >&2
  exit 1
fi
