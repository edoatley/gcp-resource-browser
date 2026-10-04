# Role risk: how roles are classified, and the evidence

`gcpe roles` / `GET /v1/roles` and `gcpe grants` / `GET /v1/grants` classify IAM roles by
risk. This page lists every rule and the published source behind it, quoted, so you can check
the classification rather than take it on trust.

The rules live in [`app/role_risk_rules.json`](../app/role_risk_rules.json). A test checks that
every rule cites a source, that every source has a URL and a quote, and that this page lists
every rule.

## How risk is decided

**Risk comes from permissions, not role names.** A role's risk is the highest risk of any
permission it contains. Both commands use the same rules:

- `roles` checks them against the IAM API's catalogue of predefined roles (about 2,400 roles).
- `grants` compiles the same permissions into a Cloud Asset Inventory query
  (`policy.role.permissions:`), so CAI does the matching across the whole scope.

So the two cannot disagree, and roles nobody listed are still covered. That includes **custom
roles** and obscure predefined ones. For example, `roles/firebase.managementServiceAgent`
contains `resourcemanager.projects.setIamPolicy`.

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
| `deploymentmanager.deployments.create` | high | Deploys as the Google APIs service account, which has Editor by default | [rhino-part1](#rhino-part1) |
| `storage.hmacKeys.create` | medium | Can mint Cloud Storage HMAC keys for a service account | [rhino-part2](#rhino-part2) |
| `serviceusage.apiKeys.create` | medium | Can create unrestricted API keys | [rhino-part2](#rhino-part2) |
| `storage.buckets.setIamPolicy` | medium | Can grant access to a bucket, including publicly | [rhino-part2](#rhino-part2) |
| `secretmanager.secrets.setIamPolicy` | medium | Can grant access to a secret | [rhino-part2](#rhino-part2) |
| `cloudfunctions.functions.setIamPolicy` | medium | Can grant invoke access to a function, including publicly | [rhino-part2](#rhino-part2) |
| `run.services.setIamPolicy` | medium | Can grant invoke access to a Cloud Run service, including publicly | [rhino-part2](#rhino-part2) |
| `pubsub.topics.setIamPolicy` | medium | Can grant publish or subscribe access to a topic | [rhino-part2](#rhino-part2) |
| `cloudkms.cryptoKeys.setIamPolicy` | medium | Can grant use of an encryption key | [rhino-part2](#rhino-part2) |

### Deliberately not listed

[Rhino Security Labs Part 1](#rhino-part1) also describes escalation through
`cloudfunctions.functions.create`, `cloudfunctions.functions.update`,
`compute.instances.create`, `run.services.create` and `cloudscheduler.jobs.create`. Each one
escalates only by running a workload as a more privileged service account, which requires
`iam.serviceAccounts.actAs`. That permission is already high. Listing these too would flag
every deployer role without finding anything new.

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

### rhino-part1

**[Rhino Security Labs (Spencer Gietzen): Privilege Escalation in Google Cloud Platform -- Part 1 (IAM)](https://rhinosecuritylabs.com/gcp/privilege-escalation-google-cloud-platform-part-1/)**

> Methods covered: deploymentmanager.deployments.create, iam.roles.update, iam.serviceAccounts.getAccessToken, iam.serviceAccountKeys.create, iam.serviceAccounts.implicitDelegation, iam.serviceAccounts.signBlob, iam.serviceAccounts.signJwt, iam.serviceAccounts.actAs, cloudfunctions.functions.create, cloudfunctions.functions.update, compute.instances.create, run.services.create, cloudscheduler.jobs.create.

### rhino-part2

**[Rhino Security Labs (Spencer Gietzen): Privilege Escalation in Google Cloud Platform -- Part 2 (Non-IAM)](https://rhinosecuritylabs.com/cloud-security/privilege-escalation-google-cloud-platform-part-2/)**

> orgpolicy.policy.set ... storage.hmacKeys.create ... serviceusage.apiKeys.create ... resourcemanager.organizations.setIamPolicy: Attach IAM roles to your user at the Organization level ... resourcemanager.folders.setIamPolicy ... resourcemanager.projects.setIamPolicy ... iam.serviceAccounts.setIamPolicy ... cloudfunctions.functions.setIamPolicy ... As of the time of writing this blog post, the following permissions are all the different IAM policies that you can update: [list includes storage.buckets.setIamPolicy, secretmanager.secrets.setIamPolicy, run.services.setIamPolicy, pubsub.topics.setIamPolicy, cloudkms.cryptoKeys.setIamPolicy].

## Service agents

Google creates and manages service agents for its own services
([gcp-sa-types](#gcp-sa-types)). Their grants are Google's, not yours to review, but they hold
many risky permissions: 60 of the high-risk predefined roles are service-agent roles. They are
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
```

## Changing the rules

Add, override or downgrade a rule for your site in [configuration](CONFIGURATION.md), under
`role_risk_rules`. A site rule for a permission replaces the default rule for it.
