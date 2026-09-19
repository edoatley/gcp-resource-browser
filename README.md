# GCP Resource Explorer

A CLI and HTTP API for searching Google Cloud resources across a large estate — built for
organisations running thousands of projects, where per-project API iteration is too slow and
too quota-hungry to be viable.

- **[PRD](docs/PRD.md)** — requirements and the recorded architecture decisions.
- **[Delivery plan](docs/DELIVERY_PLAN.md)** — phased breakdown of what ships when.

> **Status: early.** Phases 0–1 are complete: typed models, real status codes, explicit limits,
> and a test suite. Two resource types are supported and filtering is not yet implemented
> (Phase 2). See the delivery plan for what is coming.

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
# List buckets in a project
uv run gcp-explorer list-resources projects/my-project bucket

# List Cloud Run services across an entire organisation — one API call, not 2000
uv run gcp-explorer list-resources organizations/123456789 cloudrun

# Free-text filter and a raised result cap
uv run gcp-explorer list-resources folders/456 bucket --query name:backup --limit 5000

uv run gcp-explorer --help
```

`scope` is any CAI scope: `projects/<id>`, `folders/<id>`, or `organizations/<id>`.
`resource_type` is currently `bucket` or `cloudrun`.

Results are capped at 1000 by default so an org-wide search cannot run away; when the cap
bites, the CLI says so rather than silently returning a short list. Raise it with `--limit`.

**Exit codes:** `0` success · `2` bad usage (unknown type, malformed scope) · `3` permission
denied · `4` scope not found · `5` upstream CAI failure.

### HTTP API

```bash
uv run gcp-explorer serve      # http://127.0.0.1:8000
```

| Endpoint | Description |
|:---|:---|
| `GET /v1/resources?scope=&type=&q=&limit=` | Search a scope for resources of one type |
| `GET /healthz` | Liveness probe |
| `GET /docs` | Swagger UI (auto-generated) |
| `GET /openapi.json` | OpenAPI schema |

```bash
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/my-project&type=bucket'
```

```json
{
  "scope": "projects/my-project",
  "asset_type": "storage.googleapis.com/Bucket",
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
   exceptions into domain errors.
2. **CLI** (`app/cli.py`) — renders a `rich` table, maps domain errors to exit codes.
3. **API** (`app/api.py`) — returns JSON, maps domain errors to status codes.

`app/models.py` holds the shared Pydantic models; `app/main.py` is a thin entry shim.

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
./scripts/compare-resources.sh projects/my-project bucket
```

It diffs the tool's output against `gcloud asset search-all-resources` and exits non-zero if
they disagree. Every new capability should ship with an equivalent here.

Code lives in `app/`, which does not match the project name, so `pyproject.toml` names the
package explicitly under `[tool.hatch.build.targets.wheel]`. Without that, `uv sync` fails at
the wheel-build step.
