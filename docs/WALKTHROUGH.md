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

## 11. The API

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
