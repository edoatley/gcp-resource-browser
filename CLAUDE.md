# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

GCP Resource Explorer — a CLI plus HTTP API for searching Google Cloud resources across a very
large estate (the PRD targets orgs with 2000+ projects). See `docs/PRD.md` for the full
requirements and the recorded architecture decisions.

## Environment & commands

Python >= 3.13, managed with `uv`.

```bash
uv sync
uv run gcp-explorer --help
uv run gcp-explorer list-resources projects/my-project bucket
uv run gcp-explorer serve                # FastAPI on http://127.0.0.1:8000 (docs at /docs)
```

There are no tests, linter config, or CI in the repo yet — they are Phase 1 work.

Packaging note: the code lives in `app/`, which does not match the project name
`gcp-resource-browser`, so `pyproject.toml` declares
`[tool.hatch.build.targets.wheel] packages = ["app"]`. Removing that breaks `uv sync` at the
wheel-build step.

## Authentication

All GCP access goes through Application Default Credentials. The tool does no auth of its own —
the user runs `gcloud auth application-default login` first. This is a deliberate decision
(see PRD "Resolved Decisions") so the same code works unchanged under a service account.

## Architecture

Everything currently lives in `app/main.py`, which hosts three layers in one module:

1. `search_cai_resources(scope, asset_type, query)` — the only code that talks to GCP. It calls
   Cloud Asset Inventory's `search_all_resources` and flattens each hit to a plain dict
   (`name`, `project`, `location`, `state`).
2. FastAPI app (`app`) — thin HTTP wrapper over that function.
3. Typer CLI (`cli`) — thin terminal wrapper, rendering results as a `rich` table. `cli.serve`
   starts uvicorn against the same FastAPI app, so CLI and API are one process/one deployable.

Both wrappers are intentionally dumb: any new capability (filtering, aggregation, pagination)
belongs in the shared core function so CLI and API stay in sync.

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

Other things a change is likely to touch: the API is project-scoped while the CLI accepts any
scope; invalid input returns HTTP 200 with an `{"error": ...}` body rather than a 400;
`pydantic` is a declared dependency but unused; `AssetServiceClient()` is constructed on every
call instead of being reused.

**Do not convert the code to `async` without a measured reason.** Starlette already runs plain
`def` endpoints in a threadpool, so the existing sync handler is non-blocking and concurrent
(40 workers by default). Going async would make the core a coroutine and force `asyncio.run`
through the whole CLI. The trigger conditions and the migration cost are written up as the
deliberately unscheduled Phase 7 in the delivery plan.
