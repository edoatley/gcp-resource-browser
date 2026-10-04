# HTTP API

A JSON API over the same core as the [CLI](CLI.md). Filter semantics (how filters combine,
noise reduction, IAM caveats) are in [Concepts](CONCEPTS.md).

```bash
uv run gcpe serve                       # http://127.0.0.1:8000
uv run gcpe serve --host 0.0.0.0 --port 9000
```

## OpenAPI spec

| Where | What |
|:---|:---|
| **[edoatley.github.io/gcp-resource-browser](https://edoatley.github.io/gcp-resource-browser/)** | Browsable reference, no server needed. Published from `main` by `.github/workflows/pages.yml`. Read-only. |
| `http://127.0.0.1:8000/docs` | Swagger UI on a running server, with **Try it out** against your real estate |
| `http://127.0.0.1:8000/redoc` | The same spec rendered with ReDoc |
| `http://127.0.0.1:8000/openapi.json` | The live schema |
| [`openapi.yml`](../openapi.yml) | The committed spec, for code generation. CI fails if it differs from the code. |

Regenerate it after changing the API with `uv run gcpe openapi --out openapi.yml`.

## Endpoints

| Endpoint | Returns |
|:---|:---|
| `GET /v1/resources` | A page of resources matching the filters, with metadata |
| `GET /v1/resources/stream` | The same resources as newline-delimited JSON, streamed |
| `GET /v1/summary` | Counts by asset type, project and location |
| `GET /v1/types` | Friendly type names mapped to CAI asset types |
| `GET /v1/roles` | IAM roles classed as risky, with the permissions and sources behind each |
| `GET /v1/grants` | Principals holding risky roles across a scope |
| `GET /healthz` | Liveness: `{"status": "ok"}`, makes no external calls |
| `GET /readyz` | Readiness: `200` when ADC resolves, `503` otherwise; never calls CAI |

Parameters marked *repeatable* are passed more than once:
`?type=bucket&type=vm&location=europe-west2&location=global`.

### `GET /v1/resources`

| Parameter | Type | Default | Description |
|:---|:---|:---|:---|
| `scope` | string, **required** | | `projects/…`, `folders/…` or `organizations/…` |
| `type` | repeatable | all types | Friendly name, raw CAI type, or RE2 pattern |
| `q` | string | | Free-text term |
| `label` | repeatable | | `key=value` or `key`. Repeats are ANDed. |
| `location` | repeatable | | Repeats are ORed. `*` wildcards work. |
| `project` | repeatable | | Project ID or number. Repeats are ORed. |
| `raw_query` | string | | Raw CAI query syntax, ANDed with the rest |
| `limit` | int, 1–10000 | `1000` | Maximum visible resources returned |
| `show_all` | bool | `false` | Disable noise reduction |
| `sort` | repeatable | | Sortable field, optionally followed by ` DESC` (URL-encode the space) |
| `include_iam` | bool | `false` | Attach directly-attached IAM bindings |
| `cache_ttl` | float, 0–3600 | `0` | Seconds to cache this exact search. 0 disables caching. |

```bash
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/my-project&type=bucket'

curl 'http://127.0.0.1:8000/v1/resources?scope=organizations/123&type=bucket&type=vm&label=env%3Dprod&location=europe-west2&location=global'

curl 'http://127.0.0.1:8000/v1/resources?scope=projects/my-project&sort=createTime%20DESC&limit=10'
```

Response (`ResourceList`):

```json
{
  "scope": "projects/my-project",
  "asset_types": ["storage.googleapis.com/Bucket"],
  "query": "labels.env:prod",
  "count": 1,
  "truncated": false,
  "suppressed": 0,
  "suppressed_summary": "",
  "iam_note": null,
  "data": [
    {
      "full_name": "//storage.googleapis.com/buckets/my-bucket",
      "asset_type": "storage.googleapis.com/Bucket",
      "display_name": "my-bucket",
      "project": "123456789",
      "project_id": "my-project",
      "location": "europe-west2",
      "state": null,
      "labels": { "env": "prod" },
      "create_time": "2024-03-11T09:22:41Z",
      "parent_full_resource_name": "//cloudresourcemanager.googleapis.com/projects/my-project",
      "iam_bindings": null
    }
  ]
}
```

| Field | Meaning |
|:---|:---|
| `query` | The CAI query the filters compiled to. Check this when a result looks short. |
| `truncated` | `true` when `limit` cut the result short and more resources exist |
| `suppressed`, `suppressed_summary` | How many rows noise reduction hid, and why |
| `iam_note` | Present when `include_iam=true`. States that inherited bindings are excluded. |
| `data[].project` | Project **number**, as CAI reports it |
| `data[].project_id` | Project ID, recovered where possible. `null` when the parent is not a project. |
| `data[].iam_bindings` | `null` when IAM was not requested, `[]` when it was and none are attached, otherwise `[{"role", "members"}]` |

### `GET /v1/resources/stream`

<a id="streaming"></a>

Emits one resource per line (`application/x-ndjson`) as CAI returns them. Use it for result
sets large enough that parsing incrementally beats waiting for the whole payload.

```bash
curl -N 'http://127.0.0.1:8000/v1/resources/stream?scope=organizations/123&type=vm' | jq -c .
```

It accepts `scope`, `type`, `q`, `label`, `location`, `project`, `sort` and `show_all`, with
these differences from `/v1/resources`:

- `limit` defaults to **no limit**.
- `raw_query`, `include_iam` and `cache_ttl` are not supported.
- Each line is a bare resource with unset fields left out. There is no envelope, so there is
  no `query`, `truncated` or **suppression count**: those aren't known until the stream ends.
  Call `/v1/summary` for totals.
- `project_id` is only set where it can be read from the resource itself. `/v1/resources`
  makes one extra call per scope to fill in the rest, and the stream skips that call so the
  first row is not delayed.
- Request errors (bad scope, unknown type, bad sort) return a normal error response before
  streaming starts. A CAI failure after rows have been sent cannot change the status code, so
  the stream just ends early. Treat a stream that ends without a clean close as incomplete.

### `GET /v1/summary`

Takes `scope` (required), `type`, `q`, `label`, `location`, `project` and `show_all`. It has
no `limit`: it always counts every match.

```bash
curl 'http://127.0.0.1:8000/v1/summary?scope=organizations/123&label=env%3Dprod'
```

```json
{
  "scope": "organizations/123",
  "total": 84,
  "suppressed": 431,
  "by_asset_type": { "storage.googleapis.com/Bucket": 31, "compute.googleapis.com/Instance": 12 },
  "by_project": { "app-prod": 40, "app-staging": 22 },
  "by_location": { "europe-west2": 60, "global": 24 }
}
```

Each breakdown is sorted by count, highest first. `total` excludes suppressed resources.

### `GET /v1/types`

```json
{ "address": "compute.googleapis.com/Address", "bucket": "storage.googleapis.com/Bucket", "…": "…" }
```

This lists friendly names only. Any raw CAI type is accepted wherever `type` is.

### `GET /v1/roles`

Lists predefined roles containing at least one permission at or above `risk`. Risk comes from
permissions, not role names: see [Role risk](ROLE_RISK.md) for every rule and its quoted
source.

| Parameter | Type | Default | Description |
|:---|:---|:---|:---|
| `risk` | `high` \| `medium` | `high` | Lowest risk included. `medium` returns medium and high. |
| `show_all` | bool | `false` | Include Google service-agent roles, which are hidden and counted by default |

```bash
curl -s 'http://127.0.0.1:8000/v1/roles?risk=high' | jq -r '.data[] | "\(.name)  \(.permissions | map(.permission) | join(", "))"'
```

```json
{
  "min_risk": "high",
  "count": 35,
  "suppressed": 60,
  "suppressed_summary": "60 Google service-agent role(s) hidden. Use --show-all to include them.",
  "data": [
    {
      "name": "roles/billing.admin",
      "title": "Billing Account Administrator",
      "stage": "GA",
      "risk": "high",
      "permissions": [
        {
          "permission": "billing.accounts.setIamPolicy",
          "risk": "high",
          "reason": "Can decide who administers a billing account",
          "sources": ["gcp-billing-access"]
        }
      ]
    }
  ],
  "sources": {
    "gcp-billing-access": { "title": "…", "url": "https://cloud.google.com/billing/docs/how-to/billing-access", "quote": "…" }
  }
}
```

`sources` contains every source cited by the roles returned, each quoted. The role catalogue
comes from the IAM API (`iam.googleapis.com` must be enabled on the quota project) and is
cached for 24 hours.

### `GET /v1/grants`

Returns every principal holding a role at or above `role_risk` across `scope`. Matching is by
permission and done by CAI, so custom roles are covered too.

| Parameter | Type | Default | Description |
|:---|:---|:---|:---|
| `scope` | string, **required** | | Search the organization to include grants made at every level |
| `role_risk` | `high` \| `medium` | `high` | Lowest role risk included |
| `member_type` | repeatable | all | `user`, `group`, `serviceAccount`, `domain`, `allUsers`, `allAuthenticatedUsers`, `principal`, `principalSet` |
| `group_by` | `member` \| `role` | | Add `groups`: what each principal holds, or who holds each role |
| `show_all` | bool | `false` | Include grants to Google service agents |
| `limit` | int, 1–10000 | `1000` | Maximum grants returned. `truncated` reports when more exist. |

```bash
curl -s 'http://127.0.0.1:8000/v1/grants?scope=organizations/123&role_risk=high&member_type=user&member_type=group&group_by=member'
```

```json
{
  "scope": "organizations/123",
  "min_risk": "high",
  "member_types": ["user", "group"],
  "queries": ["policy.role.permissions:(resourcemanager.organizations.setIamPolicy OR …) memberTypes:(user OR group)"],
  "count": 1,
  "truncated": false,
  "suppressed": 0,
  "coverage_note": "Covers the organization and everything beneath it. Billing account IAM is not indexed by Cloud Asset Inventory and is not included. Group membership is not expanded: a group grant applies to every member.",
  "grants": [
    {
      "resource": "//cloudresourcemanager.googleapis.com/organizations/123",
      "asset_type": "cloudresourcemanager.googleapis.com/Organization",
      "role": "roles/resourcemanager.organizationAdmin",
      "member": "group:platform-admins@example.com",
      "member_type": "group",
      "risk": "high",
      "matched_permissions": ["resourcemanager.folders.setIamPolicy", "resourcemanager.organizations.setIamPolicy", "resourcemanager.projects.setIamPolicy"]
    }
  ],
  "groups": [
    { "key": "group:platform-admins@example.com", "highest_risk": "high", "grant_count": 1, "resource_count": 1, "roles": ["roles/resourcemanager.organizationAdmin"] }
  ]
}
```

| Field | Meaning |
|:---|:---|
| `queries` | The CAI queries sent. CAI limits each query to 32 alternative values (permissions × member types), so longer permission lists are searched in batches. |
| `matched_permissions` | The risky permissions in this role that CAI matched: *why* the grant is listed |
| `condition` | The IAM condition expression, when the binding is conditional |
| `suppressed` | Grants to Google service agents that were hidden |
| `coverage_note` | What the search cannot see. **Read it before treating the list as complete.** |

## Errors

Every error returns a real status code and a typed body:

```json
{
  "error": "unknown_resource_type",
  "detail": "Unsupported resource type 'widget'. Choose from: address, bucket, …, vm, or pass a raw CAI asset type such as storage.googleapis.com/Bucket."
}
```

| Status | `error` | Cause |
|:---:|:---|:---|
| `400` | `unknown_resource_type` | `type` is not a friendly name and does not look like a CAI type |
| `400` | `invalid_scope` | Malformed scope |
| `400` | `invalid_filter` | A filter or sort field could not be compiled, or CAI rejected the query (including an RE2 type pattern that matches nothing) |
| `403` | `permission_denied` | Caller lacks `cloudasset.viewer` on the scope |
| `404` | `scope_not_found` | The scope does not exist |
| `422` | (FastAPI validation) | Missing `scope`, or a parameter out of range |
| `502` | `upstream_error` | The Cloud Asset Inventory call failed |
| `503` | `api_not_enabled` | Cloud Asset API disabled on the **quota** project (see [GCP setup](GCP_SETUP.md)) |

## Concurrency

Endpoints are plain synchronous functions. Starlette runs them in a threadpool, so the server
handles concurrent requests without blocking. See Phase 7 of the
[delivery plan](DELIVERY_PLAN.md) for why the code is deliberately not `async`.
