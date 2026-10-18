# Scripts

Setup, verification and measurement tooling. Everything here is **read-only** against GCP
except `setup-gcp.sh`.

| Script | Purpose |
|:---|:---|
| `setup-gcp.sh` | Provision the quota project, service account and viewer grants ([GCP setup](../docs/GCP_SETUP.md)) |
| `compare-resources.sh` | Diff the tool's results against `gcloud` for the same filters; exits non-zero if they differ |
| `gcloud-search-resources.sh` | Independent `gcloud` implementation of `search`, emitting normalised JSON lines |
| `explorer-search-resources.sh` | The tool's own results in the same normalised form (implemented in `_explorer_search.py`) |
| `compare-grants.sh` | Diff `gcpe grants --show-all` against gcloud. The gcloud side runs one search per permission, independent of the tool's batching. |
| `gcloud-search-grants.sh` / `_explorer_grants.py` | The two sides of `compare-grants.sh`, as sorted `resource<TAB>role<TAB>member` lines |
| `compare-roles.sh` | Diff `gcpe roles` against an independent jq classification of the same rules |
| `gcloud-list-roles.sh` / `_explorer_roles.py` | The two sides of `compare-roles.sh`, as sorted `risk<TAB>role` lines |
| `compare-billing-grants.sh` | Diff `gcpe billing-grants --show-all` against gcloud's reading of the Cloud Billing API |
| `gcloud-billing-grants.sh` / `_explorer_billing_grants.py` | The two sides of `compare-billing-grants.sh`, as sorted `resource<TAB>role<TAB>member` lines |
| `compare-effective-iam.sh` | Diff `--include-iam --effective --show-all`'s inherited bindings against gcloud. The gcloud side reads one name per call, independent of the tool's batching and its retry-on-404 split. |
| `gcloud-effective-iam.sh` / `_explorer_effective_iam.py` | The two sides of `compare-effective-iam.sh`, as sorted `resource<TAB>inherited_from<TAB>role<TAB>member` lines, plus `resource<TAB>UNREADABLE` for each resource neither could read |
| `compare-projects.sh` | Diff the `gcpe docs` scope picker (Resource Manager v3 search) against `gcloud projects list` (v1) |
| `gcloud-list-projects.sh` / `_explorer_projects.py` | The two sides of `compare-projects.sh`, as sorted `project_id<TAB>number` lines |
| `compare-member-expansion.sh` | Diff `--expand-members` against gcloud: project roles via Resource Manager `getIamPolicy` (the tool uses CAI), groups via `gcloud identity` |
| `gcloud-expand-members.sh` / `_explorer_expansions.py` | The two sides of `compare-member-expansion.sh`, as sorted `member<TAB>principal` lines |
| `check-rule-permissions.sh` | Checks every role risk rule (and, given a site config, its rules) against the live IAM catalogue and the permissions testable on a project (`--project`, default gcloud's). It reports `UNKNOWN` for a typo, `NO-ROLE` for a real permission no predefined role grants, and `WARN` for `DEPRECATED`, and exits non-zero for the first two. |
| `_snapshot_permissions.py` | Refreshes `tests/fixtures/iam_permissions.txt.gz`, the offline copy that check's unit test uses |
| `check-noise.sh` | Checks that the noise-suppressed result is a strict subset of the full result |
| `benchmark.py` | Records a latency baseline for the delivery plan |

## Why the differential checks exist

The point is to catch a wrong CAI query: a bug where the tool returns a plausible result that
is quietly missing rows, or filters locally when it should filter in CAI. A test with a fake
client can't catch that, because the fake answers whatever it is asked. Only a second,
independent implementation run against the real API can.

**Practice: every new capability ships with a `gcloud` equivalent here.** This matters most for
filters, where a slightly wrong query string is easy to miss.

## Usage

All of these need ADC and Cloud Asset Viewer on the scope. `compare-roles.sh --scope` also
needs `iam.roles.list` there (`roles/iam.roleViewer`), and `compare-billing-grants.sh` needs
`roles/billing.viewer` plus `cloudbilling.googleapis.com` on the quota project.

```bash
# Differential check. Takes --scope, --type, --term, --label, --location (all but --scope repeatable or optional)
./scripts/compare-resources.sh --scope projects/my-project
./scripts/compare-resources.sh --scope projects/my-project --type bucket
./scripts/compare-resources.sh --scope organizations/123 --type bucket --type vm \
    --label env=prod --location europe-west2

# Run either side on its own
./scripts/gcloud-search-resources.sh --scope projects/my-project --type bucket
./scripts/explorer-search-resources.sh --scope projects/my-project --type bucket

# Risky grants: tool vs gcloud
./scripts/compare-grants.sh --scope projects/my-project
./scripts/compare-grants.sh --scope organizations/123 --role-risk medium --member-type user --member-type group

# Risky roles: the tool's classifier vs an independent jq one over the same rules.
# --scope adds the custom roles defined on an organization or project.
./scripts/compare-roles.sh
./scripts/compare-roles.sh --risk medium --scope projects/my-project

# Billing account IAM. Needs roles/billing.viewer; takes no scope, because
# billing accounts are not under an organization.
./scripts/compare-billing-grants.sh
./scripts/compare-billing-grants.sh --role-risk medium --member-type user

# Noise reduction may only remove rows, never add or alter them
./scripts/check-noise.sh projects/my-project

# Latency baseline; --scope is repeatable
uv run python -m scripts.benchmark --scope organizations/123456789 --repeats 3 --limit 1000
```

`setup-gcp.sh` reads `PROJECT_ID` (default `gcp-resource-browser-eo`), `SA_NAME` (default
`resource-browser`) and optionally `ORGANIZATION_ID` (to grant `roles/iam.roleViewer`, for
`gcpe roles --scope`) from the environment. Edit `TARGET_PROJECTS` in the script to list the
projects to grant on, and re-run it after adding one, since it is idempotent.

## Not yet covered

The differential scripts do not yet cover `--project`, `--raw-query` or `--sort`.

`gcloud-list-roles.sh` calls the IAM REST API with `curl` rather than `gcloud iam roles list`,
because that command cannot ask for the `FULL` view and so never returns `includedPermissions`
— without which there is nothing to classify. `gcloud auth print-access-token` still provides
the credentials, and the classification, which is the part under test, is entirely jq's.
