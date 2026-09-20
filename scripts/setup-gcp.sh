#!/usr/bin/env bash
# Provision the GCP side of gcp-resource-browser.
#
# Creates a dedicated project to act as the API/quota home, a read-only service
# account, and the IAM grants the tool needs. Idempotent: safe to re-run, and
# re-run it to add a newly created project to TARGET_PROJECTS.
#
# Deliberately does NOT link a billing account. The Cloud Asset API enables on
# an unbilled project and its search calls are free, so billing adds a quota
# constraint for nothing.
#
# The final step -- pointing ADC at the service account -- is interactive and is
# printed for you to run rather than executed here.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-gcp-resource-browser-eo}"
SA_NAME="${SA_NAME:-resource-browser}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Projects the tool may search. Each needs its own grant: without an
# organisation there is no parent to inherit from.
TARGET_PROJECTS=(
  ace-gcp-training
  gcp-sandbox-2026-18798
  gen-lang-client-0835246655
  idp-prototype-edo
  sudoku-app-eo
  sudoku-eo-2026
  "$PROJECT_ID"
)

say() { printf '\n=== %s ===\n' "$1"; }

say "Project $PROJECT_ID"
if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "already exists"
else
  gcloud projects create "$PROJECT_ID" --name="GCP Resource Browser"
fi

say "APIs"
# cloudasset: what the tool calls. iamcredentials: needed to impersonate the SA.
gcloud services enable cloudasset.googleapis.com iamcredentials.googleapis.com \
  --project="$PROJECT_ID"

say "Service account $SA_EMAIL"
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "already exists"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name="GCP Resource Browser" \
    --description="Read-only Cloud Asset Inventory search for the gcp-resource-browser tool"
fi

say "Impersonation rights for the current user"
CALLER="$(gcloud config get-value account 2>/dev/null)"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --member="user:${CALLER}" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null
echo "$CALLER may impersonate $SA_EMAIL"

say "IAM grants"
# Lets the SA bill API usage to the quota project.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/serviceusage.serviceUsageConsumer" \
  --condition=None >/dev/null
echo "  serviceUsageConsumer on $PROJECT_ID"

for project in "${TARGET_PROJECTS[@]}"; do
  if gcloud projects add-iam-policy-binding "$project" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="roles/cloudasset.viewer" \
      --condition=None >/dev/null 2>&1; then
    printf '  cloudasset.viewer on %s\n' "$project"
  else
    printf '  FAILED on %s (no access, or it does not exist)\n' "$project" >&2
  fi
done

say "Next step (interactive, needs a browser)"
cat <<NEXT
Point Application Default Credentials at the service account:

    gcloud auth application-default login \\
        --impersonate-service-account=${SA_EMAIL}

    gcloud auth application-default set-quota-project ${PROJECT_ID}

Then verify:

    uv run gcp-explorer list-resources projects/idp-prototype-edo bucket

IAM changes can take up to a minute to propagate; retry once if the first call
fails with a permission error.
NEXT
