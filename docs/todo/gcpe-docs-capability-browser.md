# `gcpe docs` — interactive capability browser

**Summary:** A local browser page at `/docs`, launched by `gcpe docs`, with one page per
capability. Each page shows copyable CLI / API / gcloud forms of every example, filled in with
the logged-in session's real scope rather than placeholders, and a "Try it" button that shows
the result inline.

**Origin:** Phase 8 (#2, `54b1a89`) took the tool to 9 CLI commands and 7 endpoints. The design
below was settled in a planning review on 2026-10-17. The first half, the catalogue
(`app/capabilities.py` + `tests/test_capabilities.py`), shipped on its own as PR 1 of 2. This
handoff is the second half. Branch fresh from `main`.

## Why deferred

The catalogue is the valuable, drift-proof half and was reviewable alone. The page is a new
kind of surface for this codebase (served HTML plus JS, and a new GCP call for the scope
picker), so it gets its own review.

## Context

**Relevant files:**
- `app/capabilities.py` — **already built.** `CAPABILITIES`, `params(id)`, `purpose(cap)`,
  `to_cli` / `to_url` / `to_gcloud(cap, example, scope, billing_project)`. The page is a
  renderer over this. Do not restate anything it derives.
- `app/api.py` — `FastAPI(...)` currently leaves Swagger at the default `/docs`.
- `app/cli.py` — `serve` runs `uvicorn.run("app.api:app", ...)`. `docs` follows that pattern.
- `app/core.py` — `adc_quota_project()` reads local credentials with no network call.
  `get_rest_session()`, `_paginate` and `_rest_error` are how a new Google REST API is added
  (see `docs/ARCHITECTURE.md`, "Adding a Google REST API").
- `app/gcloud.py` — renderers already reused by `to_gcloud`.

**Decisions already made (do not reopen without the user):**

| Topic | Decision |
|---|---|
| Route | Browser at `/docs`. Swagger moves to `/api/docs`, ReDoc to `/api/redoc` |
| Deployed | Local only. `gcpe docs` mounts the browser and `gcpe serve` does not. On serve, `/docs` returns 307 to `/api/docs` |
| Page unit | One page per capability (`CAPABILITIES`), grouped by `THEMES` in a left nav |
| Clicking | Plain GET: "Try it" `fetch`es `to_url(...)` and renders inline. **No run/POST endpoint** |
| Forms | CLI / API / gcloud tabs, each with a copy button |
| Prose | Purpose, the `params()` table, standing caveats, and links to `Capability.docs`. No restated prose |
| Context | Eager and local: principal, quota project, default project. Lazy: a scope picker from one Resource Manager `projects.search` call |

**Key constraints:**
- The page never executes a string. It only fetches URLs the catalogue rendered, and the API
  validates them as it would for any client.
- No CDN or external requests. CSS and JS are vendored under `app/static/docs/`, and a test
  greps them for `http`.
- `/healthz` stays I/O-free. Session and picker endpoints are docs-only and excluded from the
  OpenAPI schema (`include_in_schema=False`), so `openapi.yml` doesn't change for them.
- The picker's project list is a picker, not an audit answer. Cache it per process. This is a
  narrow exception to "cache off", and the code should say why.
- `projects.search` is one paginated call, not a per-project walk. With no organization
  (the dev estate has none), CAI has no scope that could list projects, so Resource Manager is
  the only route.

## What to do

1. **Swagger move.** Set `FastAPI(docs_url="/api/docs", redoc_url="/api/redoc")` in
   `app/api.py`, and add a `/docs` → `/api/docs` 307 for the serve app.
2. **`app/docs_site.py`.** `mount_docs(app)` adds an `APIRouter(include_in_schema=False)`
   that replaces the redirect, with:
   - `GET /docs`, the HTML shell;
   - `/docs/static/*`;
   - `GET /docs/_/catalogue?scope=`: capabilities, `params`, and each example's three forms
     rendered for that scope, with `billing_project=core.adc_quota_project()`;
   - `GET /docs/_/session`;
   - `GET /docs/_/projects`.
3. **`core.session_context()`.** Returns:
   - credential type;
   - principal: the service account for impersonated or SA credentials, and for user
     credentials one best-effort, cached tokeninfo call;
   - quota project;
   - the ADC default project.

   It must never raise: if ADC is missing it returns the error, the page shows a
   `gcloud auth application-default login` banner, and the placeholders remain.
4. **`core.list_visible_projects()`.** Resource Manager v3 `projects:search` via
   `get_rest_session` and `_paginate`, translated with `_rest_error`. The `remedy` names
   `cloudresourcemanager.googleapis.com` on the quota project and the
   `resourcemanager.projects.get` permission. Return id, number and display name.
   - Add `scripts/gcloud-list-projects.sh` (`gcloud projects list --format=json`) and a
     comparison against the tool's list, per the "every capability ships with a gcloud
     equivalent" rule.
5. **The page** (`app/static/docs/index.html`, `docs.js`, `docs.css`), in vanilla JS with
   hash routing (`#/grants`).
   - Header: principal, quota project, and a scope `<input list>` filled lazily from the
     picker. Free text `folders/…` and `organizations/…` is allowed. Changing scope
     re-fetches the catalogue.
   - A standing note that Try it makes a real, read-only call billed to the quota project.
   - Each "Try it" result panel shows `truncated`, `suppressed_summary`, `query`, `iam_note`
     and `coverage_note` verbatim, plus an "open raw" link. The stream example reads NDJSON
     incrementally.
   - The `roles:custom` example is rejected for folder scopes. Disable it with the reason
     rather than letting it 400.
6. **`gcpe docs [--host 127.0.0.1] [--port 8000] [--no-browser]`.** Build the app, call
   `mount_docs`, `uvicorn.run(app_obj, ...)`, and open `/docs` with `webbrowser`. Add
   `"docs"` to `capabilities.EXCLUDED_CLI_COMMANDS` with a reason, or the staleness test fails.
7. **Docs.**
   - `README.md` index and status note.
   - `docs/CLI.md` (`gcpe docs`).
   - `docs/API.md` (Swagger is now at `/api/docs`).
   - `docs/DEPLOYMENT.md` (the browser is local-only).
   - Tick the boxes in `DELIVERY_PLAN.md`.
   - Regenerate `openapi.yml` and `docs/CLI_REFERENCE.md`.
   - Delete this file.

## Acceptance criteria

- [ ] `gcpe docs` opens `/docs`, listing every catalogued capability grouped by theme
- [ ] Every example shows CLI, API and gcloud forms with the real scope, each copyable
- [ ] Try it renders inline without a reload and shows the response's caveat fields verbatim
- [ ] `gcpe serve`: `/docs` is a 307 to `/api/docs`, and `/docs/_/*` returns 404
- [ ] No `/docs/_/*` path appears in `openapi.yml`
- [ ] The page loads with no external network requests
- [ ] With ADC missing, the page still loads and says how to log in
- [ ] A `SERVICE_DISABLED` from the picker is reported as such, and the picker falls back to
      free text
- [ ] **Live, before ticking:** on the dev estate, the picker lists all 7 projects and agrees
      with `scripts/gcloud-list-projects.sh`, and every example's Try it result agrees with its
      pasted CLI and gcloud forms
- [ ] `uv run pytest`, `ruff`, and both staleness checks pass

## Related specs / docs

- `app/capabilities.py`, `tests/test_capabilities.py` — the catalogue this renders
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — "The capability catalogue", "Adding a Google REST API"
- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md) — Phase 9
