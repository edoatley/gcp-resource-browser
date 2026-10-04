# GCP Resource Explorer

A CLI and HTTP API for searching Google Cloud resources across a large estate — built for
organisations running thousands of projects, where per-project API iteration is too slow and
too quota-hungry to be viable. Every search is a single Cloud Asset Inventory (CAI) call over a
whole project, folder or organisation, with filters evaluated server-side.

> **Status: usable.** Phases 0–3, 5 and 6 are complete: search every resource type by default with
> noise reduction, server-side filtering and sorting, IAM enrichment, aggregated summaries, and
> JSON/CSV output, concurrent multi-scope search, streaming, and a container image with CI.
> Phase 4's tuning is built but its baseline still needs measuring on a real organisation —
> run `scripts/benchmark.py` there. See the [delivery plan](docs/DELIVERY_PLAN.md).

## Quickstart

Requires Python 3.13+, [`uv`](https://docs.astral.sh/uv/), and an authenticated `gcloud`.

```bash
uv sync
gcloud auth application-default login     # see docs/GCP_SETUP.md for the full setup

uv run gcp-explorer search projects/my-project                       # everything, noise hidden
uv run gcp-explorer search organizations/123 backup --type bucket    # one call, whole org
uv run gcp-explorer summary organizations/123                        # counts by type/project/location

uv run gcp-explorer serve                                            # API on http://127.0.0.1:8000
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/my-project&type=bucket'
```

The caller needs `roles/cloudasset.viewer` on each scope searched, and the Cloud Asset API
must be enabled on the ADC quota project. These are usually different projects —
[GCP setup](docs/GCP_SETUP.md) explains both.

## Documentation

| Read this | For |
|:---|:---|
| [GCP setup](docs/GCP_SETUP.md) | Credentials, IAM, the quota project, `setup-gcp.sh` |
| [Concepts](docs/CONCEPTS.md) | Scopes, types, filters, noise reduction, limits, IAM semantics — shared by CLI and API |
| [CLI guide](docs/CLI.md) | Using `gcp-explorer`: recipes, output formats, exit codes |
| [CLI reference](docs/CLI_REFERENCE.md) | Every command and flag (generated from the code) |
| [HTTP API](docs/API.md) | Endpoints, parameters, response and error bodies |
| [Configuration](docs/CONFIGURATION.md) | Site config: extra types, noise rules |
| [Deployment](docs/DEPLOYMENT.md) | Container image, Cloud Run/GKE, health probes |
| [Architecture](docs/ARCHITECTURE.md) | How it is built, and how to extend it |
| [Development](docs/DEVELOPMENT.md) | Tests, CI, differential checks against `gcloud` |
| [Scripts](scripts/README.md) | `gcloud` equivalents, setup and benchmark tooling |
| [PRD](docs/PRD.md) · [Delivery plan](docs/DELIVERY_PLAN.md) · [Walkthrough](docs/WALKTHROUGH.md) | Requirements, phasing, manual verification against real GCP |
