#!/usr/bin/env bash
# The tool's own output, normalised to match gcloud-search-resources.sh.
#
# The work is in scripts/_explorer_search.py rather than inline here, so it is
# covered by ruff and by tests. An earlier inline version silently went stale
# when search_resources changed signature.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uv run --project "$REPO_ROOT" python -m scripts._explorer_search "$@" | sort
