# CLI guide

`gcpe` searches GCP resources from the terminal. This page covers common tasks. For
every flag, see the generated [CLI reference](CLI_REFERENCE.md). For filter semantics shared
with the API (how filters combine, noise reduction, IAM caveats), see [Concepts](CONCEPTS.md).

Set up credentials first: [GCP setup](GCP_SETUP.md).

```bash
uv run gcpe --help              # or: uv run python -m app.main --help
```

`gcpe` is short for GCP Explorer. The longer `gcp-explorer` also works and runs the same
command.

## Commands

| Command | Does |
|:---|:---|
| `search SCOPE [TERM]` | The main search: any number of types and filters, sorting, IAM, JSON/CSV, several scopes |
| `list-resources SCOPE TYPE` | Shorthand for a single-type search |
| `summary SCOPE [TERM]` | Counts by asset type, project and location, with no row limit |
| `types` | List the friendly type names |
| `roles` | List IAM roles classed as risky, and the permissions that make them so |
| `grants SCOPE` | Find principals holding risky roles across a scope |
| `serve` | Run the [HTTP API](API.md) (`--host`, `--port`; default `127.0.0.1:8000`) |
| `openapi` | Write the API's OpenAPI spec (`--out`, default `openapi.yml`) |

## Searching

```bash
# Everything in a scope, with low-signal resources hidden
gcpe search projects/my-project

# Find a resource by name across an organisation — one API call, not 2000
gcpe search organizations/123456789 backup --type bucket

# Several types and several filters, evaluated by CAI rather than locally
gcpe search organizations/123456789 \
    --type bucket --type vm \
    --label env=prod \
    --location europe-west2 --location europe-west1

# Resources carrying an `owner` label at all, whatever its value
gcpe search folders/456 --type vm --label owner

# A raw CAI type or RE2 pattern instead of a friendly name
gcpe search projects/my-project --type 'compute.googleapis.com/.*'

# Raw CAI query syntax, for anything not modelled as a flag
gcpe search projects/my-project --type vm --raw-query 'NOT state:RUNNING'

# Restrict an org-wide search to some projects (IDs or numbers)
gcpe search organizations/123 --type bucket --project app-prod --project app-staging

# Single-type shorthand
gcpe list-resources projects/my-project bucket -q backup
```

### Checking what was sent

```bash
gcpe search projects/my-project --type bucket --label env=prod --show-query
# CAI query: labels.env:prod
```

When a filtered search returns nothing, the query is printed automatically. That lets you tell
"nothing matched" apart from "the filter compiled to something unexpected".

### Reproducing a search in `gcloud`

```bash
gcpe search projects/my-project --type cloudrun --label env=prod --show-gcloud
```

`--show-gcloud` prints the equivalent `gcloud asset search-all-resources` command before the
results, or to stderr with `-o json`/`csv`. It is built from what was actually sent to CAI:
the compiled query (with project IDs already resolved to numbers), the resolved asset types and
the sort order. It also adds:

- `--billing-project` set to your ADC quota project, so the pasted command bills where the
  tool does. `gcloud` keeps its own configured project, which may not have the Cloud Asset
  API enabled.
- A comment with the number of hidden rows, because `gcloud` has no noise reduction and will
  return them.
- `--limit` only when the result was truncated, with a note when noise reduction means the two
  may stop at different rows.
- The matching `search-all-iam-policies` command with `--include-iam`, and one command per
  scope with `--also-scope`.

It reuses the tool's own query compiler, so it shows what the tool did, not whether the tool
was right. For that, use the independent `scripts/compare-resources.sh`
([Development](DEVELOPMENT.md#checking-against-gcloud)).

### Hidden resources

By default the table ends with a line such as:

```text
38 hidden: 22 enabled API services, 9 container image layers, 7 default routes. Use --show-all to include them.
```

Pass `--show-all` to `search`, `list-resources` or `summary` to disable suppression. See
[Concepts → noise reduction](CONCEPTS.md#noise-reduction) for the rules.

### Sorting and limits

```bash
gcpe search projects/my-project --sort 'createTime DESC' --sort name
gcpe search organizations/123 --type vm -n 5000
```

Results are capped at 1000 by default. When the cap cuts them short, a warning tells you to
raise `--limit`.

## Output formats

`search` and `summary` take `-o table|json|csv`. `list-resources` always prints a table.

```bash
gcpe search projects/my-project --sort 'createTime DESC' -o json | jq '.[0]'
gcpe search projects/my-project --type bucket -o csv > buckets.csv
gcpe summary organizations/123 -o csv > census.csv
```

- **With `json` or `csv`, stdout holds only the data.** Every warning goes to stderr: truncation,
  suppression, the IAM caveat and failed scopes. A pipeline reading stdout always gets
  parseable output.
- `search -o json` emits a JSON array of resources. Unset fields are left out rather than
  emitted as `null`.
- `search -o csv` has the columns `full_name, asset_type, display_name, project_id, project,
  location, state, create_time, labels`. Labels are stored as compact JSON in their cell
  rather than dropped.
- `summary -o json` emits the same `Summary` object as `GET /v1/summary`. `summary -o csv`
  emits long-form `dimension,key,count` rows, with `total` and `suppressed` rows first.

The table's Project column shows the project ID where it can be recovered. CAI itself only
reports project numbers. Both are in JSON and CSV output.

## IAM bindings

```bash
gcpe search projects/my-project --type bucket --include-iam -o json
```

This attaches the bindings **attached directly** to each resource. Inherited grants are not
included. See [Concepts → IAM](CONCEPTS.md#iam-enrichment) before using it to answer "who has
access".

## Summaries

```bash
gcpe summary organizations/123456789
gcpe summary organizations/123 --type vm --label env=prod
```

`summary` counts every match, with no `--limit`, because counts from a truncated set would be
wrong. It takes the free-text term, `--type`, `--label`, `--location`, `--project` and
`--show-all`, the same filters as `GET /v1/summary`.

## High-risk roles and grants

```bash
gcpe roles --risk high                       # which roles are high risk, and why
gcpe roles --risk medium -o csv > roles.csv

gcpe grants organizations/123                # every high-risk grant in the org
gcpe grants organizations/123 --by member    # rolled up: what each principal holds
gcpe grants organizations/123 --by role      # who holds each risky role
gcpe grants organizations/123 -m user -m group --role-risk medium
gcpe grants organizations/123 -m serviceAccount     # CIS 1.5: service accounts with admin access
gcpe grants projects/my-project --show-gcloud       # the gcloud equivalent
```

- **Risk comes from permissions, not role names.** A role is high risk if it contains a high-risk
  permission such as `resourcemanager.projects.setIamPolicy` or `iam.serviceAccounts.actAs`.
  This also covers custom roles. Every rule and its quoted source is in
  [Role risk](ROLE_RISK.md).
- **Google service agents are hidden and counted.** `--show-all` includes them. Default service
  accounts (`…-compute@developer…`, `…@appspot…`) are always shown, because they are yours and
  often hold Editor.
- **Each result ends with what it cannot see.** Searching a project misses grants on its folders
  and organisation, billing-account IAM is not included, and groups are not expanded. Search the
  organisation for the full picture.
- With `-o json`/`csv`, these notes go to stderr. `--show-gcloud` prints one
  `gcloud asset search-all-iam-policies` command per permission batch.

## Searching several scopes

CAI checks permission on the scope itself. If you hold viewer on individual projects but not
on their organisation, search the projects together:

```bash
gcpe search projects/a --also-scope projects/b --also-scope projects/c --type bucket
```

- Scopes are searched concurrently, up to `--max-concurrency` at a time (default 8).
- Each scope is still one scope-wide CAI search. Only the scopes run in parallel.
- `--limit` applies **per scope**.
- A scope that fails is listed as `FAILED <scope>: <reason>` on stderr, and the others still
  return. The exit code is then **7**, so a script cannot mistake a partial answer for a full
  one.
- Results are sorted by project, type and name, so repeated runs can be diffed.

This option exists only in the CLI. With the API, make one request per scope.

## Caching

```bash
gcpe search organizations/123 --type bucket --cache-ttl 60
```

Caching is off by default. It only helps within one process, so it matters for `serve` more
than for one-off CLI runs. See [Concepts → caching](CONCEPTS.md#caching).

## Exit codes

| Code | Meaning |
|:---:|:---|
| `0` | Success, including an empty result |
| `1` | Unexpected error |
| `2` | Bad usage: unknown type, malformed scope, invalid filter or sort field |
| `3` | Permission denied on the scope |
| `4` | Scope not found |
| `5` | Cloud Asset Inventory call failed |
| `6` | Cloud Asset API not enabled on the **quota** project (see [GCP setup](GCP_SETUP.md)) |
| `7` | Partial: some `--also-scope` scopes answered and some failed |

## Running from the container

```bash
docker run --rm \
    -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
    -e GOOGLE_CLOUD_QUOTA_PROJECT=<your-quota-project> \
    gcp-explorer search projects/my-project --type bucket
```

See [Deployment](DEPLOYMENT.md).
