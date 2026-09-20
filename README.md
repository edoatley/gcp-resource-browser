# GCP Resource Explorer

A CLI and HTTP API for searching Google Cloud resources across a large estate — built for
organisations running thousands of projects, where per-project API iteration is too slow and
too quota-hungry to be viable.

- **[PRD](docs/PRD.md)** — requirements and the recorded architecture decisions.
- **[Delivery plan](docs/DELIVERY_PLAN.md)** — phased breakdown of what ships when.

> **Status: usable.** Phases 0–2 are complete: free-text search, label/location/project
> filtering compiled server-side, 20 resource types plus raw CAI types. Searching *all* types
> with noise reduction is Phase 3. See the delivery plan for what is coming.

## Requirements

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- `gcloud` CLI, authenticated for Application Default Credentials
- The `cloudasset.assets.searchAllResources` permission on the scope you query
  (`roles/cloudasset.viewer`), and the Cloud Asset API enabled on the project your credentials
  bill to

## Setup

```bash
uv sync
gcloud auth application-default login
```

The tool performs no authentication of its own — it reads ADC. The same code therefore runs
unchanged under a service account in CI or a container.

## Usage

### CLI

```bash
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

uv run gcp-explorer types      # list supported type names
uv run gcp-explorer --help
```

`scope` is any CAI scope: `projects/<id>`, `folders/<id>`, or `organizations/<id>`.

**Types:** 20 friendly names (`uv run gcp-explorer types`), or any raw CAI asset type
(`dns.googleapis.com/ManagedZone`) or RE2 pattern (`compute.googleapis.com/.*`).

**Filters** combine as you would expect: different kinds are ANDed, repeated values of the same
kind are ORed. `--label env=prod --label tier=web` means both; `--location a --location b` means
either.

Results are capped at 1000 by default so an org-wide search cannot run away; when the cap
bites, the CLI says so rather than silently returning a short list. Raise it with `--limit`.

**Exit codes:** `0` success · `2` bad usage (unknown type, malformed scope or filter) · `3`
permission denied · `4` scope not found · `5` upstream CAI failure.

### HTTP API

```bash
uv run gcp-explorer serve      # http://127.0.0.1:8000
```

| Endpoint | Description |
|:---|:---|
| `GET /v1/resources?scope=&type=&q=&label=&location=&project=&limit=` | Search a scope; `type`, `label`, `location` and `project` are repeatable |
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
`502` upstream failure:

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

`app/models.py` holds the shared Pydantic models; `app/main.py` is a thin entry shim.

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
