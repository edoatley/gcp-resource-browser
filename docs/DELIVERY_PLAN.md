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
- ☑ `pytest` suite with the CAI client faked (34 tests at the time; it has grown since) — no network, no credentials — plus
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

## Phase 3 — Query-all-by-default with noise reduction ☑

**Goal:** the PRD's resolved decision that the tool queries *all* resource types by default and
hides low-value noise unless asked.

- ☑ `asset_types` optional — omitting `--type` / `type=` searches every supported type.
- ☑ Noise ruleset in `app/noise_rules.json`, 15 rules, each carrying the reason it exists.
- ☑ `--show-all` (CLI) and `?show_all=true` (API), exactly as the PRD names them.
- ☑ What was suppressed is always reported, with the top reasons named.
- ☑ Site overrides via `app/config.py` — one mechanism, shared with the asset-type mapping.

### Suppression is client-side, and that was forced

Verified against the live API: `assetType` is **not** a queryable CAI field
(`400 Unsupported field: 'assetType'`), and `asset_types` is an include list with no way to
express exclusion — RE2 has no negative lookahead. So unlike user filters, which are always
compiled into the query and evaluated upstream, noise rules are applied to results as they
stream back.

The distinction is worth keeping straight: **a user filter narrows what is fetched; a noise rule
hides low-value rows from a result the user asked for broadly.** The server-side rule from
Phase 2 is not weakened by this.

The consequence is that `limit` caps *visible* results. The pager is consumed lazily, skipping
noise, stopping one past the limit — so a `--limit 5` yields five real resources rather than
five rows of which four were noise, without draining the pager.

### The ruleset was derived from a census, not from intuition

A survey of the real estate (515 resources, 37 types) drove every rule:

| Asset type | Share | Verdict |
|:---|---:|:---|
| `serviceusage.../Service` | 42% | enabled APIs, not resources — suppress |
| `artifactregistry.../DockerImage` | 10% | image layers; the Repository is the resource |
| `compute.../Route` | 8% | only `default-route-*` — user routes kept |
| `compute.../Subnetwork` | 8% | only `default` — custom subnets kept |
| `run.../Revision` | 5% | per-deploy rows; the Service is the resource |

**Measured effect: 515 resources become 84. 84% suppressed.** Spot-checked rather than trusted:
`ace-gcp-training` hides 22 enabled APIs and 4 system log sinks/buckets, keeping the Project;
`gcp-sandbox-2026-18798` hides 43 default routes, 42 auto-created subnets and the default VPC,
while keeping service accounts, buckets, the Artifact Registry repository and workload identity.

Seven of the fifteen rules are **name-scoped**, which is what makes 84% safe: a default VPC
subnet is noise, a subnet someone deliberately built is not, and both are
`compute.googleapis.com/Subnetwork`. Whole-type suppression alone would have hidden real
infrastructure.

### Two invariants, because hiding data from an audit tool is dangerous

1. `--show-all` disables suppression entirely — no rule is exempt.
2. The count and reasons are always reported. A fully-suppressed result says
   `No resources found after hiding 22`, never a bare "No resources found", because the two
   mean very different things.

### Site configuration

`app/config.py` is the single override mechanism the Phase 1 Interlude deferred. Discovery:
`$GCP_EXPLORER_CONFIG`, then `./gcp-explorer.json`, then `~/.config/gcp-explorer/config.json`.

It **merges** rather than replaces — a site adds the types and rules it cares about without
restating the defaults, so an upgrade cannot silently drop newly added ones. `unsuppress` turns
off a default rule by asset type. Unknown keys are rejected rather than ignored: a typo'd key
would otherwise leave someone convinced their config had applied. A missing
`$GCP_EXPLORER_CONFIG` path is an error, since naming a path and having it quietly ignored is
worse than failing.

---

## Phase 4 — Performance, staying synchronous ◐

**Goal:** the PRD's scalability objective, met with the cheapest mechanism that reaches it.

**The core stays synchronous.** Starlette wraps a plain `def` endpoint in `run_in_threadpool`
automatically (`starlette/routing.py`, `request_response`), so the API already serves
concurrent requests off the event loop — up to anyio's 40-thread default, verified here. Going
`async` would make the core a coroutine and force `asyncio.run` through the whole CLI, for
concurrency the threadpool already provides. See Phase 7.

- ☑ **Client reuse** (`core.get_client`, `lru_cache`). Delivered in Phase 1; measured at
  **~1.1 s per avoided construction**, roughly three round trips.
- ☑ **Bounded concurrent fan-out** (`app/fanout.py`, `--also-scope`). Measured **6.0x over 6
  scopes** — near-linear, as expected for I/O-bound work.
- ☑ **Streaming responses** — `core.stream_resources` and `GET /v1/resources/stream` (NDJSON).
- ☑ **Opt-in response cache** (`--cache-ttl`, `?cache_ttl=`), off by default.
- ☑ **Benchmark tooling** (`scripts/benchmark.py`) producing a paste-ready table.
- ◐ **The baseline itself.** Recorded on the dev estate; the number that matters must come from
  a real organisation. See below.

### Fan-out is not the iteration the PRD forbids

That rule is about *discovery*: never walk service APIs resource-by-resource to find what
exists. Here each scope is still answered by one scope-wide CAI search; only the scopes run in
parallel.

It exists because of a permission fact: **CAI checks
`cloudasset.assets.searchAllResources` on the scope itself, not on its children.** Holding
viewer on 50 projects but not the organisation means `organizations/X` returns 403, and
searching the 50 scopes is the only route. Sequentially that is precisely the latency the PRD
rejects; concurrently it is one round trip plus change.

Concurrency is bounded (default 8, below anyio's 40 so a fan-out inside a request cannot starve
the threadpool it runs on). Unbounded fan-out over 2000 projects trades a latency problem for a
quota problem, which is worse: a 429 fails the whole search, where slowness merely annoys.

A failing scope is **recorded, not fatal** — one project lacking permission must not lose the
other 49 — and never silent: failures print and the CLI exits **7**, distinct from success, so
a script cannot mistake a partial answer for a complete one.

### The cache is off by default, deliberately

CAI is a near-real-time index, so caching costs little accuracy in principle. But this is an
auditing tool, and silently answering from a stale cache is the wrong default when someone is
checking whether a fix landed. The benefit — absorbing repeated identical polls — is a
deployment decision, so it is opt-in. `show_all`, filters and sort are all part of the cache
key: serving one filter's results for another would be silently wrong.

### Dev-estate baseline (515 resources, one CAI page)

| Measurement | Median | Result |
|:---|---:|:---|
| Cold (client construction included) | 1630 ms | 33 shown |
| Warm, all types, noise reduced | 709 ms | 33 shown, 90 hidden |
| Warm, all types, `--show-all` | 790 ms | 123 shown |
| Single asset type | 315 ms | 1 shown |
| All types, `--limit 10` | 1244 ms | 10 shown |
| Streaming: time to first resource | 986 ms | first row only |
| Sequential across 2 scopes | 2528 ms | 44 resources |
| Concurrent across 2 scopes | 837 ms | 44 resources, **3.0x** |
| Cached repeat (`--cache-ttl 60`) | 0 ms | cache hit |

### What this baseline cannot tell us

515 resources is one 500-row page. Pagination, fan-out breadth and quota pressure are all
untested, and two numbers above are actively misleading at this size:

- **`--limit 10` is no faster than unlimited** (indeed slower, within noise), because the whole
  estate arrives in one page and stopping early saves no network. Past one page this should
  invert sharply.
- **Streaming time-to-first-resource is close to the full search**, for the same reason. The
  gap is the entire value of streaming and should widen with result size.

**Run `scripts/benchmark.py` against a real organisation and replace this table.** Judge the
remaining work against those numbers, not these. If concurrent fan-out plateaus well below
`--max-concurrency`, the bottleneck is elsewhere and Phase 7 should be reconsidered rather than
assumed.

---

## Phase 5 — API maturity ☑

**Goal:** the PRD's OpenAPI and aggregated-response objectives — the point at which the API is
fit for other teams to consume.

- ☑ `gcp-explorer openapi --out openapi.yml` exports the spec; `openapi.yml` is committed.
- ☑ `GET /v1/summary` and `gcp-explorer summary` — counts by type, project and location in one
  payload.
- ☑ `?include=iam` / `--include-iam` — resources joined to their attached IAM bindings.
- ☑ `--output json|csv|table` on the CLI.
- ☑ Sorting via `--sort` / `?sort=`.
- ☑ API versioning prefix (`/v1/...`) — pulled forward and delivered in Phase 1.

### Sorting is server-side, like every other filter

CAI's `order_by` accepts a fixed field list (`name`, `assetType`, `project`, `displayName`,
`location`, `createTime`, …), verified from the request contract. Sorting locally would order
*one page of an arbitrary selection* — output that looks right and is wrong. An unknown field
is rejected rather than dropped, for the same reason: a silently ignored sort produces
plausible output in the wrong order.

Note the asymmetry: `assetType` is **sortable** but not **queryable**, which is why Phase 3's
noise reduction still cannot be pushed upstream.

### The IAM join, and the caveat that ships with it

`search_all_iam_policies` is the second CAI surface. Its `resource` field is the same full
resource name a resource search reports as `name`, confirmed against live data, so that is the
join key — and the reason Phase 1's "capture `full_name`" was a prerequisite.

It is **one extra call for the whole scope**, not one per resource. That is what makes it an
aggregation rather than the per-project fan-out the PRD rejects.

Two honesty guards, both tested:

- **The response always states its semantics.** `iam_note` says the bindings are
  *attached only*, and that inherited grants from a parent project, folder or organization are
  not included. Confirmed against live data: a project-level policy comes back as its own row
  and does not attach to the bucket beneath it. Presenting attached-only bindings as "who can
  access this" would be wrong in the unsafe direction.
- **`None` and `[]` mean different things.** `None` is "IAM was not requested"; `[]` is "it was,
  and nothing is attached". Conflating them would let a reader mistake *not asked* for
  *nothing granted*.

### Machine-readable output keeps stdout clean

`-o json|csv` writes the payload to stdout and every warning — truncation, suppression, the IAM
caveat — to stderr, so a pipeline reading stdout gets valid JSON or CSV and nothing else. CSV
renders nested labels as compact JSON in the cell rather than dropping them, since silently
omitting data would misrepresent the resource.

`summary` takes no `limit`: a summary of a truncated result set would be a lie.

### Deferred, deliberately

- **Effective IAM** (`batch_get_effective_iam_policies`, max 20 names per call) resolves
  inheritance properly but needs chunked, concurrency-bounded batching — which belongs after
  Phase 4's bounded fan-out, not before it. The attached/effective distinction is already
  surfaced in `iam_note`, so adding the mode later is additive rather than a correction.
- **Non-CAI enrichment** stays gated, per the rule below.

---

## Phase 6 — Distribution ☑

**Goal:** the PRD's stated maturation path from local execution to container distribution.

- ☑ `Dockerfile` serving the API. Multi-stage, non-root (uid 10001), 323 MB.
- ☑ `.dockerignore` keeping credentials and local state out of the build context entirely.
- ☑ `/healthz` (liveness) and `/readyz` (readiness), with a `HEALTHCHECK` in the image.
- ☑ GitHub Actions CI: lint, format check, tests, a staleness check on `openapi.yml`, an image
  build, and a check that no credential is baked into the image.
- ☑ Service-account usage documented — the auth decision was made precisely so this needs no
  code change.

### No credentials in the image, enforced rather than asserted

ADC is supplied at runtime: by the metadata server on Cloud Run or GKE, or by mounting a local
ADC file read-only. Copying a key into an image puts a long-lived credential into every layer
and every registry that ever holds it, where deleting the file in a later layer does not remove
it.

Three overlapping guards, because this is cheap to check and catastrophic to get wrong:
`.dockerignore` excludes credential-shaped filenames from the build context, the Dockerfile
carries a comment saying why, and CI fails the build if such a file appears in the image.

### Liveness and readiness are deliberately different

- `/healthz` does **no I/O**. A liveness probe that called GCP would restart containers whenever
  Google had a bad minute, turning an upstream blip into a self-inflicted outage.
- `/readyz` checks that ADC resolves and the CAI client constructs — the two failures that make
  an instance useless rather than merely slow — and returns 503 with the reason otherwise. It
  does **not** call CAI: a probe on every pod every few seconds would burn quota to report
  something a real request surfaces anyway.

Verified in the running container: without credentials, `/healthz` stays 200 while `/readyz`
returns 503 naming `DefaultCredentialsError`. So a misconfigured instance stops taking traffic
without being restart-looped.

### Verified, not assumed

The image was built and run against real GCP, not just written: `docker run ... list-resources
projects/idp-prototype-edo bucket` returns the two buckets, and the containerised API answers
`/v1/resources` with live data.

One trap found by building it: a virtualenv's entry points carry an **absolute** shebang, so
building in `/build` and copying `.venv` to `/app` leaves every script pointing at a python
that no longer exists. The builder now works at the final path.

---

## Phase 7 (conditional) — Async, only if measurement demands it ☐

**Status: planned, not scheduled.** Everything below is ready to execute; none of it should be
executed yet. The PRD's stated rationale for FastAPI includes non-blocking calls to GCP, but
plain `def` endpoints already run in a threadpool, so the API is non-blocking today. This phase
addresses a *ceiling*, not a defect.

Phase 4 was skipped for the same reason this is unscheduled: the dev estate is 515 resources in
one CAI page, and neither phase's bottleneck can be reproduced there.

### Trigger conditions — measured, never anticipated

Execute only when one of these is observed:

1. **Thread exhaustion.** Sustained concurrent requests saturate anyio's limiter (40 by
   default) *and* raising it stops helping — thread memory or context-switching becomes the
   constraint. Measure by raising the limiter first; it is one line, and if that fixes it, this
   phase is not the answer.
2. **Wasteful fan-out breadth.** A single request fans out across enough scopes that one thread
   per in-flight CAI call is genuinely expensive. Threads are fine for tens of concurrent
   calls; thousands is where `asyncio` wins.
3. **Streaming pressure.** Long-lived streaming responses over an org-wide result set hold a
   thread each for their whole lifetime. This is the most likely real trigger, and it arrives
   with Phase 4's streaming work rather than independently.

None of these can be observed below roughly a few hundred projects.

### Ordered plan, if triggered

1. **Re-measure first.** Record the ceiling as a number, the way Phase 4's baseline is
   recorded. Without it there is no way to tell afterwards whether this helped.
2. **Raise the anyio limiter and re-measure.** The cheap fix must be ruled out before the
   expensive one. If `to_thread` capacity resolves it, stop here.
3. **Swap the client.** `AssetServiceAsyncClient` exists in the installed
   `google-cloud-asset`; `search_all_resources` is a true coroutine returning a
   `SearchAllResourcesAsyncPager` supporting `__aiter__`, so `for item in pager` becomes
   `async for`.
4. **Make the core async and bridge the CLI.** `search_resources` becomes `async def`, and each
   Typer command wraps it in `asyncio.run(...)`. **Do not** maintain parallel sync and async
   cores — that breaks the rule that the CLI and API share all behaviour, which is the property
   every phase so far has relied on.
5. **Move client construction inside the loop.** An async gRPC client binds to the running event
   loop, so it must be built in a FastAPI `lifespan` handler rather than at import. The
   `lru_cache` on `get_client` becomes wrong, not merely suboptimal.
6. **Re-verify the differential checks.** `scripts/` calls the core directly; an async core
   changes that call site. They caught a stale signature once already.

### Known blockers and costs

- **Transport.** The only async transport shipped is `AssetServiceGrpcAsyncIOTransport` — there
  is no async REST, though a sync `AssetServiceRestTransport` exists. `grpcio` is already in the
  dependency tree and `grpc.aio` imports cleanly, but anywhere permitting HTTPS and not gRPC
  (some corporate egress proxies) this phase is simply unavailable. Confirm the deployment
  target's egress before starting.
- **Blast radius.** Async is viral: core, both surfaces, the scripts module, and every test that
  calls `search_resources`. Roughly 260 tests touch that path today.
- **The trap it must avoid.** Marking handlers `async def` while still calling the *sync*
  client would stall the event loop and make throughput worse than today. If this phase is done
  halfway, it is worse than not done.

### What would make this unnecessary

Phase 4's caching and bounded fan-out address the same pressure more cheaply. If an org-scale
measurement ever becomes possible, do Phase 4 first and re-measure: it may close the gap
entirely.

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
