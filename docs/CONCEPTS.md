# Concepts

What a search takes and what it returns, regardless of whether you use the
[CLI](CLI.md) or the [HTTP API](API.md). Both surfaces call the same core, so everything here
applies to both. Flag and parameter names are given as `--cli-flag` / `?api_param=`.

## Scope

Every search runs against one CAI scope:

- `projects/<id-or-number>`
- `folders/<number>`
- `organizations/<number>`

A single call covers everything beneath the scope. A malformed scope is rejected before any
API call is made.

Permission is checked on the scope itself. If you can't read the organisation, search the
projects you can read, either several at once with the CLI's `--also-scope` or one per API
request.

## Resource types

`--type` / `?type=` is repeatable and accepts three forms:

| Form | Example |
|:---|:---|
| Friendly name | `bucket`, `vm`, `cloudrun` (list them with `gcpe types` or `GET /v1/types`) |
| Raw CAI asset type | `dns.googleapis.com/ManagedZone` |
| RE2 pattern | `compute.googleapis.com/.*` |

There are 20 built-in friendly names, defined in `app/asset_types.json`. A site can add more
in its [configuration](CONFIGURATION.md).

**With no type, every asset type is searched.** That is the default, and it only gives a
readable result because of [noise reduction](#noise-reduction).

## Filters

All filters are compiled into a CAI query and evaluated by Google, never locally. Filters of
different kinds are ANDed together. Repeated values of the same kind are ORed, except for
labels, where every label filter must match.

| Filter | CLI | API | Notes |
|:---|:---|:---|:---|
| Free text | positional `term` | `q` | Matched across all searchable fields |
| Label | `--label` / `-l` | `label` | `key=value`, or bare `key` for "has this label at all". Repeats are ANDed. |
| Location | `--location` | `location` | Repeats are ORed. `*` wildcards work: `europe-*`. |
| Project | `--project` | `project` | Repeats are ORed. Takes an ID or a number. |
| Raw query | `--raw-query` | `raw_query` | Raw [CAI query syntax](https://cloud.google.com/asset-inventory/docs/query-syntax), ANDed with the rest |

```text
--label env=prod --label tier=web            → env is prod AND tier is web
--location europe-west1 --location global    → either location
--type vm --raw-query 'NOT state:RUNNING'    → VMs that are not running
```

**Namespaced label keys** such as `cloud.googleapis.com/location` or
`serving.knative.dev/service` work as written. CAI rejects them unless the key is quoted, and
the tool does the quoting.

**Project IDs are resolved to numbers.** CAI only matches `project:` on the project number,
so an ID passed straight through would return an empty result instead of an error. The tool
looks the ID up first and caches the answer for the life of the process.

**The compiled query is always visible.** It is returned as `query` in API responses, printed
by `--show-query` (or as a full `gcloud` command by `--show-gcloud`), and shown automatically when a filtered CLI search returns nothing. A filter
that compiles to the wrong thing returns plausible but incomplete results, so check the query
when a result looks short.

## Sorting

`--sort` / `?sort=` is repeatable, with later values breaking ties. It takes a field name,
optionally followed by ` DESC`. Sorting is done by CAI, so only CAI's sortable fields are
accepted, and an unknown field is rejected rather than ignored:

`name`, `assetType`, `project`, `displayName`, `description`, `location`, `createTime`,
`updateTime`, `state`, `parentFullResourceName`, `parentAssetType`.

## Noise reduction

An unfiltered search of a real estate is mostly rows nobody is auditing: enabled API services,
container image layers, Cloud Run revisions, auto-created default routes. In one surveyed
estate these were about two thirds of all resources, and suppression took 515 resources down
to 84.

Fifteen rules in `app/noise_rules.json` hide these by default. Each rule names an asset type
and can be narrowed with a name regex, so an auto-created `default` subnet is hidden while a
subnet you built is kept.

**Rules apply to every search, including ones that name a type.** For example,
`--type subnet` still hides `default` subnets.

Two guarantees:

1. **`--show-all` / `?show_all=true` disables every rule.**
2. **What was hidden is always reported**, with counts and reasons: `suppressed` and
   `suppressed_summary` in the API, and a closing line in the CLI. If every match was
   suppressed, the result says so instead of saying nothing was found.

Noise rules are applied to results as they come back, not compiled into the query, because CAI
cannot exclude an asset type (`assetType` is not a queryable field). As a result, `limit`
counts visible rows only. The one exception is `/v1/resources/stream`, which cannot report
suppression counts (see [API](API.md#streaming)).

## Limits and truncation

Results are capped at **1000** by default, so an org-wide search cannot run away. Change the
cap with `--limit` / `-n` or `?limit=` (the API allows up to 10,000).

When the cap cuts results short, both surfaces say so: `truncated: true` in the API, and a
warning in the CLI. Summaries are never capped, because counts from a truncated set would be
wrong.

## IAM enrichment

`--include-iam` / `?include_iam=true` attaches each resource's IAM bindings, at the cost of
**one extra call for the whole scope**, not one per resource.

These are bindings **attached directly** to the resource. A grant inherited from a parent
project, folder or organisation gives real access and is **not** included, so this does not
fully answer "who can reach this bucket". Every response that includes IAM carries an
`iam_note` saying so.

| `iam_bindings` | Meaning |
|:---|:---|
| absent / `null` | IAM was not requested |
| `[]` | IAM was requested, and nothing is attached |

## Caching

Caching is off by default. `--cache-ttl SECONDS` / `?cache_ttl=` caches identical searches for
that long (the API allows up to 3600). It is opt-in because answering from a stale cache gives
the wrong answer to someone checking whether a fix landed. It is useful for absorbing repeated
polling of the same search.

## Freshness

CAI is a near-real-time index, not a live read of each service. A resource created or deleted
moments ago may not appear yet. The [PRD](PRD.md) explains why this trade-off was accepted.

## Role risk

`roles` and `grants` classify IAM roles as `high` or `medium` risk **by the permissions they
contain**. A role is as risky as its riskiest permission. The rules, the reasons and the quoted
sources (Google's IAM documentation, the CIS GCP Benchmark, and Rhino Security Labs'
privilege-escalation research) are in [Role risk](ROLE_RISK.md). A risk filter means "at least":
`medium` includes `high`.

Because risk is a property of permissions, **custom roles are classified too** — nobody has to
name them. `grants` matches them at any scope, since the match happens on permissions inside
CAI. `roles` lists them when given a scope (`--scope` / `?scope=`), which costs one call per
organization or project; IAM has no folder-level custom roles, so a folder scope is rejected.

**Billing accounts need their own command.** Cloud Asset Inventory does not index them and
`billingAccounts/…` is not a valid CAI scope, so no `grants` scope reaches them however wide.
`billing-grants` (`GET /v1/billing-grants`) reads the Cloud Billing API instead, and takes no
scope: billing accounts sit outside the resource hierarchy, so there is nothing to bound them
by. Role names are all that API returns, so risk is resolved against the predefined catalogue
and a role that is not in it is reported as unclassified rather than assumed safe.

### Who is behind a member

Some members are not one principal, and the headcount behind them is usually the finding.
`expand_members` (`--expand-members`) reads them. It is off by default, because it turns an
infrastructure audit into a list of named people.

| Member | What is read |
|:---|:---|
| `group:…` | Its members through the Cloud Identity Groups API, with nested groups flattened. Needs `cloudidentity.googleapis.com` on the quota project and a Groups Reader *directory* role. Groups owned by a personal Google account cannot be read. |
| `projectOwner:p`, `projectEditor:p`, `projectViewer:p` | Who holds Owner / Editor / Viewer in the policy attached to project `p`, read through Cloud Asset Inventory, so no extra role is needed. These usually appear on legacy storage roles. |
| `principalSet://…` | Explained, not listed: which workload identity pool and which attribute. Federated identities are issued outside Google. |
| `domain:`, `allUsers`, `allAuthenticatedUsers` | Explained, not listed. |

Expansion is **one level deep and attached-only**. A group inside a project role is listed, not
expanded again, and owners inherited from a folder or organization are not counted. A row is
still one binding: expansion adds `member_count` to it and puts the names in `expansions`, so
`group_by` and the CSV columns are unchanged. A member that cannot be read is listed with its
error, and the coverage note names it. It still applies in full.
