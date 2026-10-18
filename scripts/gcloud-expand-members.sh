#!/usr/bin/env bash
# Independent equivalent of --expand-members, for the members given as arguments.
#
# The tool reads a project role from the project's policy through Cloud Asset
# Inventory; this reads it through Resource Manager's getIamPolicy, so the two
# share neither code nor API. Groups go through `gcloud identity`.
#
# Emits sorted "member<TAB>principal" lines, or "member<TAB>-" for none.
set -euo pipefail

[[ $# -gt 0 ]] || { echo "usage: gcloud-expand-members.sh MEMBER..." >&2; exit 2; }

for member in "$@"; do
  kind="${member%%:*}" target="${member#*:}"
  case "$kind" in
    projectOwner) role=roles/owner ;;
    projectEditor) role=roles/editor ;;
    projectViewer) role=roles/viewer ;;
    group) role="" ;;
    *) continue ;;
  esac
  if [[ -n "$role" ]]; then
    found=$(gcloud projects get-iam-policy "$target" --flatten='bindings[].members' \
      --filter="bindings.role=$role" --format='value(bindings.members)')
  else
    found=$(gcloud identity groups memberships search-transitive-memberships \
      --group-email="$target" --format='value(preferredMemberKey.id)')
  fi
  if [[ -z "$found" ]]; then
    printf '%s\t-\n' "$member"
  else
    printf '%s\n' "$found" | sort -u | sed "s|^|$member\t|"
  fi
done | sort
