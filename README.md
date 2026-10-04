# GCP Resource Explorer

A CLI and HTTP API for searching Google Cloud resources across a large estate — built for
organisations running thousands of projects, where per-project API iteration is too slow and
too quota-hungry to be viable. Every search is a single Cloud Asset Inventory (CAI) call over a
whole project, folder or organisation, with filters evaluated server-side.

> **Status: usable.** Phases 0–3, 5 and 6 are complete: search every resource type by default with
> noise reduction, server-side filtering and sorting, IAM enrichment, aggregated summaries, and
> JSON/CSV output, concurrent multi-scope search, streaming, and a container image with CI.
> Phase 4's tuning is built but its baseline still needs measuring on a real organisation —
> run `scripts/benchmark.py` there. Phase 8 adds high-risk role and grant review; billing-account
> IAM and group expansion are still to come. See the [delivery plan](docs/DELIVERY_PLAN.md).

## Quickstart

Requires Python 3.13+, [`uv`](https://docs.astral.sh/uv/), and an authenticated `gcloud`.

```bash
uv sync
gcloud auth application-default login     # see docs/GCP_SETUP.md for the full setup

uv run gcpe search projects/my-project                       # everything, noise hidden
uv run gcpe search organizations/123 backup --type bucket    # one call, whole org
uv run gcpe summary organizations/123                        # counts by type/project/location
uv run gcpe grants organizations/123 --by member             # who holds high-risk roles

uv run gcpe serve                                            # API on http://127.0.0.1:8000
curl 'http://127.0.0.1:8000/v1/resources?scope=projects/my-project&type=bucket'
```

The caller needs `roles/cloudasset.viewer` on each scope searched, and the Cloud Asset API
must be enabled on the ADC quota project. These are usually different projects —
[GCP setup](docs/GCP_SETUP.md) explains both.

## Documentation

| Read this | For |
|:---|:---|
| [GCP setup](docs/GCP_SETUP.md) | Credentials, IAM, the quota project, `setup-gcp.sh` |
| [Concepts](docs/CONCEPTS.md) | Scopes, types, filters, noise reduction, limits, IAM semantics — shared by CLI and API |
| [CLI guide](docs/CLI.md) | Using `gcpe`: recipes, output formats, exit codes |
| [CLI reference](docs/CLI_REFERENCE.md) | Every command and flag (generated from the code) |
| [HTTP API](docs/API.md) | Endpoints, parameters, response and error bodies |
| [OpenAPI reference](https://edoatley.github.io/gcp-resource-browser/) | The API spec in Swagger UI, in the browser (also at `/docs` on a running server) |
| [Role risk](docs/ROLE_RISK.md) | Which roles count as high risk, and the quoted sources behind each rule |
| [Configuration](docs/CONFIGURATION.md) | Site config: extra types, noise rules |
| [Deployment](docs/DEPLOYMENT.md) | Container image, Cloud Run/GKE, health probes |
| [Architecture](docs/ARCHITECTURE.md) | How it is built, and how to extend it |
| [Development](docs/DEVELOPMENT.md) | Tests, CI, differential checks against `gcloud` |
| [Scripts](scripts/README.md) | `gcloud` equivalents, setup and benchmark tooling |
| [PRD](docs/PRD.md) · [Delivery plan](docs/DELIVERY_PLAN.md) · [Walkthrough](docs/WALKTHROUGH.md) | Requirements, phasing, manual verification against real GCP |

## Getting started

A walkthrough of the main features, in order. It assumes [GCP setup](docs/GCP_SETUP.md) is
done: ADC is configured, the Cloud Asset API is enabled on your quota project, and you hold
`cloudasset.viewer` on the scope below. Set the scope once:

```bash
SCOPE=projects/my-project      # or folders/<number>, or organizations/<number>
```

**1. Check you can reach Cloud Asset Inventory.** List the friendly type names, then run one
small search:

```bash
uv run gcpe types
uv run gcpe list-resources $SCOPE bucket
```

An error here names the problem: exit code `3` is missing viewer on the scope, and `6` is the
API disabled on the quota project.

**2. Get the shape of the scope.** Counts by type, project and location from a single call:

```bash
uv run gcpe summary $SCOPE
```

**3. Search everything.** With no `--type`, every asset type is searched and low-signal rows
(enabled APIs, image layers, default routes and similar) are hidden. The last line says how
many were hidden and why:

```bash
uv run gcpe search $SCOPE
uv run gcpe search $SCOPE --show-all      # hide nothing
```

**4. Narrow it down.** Filters are evaluated by CAI, not locally. Different filters are ANDed
together, and repeated values of one filter are ORed. A filter only returns rows if its values
exist in your scope, so start by listing which labels are in use (`summary` from step 2 already
shows the types and locations):

```bash
uv run gcpe search $SCOPE -o json | jq -r '.[].labels // {} | keys[]' | sort | uniq -c
```

The examples below use the labels and locations of a small Cloud Run and Firebase project
managed with Pulumi. Swap in values from your own output:

```bash
# Free text, matched across names, descriptions, labels and more
uv run gcpe search $SCOPE pulumi

# Several types at once
uv run gcpe search $SCOPE --type cloudrun --type serviceaccount

# A label value AND a location
uv run gcpe search $SCOPE --label goog-pulumi-provisioned=true --location us-central1

# Either location (repeats are ORed), restricted to three types
uv run gcpe search $SCOPE --type bucket --type cloudrun --type topic \
    --location us-central1 --location global

# Has the label at all, whatever its value. The key is namespaced, so the tool quotes it
uv run gcpe search $SCOPE --type cloudrun --label cloud.googleapis.com/location --show-query

# Audit: resources NOT created by infrastructure-as-code, using raw CAI syntax
uv run gcpe search $SCOPE --type bucket --type cloudrun --type topic --type serviceaccount \
    --raw-query 'NOT labels.goog-pulumi-provisioned:*' --show-query
```

`--show-query` prints the CAI query the filters compiled to. When a filtered search finds
nothing, the query is printed automatically, so you can tell "nothing matched" apart from "the
filter wasn't what I meant".

**5. Sort and export.** JSON and CSV go to stdout, and warnings go to stderr:

```bash
uv run gcpe search $SCOPE --sort 'createTime DESC' -n 5     # the five newest resources
uv run gcpe search $SCOPE --type serviceaccount -o csv > service-accounts.csv
uv run gcpe search $SCOPE --type serviceaccount -o json | jq -r '.[].display_name'
```

**6. See who has access.** Add IAM bindings attached to each resource. Inherited grants are
*not* included, and the output says so:

```bash
uv run gcpe search $SCOPE --type bucket --type cloudrun --type topic --include-iam -o json \
    | jq -c '.[] | {display_name, roles: [.iam_bindings[]?.role]}'
```

**7. Search several scopes.** If you hold viewer on some projects but not on their organisation,
search them all at once. Use projects you can read, such as the ones `setup-gcp.sh` granted:

```bash
uv run gcpe search $SCOPE --also-scope projects/idp-prototype-edo --type bucket
```

A scope that can't be searched is listed as `FAILED` and the exit code is `7`, but the others
still return. To see this, add a project you can't read. CAI reports a nonexistent project as
permission denied, so it doesn't reveal which projects exist:

```bash
uv run gcpe search $SCOPE --also-scope projects/does-not-exist --type bucket; echo "exit=$?"
```

**8. Get the equivalent `gcloud` command.** `--show-gcloud` prints the
`gcloud asset search-all-resources` command that runs the same search. It includes the
compiled query, the resolved asset types, the sort order, and your ADC quota project as
`--billing-project`, so it works when pasted even if `gcloud` is configured with a different
project:

```bash
uv run gcpe search $SCOPE --type cloudrun --label cloud.googleapis.com/location --show-gcloud
```

```text
gcloud asset search-all-resources \
    --scope=projects/my-project \
    --asset-types=run.googleapis.com/Service \
    --query='labels."cloud.googleapis.com/location":*' \
    --billing-project=my-quota-project
```

Paste the command to see the same rows from `gcloud`. With `--include-iam` it also prints the
matching `search-all-iam-policies` command. With `--also-scope` it prints one command per
scope. The command is printed to stderr with `-o json`/`csv`, so stdout stays parseable. If
the tool hid rows, a comment says how many more `gcloud` will return, because `gcloud` has no
noise reduction.

**9. Review high-risk access.** See which roles count as high risk and why, then who holds
them. Risk comes from the permissions a role contains, and the evidence for every rule is in
[Role risk](docs/ROLE_RISK.md):

```bash
uv run gcpe roles --risk high                              # Owner, Editor, Billing Admin, Org Admin, ...
uv run gcpe grants $SCOPE                                  # every high-risk grant
uv run gcpe grants $SCOPE --by member                      # rolled up: what each principal holds
uv run gcpe grants $SCOPE --member-type user --member-type group --role-risk medium
```

Google's own service agents are hidden and counted (`--show-all` includes them). Each result
also says what the scope cannot see. In particular, searching a project misses grants made on
its folders and organisation, so search the organisation to cover every level.

**10. Use the HTTP API.** Searches, summaries, type listings, risky roles and grants are also
available over HTTP (multi-scope search is CLI-only):

```bash
uv run gcpe serve &

curl -s "http://127.0.0.1:8000/v1/resources?scope=$SCOPE&type=bucket" | jq '{count, query, truncated}'
curl -s "http://127.0.0.1:8000/v1/summary?scope=$SCOPE" | jq .by_asset_type
curl -sN "http://127.0.0.1:8000/v1/resources/stream?scope=$SCOPE" | head -5   # NDJSON, as rows arrive
curl -s "http://127.0.0.1:8000/v1/roles?risk=high" | jq -r '.data[].name'
curl -s "http://127.0.0.1:8000/v1/grants?scope=$SCOPE&role_risk=high&group_by=member" | jq .groups
```

Open <http://127.0.0.1:8000/docs> to explore and call every endpoint in your browser. The
same spec is [published online](https://edoatley.github.io/gcp-resource-browser/) as a
read-only reference.

Next: [CLI guide](docs/CLI.md) for every option, [Concepts](docs/CONCEPTS.md) for how filters
and noise reduction behave, and [Deployment](docs/DEPLOYMENT.md) to run it as a service.
