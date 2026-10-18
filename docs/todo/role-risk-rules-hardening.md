# Phase 10, part 2 — candidate risk rules

**Summary:** Add the escalation paths an external review of `app/role_risk_rules.json` says it
misses, each one checked live and given a quoted primary source. Every rule decides what
`roles`, `grants` and `billing-grants` report.

**Branch context:** Part 1 (the guardrails, branch `phase-10-role-risk-hardening`) shipped
three things:
- the permission-existence check: the snapshot test plus `scripts/check-rule-permissions.sh`;
- source-coverage checks;
- confirmed-only site downgrades.

It also replaced a dead rule (`serviceusage.apiKeys.create` → `apikeys.keys.create`). This is
what remains. Branch fresh from `main`.

## Why deferred

An external review arrived mid-Phase 9. It is a separate concern (rule content and validation,
not the docs browser) and needs live verification and sourcing, so it is its own phase rather
than an addition to #5.

## Context

**Relevant files:**
- `app/role_risk_rules.json` — 22 rules (14 high, 8 medium) and 9 quoted sources.
- `app/role_risk.py` — `load_rules()` merges site rules over defaults by permission;
  `classify`, `rules_at_least`, `permission_batches` (32 alternations ÷ member-type count).
- `app/config.py` — validates site `role_risk_rules`: keys and risk level only.
- `tests/test_role_risk.py` — cites-a-real-source, one-rule-per-permission, ROLE_RISK.md completeness.
- `docs/ROLE_RISK.md` — the evidence page. It must list every rule and source URL (tested).
- `scripts/compare-roles.sh`, `gcloud-search-grants.sh`, `gcloud-billing-grants.sh` — read
  the same JSON with `jq`. They validate the classifier, **not** the rule content.

**Current state:** 22 rules and 11 sources. Every rule's permission is checked against
`tests/fixtures/iam_permissions.txt.gz`. Refresh it with
`uv run python -m scripts._snapshot_permissions` if a candidate is newer than the snapshot.
`scripts/check-rule-permissions.sh` is the live check.

**Key constraints:**
- **No rule without a quoted source** (`docs/ARCHITECTURE.md`, "Adding a risk rule"). The
  review is not a source; each new rule needs a primary citation whose quote supports it.
- Risk is defined by permissions, never role names. Don't add a role-name list.
- Verify against real GCP before ticking. Unit tests use a faked role catalogue.
- Unknown is not safe: prefer failing or reporting over silently dropping.

**Review claims corrected on inspection (do not apply as written):**
- `gcp-sa-types` and `cis-gcp-1.5` are not dead. `ROLE_RISK.md` cites both to justify
  service-agent suppression and default-SA handling. A test of `sources == cited by rules`
  would fail legitimately. Instead, test that every source is cited by a rule **or** by
  `ROLE_RISK.md`.
- The `gcp-sa-types` quote is about *default service accounts* getting Editor, not the Google
  APIs service agent. It does not support the Deployment Manager rule's reason, which needs
  its own source.
- The Rhino Part 1 quote does not list `cloudbuild.builds.create`. The review's attribution to
  Part 2 is unverified.
- The review's claim that "all 22 permissions are valid" was **wrong**.
  `serviceusage.apiKeys.create` was in no predefined role, and part 1 fixed it. Treat the
  review's other factual claims with the same suspicion.

## What to do

Steps 1–3 of the original plan (existence check, source coverage, downgrade protection) are
done. Keep going with the numbering below.

4. **Candidate rules.** Verify each live and source it before adding, and drop any that fail:

   | Permission | Proposed risk | Claim to verify |
   |---|---|---|
   | `cloudbuild.builds.create` | high | Runs as the Cloud Build SA without `actAs` (Google changed the default SA in 2024, so check current behaviour) |
   | `compute.instances.setMetadata` | high | Inject SSH keys or a startup script into a VM already running as a privileged SA |
   | `compute.projects.setCommonInstanceMetadata` | high | Same, for every VM in the project |
   | `iam.serviceAccounts.getOpenIdToken` | high | Mints an OIDC token as the SA |
   | `deploymentmanager.deployments.update` | high | Same path as `.create` |
   | `composer.environments.create` / `update` | high? | Unclear: DAG writes are `storage.objects.create` on the DAG bucket. May be a non-rule |
   | `artifactregistry.repositories.setIamPolicy` | medium | Resource-policy widening (supply chain) |
   | `compute.instances.setIamPolicy` | medium | Resource-policy widening |
   | `bigquery.datasets.setIamPolicy` and/or `bigquery.datasets.update` | medium | Which one actually changes dataset access |

5. **Deployment Manager is deprecated.** Keep the rule while the permission exists in live
   roles; step 1's check flags it once retired. Add a source for its reason.
6. **Batching.** At about 32 rules, a search with no member types sits at the one-batch
   boundary. Re-run `scripts/compare-grants.sh` and update the latency note if the batch
   count rises.
7. Update `ROLE_RISK.md` for each rule, and the delivery plan. Each new permission must pass
   `scripts/check-rule-permissions.sh`, and each quote must be checked against the page itself
   rather than a summary of it.

## Acceptance criteria

- [x] A rule with a non-existent permission fails a unit test, and the live script confirms
      every rule (part 1)
- [x] A source cited nowhere, or a site rule citing an unknown source, fails (part 1)
- [x] A site downgrade can't happen silently (part 1)
- [ ] Every new rule passes `check-rule-permissions.sh`
- [ ] Each new rule has a primary source whose quote supports its reason, and is listed on
      `ROLE_RISK.md`
- [ ] `compare-roles.sh` and `compare-grants.sh` match on the dev estate after the changes
- [ ] `uv run pytest`, `ruff`, and both staleness checks pass

## Related specs / docs

- [`docs/ROLE_RISK.md`](../ROLE_RISK.md) — the evidence page
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — "Role risk is derived from permissions", "Adding a risk rule"
- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md) — Phase 8, which introduced the rules
