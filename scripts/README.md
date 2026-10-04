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

All of these need ADC and Cloud Asset Viewer on the scope.

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

# Noise reduction may only remove rows, never add or alter them
./scripts/check-noise.sh projects/my-project

# Latency baseline; --scope is repeatable
uv run python -m scripts.benchmark --scope organizations/123456789 --repeats 3 --limit 1000
```

`setup-gcp.sh` reads `PROJECT_ID` (default `gcp-resource-browser-eo`) and `SA_NAME` (default
`resource-browser`) from the environment. Edit `TARGET_PROJECTS` in the script to list the
projects to grant on, and re-run it after adding one, since it is idempotent.

## Not yet covered

The differential scripts do not yet cover `--project`, `--raw-query` or `--sort`.
