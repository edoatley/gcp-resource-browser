# Grants made above the search scope

**Summary:** A project-scoped `gcpe grants` cannot see Owner granted on the folder or
organization above the project, though it applies there. The coverage note admits this. This
handoff covers what can still be done about it. Effective IAM turned out not to be the route.

**Branch context:** written on `effective-iam`, which delivered `--include-iam --effective`
(inherited bindings on resources, up to the scope). Branch fresh from `main` once that merges.

## Why the original plan does not work

The first version of this handoff (`docs/todo/effective-iam.md`, now replaced) planned to call
`batchGetEffectiveIamPolicies` on the project itself and report the ancestor policies as
inherited grants. **The API cannot do that.** Its own reference, in the installed client
(`BatchGetEffectiveIamPoliciesResponse.EffectiveIamPolicy.policies`), says the policies returned
are "the policy set on the full_resource_name and those set on its parents and ancestors up to
the BatchGetEffectiveIamPoliciesRequest.scope". `gcloud asset get-effective-iam-policy --help`
agrees: "The search is limited to the IAM policies within this scope."

So with a project scope, the project's own policy is the top of what comes back. Folder and
organization policies never appear. The plan's dev-estate acceptance check ("reports no
inherited grants, because there is no organization") would have passed even under a real
organization, which makes it a silent wrong answer rather than a test. This could not be shown
live (the estate has no organization). It rests on the API's documentation, quoted above.

## Options

1. **Name the ancestors that were not read (recommended first).** Resource Manager
   `projects.getAncestry` (v1) needs only `resourcemanager.projects.get` on the project. It
   returns the folder and organization chain. The coverage note can then say "this project sits
   under folders/1 and organizations/2; grants made there apply here and were not read", in
   place of the generic sentence. One REST call per search, through `core.get_rest_session()`
   and `_rest_error`. **Verifiable here**: on this estate it should return just the project, and
   the note should say there is nothing above.
2. **`--ancestry-scope organizations/X` on `grants`.** Call `batchGetEffectiveIamPolicies`
   with the wider scope and the project as the one name, then classify the ancestor bindings
   through `_catalogue_permissions`, as `search_billing_grants` does. Unclassified (custom org)
   roles go to an `unclassified_roles` list. This needs Cloud Asset Viewer **on that wider
   scope**, the same permission an organization-wide `grants` search needs. So it narrows the
   answer to the project's own chain. It does not help a caller who lacks org access, which was
   the original motivation. **Not verifiable on this estate.**
3. **Resource Manager `getIamPolicy` on each ancestor.** This needs
   `resourcemanager.folders.getIamPolicy` / `organizations.getIamPolicy` on each ancestor, which
   a project-only caller usually lacks too. It has no advantage over option 2.

Nothing gets around the fact that a caller who cannot read a folder's policy cannot read its
grants. That is IAM working as designed. The useful thing the tool can add is to **say which
ancestors exist** (option 1).

## Context

- `app/core.py`: `_coverage_note` (the sentence to make specific), `search_grants`,
  `effective_iam_policies` and `effective_iam_note` (from the effective-IAM work),
  `_catalogue_permissions` (role name → permissions, for option 2).
- `app/effective.py`: batching, and the split when a batch 404s.
- Live facts from 2026-10-18: 21 names in one call is `InvalidArgument`. One unknown name 404s
  the whole batch. Project names must use the **ID**, because `//…/projects/<number>` is
  NotFound. Workload identity pools always 404, because CAI names them by project number.

## Acceptance criteria (option 1)

- [ ] A project-scoped `grants` response names the project's ancestors, or says there are none
- [ ] A failure to read the ancestry is stated in the note, never treated as "no ancestors"
- [ ] On this estate the note says nothing is above each project, and agrees with
      `gcloud projects get-ancestors <id>`

## Related

- [`docs/DELIVERY_PLAN.md`](../DELIVERY_PLAN.md): Phase 5, "Deferred, deliberately"
- [`docs/ROLE_RISK.md`](../ROLE_RISK.md): "What a grant search cannot see"
- [`docs/CONCEPTS.md`](../CONCEPTS.md#inherited-bindings-effective): what `--effective` does
