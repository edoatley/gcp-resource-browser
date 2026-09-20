# Manual walkthrough

Exercises every Phase 2 capability against real GCP. **Every step returns data** — an empty
result proves nothing, since it cannot distinguish "works correctly" from "silently broken".
Where a negative result is the point, it is paired with a positive control on the same filter.

Targets are chosen from what actually exists in the estate:

| Project | Resources | Distinct types | Notable |
|:---|---:|---:|:---|
| `sudoku-eo-2026` | 123 | 29 | richest; Cloud Run, DNS, namespaced labels |
| `sudoku-app-eo` | 132 | 23 | DNS zones, API keys |
| `gcp-sandbox-2026-18798` | 130 | 16 | all the compute resources |
| `idp-prototype-edo` | 28 | 9 | buckets, terraform labels |

Prerequisites: `./scripts/setup-gcp.sh`, plus ADC pointed at the quota project
(`gcloud auth application-default set-quota-project gcp-resource-browser-eo`).

---

## 1. Baseline — does it reach CAI

```bash
uv run gcp-explorer list-resources projects/idp-prototype-edo bucket
```
Expect 2 buckets, with the Project column showing `idp-prototype-edo` (the ID, not the number).

## 2. Free-text search narrows a result set

```bash
uv run gcp-explorer search projects/idp-prototype-edo --type bucket          # 2 buckets
uv run gcp-explorer search projects/idp-prototype-edo tfstate --type bucket  # 1 of them
```
The pair is the point: the term must *reduce* 2 to 1, not return everything or nothing.

## 3. Several types in one upstream call

```bash
uv run gcp-explorer search projects/sudoku-eo-2026 \
    --type cloudrun --type serviceaccount --type topic
```
Expect a mixed table. Check the Type column stays legible — a long name used to squeeze every
other column to an ellipsis.

## 4. Pass-through: raw CAI types and RE2

```bash
# Raw type, in a project that actually has them
uv run gcp-explorer search projects/sudoku-app-eo --type dns.googleapis.com/ManagedZone

# RE2 across a whole service — 90+ compute resources live here
uv run gcp-explorer search projects/gcp-sandbox-2026-18798 --type 'compute.googleapis.com/.*' -n 10

# Narrower RE2, proving the pattern discriminates rather than matching everything
uv run gcp-explorer search projects/gcp-sandbox-2026-18798 --type 'compute.googleapis.com/.*wall'

# An invalid pattern must fail loudly, not return empty
uv run gcp-explorer search projects/sudoku-eo-2026 --type 'nonsense.googleapis.com/Nope'; echo "exit=$?"
```
Last one: exit 2 with a message naming the unsupported type.

## 5. Label filtering, including namespaced keys

```bash
# Plain key
uv run gcp-explorer search projects/idp-prototype-edo --type bucket \
    --label goog-terraform-provisioned=true --show-query

# Namespaced key — Google's own system labels, which CAI rejects unless quoted
uv run gcp-explorer search projects/sudoku-eo-2026 --type cloudrun \
    --label cloud.googleapis.com/location=us-central1 --show-query

# Negative control: same filter, wrong value. Must return nothing AND show its query.
uv run gcp-explorer search projects/sudoku-eo-2026 --type cloudrun \
    --label cloud.googleapis.com/location=europe-west2
```
The third is the only deliberate empty. It matters *because* the second returned data with the
same filter shape — together they prove the filter discriminates.

## 6. Location and project filters

```bash
# Locations OR together
uv run gcp-explorer search projects/sudoku-eo-2026 --type cloudrun \
    --location us-central1 --location europe-west1 --show-query

# --project takes an ID; CAI matches on the number, so watch the compiled query
uv run gcp-explorer search projects/idp-prototype-edo --type bucket \
    --project idp-prototype-edo --show-query
```

## 7. Truncation is reported, never silent

```bash
uv run gcp-explorer search projects/gcp-sandbox-2026-18798 \
    --type 'compute.googleapis.com/.*' --limit 5
```
Expect 5 rows plus a warning that more exist.

## 8. Query everything, with noise reduction (Phase 3)

```bash
# No --type: every asset type, low-signal resources hidden
uv run gcp-explorer search projects/gcp-sandbox-2026-18798 --limit 8

# The same search with nothing hidden — note the volume difference
uv run gcp-explorer search projects/gcp-sandbox-2026-18798 --show-all --limit 8

# A project where almost everything is noise: must say so, not claim emptiness
uv run gcp-explorer search projects/ace-gcp-training
```

Expect a `N hidden: ...` line naming the top reasons on the first and third, and none on the
second. Measured across the estate: 515 resources become 84.

The third is the important one — `ace-gcp-training` has 28 resources of which 27 are enabled
APIs and system log sinks. It must report what it hid, never a bare "No resources found".

```bash
# --limit counts VISIBLE results, not fetched rows
uv run gcp-explorer search projects/gcp-sandbox-2026-18798 --limit 3
```
Expect exactly 3 real resources, despite ~119 noise rows being skipped to find them.

## 9. Noise reduction only ever removes

```bash
./scripts/check-noise.sh projects/gcp-sandbox-2026-18798
./scripts/check-noise.sh projects/sudoku-eo-2026
```
Asserts the suppressed result is a strict subset of the full one. Verified: 11 of 130 (91%
hidden) and 33 of 123 (73% hidden), both strict subsets.

## 10. The differential check — the one that matters

```bash
./scripts/compare-resources.sh --scope projects/sudoku-eo-2026 --type cloudrun
./scripts/compare-resources.sh --scope projects/gcp-sandbox-2026-18798 --type subnet --type firewall
./scripts/compare-resources.sh --scope projects/sudoku-eo-2026 --type cloudrun \
    --label cloud.googleapis.com/location=us-central1
./scripts/compare-resources.sh --scope projects/idp-prototype-edo --type bucket --term tfstate
./scripts/compare-resources.sh --scope projects/sudoku-app-eo --type dns.googleapis.com/ManagedZone

# No --type: the whole unfiltered fetch, compared against gcloud
./scripts/compare-resources.sh --scope projects/idp-prototype-edo
```
Verified results: 2, 46, 2, 1, 1 and 28 resources respectively — all `MATCH`.

Note this compares with suppression **off**. gcloud has no notion of noise reduction, so
including it would compare two different questions; step 9 covers that property instead. This is the only check that validates the
*query itself* — unit tests fake the client, so they can only prove we sent what we intended.

## 11. Sorting, output formats and aggregation (Phase 5)

```bash
# Server-side sort. CAI orders it, not us -- sorting locally would order one
# page of an arbitrary selection, which looks right and is wrong.
uv run gcp-explorer search projects/sudoku-eo-2026 --type serviceaccount \
    --sort 'displayName DESC'

# An unknown sort field must be REJECTED, not quietly ignored
uv run gcp-explorer search projects/sudoku-eo-2026 --type bucket --sort bogus; echo "exit=$?"

# Machine-readable output. Payload on stdout, warnings on stderr.
uv run gcp-explorer search projects/sudoku-eo-2026 -o json 2>/dev/null | jq 'length'
uv run gcp-explorer search projects/idp-prototype-edo --type bucket -o csv | head -2

# Prove stdout stays parseable even when there is plenty to warn about
uv run gcp-explorer search projects/gcp-sandbox-2026-18798 -o json 2>/dev/null | jq 'length'
```
The `--sort bogus` case must exit 2 and list the sortable fields.

### Aggregation

```bash
uv run gcp-explorer summary projects/sudoku-eo-2026
uv run gcp-explorer summary projects/sudoku-eo-2026 -o json | jq '.by_asset_type'
```
Check the **By project** block names each project once. It previously split one project
across two rows — an ID row and a project-number row.

### IAM enrichment

```bash
uv run gcp-explorer search projects/idp-prototype-edo --type bucket --include-iam -o json \
    2>/dev/null | jq '.[] | {display_name, iam_bindings: [.iam_bindings[].role]}'
```
Then read the caveat printed to stderr: these are **attached** bindings only. A grant inherited
from the parent project confers real access and is not shown. Confirmed against live data — the
project-level policy comes back as its own row, not attached to the bucket beneath it.

```bash
# Without --include-iam the field is absent, not empty: "not requested" and
# "nothing granted" must never look alike.
uv run gcp-explorer search projects/idp-prototype-edo --type bucket -o json \
    2>/dev/null | jq '.[0] | has("iam_bindings")'      # false
```

## 12. Concurrency and caching (Phase 4)

```bash
# Several scopes at once. This is the route when you hold viewer on individual
# projects but not on the organization -- CAI checks permission on the scope
# itself, so organizations/<id> would simply 403.
uv run gcp-explorer search projects/idp-prototype-edo \
    --also-scope projects/sudoku-eo-2026 \
    --also-scope projects/gcp-sandbox-2026-18798 \
    --type bucket --type serviceaccount

# A scope you cannot read must be REPORTED, not silently dropped, and must not
# cost you the scopes you can read. Expect results plus a FAILED line, exit 7.
uv run gcp-explorer search projects/idp-prototype-edo \
    --also-scope projects/does-not-exist-xyz --type bucket; echo "exit=$?"

# Streaming: rows arrive as CAI returns them
curl -sN 'http://127.0.0.1:8000/v1/resources/stream?scope=projects/sudoku-eo-2026&limit=5' \
    | jq -c '{display_name, asset_type}'

# Caching is OFF unless asked for. Repeat an identical search to see the effect.
uv run gcp-explorer search projects/sudoku-eo-2026 --cache-ttl 60 -o json >/dev/null 2>&1
time uv run gcp-explorer search projects/sudoku-eo-2026 --cache-ttl 60 -o json >/dev/null 2>&1
```

### The baseline — run this on the large organization

```bash
uv run python -m scripts.benchmark --scope organizations/<id> --repeats 3

# Or, without org-level access, across many projects
uv run python -m scripts.benchmark \
    --scope projects/a --scope projects/b --scope projects/c --repeats 3
```

It prints a markdown table ready to paste into `docs/DELIVERY_PLAN.md`. **This is the
measurement Phase 4 is blocked on** — the dev estate is ~515 resources in a single CAI page, so
pagination, fan-out breadth and quota pressure are all untested. On the dev estate it reports a
3.0x speedup over 2 scopes and a ~1.6 s cold cost for client construction.

Watch three things there in particular:

1. **`All types, --limit 10` vs unlimited.** On a single-page estate these are the same; past
   one page the limited one should be dramatically faster. If it is not, the early stop is not
   saving network.
2. **Streaming time-to-first-resource** versus the full warm search. The gap is the value of
   streaming, and should widen sharply with result size.
3. **Concurrent versus sequential** across many scopes. Near-linear speedup is expected up to
   `--max-concurrency` (default 8); if it plateaus early, the bottleneck is elsewhere and the
   remaining Phase 4 work should be re-judged.

## 13. The container (Phase 6)

```bash
docker build -t gcp-explorer .

# The CLI, with ADC mounted read-only
docker run --rm \
    -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
    -e GOOGLE_CLOUD_QUOTA_PROJECT=gcp-resource-browser-eo \
    gcp-explorer list-resources projects/idp-prototype-edo bucket

# The API
docker run --rm -d --name gx -p 8000:8000 \
    -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
    -e GOOGLE_CLOUD_QUOTA_PROJECT=gcp-resource-browser-eo \
    gcp-explorer
curl -s http://127.0.0.1:8000/v1/resources?scope=projects/idp-prototype-edo\&type=bucket | jq .count
docker stop gx
```

### The probes must behave differently

```bash
# No credentials at all: liveness stays UP, readiness reports 503.
docker run --rm -d --name gx-bare -p 8001:8000 gcp-explorer
sleep 3
curl -s http://127.0.0.1:8001/healthz                    # {"status":"ok"}
curl -s -w ' [%{http_code}]' http://127.0.0.1:8001/readyz # 503, DefaultCredentialsError
docker stop gx-bare
```
This is the important distinction: a liveness probe that called GCP would restart containers
whenever Google had a bad minute. A misconfigured instance should stop taking traffic without
being restart-looped.

### No credentials in the image

```bash
docker run --rm --entrypoint sh gcp-explorer -c \
    'ls /app/*credentials*.json /app/.env 2>/dev/null || echo "clean"'
```
CI runs this too. A key copied into an image is in every layer and every registry that holds
it, and deleting it in a later layer does not remove it.

## 14. The API, end to end

```bash
uv run gcp-explorer serve
```
Then in another shell:
```bash
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/sudoku-eo-2026&type=cloudrun' | jq '.count, .query'
curl -s -o /dev/null -w '%{http_code}\n' \
    'http://127.0.0.1:8000/v1/resources?scope=projects/sudoku-eo-2026&type=nonsense'   # 400
curl 'http://127.0.0.1:8000/v1/types' | jq 'keys | length'                             # 20

# No type param: everything, with suppression reported in the body
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/ace-gcp-training' \
    | jq '{count, suppressed, suppressed_summary}'
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/ace-gcp-training&show_all=true' \
    | jq '{count, suppressed}'
open http://127.0.0.1:8000/docs
```
