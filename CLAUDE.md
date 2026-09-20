# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

GCP Resource Explorer — a CLI plus HTTP API for searching Google Cloud resources across a very
large estate (the PRD targets orgs with 2000+ projects). See `docs/PRD.md` for the full
requirements and the recorded architecture decisions.

## Environment & commands

Python >= 3.13, managed with `uv`.

```bash
uv sync                                  # includes the dev group (pytest, ruff)
uv run pytest                            # CAI client is faked; no network or creds needed
uv run ruff check . && uv run ruff format .
uv run gcp-explorer list-resources projects/my-project bucket
uv run gcp-explorer serve                # FastAPI on http://127.0.0.1:8000 (docs at /docs)
```

CI (`.github/workflows/ci.yml`) runs lint, format check, tests, an `openapi.yml` staleness
check, an image build, and a check that no credential is baked into the image. If you change
the API surface, regenerate the spec or CI fails:
`uv run gcp-explorer openapi --out openapi.yml`.

The Dockerfile builds at `/app` rather than `/build` on purpose: a virtualenv's entry points
carry an absolute shebang, so relocating `.venv` breaks every console script.

`scripts/` holds `gcloud` equivalents of each capability plus `compare-resources.sh`, which
diffs the two and exits non-zero on disagreement. Unit tests use a fake client, so they cannot
catch a *wrong CAI query* — only the differential check can. **Every new capability should ship
with a `gcloud` equivalent there**, especially from Phase 2 on, where filters compile into CAI
query syntax.

Packaging note: the code lives in `app/`, which does not match the project name
`gcp-resource-browser`, so `pyproject.toml` declares
`[tool.hatch.build.targets.wheel] packages = ["app"]`. Removing that breaks `uv sync` at the
wheel-build step.

**`/healthz` must never do I/O** — a liveness probe that calls GCP turns an upstream blip into
a restart loop. `/readyz` may check that credentials resolve, but must not call CAI: a probe
per pod every few seconds would burn quota to report what a real request already surfaces.

## Authentication

`scripts/setup-gcp.sh` provisions the GCP side (dedicated project `gcp-resource-browser-eo`,
service account `resource-browser@...`, IAM grants) and is idempotent — re-run it to add a
project. ADC uses impersonation rather than a downloaded key. Billing is deliberately not
linked: the Cloud Asset API enables on an unbilled project and its search calls are free.

Two distinct prerequisites, and conflating them wastes time: the caller needs
`roles/cloudasset.viewer` **on the scope searched**, and the Cloud Asset API must be enabled on
the **ADC quota project** the call bills to. Those are frequently different projects. Google
returns 403 for both, so `core._service_disabled_error` inspects the structured `ErrorInfo` for
`reason == "SERVICE_DISABLED"` and raises `ApiNotEnabledError` naming the billing project.
Keep that distinction: the first version of this message blamed the searched project for a
disabled API elsewhere and sent a real user to fix the wrong thing.

Generally: when translating a Google exception, include `exc.message` rather than replacing it
with an assumption about the cause.

All GCP access goes through Application Default Credentials. The tool does no auth of its own —
the user runs `gcloud auth application-default login` first. This is a deliberate decision
(see PRD "Resolved Decisions") so the same code works unchanged under a service account.

## Architecture

- `app/core.py` — the only module that talks to GCP. `search_resources` wraps CAI's
  `search_all_resources`, flattens hits into Pydantic models, and translates
  `google.api_core` exceptions into the domain errors declared there.
- `app/query.py` — compiles filters into CAI query syntax. See the warning below.
- `app/noise.py` + `app/noise_rules.json` — default-suppressed low-signal resources.
- `app/config.py` — site overrides for the asset-type mapping and noise rules.
- `app/aggregate.py` — IAM join and the summary rollup.
- `app/output.py` — JSON/CSV emitters for the CLI.
- `app/params.py` — `SearchFilters` (what a search takes) and `Help` (how each filter is
  described). Both surfaces read from here.
- `app/asset_types.json` — the friendly-name mapping, loaded by `core.py` and read with `jq`
  by `scripts/gcloud-search-resources.sh`.
- `app/models.py` — `Resource`, `ResourceList`, `ErrorResponse`.
- `app/api.py` — FastAPI. `GET /v1/resources` (repeatable `type`, `label`, `location`,
  `project`), `GET /v1/types`.
- `app/cli.py` — Typer + `rich`. `serve` runs uvicorn against the same FastAPI app, so CLI and
  API are one process and one deployable.
- `app/main.py` — thin entry shim for `python -m app.main`.

Both wrappers are intentionally dumb: any new capability (filtering, aggregation, pagination)
belongs in the core so CLI and API stay in sync. Errors follow the same rule — the core raises
a domain error, and each surface maps it to a status code (`app.api._STATUS_BY_ERROR`) or an
exit code (`app.cli._EXIT_BY_ERROR`). Adding an error means adding it in all three places.

**Adding a filter:** add it to `Help` and `SearchFilters` in `app/params.py`, handle it in
`build_query`, then reference `Help.X` from both surfaces and pass it through the
`SearchFilters(...)` each builds. Do not retype the help text — `tests/test_params.py` asserts
each shared string appears verbatim in both surfaces, and will fail if you do.

The two signatures stay separate on purpose. FastAPI and Typer both build their artifacts by
introspecting the function signature, so collapsing them to `**kwargs` would destroy the
OpenAPI schema and the generated CLI help. Share the *knowledge* (help text, filter set), not
the declarations. Surface-specific detail — short flags, OpenAPI `examples`, `ge=1` vs `min=1`,
`--show-query` — stays local, and either surface may append to a shared `Help` string.

The API takes scope as a query param rather than a path segment because CAI scopes contain a
slash (`projects/x`), and because Phase 2's filters are all naturally query params.

### Cloud Asset Inventory is the data engine

Do not add code that iterates per-project GCP service APIs for discovery. The PRD explicitly
rejects that approach — it does not survive 2000 projects (latency + quota). CAI's
"search all resources" is scope-based, so a single call covers an org, folder, or project;
`scope` strings look like `projects/my-project`, `folders/123`, or `organizations/123`.

### ASSET_TYPES mapping

`ASSET_TYPES` maps friendly names (`bucket`, `cloudrun`) to CAI asset type strings
(`storage.googleapis.com/Bucket`). Both the CLI and the API validate against this dict, so
adding a resource type is a one-line change that lights up both surfaces.

## Not yet built

Much of the PRD is unimplemented: querying *all* resource types by default with a "noise
reduction" filter bypassed via `--show-all` / `?show_all=true`; label/region filters; free-text
cross-project search; aggregated endpoints. **Check `docs/DELIVERY_PLAN.md` before assuming a
missing feature is out of scope** — it phases this work and records the sequencing rationale. Keep its checkboxes
current as phases land, and keep the status note near the top of `README.md` in step.

**Two filtering mechanisms, deliberately different.** User filters (`app/query.py`) are always
compiled into the CAI query and evaluated server-side. Noise rules (`app/noise.py`) are applied
client-side to results as they stream back — forced, not chosen: `assetType` is not a queryable
CAI field and `asset_types` is an include list with no exclusion. A user filter narrows what is
*fetched*; a noise rule hides low-value rows from a result asked for broadly. Because of this,
`limit` caps *visible* results, and the pager is consumed lazily.

**Sorting is server-side** (`order_by`), on CAI's fixed field list in `params.SORTABLE_FIELDS`.
Unknown fields are rejected, never dropped — a silently ignored sort gives plausible output in
the wrong order. Note `assetType` is sortable but not queryable, which is why noise reduction
still cannot move upstream.

**IAM is attached-only, and must keep saying so.** `iam_note` on every IAM-bearing response
states that inherited grants are excluded; presenting attached bindings as effective access is
wrong in the unsafe direction. `iam_bindings is None` means not requested, `[]` means requested
and none attached — do not collapse them. Effective IAM (`batch_get_effective_iam_policies`,
20 names per call) is deferred until after bounded fan-out exists.

**`-o json|csv` writes the payload to stdout and every warning to stderr.** Keep it that way,
or piped output stops being parseable.

**Never weaken the two noise invariants:** `--show-all` / `?show_all=true` disables every rule,
and what was suppressed is always reported with reasons. An audit tool that silently drops rows
is worse than one that shows too many. Suppression currently removes ~84% of a real estate, so
a bug here is not a small matter.

**`app/query.py` is the highest-risk code here.** Filters are compiled into CAI query syntax
and evaluated server-side — never filtered locally, which would mean fetching an org-wide
result set to discard most of it. A filter that compiles to *valid but wrong* syntax returns a
plausible result set quietly missing rows, which is worse than an error because it does not
announce itself. Preserve both guards when adding filters: values go through `quote()` so they
can never alter the query's structure, and the compiled query stays recoverable (`query` in the
API response, `--show-query` on the CLI, shown automatically on an empty filtered result). New
filters need a test pinning the exact compiled string, and a `scripts/` equivalent.

**CAI never reports a project ID, only a number.** `Resource.project` keeps the raw number;
`Resource.project_id` is recovered from `additional_attributes.projectId` (Project assets) or
the `parent_full_resource_name` path (everything else), and is None when the parent is not a
project. Displays prefer the ID. Likewise `--project` resolves an ID to a number before
querying — verified against the live API that `project:<id>` matches nothing while
`project:<number>` matches, so forwarding an ID is a silent empty result.

Results are capped (`DEFAULT_LIMIT`, 1000) so an org-wide search cannot run away. When the cap
bites, both surfaces say so — `truncated` in the API body, a warning line in the CLI. Keep that
property: a silently shortened list misrepresents the estate, which is the one thing this tool
exists to report accurately.

**Do not convert the code to `async` without a measured reason.** Starlette already runs plain
`def` endpoints in a threadpool, so the existing sync handler is non-blocking and concurrent
(40 workers by default). Going async would make the core a coroutine and force `asyncio.run`
through the whole CLI. The trigger conditions and the migration cost are written up as the
deliberately unscheduled Phase 7 in the delivery plan.
