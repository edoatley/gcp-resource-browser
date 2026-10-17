#!/usr/bin/env bash
# Independent equivalent of the `gcpe docs` scope picker.
#
# `gcloud projects list` reads Resource Manager v1, while the tool searches v3
# (`projects:search`), so the two share neither code nor API version. Only
# ACTIVE projects: the picker drops those pending deletion, which cannot be
# searched.
#
# Emits sorted "project_id<TAB>number" lines for diffing.
set -euo pipefail

[[ $# -eq 0 ]] || { echo "usage: gcloud-list-projects.sh" >&2; exit 2; }

gcloud projects list --filter='lifecycleState:ACTIVE' \
  --format='value(projectId,projectNumber)' | tr -s ' ' '\t' | sort
