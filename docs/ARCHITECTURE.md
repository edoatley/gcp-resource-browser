# Architecture

How the tool is built, why, and how to extend it without breaking the properties it depends
on. The requirements and recorded decisions are in the [PRD](PRD.md), and the phasing is in
the [delivery plan](DELIVERY_PLAN.md).

## Overview

```
                ┌──────────────────┐            ┌──────────────────┐
  terminal ────►│  CLI  (cli.py)   │            │  API  (api.py)   │◄──── HTTP clients
                │ Typer · rich     │            │ FastAPI · JSON   │
                │ errors→exit code │            │ errors→status    │
                └────────┬─────────┘            └─────────┬────────┘
                         │      SearchFilters (params.py)  │
                         └───────────────┬─────────────────┘
                                         ▼
          ┌─────────────────────────── core.py ───────────────────────────┐
          │ search_resources() / stream_resources()                       │
          │   validate scope · resolve types · resolve project IDs        │
          │   build_query() ── query.py      build_order_by()              │
          │   NoiseFilter   ── noise.py      TTLCache ── cache.py          │
          │   IAM join      ── aggregate.py                                │
          └──────────────┬─────────────────────────────────┬──────────────┘
                         │ one call per scope               │
       fanout.py ────────┤ (bounded thread pool             │
       (--also-scope)    │  when several scopes)            │
                         ▼                                  ▼
            CAI searchAllResources              CAI searchAllIamPolicies
                                                (only with include_iam)
```

`core.py` is the only module that talks to GCP. The CLI and API are deliberately thin: each
builds a `SearchFilters`, calls the core, and renders the result. New behaviour belongs in the
core so the two surfaces cannot drift apart.

## Modules

| Module | Responsibility |
|:---|:---|
| `app/core.py` | Also `list_risky_roles` (IAM role catalogue, cached 24 h) and `search_grants` (CAI IAM policy search, batched). Validation, type resolution, project ID↔number resolution, the CAI call, exception translation into domain errors. `stream_resources` yields rows. `search_resources` collects them and adds project IDs, IAM and caching. |
| `app/query.py` | Compiles filters into CAI query syntax. **The highest-risk code here** (see below). |
| `app/params.py` | `SearchFilters` (what a search takes), `Help` (how each filter is described), `SORTABLE_FIELDS`, `DEFAULT_LIMIT`. Shared by both surfaces. |
| `app/noise.py` + `noise_rules.json` | Client-side suppression of low-signal resources. |
| `app/config.py` | Site config discovery, validation and merging ([Configuration](CONFIGURATION.md)). |
| `app/aggregate.py` | IAM fetch and join, and the `summary` rollup. |
| `app/fanout.py` | Bounded concurrent search across several scopes. |
| `app/cache.py` | Opt-in TTL response cache, inactive unless a request sets a TTL. |
| `app/output.py` | JSON and CSV emitters for the CLI. |
| `app/role_risk.py` + `role_risk_rules.json` | Which permissions make a role risky, with cited sources. Pure; `core` does the I/O. |
| `app/gcloud.py` | Renders a search as the equivalent `gcloud asset` command (`--show-gcloud`). |
| `app/models.py` | Pydantic wire models: `Resource`, `ResourceList`, `Summary`, `ErrorResponse`. These also produce the OpenAPI schema. |
| `app/asset_types.json` | Friendly name → CAI asset type. Also read by `scripts/` with `jq`. |
| `app/cli.py`, `app/api.py` | The two surfaces. |
| `app/main.py` | Entry point for `python -m app.main`. |

## Cloud Asset Inventory is the data engine

Iterating per-project service APIs across 2000+ projects fails on both latency and quota. CAI
answers a search over a whole organisation, folder or project in one paged call. The accepted
cost is freshness: CAI is a near-real-time index, not live data.

**Never iterate APIs to *find* resources.** Additional APIs may be called to *enrich* a set the
search has already narrowed, which is how the IAM join works (one extra call per scope, not one
per resource). Discovery is always a single scope-wide CAI search.

Fan-out (`--also-scope`) does not break this rule. Each scope is still one scope-wide search;
only the scopes run in parallel. It exists because CAI checks permission on the scope itself,
so someone without organisation-level viewer has no other way to search. It is bounded (default
8, below anyio's 40-thread pool), and failures are reported loudly, never dropped.

## Two filtering mechanisms, deliberately different

**User filters are compiled into the CAI query and evaluated by Google.** Nothing is filtered
locally, because fetching an org-wide result set only to discard most of it is the scaling
failure this tool exists to avoid.

**Noise rules are applied client-side** as results come back. This was forced, not chosen:
`assetType` is not a queryable CAI field, and `asset_types` is an include list that cannot
exclude anything. A user filter narrows what is *fetched*, while a noise rule hides rows from a
broad result. As a result, `limit` counts visible rows, and the pager is read lazily so it stops
as soon as the limit is reached.

Two invariants must never be weakened: `show_all` disables every rule, and suppressed rows are
always counted and explained.

### Why `query.py` is the risky part

A filter that compiles to *valid but wrong* syntax returns a plausible result that is quietly
missing rows. That is worse than an error, because nothing signals it. Two guards:

1. Every value goes through `quote()`, so a value can never change the structure of the query.
2. The compiled query is always visible: `query` in API responses, `--show-query` in the CLI,
   and printed automatically when a filtered CLI search returns nothing.

Unit tests use a fake CAI client, which answers any query. They can prove the tool sent the
query it meant to send, but not that the query was *right*. That is the job of the differential
scripts in `scripts/` ([Development](DEVELOPMENT.md#checking-against-gcloud)).

## Accuracy properties

These properties are what make the tool trustworthy for audits, and code review should check
them:

- **Truncation is announced.** `limit` (default 1000) sets `truncated`, and the CLI prints a
  warning. Summaries are never capped.
- **Suppression is announced**, with reasons.
- **Partial fan-out is announced**: failed scopes are listed, and the CLI exits 7.
- **Sorting is done by CAI**, and unknown sort fields are rejected, not ignored.
- **Project IDs are resolved to numbers** before querying, because `project:<id>` matches
  nothing. `Resource.project` keeps the number, and `project_id` is recovered for display.
- **IAM is attached-only and says so** in `iam_note`. `iam_bindings: null` (not requested) and
  `[]` (none attached) are kept distinct.
- **Errors are typed.** The core raises a domain error, and each surface maps it to an exit
  code (`cli._EXIT_BY_ERROR`) or HTTP status (`api._STATUS_BY_ERROR`).
- **The cache is off by default**, and every filter is part of its key.

## Role risk is derived from permissions

`/v1/roles` and `/v1/grants` share one rule set, which maps permissions to a risk level. A
role's risk is the highest risk of any permission it holds. `/v1/roles` evaluates the rules
against the IAM role catalogue, and `/v1/grants` compiles them into
`policy.role.permissions:(…)` for CAI. The two cannot disagree, and custom roles are covered
without being named. See [Role risk](ROLE_RISK.md).

Four constraints shape `search_grants`, all verified against the live API:

- **32 alternations per query**, counted as permissions × member types. Permissions are batched
  to fit, and results are merged per (resource, role, member, condition).
- **CAI returns whole policies.** Bindings are narrowed to the roles CAI reports in
  `explanation.matched_permissions`, and members to the requested types. This narrows rows
  already fetched; it never fetches more.
- **No negation in IAM policy search**, so service agents are hidden client-side, counted, and
  shown with `show_all`. These are the same invariants as noise reduction.
- **A scope cannot see grants above it.** Every response carries a `coverage_note`.

The IAM role catalogue is the **only cache that is on by default** (24 hours). It is Google's
reference data about what roles contain, not the customer's estate, so a day-old copy cannot
hide a fix someone is verifying. Results from estate searches stay uncached by default.

## Synchronous by design

API endpoints are plain `def` functions. Starlette runs them in a threadpool, so they are
already concurrent. Converting to `async` would turn the core into coroutines and force
`asyncio.run` through the whole CLI. Phase 7 of the delivery plan lists the measured conditions
that would justify that change.

## Extending

### Adding a resource type

Add a line to `app/asset_types.json`:

```json
{ "types": { "zone": "dns.googleapis.com/ManagedZone" } }
```

Both surfaces validate against this mapping, so the new name works in the CLI and the API at
once. Sites can do the same without a code change through
[configuration](CONFIGURATION.md).

### Adding a filter

1. Add the help text to `Help` and the field to `SearchFilters` in `app/params.py`.
2. Compile it in `query.build_query`, passing values through `quote()`.
3. Add it to `core._cache_key`, or one filter's cached results will be returned for another.
4. Add a parameter to `cli.search` and `api.search_resources` (and to `summary` and `stream` if
   it applies) that references `Help.X`, and pass it into the `SearchFilters` each surface
   builds. Don't retype the help text: `tests/test_params.py` checks each shared string appears
   verbatim on both surfaces.
5. Write a test that pins the exact compiled query string.
6. Add the `gcloud` equivalent to `scripts/gcloud-search-resources.sh`.
7. Regenerate `openapi.yml` and `docs/CLI_REFERENCE.md`, and update the parameter tables in
   [API](API.md) and [Concepts](CONCEPTS.md).

The CLI and API keep separate function signatures on purpose. FastAPI and Typer both build
their output (the OpenAPI schema, `--help`) by inspecting the signature, so merging them into
`**kwargs` would break both. What is shared is the knowledge (help text, filter set), not the
declarations.

### Adding an error

Define it in `core.py` with a `code`, then map it in **both** `api._STATUS_BY_ERROR` and
`cli._EXIT_BY_ERROR`. When translating a Google exception, include `exc.message` in the error
instead of guessing at the cause.
