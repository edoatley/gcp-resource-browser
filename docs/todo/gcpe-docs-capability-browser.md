# `gcpe docs` — interactive capability browser

**Summary:** A local browser page, launched by `gcpe docs`, that lays out every capability the
tool has with a runnable example per capability — so you can see what it does without
reconstructing it from six markdown files.

**Origin:** Phase 8 (merged in #2, `54b1a89`) added `roles`, `grants` and `billing-grants`,
taking the tool to 9 CLI commands and 7 endpoints. That growth is what prompted this. Branch
fresh from `main`.

## Why deferred

Raised while finishing Phase 8; the PR was already large. It is also a new kind of surface for
this codebase (a served HTML page that executes things), so it deserves its own review rather
than riding along with an IAM feature.

## Context

**Relevant files:**
- `app/cli.py` — 9 commands. `serve` already runs uvicorn against the FastAPI app, so CLI and
  API are one process; `docs` would follow that pattern.
- `app/api.py` — 7 endpoints. **Note `/docs` is already FastAPI's Swagger UI** — pick another
  route, e.g. `/guide`.
- `app/params.py` — `Help`, `SearchFilters`, `GrantFilters`, `BillingGrantFilters`,
  `SORTABLE_FIELDS`. The authoritative descriptions already live here.
- `docs/WALKTHROUGH.md` (313 lines) and the numbered walkthrough in `README.md` — two existing
  hand-maintained example sets that a catalogue could eventually generate.
- `docs/CLI.md`, `docs/API.md`, `docs/CONCEPTS.md` — the prose this page summarises, not replaces.

**Current state:**
Capabilities are documented across `README.md`, `docs/CLI.md`, `docs/API.md`,
`docs/CONCEPTS.md`, `docs/CLI_REFERENCE.md` (generated) and `docs/WALKTHROUGH.md`. Swagger at
`/docs` covers the API endpoint-by-endpoint, with no CLI and no narrative. There is no single
place that answers "what can this thing do?", and nothing executable.

**Key constraints:**
- **Never execute a user-supplied string.** The page posts an example *id* from a fixed
  server-side catalogue; the server calls `app.core` in-process. No shell, no `subprocess`,
  no eval. The only free-text input is the scope, which already goes through
  `core.validate_scope`.
- **Bind 127.0.0.1 by default**, as `serve` does. This page runs searches against a real estate.
- **One authoritative source per fact** (see the Interlude phase in the delivery plan). The
  catalogue must reuse `Help`, `ASSET_TYPES` and `SORTABLE_FIELDS` rather than restating them.
  `tests/test_params.py` already enforces this pattern for help strings.
- **It must not go stale.** CI diffs regenerated `openapi.yml` and `CLI_REFERENCE.md`; this
  needs the equivalent — a test that every CLI command and every route appears in the
  catalogue, so adding a capability without listing it fails the build.
- Read-only capabilities only. Exclude `openapi` (writes a file) and `serve`.

## What to do

1. New module `app/capabilities.py`: a declarative catalogue — id, title, one-line purpose,
   the CLI form, the equivalent API call, and a runnable example referencing `Help.*`. Group
   by theme (search · filtering · aggregation · IAM risk · output · operations).
2. Test: assert every `cli` command name and every `app.routes` path appears in the catalogue.
   This is the staleness guard, and the reason to build the catalogue before the page.
3. `GET /guide` returns the page; `GET /v1/capabilities` returns the catalogue as JSON.
   `POST /v1/capabilities/{id}/run` takes `{scope}`, dispatches to the mapped `core` function,
   and returns the result plus the rendered command string for copying.
4. Self-contained HTML/CSS/JS — no CDN. Capability cards, a scope box at the top, a **Run**
   button per example, result rendered inline, and a copy button for the CLI form.
5. `gcpe docs [--host --port --no-browser]`: starts uvicorn and opens a browser at `/guide`.
6. Make the page say plainly that Run executes a real read-only call against the configured
   scope and bills the ADC quota project.
7. Docs: `README.md` index, `docs/CLI.md`, `docs/API.md`, `docs/ARCHITECTURE.md` (why the
   catalogue is the source of truth), and regenerate the two generated files.

Suggested start: steps 1 and 2 alone are useful — a tested catalogue nothing can drift from.
The page is then a renderer over it.

## Acceptance criteria

- [ ] `gcpe docs` opens a page listing every capability, grouped, each with a purpose line
- [ ] Each example runs on click and renders its result inline, without a page reload
- [ ] Adding a CLI command or route without adding it to the catalogue fails a test
- [ ] No endpoint accepts a command, path or query string to execute — only a catalogue id
- [ ] The page loads with no external network requests
- [ ] Examples reuse `Help.*` rather than restating descriptions
- [ ] `uv run pytest`, `ruff check`, and both staleness checks pass

## Related specs / docs

- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — extension checklists; both surfaces stay thin
- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md) — Interlude, "One authoritative source per fact"
- [`docs/WALKTHROUGH.md`](../WALKTHROUGH.md) — existing example set the catalogue could generate
