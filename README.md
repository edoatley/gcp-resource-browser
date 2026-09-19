# GCP Resource Explorer

A CLI and HTTP API for searching Google Cloud resources across a large estate — built for
organisations running thousands of projects, where per-project API iteration is too slow and
too quota-hungry to be viable.

- **[PRD](docs/PRD.md)** — requirements and the recorded architecture decisions.
- **[Delivery plan](docs/DELIVERY_PLAN.md)** — phased breakdown of what ships when.

> **Status: early.** Phase 0 (walking skeleton) is complete. Two resource types are supported
> and filtering is not yet implemented. See the delivery plan for what is coming.

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

uv run gcp-explorer --help
```

`scope` is any CAI scope: `projects/<id>`, `folders/<id>`, or `organizations/<id>`.
`resource_type` is currently `bucket` or `cloudrun`.

### HTTP API

```bash
uv run gcp-explorer serve      # http://127.0.0.1:8000
```

| Endpoint | Description |
|:---|:---|
| `GET /projects/{project_id}/resources/{resource_type}` | Resources of one type in one project |
| `GET /docs` | Swagger UI (auto-generated) |
| `GET /openapi.json` | OpenAPI schema |

```bash
curl http://127.0.0.1:8000/projects/my-project/resources/bucket
```

```json
{
  "project": "my-project",
  "resource_type": "bucket",
  "count": 2,
  "data": [
    { "name": "my-bucket", "project": "my-project", "location": "europe-west2", "state": "" }
  ]
}
```

Note: the API is currently project-scoped, while the CLI accepts any scope. Unifying this is
Phase 1 work.

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

Three layers, currently all in `app/main.py`:

1. **Core** — `search_cai_resources(scope, asset_type, query)` calls CAI's
   `search_all_resources` and flattens each hit to a plain dict.
2. **CLI** (`typer`) — renders results as a `rich` table.
3. **API** (`fastapi`) — returns JSON.

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
uv sync
uv run gcp-explorer --help
uv run python -m app.main --help      # equivalent, without the console script
```

There is no test suite or linter yet — both land in Phase 1.

Code lives in `app/`, which does not match the project name, so `pyproject.toml` names the
package explicitly under `[tool.hatch.build.targets.wheel]`. Without that, `uv sync` fails at
the wheel-build step.
