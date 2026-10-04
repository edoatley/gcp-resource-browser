# GCP setup

The tool does no authentication of its own. It reads
[Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials)
(ADC), so the same code runs unchanged on a laptop, in CI, in a container, or under a service
account on Cloud Run or GKE.

## Two prerequisites, often in different projects

| Requirement | Where it applies |
|:---|:---|
| Cloud Asset API (`cloudasset.googleapis.com`) enabled | The **quota project** that ADC bills calls to |
| `roles/cloudasset.viewer` | Each **scope** you search (project, folder or organisation) |

Google returns HTTP 403 for both failures, but the fixes are different and usually apply to
different projects. The tool tells them apart:

| Symptom | Meaning | CLI exit | HTTP |
|:---|:---|:---:|:---:|
| `... is not enabled on <project>, the project this request bills to` | Enable the API on the named **quota** project | `6` | `503` |
| Permission denied on the scope | Grant `cloudasset.viewer` on the **scope** | `3` | `403` |

The "not enabled" message names the project to fix and the `gcloud services enable` command
to run. Trust that over the scope you searched.

## Provisioning with `setup-gcp.sh`

`scripts/setup-gcp.sh` provisions:

- a dedicated project (`gcp-resource-browser-eo`) to act as the API and quota home
- a read-only service account (`resource-browser@…`)
- `cloudasset.viewer` grants on each project listed in `TARGET_PROJECTS` in the script

It is idempotent, so re-run it after adding a project to `TARGET_PROJECTS`. For your own
estate, edit `TARGET_PROJECTS` in the script, and override `PROJECT_ID` and `SA_NAME` from the
environment if the defaults don't suit you.

```bash
./scripts/setup-gcp.sh

# then, interactively (needs a browser):
gcloud auth application-default login \
    --impersonate-service-account=resource-browser@gcp-resource-browser-eo.iam.gserviceaccount.com
gcloud auth application-default set-quota-project gcp-resource-browser-eo
```

The script uses impersonation rather than a downloaded JSON key, so there is no long-lived
credential on disk to leak or rotate.

**Billing is not required.** The Cloud Asset API can be enabled on an unbilled project and its
search calls are free. The script deliberately skips linking a billing account, because doing
so would use up a billing-account project slot for no benefit.

## Without an organisation

If you have no organisation, there is no `organizations/` scope to search and no parent to
grant viewer on once, so each project needs its own grant. Searching 2,000 projects in one
call needs an organisation. Without one, search several projects concurrently with
`--also-scope` (see [CLI guide](CLI.md#searching-several-scopes)).

The same applies inside an organisation: CAI checks permission on the scope **itself**, not on
its children. Holding viewer on 50 projects does not let you search the organisation.

## Running under a service account

On Cloud Run or GKE, ADC comes from the metadata server. Grant `roles/cloudasset.viewer` on the
scopes to the runtime service account and nothing else is needed. See
[Deployment](DEPLOYMENT.md).
