# Role risk: how roles are classified, and the evidence

`gcpe roles` / `GET /v1/roles`, `gcpe grants` / `GET /v1/grants` and `gcpe billing-grants` /
`GET /v1/billing-grants` classify IAM roles by risk. This page lists every rule and the published source behind it, quoted, so you can check
the classification rather than take it on trust.

The rules live in [`app/role_risk_rules.json`](../app/role_risk_rules.json). Tests check that:

- every rule cites a source;
- every source has a URL and a quote, and is cited by a rule or linked from this page;
- this page lists every rule;
- every rule's permission is granted by at least one predefined role. A rule matches an exact
  permission string, so a typo or a retired permission would otherwise match nothing,
  silently. This check runs against a committed snapshot
  (`tests/fixtures/iam_permissions.txt.gz`). `scripts/check-rule-permissions.sh` runs it live,
  and also asks IAM which permissions exist at all (`queryTestablePermissions`, the API behind
  `gcloud iam list-testable-permissions`). That separates a typo (`UNKNOWN`) from a real
  permission no standard role grants (`NO-ROLE`), and flags `DEPRECATED` ones.

  When it was introduced, it caught `serviceusage.apiKeys.create` as `NO-ROLE`. The permission
  still exists (BETA), but no predefined role grants it, and the API Keys API checks
  `apikeys.keys.create` for key creation ([gcp-api-keys-iam](#gcp-api-keys-iam)). The rule now
  names that permission.

## How risk is decided

**Risk comes from permissions, not role names.** A role's risk is the highest risk of any
permission it contains. All three commands use the same rules:

- `roles` checks them against the IAM API's catalogue of predefined roles (about 2,400 roles),
  plus the custom roles defined on `--scope` when one is given.
- `grants` compiles the same permissions into a Cloud Asset Inventory query
  (`policy.role.permissions:`), so CAI does the matching across the whole scope.
- `billing-grants` resolves each role's permissions from the catalogue, because the Cloud
  Billing API reports role names and nothing else.

So the three cannot disagree, and roles nobody listed are still covered. That includes
**custom roles** and obscure predefined ones. For example,
`roles/firebase.managementServiceAgent` contains `resourcemanager.projects.setIamPolicy`.

Two limits worth knowing, both of which the responses state:

- `grants` matches custom roles anywhere in the scope, since the match is on permissions.
  `roles` can only *list* them for one organization or project at a time, because listing
  takes a call per scope. An organization scope does not reach the custom roles defined
  inside its projects.
- `billing-grants` cannot classify a role that is not in the predefined catalogue — a custom
  role granted on a billing account. Those are reported in `unclassified_roles` for manual
  review rather than assumed safe.

| Level | Meaning |
|:---|:---|
| **high** | The holder can grant themselves or anyone more access, or act as a more privileged identity. This is privilege escalation. |
| **medium** | The holder can widen access to a single resource, or create a credential limited to one service. |

On the live catalogue, the high rules match 35 predefined roles, plus 60 Google service-agent
roles that are hidden by default. Among them:

| Role | Why it is high |
|:---|:---|
| `roles/owner` | `resourcemanager.projects.setIamPolicy`, `iam.serviceAccounts.actAs`, `iam.serviceAccountKeys.create`, `iam.roles.update`, `deploymentmanager.deployments.create` |
| `roles/editor` | `iam.serviceAccounts.actAs`, `iam.serviceAccountKeys.create`, `deploymentmanager.deployments.create` |
| `roles/billing.admin` | `billing.accounts.setIamPolicy` |
| `roles/resourcemanager.organizationAdmin` | `resourcemanager.organizations.setIamPolicy`, `resourcemanager.folders.setIamPolicy`, `resourcemanager.projects.setIamPolicy` |
| `roles/iam.securityAdmin` | `*.setIamPolicy` at organization, folder, project and billing account level |
| `roles/iam.serviceAccountUser` | `iam.serviceAccounts.actAs` |
| `roles/iam.serviceAccountTokenCreator` | `iam.serviceAccounts.getAccessToken`, `signBlob`, `signJwt`, `implicitDelegation` |

Run `gcpe roles --risk high` (or `--risk medium`) for the current full list. It is computed
live from Google's catalogue.

## The rules

| Permission | Risk | What a holder can do | Sources |
|:---|:---|:---|:---|
| `resourcemanager.organizations.setIamPolicy` | high | Can grant any role on the organization, and so everything in it | [rhino-part2](#rhino-part2) |
| `resourcemanager.folders.setIamPolicy` | high | Can grant any role on a folder and every project beneath it | [rhino-part2](#rhino-part2) |
| `resourcemanager.projects.setIamPolicy` | high | Can grant any role on the project, including to themselves | [rhino-part2](#rhino-part2), [gcp-roles-overview](#gcp-roles-overview) |
| `billing.accounts.setIamPolicy` | high | Can decide who administers a billing account | [gcp-billing-access](#gcp-billing-access) |
| `iam.serviceAccounts.getAccessToken` | high | Can impersonate a service account and use all of its access | [rhino-part1](#rhino-part1), [gcp-sa-impersonation](#gcp-sa-impersonation), [cis-gcp-1.6](#cis-gcp-16) |
| `iam.serviceAccounts.signBlob` | high | Can sign as a service account to obtain its access tokens | [rhino-part1](#rhino-part1) |
| `iam.serviceAccounts.signJwt` | high | Can sign JWTs as a service account to obtain its access tokens | [rhino-part1](#rhino-part1) |
| `iam.serviceAccounts.implicitDelegation` | high | Can chain through one service account to impersonate another | [rhino-part1](#rhino-part1) |
| `iam.serviceAccountKeys.create` | high | Can mint a long-lived key for a service account | [rhino-part1](#rhino-part1), [gcp-sa-best-practices](#gcp-sa-best-practices) |
| `iam.serviceAccounts.actAs` | high | Can run workloads as a service account, gaining its access | [rhino-part1](#rhino-part1), [cis-gcp-1.6](#cis-gcp-16), [gcp-sa-best-practices](#gcp-sa-best-practices) |
| `iam.serviceAccounts.setIamPolicy` | high | Can grant themselves the right to impersonate a service account | [rhino-part2](#rhino-part2) |
| `iam.roles.update` | high | Can add permissions to a custom role they already hold | [rhino-part1](#rhino-part1) |
| `orgpolicy.policy.set` | high | Can disable organization policy guardrails | [rhino-part2](#rhino-part2) |
| `deploymentmanager.deployments.create` | high | Deploys as the Google APIs service agent, which has Editor by default. Deployment Manager is unsupported but runs for existing customers until June 30, 2027 | [rhino-part1](#rhino-part1), [gcp-dm-access](#gcp-dm-access), [gcp-dm-deprecation](#gcp-dm-deprecation) |
| `deploymentmanager.deployments.update` | high | Redeploys as the Google APIs service agent, which has Editor by default, without actAs. Runs until June 30, 2027 | [gcp-dm-access](#gcp-dm-access), [gcp-dm-deprecation](#gcp-dm-deprecation) |
| `cloudbuild.builds.create` | high | Runs builds as the legacy Cloud Build service account without actAs, where that account is still enabled (the default before May-June 2024). Newer projects build as an account that needs actAs, already high | [gcp-cloudbuild-sa](#gcp-cloudbuild-sa), [gcp-cloudbuild-sa-change](#gcp-cloudbuild-sa-change) |
| `storage.hmacKeys.create` | medium | Can mint Cloud Storage HMAC keys for a service account | [rhino-part2](#rhino-part2) |
| `apikeys.keys.create` | medium | Can create API keys, which are unrestricted by default. The API Keys API checks this permission; the older serviceusage.apiKeys.create that Rhino names still exists but no predefined role grants it | [gcp-api-keys-iam](#gcp-api-keys-iam), [gcp-api-keys](#gcp-api-keys), [rhino-part2](#rhino-part2) |
| `storage.buckets.setIamPolicy` | medium | Can grant access to a bucket, including publicly | [rhino-part2](#rhino-part2) |
| `secretmanager.secrets.setIamPolicy` | medium | Can grant access to a secret | [rhino-part2](#rhino-part2) |
| `cloudfunctions.functions.setIamPolicy` | medium | Can grant invoke access to a function, including publicly | [rhino-part2](#rhino-part2) |
| `run.services.setIamPolicy` | medium | Can grant invoke access to a Cloud Run service, including publicly | [rhino-part2](#rhino-part2) |
| `pubsub.topics.setIamPolicy` | medium | Can grant publish or subscribe access to a topic | [rhino-part2](#rhino-part2) |
| `cloudkms.cryptoKeys.setIamPolicy` | medium | Can grant use of an encryption key | [rhino-part2](#rhino-part2) |
| `iam.serviceAccounts.getOpenIdToken` | medium | Can mint an OIDC ID token as a service account: proof of its identity to one audience, such as Cloud Run, IAP or an external trust. It cannot call Google APIs | [gcp-sa-permissions](#gcp-sa-permissions) |
| `bigquery.datasets.update` | medium | Can set a dataset's access policy, as well as its other settings | [gcp-bigquery-iam](#gcp-bigquery-iam) |
| `bigquery.datasets.setIamPolicy` | medium | Can set a dataset's access policy | [gcp-bigquery-iam](#gcp-bigquery-iam) |
| `artifactregistry.repositories.setIamPolicy` | medium | Can grant access to a repository, including publicly, or let others push images | [gcp-artifact-registry-access](#gcp-artifact-registry-access) |
| `compute.instances.setIamPolicy` | medium | Can grant access to a VM | [gcp-compute-resource-iam](#gcp-compute-resource-iam) |

### Deliberately not listed

[Rhino Security Labs Part 1](#rhino-part1) also describes escalation through
`cloudfunctions.functions.create`, `cloudfunctions.functions.update`,
`compute.instances.create`, `run.services.create` and `cloudscheduler.jobs.create`. Each one
escalates only by running a workload as a more privileged service account, which requires
`iam.serviceAccounts.actAs`. That permission is already high. Listing these too would flag
every deployer role without finding anything new.

The same goes for `compute.instances.setMetadata` and
`compute.projects.setCommonInstanceMetadata`, which can add SSH keys or a startup script to a
VM. Google requires `iam.serviceAccounts.actAs` to change metadata on a VM that runs as a
service account ([gcp-compute-metadata](#gcp-compute-metadata)).

`composer.environments.create` and `composer.environments.update` are not listed either. No
source showed an escalation through them that doesn't already need `actAs`, or write access
to the environment's DAG bucket.

`cloudbuild.builds.create` *is* listed, because it is the exception. Projects that still use
the legacy Cloud Build service account let a Cloud Build Editor run builds as it with no
`actAs` ([gcp-cloudbuild-sa](#gcp-cloudbuild-sa)). That was the default until May–June 2024
([gcp-cloudbuild-sa-change](#gcp-cloudbuild-sa-change)). On newer projects the rule flags
something `actAs` already covers; its reason says so.

## Sources

### gcp-roles-overview

**[Google Cloud IAM: Roles and permissions -- Basic roles](https://cloud.google.com/iam/docs/roles-overview#basic)**

> Caution: Basic roles include thousands of permissions across all Google Cloud services. In production environments, do not grant basic roles unless there is no alternative. ... Owner: All Editor permissions, plus permissions for actions like ... Managing roles and permissions for a project and all resources within the project.

### gcp-sa-impersonation

**[Google Cloud IAM: Service account impersonation](https://cloud.google.com/iam/docs/service-account-impersonation)**

> To impersonate a service account, you need the iam.serviceAccounts.getAccessToken permission ... [direct access or keys] can create privilege-escalation and non-repudiation risks.

### gcp-sa-best-practices

**[Google Cloud IAM: Best practices for using service accounts securely](https://cloud.google.com/iam/docs/best-practices-service-accounts)**

> Avoid letting users authenticate as service accounts that are more privileged than the users themselves ... Granting a user permission to impersonate a more privileged service account can be a way to deliberately let users temporarily elevate their privileges. ... Before you assign any role that includes the iam.serviceAccountKeys.create permission to a user, ask yourself which resources ... the user could gain access to by impersonating the service account.

### gcp-sa-types

**[Google Cloud IAM: Service account types (service agents, default service accounts)](https://cloud.google.com/iam/docs/service-account-types)**

> Google Cloud creates and manages service accounts for many Google Cloud services. These service accounts are known as service agents. ... the default service account might automatically be granted the Editor role on your project. We strongly recommend that you disable the automatic role grant.

### gcp-billing-access

**[Google Cloud Billing: Overview of Cloud Billing access control -- Billing Account Administrator](https://cloud.google.com/billing/docs/how-to/billing-access)**

> Billing Account Administrator (roles/billing.admin): Manage billing accounts (but not create them). ... [can] add and edit payment methods ... link and unlink projects, and manage other user roles on the billing account.

### cis-gcp-1.5

**[CIS Google Cloud Platform Foundation Benchmark v4.0.0, 1.5 Ensure That Service Account Has No Admin Privileges (1.6 in v5.0.0)](https://www.cisecurity.org/benchmark/google_cloud_computing_platform)**

> It's recommended not to use admin access for ServiceAccount. [Remediation identifies user-managed service accounts with roles containing *Admin or *admin, or Editor, or Owner.]

### cis-gcp-1.6

**[CIS Google Cloud Platform Foundation Benchmark, 1.6 Ensure That IAM Users Are Not Assigned the Service Account User or Service Account Token Creator Roles at Project Level](https://www.cisecurity.org/benchmark/google_cloud_computing_platform)**

> Assign the Service Account User (iam.serviceAccountUser) and Service Account Token Creator (iam.serviceAccountTokenCreator) roles to a user for a specific service account rather than assigning the role to a user at project level.

### gcp-api-keys-iam

**[Google Cloud API Keys: Access control with IAM](https://docs.cloud.google.com/api-keys/docs/access-control)**

> API Keys uses Identity and Access Management to manage access to the keys. ... Method Required Permission(s) projects.locations.keys.create apikeys.keys.create

### gcp-api-keys

**[Google Cloud authentication: Manage API keys](https://docs.cloud.google.com/docs/authentication/api-keys)**

> Unrestricted API keys are insecure. To reduce security risks, you can restrict API keys

### gcp-cloudbuild-sa

**[Cloud Build: Default Cloud Build service account](https://docs.cloud.google.com/build/docs/cloud-build-service-account)**

> Legacy Cloud Build service account (if enabled): Any user with the Cloud Build Editor role can create and directly run a trigger. ... User-specified service account or the Compute Engine default service account: Any user with the Cloud Build Editor role who has the iam.serviceAccounts.actAs permission can create and directly run a trigger.

### gcp-cloudbuild-sa-change

**[Cloud Build: Cloud Build default service account change](https://docs.cloud.google.com/build/docs/cloud-build-service-account-updates)**

> The default behavior for how Cloud Build uses service accounts in new projects was changed over several weeks in May and June 2024. ... Before this change, Cloud Build used a Cloud Build-specific service account as the default, now referred to as the Cloud Build legacy service account. After this change, Cloud Build now uses the Compute Engine default service account as the default service account.

### gcp-sa-permissions

**[Google Cloud IAM: Roles for service account authentication](https://docs.cloud.google.com/iam/docs/service-account-permissions)**

> The Service Account Token Creator role lets you create the following types of short-lived credentials: OAuth 2.0 access tokens, which you can use to authenticate with Google APIs ... iam.serviceAccounts.getOpenIdToken: lets you create OpenID Connect (OIDC) ID tokens

### gcp-dm-access

**[Cloud Deployment Manager: Access control with IAM](https://docs.cloud.google.com/deployment-manager/docs/access-control)**

> To create other Google Cloud resources, Deployment Manager uses the credentials of the Google APIs Service Agent to authenticate to other APIs. ... The Google APIs Service Agent is automatically granted the Editor role at the project level

### gcp-dm-deprecation

**[Cloud Deployment Manager: Deployment Manager deprecation](https://docs.cloud.google.com/deployment-manager/docs/deprecations)**

> As of April 1, 2026, Deployment Manager is not supported on Google Cloud. Deployment Manager will remain running for all existing customers through Google Cloud console, Google Cloud CLI, and Google Cloud Deployment Manager V2 API until June 30, 2027.

### gcp-bigquery-iam

**[BigQuery: Control access to resources with IAM](https://docs.cloud.google.com/bigquery/docs/control-access-to-resources-iam)**

> To set a dataset's access policy: bigquery.datasets.update ... To set a dataset's access policy (console only): bigquery.datasets.setIamPolicy

### gcp-artifact-registry-access

**[Artifact Registry: Access control with IAM](https://docs.cloud.google.com/artifact-registry/docs/access-control)**

> To configure a repository for public read-only access, grant the Artifact Registry Reader role to the principal allUsers.

### gcp-compute-resource-iam

**[Compute Engine: Managing access to Compute Engine resources](https://docs.cloud.google.com/compute/docs/access/granting-access-to-resources)**

> To grant or revoke access to resources: ... compute.RESOURCE_TYPE.setIamPolicy on the resource ... Replace RESOURCE_TYPE with the resource that you want to manage access to. For example instances, instanceTemplates, or images.

### gcp-compute-metadata

**[Compute Engine: Set and remove custom metadata](https://docs.cloud.google.com/compute/docs/metadata/setting-custom-metadata)**

> The following permissions are required to set or remove custom metadata on VMs: If your VMs use service accounts: iam.serviceAccounts.actAs on the service accounts or project

### rhino-part1

**[Rhino Security Labs (Spencer Gietzen): Privilege Escalation in Google Cloud Platform -- Part 1 (IAM)](https://rhinosecuritylabs.com/gcp/privilege-escalation-google-cloud-platform-part-1/)**

> Methods covered: deploymentmanager.deployments.create, iam.roles.update, iam.serviceAccounts.getAccessToken, iam.serviceAccountKeys.create, iam.serviceAccounts.implicitDelegation, iam.serviceAccounts.signBlob, iam.serviceAccounts.signJwt, iam.serviceAccounts.actAs, cloudfunctions.functions.create, cloudfunctions.functions.update, compute.instances.create, run.services.create, cloudscheduler.jobs.create.

### rhino-part2

**[Rhino Security Labs (Spencer Gietzen): Privilege Escalation in Google Cloud Platform -- Part 2 (Non-IAM)](https://rhinosecuritylabs.com/cloud-security/privilege-escalation-google-cloud-platform-part-2/)**

> orgpolicy.policy.set ... storage.hmacKeys.create ... serviceusage.apiKeys.create ... resourcemanager.organizations.setIamPolicy: Attach IAM roles to your user at the Organization level ... resourcemanager.folders.setIamPolicy ... resourcemanager.projects.setIamPolicy ... iam.serviceAccounts.setIamPolicy ... cloudfunctions.functions.setIamPolicy ... As of the time of writing this blog post, the following permissions are all the different IAM policies that you can update: [list includes storage.buckets.setIamPolicy, secretmanager.secrets.setIamPolicy, run.services.setIamPolicy, pubsub.topics.setIamPolicy, cloudkms.cryptoKeys.setIamPolicy].

## Service agents

Google creates and manages service agents for its own services
([gcp-sa-types](#gcp-sa-types)). Their grants are Google's, not yours to review, but they hold
many risky permissions: 61 of the 104 high-risk predefined roles are service-agent roles (October 2026). They are
**hidden by default and always counted**. `--show-all` / `?show_all=true` includes them.

A grant is treated as a service agent's when the role name ends in `serviceAgent`, or the
member is one of these:

- `service-<PROJECT_NUMBER>@…`
- `<PROJECT_NUMBER>@cloudservices.gserviceaccount.com`
- `…@gcp-sa-<service>.iam.gserviceaccount.com`

The **default** service accounts (`<NUMBER>-compute@developer.gserviceaccount.com`,
`<PROJECT>@appspot.gserviceaccount.com`) are **not** service agents. You manage them, and
Google strongly recommends against their automatic Editor grant ([gcp-sa-types](#gcp-sa-types)).
CIS 1.5 says service accounts should not hold admin roles ([cis-gcp-1.5](#cis-gcp-15)). These
accounts are always shown.

This filtering has to happen after results come back, because CAI's IAM policy search does not
support negation, so the query cannot exclude them.

## What a grant search cannot see

Every response states this in `coverage_note`:

- **Grants above the scope.** Searching a project cannot see grants made on its folder or
  organization, even though they apply to the project. Search the organization to cover every
  level. A grant made on a parent applies to everything beneath it, so an organization-wide
  search shows every grant that applies to each resource in it.
- **Billing account IAM.** CAI does not index billing accounts: it rejects `billingAccounts/…`
  as a scope. A `billing.admin` grant made **on the organization** is found. A grant made
  **on the billing account itself** is not yet. This is planned, using the Cloud Billing API.
- **Group membership.** A grant to `group:…` is reported as the group. Who is in the group needs
  the Cloud Identity API, which is not yet supported.
- **Public exposure.** `allUsers` holding `roles/run.invoker` is public access, not privilege,
  and is not flagged unless the role contains a listed permission. Use
  `--member-type allUsers --member-type allAuthenticatedUsers --role-risk medium` to look for
  public grants of risky roles.

## Checking it yourself

```bash
gcpe roles --risk high                                    # the live classification
gcpe roles --risk high -o json | jq '.sources'            # sources cited by the roles returned
./scripts/compare-grants.sh --scope organizations/123     # tool vs gcloud, one search per permission
./scripts/check-rule-permissions.sh                       # every rule names a real permission
./scripts/check-rule-permissions.sh gcp-explorer.json     # ... including your site rules
```

## Changing the rules

Add, override or downgrade a rule for your site in [configuration](CONFIGURATION.md), under
`role_risk_rules`. A site rule for a permission replaces the default rule for it. A downgrade
must say `"downgrade": true`, and every coverage note then names it. A site rule may cite only
the sources listed above.
