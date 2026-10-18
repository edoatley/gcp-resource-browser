# Effective (inherited) IAM for grants and `--include-iam`

**Summary:** Report grants that apply to a scope through inheritance from its folders and
organization, not only grants attached within it. Today a project-scoped `gcpe grants` cannot
see Owner granted on the folder above, and its coverage note has to say so.

**Branch context:** `main` after #14 (all earlier PRs merged; working tree clean). This was found in a review of the risk/role
feature. The deferral reason in `docs/DELIVERY_PLAN.md` ("belongs after Phase 4's bounded
fan-out") no longer applies: bounded fan-out exists (`app/fanout.py`, `app/expansion.py`).
Branch fresh from `main`.

## Why deferred

It was deferred in Phase 5 until bounded concurrency existed, and was never picked up once it
did. It is the largest remaining gap in what a risk answer can see: every grants response
carries a coverage note admitting it.

## Context

**Relevant files:**
- `app/core.py`:
  - `search_grants` and `_coverage_note`, whose "Grants made on this project's folders or
    organization are not visible…" sentence is what this would change;
  - `fetch_iam_bindings`, in `app/aggregate.py`, for `--include-iam`;
  - `_catalogue_permissions`, which resolves role names to permissions. The billing path uses
    it, because inherited policies come back as role names with no CAI
    `matched_permissions`, exactly as billing policies do.
- `app/fanout.py`: the bounded-concurrency pattern to copy.
- `app/models.py`: `Grant`, `IamBinding`. Add an `inherited_from` field (the ancestor's full
  resource name) rather than a separate list, so `group_by` and CSV keep working.

**Current state:** Every IAM answer is attached-only and says so: `iam_note` for
`--include-iam`, and `coverage_note` for grants. Searching the organization covers everything
for grants, but needs org-level Cloud Asset Viewer, which many callers lack. That is why
fan-out exists.

**The API, confirmed against the installed client and one live call:**
`core.get_client().batch_get_effective_iam_policies(request=asset_v1.BatchGetEffectiveIamPoliciesRequest(scope=…, names=[…]))`.
The response is `policy_results[]`, each with:
- `full_resource_name`;
- `policies[]`, ordered from the resource itself up through its ancestors. Each entry has an
  `attached_resource` and a `policy`, which has `bindings[]`.

The tests' `FakeAssetClient` (`tests/conftest.py`) needs a matching method.

**Key constraints:**
- CAI `batchGetEffectiveIamPolicies` takes **at most 20 resource names per call**, and the
  scope must contain them. Batch the names and bound concurrency at 8, like fan-out. Fail
  loudly per batch.
- Inherited bindings carry role **names** only. Resolve their risk against the catalogue as
  `search_billing_grants` does. A role that isn't in the catalogue (a custom org role) goes to
  an unclassified list; it is never assumed safe.
- Keep attached and inherited distinguishable in every output. Presenting inherited as
  attached, or the reverse, misleads in one direction or the other.
- Opt-in (`--effective` / `?effective=true`). The default answer must stay byte-identical.
- **Verification is half-possible here.** The dev estate has no organization or folders, so
  *project-level* answers have no ancestors to find. But **resources do inherit from their
  project**, and that is verifiable now. A live probe on 2026-10-18, against bucket
  `edo-dev-platform-refactor-check` in `idp-prototype-edo`, returned two policies:
  1. the bucket's own (legacy bucket roles);
  2. the project's (`roles/owner`, `roles/storage.admin`, …).

  So `--include-iam effective` can be verified end to end on this estate. Grants inherited
  from folders or an organization cannot (see `CLAUDE.md`, "The dev estate has no
  organization").

## What to do

1. Grants: for a project scope with `effective`, call `batchGetEffectiveIamPolicies` on the
   project itself (one name, one call). Classify each ancestor policy's bindings through the
   catalogue, and add the risky ones as `Grant` rows with `inherited_from` set.
2. `--include-iam effective`: batch the result's resource names in groups of 20 and attach
   inherited bindings to each resource with `inherited_from`. This is bounded: the names come
   from a result that is already capped by `limit`.
3. Rewrite the coverage note and `iam_note` when `effective` ran: say what was included.
4. Surfaces: the flag on `grants`, `search`/`list-resources` (`--include-iam`) and the API;
   a CLI table marker for inherited rows; the CSV column only when requested. Then the
   catalogue example and `Help` text, and regenerate `openapi.yml` and `CLI_REFERENCE.md`.
5. Add `scripts/compare-effective-iam.sh` using
   `gcloud asset get-effective-iam-policy --scope=… --names=…`.
6. Docs: `CONCEPTS.md` (IAM enrichment, role risk), `API.md`, `CLI.md`, `ROLE_RISK.md`
   ("What a grant search cannot see"), and the delivery plan.

## Acceptance criteria

- [ ] With the flag off, every IAM-bearing response is byte-identical to today
- [ ] On the dev estate, a bucket's effective IAM shows its project's bindings marked
      `inherited_from` the project, and agrees with `gcloud asset get-effective-iam-policy`
- [ ] With it on, inherited grants are marked `inherited_from`, are classified via the
      catalogue, and an unclassifiable inherited role is listed rather than dropped
- [ ] Calls are batched by 20 names, with concurrency at most 8, and a failed batch is named
- [ ] On the dev estate, project-scoped `grants --effective` reports no inherited grants,
      because there is no organization or folder, and says so in the coverage note
- [ ] On an estate with a folder or org grant: `compare-effective-iam.sh` reports MATCH

## Related specs / docs

- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md): Phase 5, "Deferred, deliberately"
- [`docs/CONCEPTS.md`](../CONCEPTS.md#iam-enrichment): the attached-only semantics this extends
- [`docs/ROLE_RISK.md`](../ROLE_RISK.md): "What a grant search cannot see"
