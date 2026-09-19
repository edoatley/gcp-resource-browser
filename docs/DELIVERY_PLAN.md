# Delivery Plan: GCP Resource Explorer

Phased breakdown of the work described in [PRD.md](PRD.md). Each phase is independently
shippable — the tool is usable at the end of every one. Phases are ordered so that the
structural work that later phases depend on happens first, and so that the highest-value PRD
objectives (global search, noise reduction) land before the polish.

Legend: ☐ not started · ◐ in progress · ☑ delivered

---

## Phase 0 — Walking skeleton ☑

Already in the repo before planning started.

- ☑ Cloud Asset Inventory (CAI) `search_all_resources` wrapper as the single data-access point.
- ☑ FastAPI app exposing one endpoint, Typer CLI exposing one command, both over that wrapper.
- ☑ `rich` table rendering for CLI output.
- ☑ ADC authentication (no in-app credential handling).
- ☑ Packaging fixed: `[tool.hatch.build.targets.wheel] packages = ["app"]` and the
  `gcp-explorer` console script pointed at `app.main:cli`, so `uv sync` and
  `uv run gcp-explorer` both work.

**Known limitations carried into Phase 1:** everything lives in one module; only two asset
types (`bucket`, `cloudrun`); the API is project-scoped only while the CLI already accepts any
scope; bad input returns HTTP 200 with an `{"error": ...}` body; `pydantic` is a declared
dependency but unused; no tests.

---

## Phase 1 — Foundation: structure, types, correctness ☐

**Goal:** make the codebase safe to extend before adding features to it. No new user-facing
capability beyond correct error codes.

- ☐ Split `app/main.py` into `app/core.py` (CAI access), `app/api.py` (FastAPI), `app/cli.py`
  (Typer), `app/models.py` (Pydantic). Keep the rule that CLI and API are thin wrappers and all
  behaviour lives in the core.
- ☐ Define Pydantic models for a resource row and for the API response envelope. This is what
  makes the auto-generated OpenAPI schema meaningful rather than `{}` — a prerequisite for the
  Phase 5 `openapi.yml` objective.
- ☐ Return proper HTTP status codes: `400` for an unknown resource type, `403` for a CAI
  permission denial, `404` for an unknown scope. Surface the same failures as non-zero CLI exit
  codes.
- ☐ Handle CAI pagination and result caps explicitly rather than relying on implicit iterator
  exhaustion; expose `page_size` / `--limit`.
- ☐ Capture more of the CAI payload than the current four fields — at minimum `assetType`,
  `labels`, `createTime`, `parentFullResourceName`. Phases 2 and 3 filter on these.
- ☐ Test suite (`pytest`) with the CAI client faked, plus `ruff` for lint/format. Neither
  exists today.

**Exit criteria:** `pytest` and `ruff check` pass; the API's `/docs` shows real response
schemas; an invalid resource type returns 400 from the API and a non-zero exit from the CLI.

---

## Phase 2 — Search and filtering depth ☐

**Goal:** deliver the PRD's "Global Search" and "Rich Filtering" objectives.

- ☐ Free-text name search across a scope (`gcp-explorer search <scope> <term>`), built on CAI's
  `query` parameter — this is the PRD's headline "find a VM by name across 2000 projects"
  use case, and CAI already answers it in one call.
- ☐ `--label key=value` (repeatable), `--location`/`--region`, and `--project` filters,
  compiled into CAI query syntax (`labels.env=prod`, `location:europe-west2`) rather than
  filtered client-side, so the work stays server-side.
- ☐ Accept multiple resource types in one request (`--type bucket --type cloudrun`).
- ☐ Broaden `ASSET_TYPES` beyond the current two, and add a `gcp-explorer types` command that
  lists supported friendly names. Accept raw CAI asset type strings as a pass-through so the
  mapping is never a hard limit.
- ☐ Mirror every filter as an API query parameter.

**Exit criteria:** a single CLI invocation finds a named resource across an org scope and
filters it by label and region.

---

## Phase 3 — Query-all-by-default with noise reduction ☐

**Goal:** the PRD's resolved decision that the tool queries *all* resource types by default and
hides low-value noise unless asked.

- ☐ Make `asset_types` optional — omitting it searches everything in scope.
- ☐ Noise-reduction ruleset (default-suppressed asset types: standard IAM roles, default network
  routes, system-managed and auto-created resources), defined as data in one place so it is
  reviewable and testable rather than scattered through call sites.
- ☐ `--show-all` (CLI) and `?show_all=true` (API) to bypass it, exactly as named in the PRD.
- ☐ Report what was suppressed (`"142 results, 38 hidden — use --show-all"`) so the filter is
  never silently misleading.
- ☐ Allow the ruleset to be overridden from a config file for site-specific noise.

**Exit criteria:** a bare `gcp-explorer search organizations/123` returns a readable result set;
`--show-all` returns the unfiltered set; the difference between them is explained in the output.

---

## Phase 4 — Performance, staying synchronous ☐

**Goal:** the PRD's scalability objective, met with the cheapest mechanism that reaches it.
Deferred to here deliberately — optimising before Phases 2–3 settle the query shape would be
premature.

**The core stays synchronous.** The reason is a fact about FastAPI that is easy to miss:
Starlette inspects each endpoint and, when it is a plain `def` rather than `async def`, wraps it
in `run_in_threadpool` automatically (`starlette/routing.py`, `request_response`). The endpoint
in `app/main.py` is already a plain `def`, so **it already runs off the event loop and already
serves concurrent requests** — up to anyio's default limiter of 40 worker threads, verified in
this environment. The API is not currently serialising requests, and does not need `async` to
stop doing so. Adopting `async` would make `search_cai_resources` a coroutine, which forces
`asyncio.run` into every CLI command and turns a local change into a whole-codebase one — for
concurrency the threadpool is already providing.

Do the cheap wins first and measure before reaching for anything structural:

- ☐ **Create the CAI client once and reuse it.** The code constructs `AssetServiceClient()`
  inside every call, paying channel and TLS setup each time. Build it once at import or in a
  FastAPI `lifespan` handler. The client is thread-safe, so a single instance serves the
  threadpool fine. This is almost certainly the largest avoidable latency cost, and it is a
  handful of lines with no async anywhere.
- ☐ **Establish a latency baseline against a real large scope and record it**, so "acceptable
  latency thresholds" becomes a number rather than an adjective. Do this *before* the remaining
  items, so their value is measured rather than assumed.
- ☐ Short-TTL response cache. CAI is a near-real-time index, not live data (per the PRD's own
  trade-off table), so caching costs little accuracy and protects against repeated identical
  dashboard polls.
- ☐ Streaming/paged API responses so a 2000-project result set does not have to be fully
  materialised before the first byte.
- ☐ Concurrent fan-out when a request spans several scopes or asset types: a
  `ThreadPoolExecutor` with a bounded worker count. Sync, ordinary to read, and it keeps a
  2000-project fan-out from tripping CAI quota. Note it draws from the same 40-thread budget as
  the request threadpool — size it explicitly rather than letting the two compete.
- ☐ Tune the anyio threadpool limit if the baseline shows 40 concurrent requests is the
  ceiling being hit.

**Exit criteria:** a recorded latency figure for a broad org-wide query, and a measurement
showing where the remaining time actually goes.

---

## Phase 5 — API maturity ☐

**Goal:** the PRD's OpenAPI and aggregated-response objectives — the point at which the API is
fit for other teams to consume.

- ☐ `gcp-explorer openapi --out openapi.yml` to export the spec to a file. FastAPI serves the
  schema and Swagger UI already; the PRD asks specifically for a committed `openapi.yml`
  artifact, which is what downstream codegen consumes.
- ☐ Machine-readable CLI output (`--output json|csv|table`) so the CLI is scriptable, not just
  human-readable.
- ☐ Sorting and stable ordering of results.
- ☐ API versioning prefix (`/v1/...`) before any external consumer depends on the paths.
- ☐ Aggregation, as scoped in the section below.

### Resolving "aggregate data from multiple GCP sources"

The PRD asks for this (Objectives §2) while also selecting CAI as *the* data engine and
rejecting standard GCP APIs. The tension is only apparent: **"multiple sources" means multiple
CAI surfaces, not multiple data engines.** The rejected pattern is iterating per-project service
APIs *to discover what exists*. Reading several CAI methods and joining them is not that.

CAI exposes far more than the one method currently used:

| Surface | Answers |
|:---|:---|
| `search_all_resources` | What exists *(in use today)* |
| `search_all_iam_policies` | Who is granted access, by policies attached at or below a scope |
| `batch_get_effective_iam_policies` | Who *effectively* has access to specific resources, inheritance included |
| `analyze_org_policy_governed_assets` | Which org-policy constraints govern an asset |
| `batch_get_assets_history` | How an asset changed over a time window |
| `list_assets` / `export_assets` | Full metadata snapshot, incl. bulk export to GCS/BigQuery |

**The design: a two-stage model.**

1. **Discovery is always CAI search, one call, scope-wide.** Unchanged, and the rule stands.
2. **Enrichment is opt-in and operates only on an already-narrowed result set.** The user has
   filtered to N resources; enrichment attaches extra facets to those N.

This makes the architectural rule precise, and worth restating in exactly this form:

> Never iterate APIs to **find** resources. You may call additional APIs to **enrich** a bounded
> set the user has already narrowed to.

**Concrete first deliverable — `?include=iam`.** "Show me every public bucket in the
organisation and who can reach it" is the motivating query. It needs resources joined to IAM,
which no single CAI method returns, so it is a genuine aggregation and a genuinely optimised
payload.

- ☐ Resource ↔ IAM join, keyed on the CAI **full resource name** (note: the code currently
  keeps only `display_name`, so Phase 1's "capture more of the CAI payload" is a hard
  prerequisite).
- ☐ Use `search_all_iam_policies` for the broad, cheap pass. Be explicit in the response about
  what it does *not* cover: it returns policies **attached** to resources, so a binding
  inherited from a parent folder or project will not appear against the child. Presenting that
  as "who can access this bucket" would be quietly wrong, and wrong in the unsafe direction.
- ☐ For accuracy, offer `batch_get_effective_iam_policies`, which resolves inheritance.
  Constraint verified against the installed library: **a maximum of 20 resource names per
  call**, so this needs chunked, concurrency-bounded batching — which is exactly why it belongs
  after Phase 4's bounded fan-out rather than before it.
- ☐ Make the accuracy/cost trade-off explicit in the API surface (e.g.
  `iam_mode=attached|effective`) rather than picking one silently.
- ☐ Summary aggregation that needs no second surface at all: counts grouped by project, asset
  type, and location in a single payload — cheap, and probably the most-used endpoint.

**Deferred, and gated:** enriching from a genuine non-CAI service API (live config or state
that CAI does not index — say a bucket's current public-access-prevention setting). This is
*permitted* by the rule above since it enriches rather than discovers, but it re-introduces
per-resource API calls and quota exposure. It should not be built until something concrete
needs a field CAI lacks, and when it is, it must be opt-in, hard-capped on result count, and
documented as the slow path. Flagging it here so that the day someone wants it, the constraint
is already written down.

**Recommended split:** the summary aggregation and `?include=iam` with `attached` mode are
Phase 5. Effective-IAM mode and anything non-CAI can wait for demand.

---

## Phase 6 — Distribution ☐

**Goal:** the PRD's stated maturation path from local execution to container distribution.

- ☐ Dockerfile serving the API, with ADC mounted in rather than baked (credentials must never
  enter the image).
- ☐ Document service-account usage as the non-interactive alternative to
  `gcloud auth application-default login` — the auth decision was made precisely so this works
  without code changes.
- ☐ CI running lint and tests.
- ☐ Health/readiness endpoint for container orchestration.

---

## Phase 7 (conditional) — Async, only if measurement demands it ☐

**Not scheduled.** This phase exists so the option is documented, not so it gets built. The
PRD's stated rationale for FastAPI includes non-blocking calls to GCP; Phase 4 already achieves
non-blocking request handling via the threadpool, so this phase is about the remaining ceiling,
not about correctness.

**Trigger conditions — build this only if one of these is measured, not anticipated:**

- Sustained concurrent requests exceed the ~40-thread limiter and raising it stops helping
  (thread memory or context-switching becomes the constraint).
- Fan-out breadth per request grows large enough that one thread per in-flight CAI call is
  genuinely wasteful — threads are fine for tens of concurrent calls, less so for thousands.

**What it would involve, if triggered:**

- `AssetServiceAsyncClient` is available in the installed `google-cloud-asset`;
  `search_all_resources` is a true coroutine returning a `SearchAllResourcesAsyncPager` that
  supports `__aiter__`, so `for resource in pager:` becomes `async for`.
- The cost is viral: the core becomes `async def`, and the CLI must wrap every call in
  `asyncio.run(...)`. Maintaining parallel sync and async cores is the one option to rule out —
  it breaks the rule that CLI and API share all behaviour.
- An async gRPC client binds to the running event loop, so it must be built inside the loop
  (FastAPI `lifespan`) rather than at import.
- **Transport constraint:** the only async transport shipped is
  `AssetServiceGrpcAsyncIOTransport` — there is no async REST transport, though a sync
  `AssetServiceRestTransport` exists. `grpcio` is already in the dependency tree and `grpc.aio`
  imports cleanly here, but anywhere that permits HTTPS/REST and not gRPC (some corporate
  egress proxies) this phase is unavailable and the Phase 4 threadpool is the only option.
  Worth knowing before Phase 6 fixes the deployment target.

---

## Sequencing rationale

Phase 1 comes first because every later phase adds fields, filters, or response shapes, and
doing that inside a single 100-line module with no tests gets expensive quickly. Phases 2 and 3
are the PRD's actual user-facing value and could be reordered if broad discovery matters more
than targeted search. Phase 4 is intentionally after the query shape stabilises, and stays
synchronous: FastAPI already runs plain `def` endpoints in a threadpool, so the concurrency is
there without making the core a coroutine. Phases 5 and 6 are adoption work, needed only once
other people or systems consume the tool. Phase 7 is deliberately unscheduled — async is a
response to a measured ceiling, not a starting position.
