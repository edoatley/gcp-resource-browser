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

uv run gcpe search projects/my-project                       # everything, noise hidden
uv run gcpe search organizations/123 backup --type bucket    # one call, whole org
uv run gcpe summary organizations/123                        # counts by type/project/location

uv run gcpe serve                                            # API on http://127.0.0.1:8000
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
| [CLI guide](docs/CLI.md) | Using `gcpe`: recipes, output formats, exit codes |
| [CLI reference](docs/CLI_REFERENCE.md) | Every command and flag (generated from the code) |
| [HTTP API](docs/API.md) | Endpoints, parameters, response and error bodies |
| [OpenAPI reference](https://edoatley.github.io/gcp-resource-browser/) | The API spec in Swagger UI, in the browser (also at `/docs` on a running server) |
| [Configuration](docs/CONFIGURATION.md) | Site config: extra types, noise rules |
| [Deployment](docs/DEPLOYMENT.md) | Container image, Cloud Run/GKE, health probes |
| [Architecture](docs/ARCHITECTURE.md) | How it is built, and how to extend it |
| [Development](docs/DEVELOPMENT.md) | Tests, CI, differential checks against `gcloud` |
| [Scripts](scripts/README.md) | `gcloud` equivalents, setup and benchmark tooling |
| [PRD](docs/PRD.md) · [Delivery plan](docs/DELIVERY_PLAN.md) · [Walkthrough](docs/WALKTHROUGH.md) | Requirements, phasing, manual verification against real GCP |

## Getting started

A walkthrough of the main features, in order. It assumes [GCP setup](docs/GCP_SETUP.md) is
done: ADC is configured, the Cloud Asset API is enabled on your quota project, and you hold
`cloudasset.viewer` on the scope below. Set the scope once:

```bash
SCOPE=projects/my-project      # or folders/<number>, or organizations/<number>
```

**1. Check you can reach Cloud Asset Inventory.** List the friendly type names, then run one
small search:

```bash
uv run gcpe types
uv run gcpe list-resources $SCOPE bucket
```

An error here names the problem: exit code `3` is missing viewer on the scope, and `6` is the
API disabled on the quota project.

**2. Get the shape of the scope.** Counts by type, project and location from a single call:

```bash
uv run gcpe summary $SCOPE
```

**3. Search everything.** With no `--type`, every asset type is searched and low-signal rows
(enabled APIs, image layers, default routes and similar) are hidden. The last line says how
many were hidden and why:

```bash
uv run gcpe search $SCOPE
uv run gcpe search $SCOPE --show-all      # hide nothing
```

**4. Narrow it down.** Filters are evaluated by CAI, not locally. Different filters are ANDed
together, and repeated values of one filter are ORed:

```bash
uv run gcpe search $SCOPE backup                                # free text
uv run gcpe search $SCOPE --type bucket --type vm               # several types
uv run gcpe search $SCOPE --label env=prod --location 'europe-*'
uv run gcpe search $SCOPE --type vm --label owner --show-query  # has the label at all; print the CAI query
```

**5. Sort and export.** JSON and CSV go to stdout, and warnings go to stderr:

```bash
uv run gcpe search $SCOPE --sort 'createTime DESC' -n 10
uv run gcpe search $SCOPE --type bucket -o csv > buckets.csv
uv run gcpe search $SCOPE --type vm -o json | jq -r '.[].display_name'
```

**6. See who has access.** Add IAM bindings attached to each resource. Inherited grants are
*not* included, and the output says so:

```bash
uv run gcpe search $SCOPE --type bucket --include-iam -o json | jq '.[] | {display_name, iam_bindings}'
```

**7. Search several scopes.** If you hold viewer on some projects but not on their organisation,
search them all at once. Exit code `7` means some scopes failed:

```bash
uv run gcpe search projects/a --also-scope projects/b --also-scope projects/c --type bucket
```

**8. Use the HTTP API.** Searches, summaries and type listings are also available over HTTP (multi-scope search is CLI-only):

```bash
uv run gcpe serve &

curl -s "http://127.0.0.1:8000/v1/resources?scope=$SCOPE&type=bucket" | jq '{count, query, truncated}'
curl -s "http://127.0.0.1:8000/v1/summary?scope=$SCOPE" | jq .by_asset_type
curl -sN "http://127.0.0.1:8000/v1/resources/stream?scope=$SCOPE" | head -5   # NDJSON, as rows arrive
```

Open <http://127.0.0.1:8000/docs> to explore and call every endpoint in your browser. The
same spec is [published online](https://edoatley.github.io/gcp-resource-browser/) as a
read-only reference.

Next: [CLI guide](docs/CLI.md) for every option, [Concepts](docs/CONCEPTS.md) for how filters
and noise reduction behave, and [Deployment](docs/DEPLOYMENT.md) to run it as a service.
