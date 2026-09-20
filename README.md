# GCP Resource Explorer

A CLI and HTTP API for searching Google Cloud resources across a large estate — built for
organisations running thousands of projects, where per-project API iteration is too slow and
too quota-hungry to be viable.

- **[PRD](docs/PRD.md)** — requirements and the recorded architecture decisions.
- **[Delivery plan](docs/DELIVERY_PLAN.md)** — phased breakdown of what ships when.
- **[Walkthrough](docs/WALKTHROUGH.md)** — manual verification against real GCP.

> **Status: usable.** Phases 0–3, 5 and 6 are complete: search every resource type by default with
> noise reduction, server-side filtering and sorting, IAM enrichment, aggregated summaries, and
> JSON/CSV output, concurrent multi-scope search, streaming, and a container image with CI.
> Phase 4's tuning is built but its baseline still needs measuring on a real organisation —
> run `scripts/benchmark.py` there. See the delivery plan.

## Requirements

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- `gcloud` CLI, authenticated
- `jq`, for the differential scripts

## Setup

```bash
uv sync
```

### GCP access

The tool performs no authentication of its own — it reads Application Default Credentials, so
the same code runs unchanged under a service account in CI or a container.

`scripts/setup-gcp.sh` provisions everything needed: a dedicated project to act as the API and
quota home, a read-only service account, and the IAM grants. It is idempotent — re-run it to
add a newly created project to the list it grants on.

```bash
./scripts/setup-gcp.sh

# then, interactively (needs a browser):
gcloud auth application-default login \
    --impersonate-service-account=resource-browser@gcp-resource-browser-eo.iam.gserviceaccount.com
gcloud auth application-default set-quota-project gcp-resource-browser-eo
```

Impersonation is used in preference to a downloaded JSON key: there is no long-lived
credential on disk to leak or rotate.

Two prerequisites that are easy to confuse, because Google returns HTTP 403 for both:

| Requirement | Where it applies |
|:---|:---|
| Cloud Asset API enabled | The **quota project** the call bills to |
| `roles/cloudasset.viewer` | Each **scope** you search |

These are usually different projects. The tool tells them apart and names the right one — exit
code `6` and HTTP `503` mean the API is disabled, not that your IAM is wrong.

**Billing is not required.** The Cloud Asset API enables on an unbilled project and its search
calls are free, so `setup-gcp.sh` deliberately skips linking a billing account — linking one
would consume a billing-account project slot for no benefit.

**Without an organisation** there is no `organizations/` scope to search and no single place to
grant viewer, so each project needs its own grant. The one-call-across-2000-projects behaviour
needs an org to exercise.

## Usage

### CLI

```bash
# Everything in a scope, with low-signal resources hidden
uv run gcp-explorer search projects/my-project

# Find a resource by name across an entire organisation — one API call, not 2000
uv run gcp-explorer search organizations/123456789 backup --type bucket

# Several types, several filters. Filters are evaluated by CAI, not locally.
uv run gcp-explorer search organizations/123456789 \
    --type bucket --type vm \
    --label env=prod \
    --location europe-west2 --location europe-west1

# Resources carrying an `owner` label at all, whatever its value
uv run gcp-explorer search folders/456 --type vm --label owner

# Raw CAI syntax for anything not modelled as a flag
uv run gcp-explorer search projects/my-project --type vm --raw-query 'NOT state:RUNNING'

# Show the CAI query the filters compiled to
uv run gcp-explorer search projects/my-project --type bucket --label env=prod --show-query

# Single-type shorthand
uv run gcp-explorer list-resources projects/my-project bucket

# Nothing hidden
uv run gcp-explorer search projects/my-project --show-all

# Sort server-side, and emit machine-readable output
uv run gcp-explorer search projects/my-project --sort 'createTime DESC' -o json | jq '.[0]'
uv run gcp-explorer search projects/my-project --type bucket -o csv > buckets.csv

# Attach each resource's IAM bindings — one extra call for the whole scope
uv run gcp-explorer search projects/my-project --type bucket --include-iam -o json

# Count a scope by type, project and location
uv run gcp-explorer summary organizations/123456789

# Search several scopes concurrently — the route when you hold viewer on
# individual projects but not on the organisation
uv run gcp-explorer search projects/a --also-scope projects/b --also-scope projects/c

uv run gcp-explorer types      # list supported type names
uv run gcp-explorer --help
```

With `-o json` or `-o csv` the payload goes to stdout and every warning to stderr, so a
pipeline reading stdout gets valid data and nothing else.

### Noise reduction

With no `--type`, every asset type is searched — which without filtering is mostly enabled API
services, container image layers and auto-created default routes. In a survey of a real estate
those were 42%, 10% and 8% of all resources; suppression took **515 resources down to 84**.

Rules live in `app/noise_rules.json`, each carrying the reason it exists. Seven of fifteen are
name-scoped, so an auto-created `default` subnet is hidden while a subnet you built is kept.

Two guarantees, since hiding data from an audit tool is risky: `--show-all` disables every
rule, and what was hidden is always reported with its reasons. A result where everything was
suppressed says so explicitly rather than claiming nothing was found.

Suppression is applied to results rather than compiled into the query, because CAI has no way
to exclude an asset type — `assetType` is not a queryable field, and `asset_types` is an
include list. `--limit` still counts *visible* results.

### Searching many scopes at once

Cloud Asset Inventory checks permission on the **scope itself**, not on its children. Holding
`cloudasset.viewer` on 50 projects but not on the organisation means `organizations/X` returns
403 — the only route is to search the 50 scopes, and `--also-scope` does that concurrently
(bounded by `--max-concurrency`, default 8).

A scope you cannot read is reported rather than dropped, and does not cost you the others. The
CLI exits **7** when some scopes failed, so a script cannot mistake a partial answer for a
complete one.

### Streaming

`GET /v1/resources/stream` emits newline-delimited JSON as CAI returns rows, for result sets
where waiting for the whole payload is worse than parsing incrementally. It does not report
suppression counts — they are not known until the stream ends — so use `/v1/summary` for
totals.

### Caching

Off by default. `--cache-ttl SECONDS` / `?cache_ttl=` enables it. The default is off because
silently answering an audit from a stale cache is wrong when someone is checking whether a fix
landed; the benefit is absorbing repeated identical polls, which is a deployment choice.

### Measuring

```bash
uv run python -m scripts.benchmark --scope organizations/<id> --repeats 3
```

Prints a markdown table ready to paste into the delivery plan. Worth running against a real
estate: on a small one everything fits in a single 500-row CAI page, which makes `--limit` and
streaming look pointless when they are not.

### IAM enrichment

`--include-iam` / `?include_iam=true` attaches each resource's IAM bindings, joined on the CAI
full resource name. It costs **one extra call for the whole scope**, not one per resource.

The response always states what it means: bindings are those **attached directly** to each
resource. A grant inherited from a parent project, folder or organization confers real access
and is *not* included, so this is not a complete answer to "who can reach this bucket".
Resolving inheritance needs `batch_get_effective_iam_policies`, which is deferred.

In JSON output, `iam_bindings` absent means IAM was not requested; `[]` means it was, and
nothing is attached.

### OpenAPI

`openapi.yml` is committed and regenerated with:

```bash
uv run gcp-explorer openapi --out openapi.yml
```

The live schema is also served at `/openapi.json`, with Swagger UI at `/docs`.

### Site configuration

Optional. Create `gcp-explorer.json` in the working directory, or point `$GCP_EXPLORER_CONFIG`
at one, or use `~/.config/gcp-explorer/config.json`:

```json
{
  "asset_types": { "zone": "dns.googleapis.com/ManagedZone" },
  "noise_rules": [
    { "asset_type": "acme.example.com/Widget", "reason": "internal churn" }
  ],
  "unsuppress": ["compute.googleapis.com/Route"]
}
```

It merges with the defaults rather than replacing them, so upgrades keep adding new ones.
Unknown keys are rejected rather than ignored.

`scope` is any CAI scope: `projects/<id>`, `folders/<id>`, or `organizations/<id>`.

**Types:** 20 friendly names (`uv run gcp-explorer types`), or any raw CAI asset type
(`dns.googleapis.com/ManagedZone`) or RE2 pattern (`compute.googleapis.com/.*`).

**Filters** combine as you would expect: different kinds are ANDed, repeated values of the same
kind are ORed. `--label env=prod --label tier=web` means both; `--location a --location b` means
either.

Label keys may be namespaced. Google's own system labels look like
`cloud.googleapis.com/location` and `serving.knative.dev/service`; CAI rejects these unless the
key is quoted, which the tool handles for you.

`--project` accepts either a project ID or a number. CAI only matches on the number, so IDs are
resolved first — one extra lookup, cached, because the mapping is immutable. Passing an ID
straight through would return an empty result rather than an error.

Results are capped at 1000 by default so an org-wide search cannot run away; when the cap
bites, the CLI says so rather than silently returning a short list. Raise it with `--limit`.

**Exit codes:** `0` success · `2` bad usage (unknown type, malformed scope or filter) · `3`
permission denied · `4` scope not found · `5` upstream CAI failure · `6` Cloud Asset API not
enabled on the billing project.

### HTTP API

```bash
uv run gcp-explorer serve      # http://127.0.0.1:8000
```

| Endpoint | Description |
|:---|:---|
| `GET /v1/resources?scope=&type=&q=&label=&location=&project=&limit=` | Search a scope; `type`, `label`, `location` and `project` are repeatable |
| `GET /v1/summary?scope=` | Counts by type, project and location, in one payload |
| `GET /v1/types` | Friendly type names mapped to CAI asset types |
| `GET /healthz` | Liveness probe |
| `GET /docs` | Swagger UI (auto-generated) |
| `GET /openapi.json` | OpenAPI schema |

```bash
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/my-project&type=bucket'

# Repeat a parameter to search several types, or to OR several locations
curl 'http://127.0.0.1:8000/v1/resources?scope=organizations/123\
&type=bucket&type=vm&label=env%3Dprod&location=europe-west2&location=global'
```

```json
{
  "scope": "projects/my-project",
  "asset_types": ["storage.googleapis.com/Bucket"],
  "query": "labels.env:prod",
  "count": 1,
  "truncated": false,
  "data": [
    {
      "full_name": "//storage.googleapis.com/buckets/my-bucket",
      "asset_type": "storage.googleapis.com/Bucket",
      "display_name": "my-bucket",
      "project": "123456789",
      "location": "europe-west2",
      "state": null,
      "labels": { "env": "prod" },
      "create_time": "2024-03-11T09:22:41Z",
      "parent_full_resource_name": "//cloudresourcemanager.googleapis.com/projects/123456789"
    }
  ]
}
```

`scope` takes any CAI scope, matching the CLI. Errors return a typed body with a real status
code — `400` unknown type or malformed scope, `403` permission denied, `404` scope not found,
`502` upstream failure, `503` Cloud Asset API not enabled:

```json
{ "error": "unknown_resource_type", "detail": "Unsupported resource type 'vm'. Choose from: bucket, cloudrun" }
```

## Architecture

```
                  ┌──────────────┐        ┌──────────────┐
   terminal ─────►│  Typer CLI   │        │   FastAPI    │◄───── HTTP clients
                  │ (rich table) │        │   (JSON)     │
                  └───────┬──────┘        └──────┬───────┘
                          │                      │
                          └──────────┬───────────┘
                                     ▼
                        search_cai_resources()      ← the only code that talks to GCP
                                     │
                                     ▼
                     Cloud Asset Inventory · searchAllResources
```

Three layers:

1. **Core** (`app/core.py`) — the only module that talks to GCP. `search_resources` calls CAI's
   `search_all_resources`, flattens each hit into a Pydantic `Resource`, and translates Google
   exceptions into domain errors. `app/query.py` compiles filters into CAI query syntax.
2. **CLI** (`app/cli.py`) — renders a `rich` table, maps domain errors to exit codes.
3. **API** (`app/api.py`) — returns JSON, maps domain errors to status codes.

`app/models.py` holds the wire models and `app/params.py` the shared interface definition —
`SearchFilters` (what a search takes) and `Help` (how each filter is described), so the CLI and
API cannot describe the same filter differently. `app/asset_types.json` holds the friendly-name
mapping, read by both the tool and `scripts/`. `app/main.py` is a thin entry shim.

### Filtering happens server-side

Filters compile into a CAI query string and are evaluated by CAI. Nothing is filtered locally:
fetching an org-wide result set to discard most of it is the scaling failure this tool exists
to avoid.

That makes `app/query.py` the highest-risk code here — a filter compiling to *valid but wrong*
syntax returns a plausible result set quietly missing rows, which is worse than an error
because it does not announce itself. Two guards: values are quoted so they can never alter the
query's structure, and the compiled query is always recoverable (`query` in the API response,
`--show-query` on the CLI, and shown automatically when a filtered search returns nothing).

Both wrappers are deliberately thin. New capability belongs in the core function so the CLI and
API never drift apart. `serve` runs uvicorn against the same FastAPI object, so there is one
process and one deployable.

### Why Cloud Asset Inventory

CAI is the data engine for all discovery. Iterating per-project GCP service APIs across 2000+
projects is non-viable on both latency and quota, whereas CAI answers a scope-wide search —
org, folder, or project — in a single call. The trade-off accepted is freshness: CAI is a
near-real-time index rather than live data. Full reasoning in the [PRD](docs/PRD.md).

**Never iterate APIs to *find* resources.** That is the one architectural rule this project is
built around. Additional APIs may be called to *enrich* a bounded set the user has already
narrowed to — that is a different operation, and it is how the planned aggregated endpoints
(resources joined to their IAM bindings) are built. Discovery stays a single CAI search.

### Adding a resource type

`ASSET_TYPES` maps friendly names to CAI asset type strings:

```python
ASSET_TYPES = {
    "bucket": "storage.googleapis.com/Bucket",
    "cloudrun": "run.googleapis.com/Service",
}
```

Both the CLI and the API validate against this dict, so one entry lights up both surfaces.

## Running in a container

```bash
docker build -t gcp-explorer .

# The API, with local ADC mounted read-only
docker run --rm -p 8000:8000 \
    -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
    -e GOOGLE_CLOUD_QUOTA_PROJECT=<your-quota-project> \
    gcp-explorer

# Or the CLI
docker run --rm \
    -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
    -e GOOGLE_CLOUD_QUOTA_PROJECT=<your-quota-project> \
    gcp-explorer list-resources projects/my-project bucket
```

**No credentials are baked into the image.** On Cloud Run or GKE, ADC comes from the metadata
server and no mount is needed — attach `roles/cloudasset.viewer` to the runtime service account
and the tool picks it up unchanged, which is what the ADC decision was for.

| Endpoint | Purpose |
|:---|:---|
| `/healthz` | Liveness. Does no I/O, so an upstream blip never restarts the container. |
| `/readyz` | Readiness. 503 when ADC cannot be resolved; does not call CAI, to avoid burning quota on probes. |

## Development

```bash
uv sync                  # includes the dev group (pytest, ruff)
uv run pytest            # 34 tests, no network or credentials needed
uv run ruff check .
uv run ruff format .
uv run gcp-explorer --help
uv run python -m app.main --help      # equivalent, without the console script
```

The test suite fakes the CAI client throughout, so it runs anywhere.

### Checking results against `gcloud`

A faked client answers whatever query it is given, so unit tests cannot catch a *wrong query* —
one that filters client-side when it should filter server-side, or quietly misses rows.
`scripts/` holds `gcloud` equivalents and a differential checker for that:

```bash
./scripts/compare-resources.sh --scope projects/my-project --type bucket

./scripts/compare-resources.sh --scope organizations/123 \
    --type bucket --type vm --label env=prod --location europe-west2
```

It diffs the tool's output against `gcloud asset search-all-resources` and exits non-zero if
they disagree. The gcloud side builds its query with its own independent logic — comparing the
tool's query against itself would prove nothing. Every new capability should ship with an
equivalent here.

Code lives in `app/`, which does not match the project name, so `pyproject.toml` names the
package explicitly under `[tool.hatch.build.targets.wheel]`. Without that, `uv sync` fails at
the wheel-build step.
