# Phase 10 — Harden the role risk rules

**Summary:** Act on an external review of `app/role_risk_rules.json`. Add the escalation paths it
misses, close the validation gaps that let a bad rule pass silently, and make site downgrades
loud. Every rule decides what `roles`, `grants` and `billing-grants` report.

**Branch context:** `main` after `8920b40`. Raised while Phase 9 PR 1 (#5, capability
catalogue) was in review. Branch fresh from `main`.

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

**Current state:** Rules match exact permission strings, and nothing checks that a permission
exists in Google's catalogue. A typo or a retired permission matches nothing and fails
silently. A site rule can silently downgrade a default (high → medium).

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
- The review's claim that "all 22 permissions are valid" is an assertion, not a check. Step 1
  makes it a check.

## What to do

1. **Permission-existence check.**
   - Unit test: every rule permission appears in some role's `includedPermissions` in a
     committed snapshot (`tests/fixtures/`).
   - Live script: `scripts/check-rule-permissions.sh` runs the same check against
     `gcloud iam roles list --show-deleted=false` with full view, or
     `gcloud iam list-testable-permissions`.
   - Use the live script to confirm all 22 existing rules.
2. **Source-coverage test.** Every source is cited by a rule or appears on `ROLE_RISK.md`, and
   every rule's sources exist. Do the same check for site rules in `config.py`: a site rule
   citing an unknown source id is a `ConfigError`.
3. **Site downgrade protection.** Make a downgrade loud, not a log line that the API path
   would lose. The approach isn't settled; ask the user:
   - (a) reject a downgrade unless the rule sets an explicit `"downgrade": true`; or
   - (b) allow it, but name it in every `coverage_note`.
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
7. Update `ROLE_RISK.md` per rule, `docs/CONFIGURATION.md` for steps 2 and 3, and the delivery
   plan.

## Acceptance criteria

- [ ] A rule with a non-existent permission fails a unit test, and the live script confirms
      every rule
- [ ] A source cited nowhere, or a site rule citing an unknown source, fails
- [ ] A site downgrade can't happen silently
- [ ] Each new rule has a primary source whose quote supports its reason, and is listed on
      `ROLE_RISK.md`
- [ ] `compare-roles.sh` and `compare-grants.sh` match on the dev estate after the changes
- [ ] `uv run pytest`, `ruff`, and both staleness checks pass

## Related specs / docs

- [`docs/ROLE_RISK.md`](../ROLE_RISK.md) — the evidence page
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — "Role risk is derived from permissions", "Adding a risk rule"
- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md) — Phase 8, which introduced the rules
