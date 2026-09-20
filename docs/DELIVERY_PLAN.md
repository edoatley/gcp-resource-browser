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

## Phase 1 — Foundation: structure, types, correctness ☑

**Goal:** make the codebase safe to extend before adding features to it.

- ☑ Split `app/main.py` into `app/core.py` (CAI access), `app/api.py` (FastAPI), `app/cli.py`
  (Typer), `app/models.py` (Pydantic). `app/main.py` remains a thin entry shim so
  `python -m app.main` keeps working; the console script points at `app.cli:cli`.
- ☑ Pydantic models (`Resource`, `ResourceList`, `ErrorResponse`), giving the OpenAPI schema
  real content — a prerequisite for Phase 5's `openapi.yml` export, and covered by a test that
  asserts the schema is populated.
- ☑ Proper status codes: 400 unknown type / malformed scope, 403 permission denied, 404 scope
  not found, 502 upstream failure. Google exceptions are translated into domain errors in the
  core, so the CLI and API map the same failures to exit codes and status codes respectively.
- ☑ Distinct CLI exit codes (2 usage, 3 permission, 4 not-found, 5 upstream).
- ☑ Explicit pagination and limits: `page_size` upstream, `--limit`/`limit` on total results,
  defaulting to 1000. Truncation is *reported* (`truncated` in the body, a warning line in the
  CLI) rather than applied silently.
- ☑ Capture more of the CAI payload: `full_name`, `asset_type`, `labels`, `create_time`,
  `parent_full_resource_name`, `state`. `full_name` is the join key Phase 5 needs.
- ☑ `pytest` suite (34 tests) with the CAI client faked — no network, no credentials — plus
  `ruff` lint and format, both clean.
- ☑ API scope generalised to match the CLI: `GET /v1/resources?scope=...&type=...` accepts
  project, folder, or organization. The `/v1` prefix was pulled forward from Phase 5, since
  adding it before any consumer exists is free and adding it later is a breaking change.
- ☑ `scripts/` differential checks against `gcloud` (see below).
- ☑ CAI client built once and reused (`get_client`, `lru_cache`) instead of per call. Listed
  under Phase 4, but landed here: the restructure made it a natural change, and dependency
  injection for the client was needed for the tests regardless.

### Differential checking against `gcloud`

`scripts/` holds `gcloud` equivalents of what the tool does, plus `compare-resources.sh` which
diffs the two and exits non-zero on disagreement.

This covers a gap the unit tests structurally cannot: a faked client answers whatever query it
is given, so it can never reveal that the *query itself* is wrong — filtering client-side that
should be server-side, or a result set quietly missing rows. Only a second implementation
against the real API catches that.

**Practice: every new capability ships with a `gcloud` equivalent here.** This matters most from
Phase 2 on, where `--label` and `--location` compile into CAI query syntax and a subtly wrong
filter string is easy to miss and hard to notice.

**Deferred from this phase:** the CLI still exposes `list-resources <scope> <type>` with a
required single type. Making type optional is Phase 3; accepting several is Phase 2.

---

## Phase 2 — Search and filtering depth ☑

**Goal:** deliver the PRD's "Global Search" and "Rich Filtering" objectives.

- ☑ Free-text search: `gcp-explorer search <scope> <term>`, built on CAI's `query` parameter —
  the PRD's headline "find a resource by name across 2000 projects" case, answered in one call.
- ☑ `--label key=value` (repeatable, plus bare `--label key` for "has this label"),
  `--location` and `--project` (repeatable, ORed), all compiled into CAI query syntax and
  evaluated **server-side**. Filtering client-side would mean fetching an org-wide result set
  to discard most of it — the scaling failure the tool exists to avoid.
- ☑ Multiple resource types in one request (`--type bucket --type vm`), sent as a single
  upstream call rather than one per type.
- ☑ `ASSET_TYPES` broadened from 2 to 20 friendly names, plus raw CAI asset types and RE2
  patterns accepted as pass-through, so the mapping is a convenience rather than a hard limit.
- ☑ `gcp-explorer types` / `GET /v1/types` list the mapping.
- ☑ Every filter mirrored as an API query parameter.
- ☑ `--raw-query` / `?raw_query=` for CAI syntax not yet modelled (`NOT state:ACTIVE`,
  `createTime<...`), ANDed with the rest.

### Query construction is the risk, and is isolated

`app/query.py` exists as its own module because it is the highest-risk code in the project: a
filter that compiles to *valid but wrong* syntax returns a plausible result set that is quietly
missing rows. In an audit tool that is the worst failure mode — worse than an error, which at
least announces itself.

Two mitigations, both of which should be preserved as filters grow:

1. **Values are quoted defensively.** A value carrying a space, a parenthesis, or a bare `OR`
   would otherwise change the *structure* of the query rather than the value being matched.
   Safe values stay bare so CAI's `*` wildcards keep working; anything else is quoted and
   escaped. Tested, including a deliberate injection attempt.
2. **The compiled query is echoed back** — `query` in the API response, `--show-query` on the
   CLI, and automatically on an empty CLI result. An empty result with filters applied is
   otherwise ambiguous: nothing matched, or the filter compiled to something unintended. The
   user must be able to tell which.

### Verified facts that shaped this phase

- `page_size` is capped at **500 server-side** regardless of what is requested, so
  `DEFAULT_PAGE_SIZE` sits at the ceiling.
- `asset_types` accepts **RE2 patterns**; a pattern matching no supported type returns
  `INVALID_ARGUMENT`, which is why that error now maps to `InvalidFilterError` rather than
  `InvalidScopeError`.
- Query terms are space-separated for AND, with `field:(a OR b)` for alternation. `field:value`
  is word-contains; `field=value` is exact.

**Needs verification against a live API:** the `project:` query field used by `--project` is
modelled on CAI's searchable-field set but is not demonstrated in the request docstring's
examples. `scripts/compare-resources.sh` will confirm or refute it on first real run against a
project with resources. If it proves wrong, the filter is the only thing that changes — the
compiler and both surfaces are unaffected.

**Deferred to Phase 3:** `--type` is still required. Making it optional is coupled to noise
reduction, not independent of it: searching every asset type across an organisation without a
noise filter returns something unusable, so the two ship together.

---

## Interlude — One authoritative source per fact ☑

Not a PRD phase; a consolidation done after Phase 2, when duplication between the two surfaces
had produced its first real drift.

**The drift that prompted it:** the API documented RE2 patterns for `type` while the CLI's
`--type` help did not, though both supported them. Both were written in the same session, an
hour apart.

**What was unified, and what deliberately was not.** The distinction applied throughout: DRY is
about *knowledge* having one authoritative home, not about text never repeating.

- ☑ `app/params.py` holds `Help` (the prose describing each filter) and `SearchFilters` (the
  set of inputs a search takes). Both surfaces build a `SearchFilters` and hand it to the core,
  so adding a filter changes one signature rather than every forwarding call.
- ☑ `app/asset_types.json` holds the friendly-name mapping, loaded by `app/core.py` and read
  with `jq` by `scripts/gcloud-search-resources.sh`. It previously existed twice, in two
  languages — 20 entries each.
- ☑ Tests that make drift a failure: `tests/test_params.py` asserts each shared `Help` string
  appears verbatim in both surfaces, and `tests/test_asset_types.py` asserts bash and Python
  resolve identically and that the bash case statement has not crept back.
- ✗ **Signatures were not unified.** FastAPI and Typer both work by introspecting the function
  signature to build their artifacts. Collapsing them to `**kwargs` would destroy the
  auto-generated OpenAPI schema the PRD asks for, and the CLI's generated help with it. The
  declarations look alike but produce different things.
- ✗ **Surface-specific detail stays local.** Short flags (`-t`), OpenAPI `examples`, validator
  spellings (`ge=1` vs `min=1`), and `--show-query` (CLI-only — the API returns `query` in the
  body, where it costs nothing) belong to one surface. Each may *append* to a shared `Help`
  string where it has more to say: the API appends the full type list, which the schema can
  afford and `--help` cannot.

**Why the asset-type mapping is data but not yet configuration.** Two separable questions.
Making it *data* pays for itself now, because bash needs to read it. Making it *user-overridable
configuration* is a different feature — it needs discovery paths, merge-or-replace semantics,
and validation of untrusted input — and belongs with Phase 3's noise ruleset, which has the
same requirements. Building one override mechanism for both is better than two.

**One cost, accepted knowingly:** the differential check is slightly weaker. When the mapping
lived in both languages, a typo in one showed up as a mismatch. Now both read the same file, so
a wrong alias makes both sides search the wrong type and agree. That is a fair trade — the
check exists to validate *query compilation*, not the alias table, and silent drift between two
copies was the more likely failure. `tests/test_asset_types.py` covers the shape of the table
instead.

---

## Phase 3 — Query-all-by-default with noise reduction ☐

**Goal:** the PRD's resolved decision that the tool queries *all* resource types by default and
hides low-value noise unless asked.

- ☐ Make `asset_types` optional — omitting it searches everything in scope. Coupled to the
  noise filter below: without it, an org-wide all-types search is unusable, which is why
  Phase 2 left `--type` required.
- ☐ Noise-reduction ruleset (default-suppressed asset types: standard IAM roles, default network
  routes, system-managed and auto-created resources), defined as data in one place so it is
  reviewable and testable rather than scattered through call sites.
- ☐ `--show-all` (CLI) and `?show_all=true` (API) to bypass it, exactly as named in the PRD.
- ☐ Report what was suppressed (`"142 results, 38 hidden — use --show-all"`) so the filter is
  never silently misleading.
- ☐ Allow the ruleset to be overridden from a config file for site-specific noise. Design the
  override mechanism once and apply it to `app/asset_types.json` too — both need discovery
  paths, merge-or-replace semantics, and validation. See the Interlude above for why the
  asset-type mapping was made data without yet being made configurable.

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

- ☑ **Create the CAI client once and reuse it.** Delivered early in Phase 1 (`core.get_client`,
  `lru_cache`), since the restructure made it natural and the tests needed client injection
  anyway. Was the largest avoidable latency cost.
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
- ☑ API versioning prefix (`/v1/...`) — pulled forward and delivered in Phase 1.
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
