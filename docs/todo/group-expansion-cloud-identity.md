# Group expansion via the Cloud Identity Groups API

**Summary:** Expand `group:` members in risky-grant results into the people they actually
contain, so an over-permissioning review can see headcount rather than a group name.

**Branch context:** `feat/role-risk` — Phase 8 (high-risk roles and grants). The other two
Phase 8 items (billing-account IAM, custom-role listing) landed in that PR; this is the third.

## Why deferred

The PR was already large, and this item is the only one of the three that cannot be verified
on the current dev estate: it has no organization and therefore no GCP-visible groups. It
would have shipped unproven. It also needs a new API, a new auth surface and a bounding
strategy, so it is a feature in its own right rather than a finishing touch.

## Context

**Relevant files:**
- `app/core.py` — `search_grants` (CAI path) and `search_billing_grants`. Both build `Grant`
  rows; both would gain expansion. `get_rest_session()` / `_paginate` / `_rest_error` are the
  REST plumbing to reuse — Cloud Identity is plain REST like IAM and Cloud Billing.
- `app/fanout.py` — the bounded-concurrency pattern to copy. `DEFAULT_MAX_CONCURRENCY = 8`,
  kept below anyio's 40-thread default.
- `app/models.py` — `Grant`, `GrantGroup`, `GrantList`, `BillingGrantList`.
- `app/role_risk.py` — `member_type()` identifies `group:` members.
- `docs/DELIVERY_PLAN.md` — Phase 8 section, where this is the one remaining ☐.

**Current state:**
Every IAM-bearing response carries a note ending *"Group membership is not expanded: a group
grant applies to every member."* A `Grant` row for `group:platform-admins@example.com` holding
Organization Admin tells you the group holds it, not whether that is 3 people or 300 — and the
headcount is usually the actual finding. `--by member` rolls up by the literal member string,
so groups and users sit side by side with no indication of their weight.

**Key constraints:**
- **New auth surface.** Needs `cloudidentity.googleapis.com` enabled and
  `roles/cloudidentity.groups.readonly` — a Cloud Identity / Workspace directory role, not a
  GCP project role. Different admin, often a different team. Everything else in this tool
  needs only GCP IAM.
- **Must be bounded.** Two calls per *distinct group* in the results (`groups.lookup`, then
  `groups.memberships.searchTransitiveMemberships`). An org-wide medium-risk search can surface
  hundreds of groups. Follow `fanout.py`: bounded concurrency, loud partial failure.
- **Opt-in, not default.** This turns an infrastructure audit into a list of named humans.
  `--expand-groups` / `?expand_groups=true`, off by default.
- **Do not collapse a binding into N rows.** One `Grant` is one binding; the CSV columns and
  the `group_by` rollups depend on that. Add member detail alongside, do not replace.
- Use `searchTransitiveMemberships`, which flattens nested groups server-side — it avoids
  writing a cycle-safe walk.

## What to do

1. Add `expand_groups: bool = False` to `GrantFilters` and `BillingGrantFilters` in
   `app/params.py`, plus `Help.EXPAND_GROUPS`.
2. New module `app/groups.py`: `expand(emails, max_concurrency=8) -> dict[str, GroupMembers]`,
   using `core.get_rest_session()` and `core._paginate`. Lookup then transitive search per
   group, concurrent and bounded. Return partial results with a failure list — never raise away
   a whole search because one group was unreadable.
3. Add `expanded_members: list[str] | None` and `member_count: int | None` to `Grant`.
   `None` means not requested; `[]` means requested and empty. Do not collapse the two —
   same rule as `iam_bindings`.
4. Call it from `search_grants` and `search_billing_grants` after rows are built, on the
   distinct `group:` members only.
5. Amend the coverage note: when expansion ran, say so and name any groups that failed; when
   it did not, keep today's wording.
6. Surfaces: `--expand-groups` on `grants` and `billing-grants`, `?expand_groups=` on both
   endpoints, a member-count column in the tables, and a decision on whether the CSV carries
   named members (it should be opt-in within opt-in, or omitted).
7. `scripts/gcloud-expand-groups.sh` + `scripts/compare-group-expansion.sh` using
   `gcloud identity groups memberships search-transitive-memberships`. Expect to ship these
   unproven until an estate with real nested groups runs them.
8. Update `docs/DELIVERY_PLAN.md` (Phase 8), `docs/CONCEPTS.md`, `docs/API.md`, `docs/CLI.md`,
   `docs/GCP_SETUP.md` (new API + role), `scripts/README.md` and `CLAUDE.md`. Regenerate
   `openapi.yml` and `docs/CLI_REFERENCE.md`.

## Acceptance criteria

- [ ] `gcpe grants ORG --expand-groups --by member` shows a member count per group
- [ ] Without the flag, behaviour and output are byte-identical to today
- [ ] A group that cannot be read is named in the response, and the search still returns
- [ ] Expansion issues at most 2 calls per distinct group, concurrency capped at 8
- [ ] `expanded_members is None` (not requested) is distinguishable from `[]` (none found)
- [ ] The coverage note stops claiming membership is unexpanded when it was expanded
- [ ] `uv run pytest`, `ruff check`, and the `openapi.yml` / `CLI_REFERENCE.md` staleness
      checks all pass

## Related specs / docs

- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md) — Phase 8, the remaining ☐ and its rationale
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — "Adding a Google REST API" checklist
- [`CLAUDE.md`](../../CLAUDE.md) — IAM invariants; noise/suppression rules that bounding follows
