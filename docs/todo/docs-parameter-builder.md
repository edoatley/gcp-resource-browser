# `gcpe docs` parameter builder: an editable form, with examples as presets

**Summary:** Lead each capability page with a form generated from its parameter table. Clicking
an example fills it in, editing any value re-renders the CLI / API / gcloud forms live, and
**Call API** runs what was entered. Examples then teach the form instead of being the only
thing you can run.

**Branch context:** `phase-9-docs-browser`. This was raised while testing the first version of
the browser locally. Branch fresh from `main` after that PR merges.

## Why deferred

The user tried the browser and found that fixed examples are a dead end when they don't fit
the estate (Stream was "every VM", in a project with none). The builder is the better
demonstration, but it is a new feature, and the browser PR was already large. Agreed
design (2026-10-17): the **builder sits on top and the examples fill it**. There is one result
area per page.

## Context

**Relevant files:**
- `app/capabilities.py` — `params(id)` gives each input's API name, CLI flag or positional,
  `Help` text, `is_list` and `is_bool`. `to_cli` / `to_url` / `to_gcloud` render an `Example`.
  Read this first.
- `app/docs_site.py` — `catalogue_payload` renders every example for a scope.
  `/docs/_/*` routes are browser-only and absent from the OpenAPI schema.
- `app/static/docs/docs.js` — `capabilityPage`, `exampleCard`, `callApi`, and `put()`.
  `put()` is the only path for adding children to the page, and a test enforces that.
- `tests/test_capabilities.py`, `tests/test_docs_site.py` — patterns to copy.

**Current state:** Each page shows fixed example cards. Each card has three server-rendered
forms and a Call API button that fetches the example's rendered `/v1` URL. Parameters
appear as a read-only table. Nothing on the page can be edited except the scope.

**Key constraints:**
- **The server renders, never the browser.** Add a render endpoint and don't reimplement
  `to_cli` / `to_url` / `to_gcloud` in JS. One renderer is what keeps the three forms
  from drifting, and it's tested against `--show-gcloud`.
- **Rendering never executes anything.** Call API stays a plain GET of the rendered URL.
  There is no endpoint that runs a command.
- **The `project` filter can't be rendered as gcloud without a lookup.** `to_gcloud` raises,
  because resolving an ID to a number is a CAI call. Either show the gcloud tab as "resolved at
  search time" for that case, or do the lookup and say so. Never fake the number.
- **Show invalid values instead of dropping them.** Unknown params, bad labels and bad sort
  fields must come back as inline errors (`QueryError` / `InvalidFilterError` text).
- CLI-only flags (`--show-query`, `-o`) and API-only params (stream's) render as "no
  equivalent" on the other surface, as today.

## What to do

1. **`GET /docs/_/render?capability=<id>&<api params…>`** in `docs_site.py`.
   - Build an `Example` from the query params, rejecting names not in `params(id)`.
   - Return `{cli, url, gcloud, unavailable, errors}`.
   - Map `QueryError` / `InvalidFilterError` and unknown asset types into `errors`, and keep
     the other forms when one fails.
2. **Page.** Above the examples, render a form from `cap.params`:
   - a checkbox for `is_bool`;
   - a list input for `repeatable`, with add/remove rows or comma separation (pick one and
     state it);
   - a text input otherwise.

   `scope` comes from the header. Debounce input by about 250 ms, call `/render`, and update
   the three tabs. Call API uses the rendered URL.
3. **Examples become presets.** Clicking one fills the form, renders, and scrolls to the
   builder. Drop the per-example Call API buttons and result panels: there is one result area.
4. **Empty state.** Keep `emptyState()` and add "Edit the filters above" to its next steps.
5. **Docs.** Update the Phase 9 section in `docs/CLI.md` and `docs/ARCHITECTURE.md`, and tick
   the delivery-plan item.

## Acceptance criteria

- [ ] Editing a field updates the CLI, API and gcloud forms without a reload
- [ ] Every catalogue example, loaded as a preset and rendered via `/render`, gives exactly the
      strings `catalogue_payload` gives for it (test)
- [ ] An unknown parameter, a malformed label and a bad sort field each return an inline
      error, not a 500 (tests)
- [ ] `/docs/_/render` is absent from `openapi.yml`, and 404s on `gcpe serve`
- [ ] No JS reimplementation of rendering. `docs.js` builds no `gcpe` or `gcloud` strings
      (assert in a test, as the `put()` guard does)
- [ ] Checked in a real browser on the dev estate: a preset, an edit, and Call API, with the
      result matching the pasted CLI form

## Related specs / docs

- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md) — Phase 9
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — "The capability catalogue", "The browser over it"
- [`docs/CLI.md`](../CLI.md#browsing-every-capability) — the user-facing description
