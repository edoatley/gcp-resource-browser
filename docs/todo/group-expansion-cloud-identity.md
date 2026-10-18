# Verify `group:` expansion against real Cloud Identity groups

**Summary:** `--expand-members` reads `group:` members through the Cloud Identity Groups API.
It is built and unit-tested but has never run against a real group. Verify it on an estate
that has Cloud Identity groups granted risky roles, fix what that turns up, and tick the last
Phase 8 item.

**Branch context:** `member-expansion`, which shipped "who is behind a member". The
`projectOwner`/`Editor`/`Viewer` path was verified live there, and `principalSet` and similar
members are explained in place. Only the group path is unproven. Branch fresh from `main`.

## Why deferred

The dev estate cannot exercise it. All 7 projects were checked, and none has a `group:`
grant. With no organization there is no Cloud Identity directory, and groups owned by a
personal Google account cannot be read through the API. Shipping the code was still
worthwhile, because its failure modes are loud: errors are named in `expansions` and in the
coverage note. Calling it verified would not have been honest.

## Context

**Relevant files:**
- `app/core.py`: `group_members()`. It makes two calls: `groups:lookup?groupKey.id=<email>`,
  then `<group>/memberships:searchTransitiveMemberships`, paginated. The remedy text is
  `_GROUPS_REMEDY`.
- `app/expansion.py`: the bounded fan-out, which calls `group_members` once per distinct
  group.
- `tests/test_expansion.py`: the fakes. Their response shape is assumed from the API
  reference and has not been captured live. **Check this first.**
- `scripts/compare-member-expansion.sh` and `gcloud-expand-members.sh`: the gcloud side uses
  `gcloud identity groups memberships search-transitive-memberships --group-email=…
  --format='value(preferredMemberKey.id)'`.

**Assumptions to confirm live:**
1. `groups:lookup` takes `groupKey.id` and returns `{"name": "groups/<id>"}`.
2. Each transitive membership carries `preferredMemberKey: [{"id": "<email>"}]`.
3. A **nested group** appears in the transitive results as its members, not as itself. If the
   group itself appears too, decide whether to drop it or label it.
4. The API needs the `cloud-platform` scope on the ADC session, or `cloud-identity.groups.readonly`.
   `get_rest_session()` requests only `cloud-platform`. If that fails, the remedy has to say so.
5. A missing directory role gives a 403 that `_rest_error` reports as `ScopeAccessDenied`
   with the remedy, not as `SERVICE_DISABLED`.

**Key constraints:** Expansion is opt-in, one level deep, and attached-only. It never changes
rows, and with the flag off the output must be byte-identical. All of this is in `CLAUDE.md`
and tested.

## What to do

1. You need an estate with Cloud Identity (Free is enough) or Workspace, a group with a nested
   group inside, and both granted a risky role on a project. Give the caller Groups Reader in
   the directory, and enable `cloudidentity.googleapis.com` on the quota project
   (`scripts/setup-gcp.sh` now does this).
2. Run `gcpe grants projects/P --expand-members -o json | jq .expansions` and compare it with
   the Admin console.
3. Run `scripts/compare-member-expansion.sh --scope projects/P`.
4. Capture one real `searchTransitiveMemberships` response, then make the fake in
   `tests/test_expansion.py` match it.
5. Check the failure paths: remove the directory role (expect a named error and the remedy),
   then disable the API (expect it to name the quota project).
6. Tick the item in `docs/DELIVERY_PLAN.md` (Phase 8), recording what was run, and delete
   this file.

## Acceptance criteria

- [ ] `compare-member-expansion.sh` reports MATCH on a scope with at least one nested group
- [ ] The fakes match a captured live response
- [ ] The directory-role-missing and API-disabled cases each produce the right, named error
- [ ] The Phase 8 item is ☑ with the verification recorded

## Related specs / docs

- [`docs/CONCEPTS.md`](../CONCEPTS.md#who-is-behind-a-member): user-facing semantics
- [`docs/GCP_SETUP.md`](../GCP_SETUP.md): the API and directory role
- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md): Phase 8
