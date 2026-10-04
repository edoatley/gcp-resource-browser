# CLI reference

GCP Resource Explorer CLI

**Usage**:

```console
$ gcpe [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `search`: Search resources across a scope, with...
* `list-resources`: Query one resource type and print a table...
* `summary`: Count resources in a scope by type,...
* `openapi`: Export the API&#x27;s OpenAPI specification.
* `types`: List the friendly resource-type names this...
* `serve`: Run the FastAPI server.

## `gcpe search`

Search resources across a scope, with filters applied server-side.

With no --type, searches every asset type and hides low-signal resources.

**Usage**:

```console
$ gcpe search [OPTIONS] {scope} [term]
```

**Arguments**:

* `scope`: CAI scope: organizations/&lt;id&gt;, folders/&lt;id&gt;, or projects/&lt;id&gt;  [required]
* `term`: Free-text term, matched across all searchable fields

**Options**:

* `-t, --type <str>`: Resource type, repeatable. A friendly name, a raw CAI asset type, or an RE2 pattern.
* `-l, --label <str>`: Label filter, repeatable: `key=value`, or `key` to match any value
* `--location <str>`: Location filter, repeatable; several are ORed. Supports `*` wildcards.
* `--project <str>`: Project filter, repeatable; several are ORed. Accepts a project ID or number (IDs are resolved to numbers, which is what CAI matches on).
* `--raw-query <str>`: Raw CAI query syntax, ANDed with the other filters
* `-n, --limit <int range>`: Maximum resources to return  [default: 1000; x&gt;=1]
* `--show-query`: Print the CAI query the filters compiled to
* `--show-gcloud`: Print the equivalent `gcloud asset search-all-resources` command
* `--show-all`: Include resources hidden by default (enabled API services, image layers, auto-created default routes and subnets, and similar)
* `--sort <str>`: Sort field, with optional ` DESC`. Repeatable for tie-breaks. One of: name, assetType, project, displayName, description, location, createTime, updateTime, state, parentFullResourceName, parentAssetType.
* `--include-iam`: Attach each resource&#x27;s directly-attached IAM bindings. One extra call for the whole scope. Inherited bindings are not included.
* `-o, --output <table|json|csv>`: Output format  [default: table]
* `--also-scope <str>`: Additional scope to search concurrently; repeatable
* `--max-concurrency <int range>`: Maximum concurrent scope searches when several scopes are given  [default: 8; x&gt;=1]
* `--cache-ttl <float range>`: Seconds to cache identical searches. 0 (the default) disables it -- an audit tool should not answer from a stale cache unless asked to.  [default: 0.0; x&gt;=0]
* `--help`: Show this message and exit.

## `gcpe list-resources`

Query one resource type and print a table (single-type form of `search`).

**Usage**:

```console
$ gcpe list-resources [OPTIONS] {scope} {resource_type}
```

**Arguments**:

* `scope`: CAI scope: organizations/&lt;id&gt;, folders/&lt;id&gt;, or projects/&lt;id&gt;  [required]
* `resource_type`: Resource type, e.g. &#x27;bucket&#x27;  [required]

**Options**:

* `-q, --query <str>`: Free-text term, matched across all searchable fields
* `-n, --limit <int range>`: Maximum resources to return  [default: 1000; x&gt;=1]
* `--show-all`: Include resources hidden by default (enabled API services, image layers, auto-created default routes and subnets, and similar)
* `--help`: Show this message and exit.

## `gcpe summary`

Count resources in a scope by type, project and location.

**Usage**:

```console
$ gcpe summary [OPTIONS] {scope} [term]
```

**Arguments**:

* `scope`: CAI scope: organizations/&lt;id&gt;, folders/&lt;id&gt;, or projects/&lt;id&gt;  [required]
* `term`: Free-text term, matched across all searchable fields

**Options**:

* `-t, --type <str>`: Resource type, repeatable. A friendly name, a raw CAI asset type, or an RE2 pattern.
* `-l, --label <str>`: Label filter, repeatable: `key=value`, or `key` to match any value
* `--location <str>`: Location filter, repeatable; several are ORed. Supports `*` wildcards.
* `--project <str>`: Project filter, repeatable; several are ORed. Accepts a project ID or number (IDs are resolved to numbers, which is what CAI matches on).
* `--show-all`: Include resources hidden by default (enabled API services, image layers, auto-created default routes and subnets, and similar)
* `-o, --output <table|json|csv>`: Output format  [default: table]
* `--help`: Show this message and exit.

## `gcpe openapi`

Export the API&#x27;s OpenAPI specification.

FastAPI serves the schema live at /openapi.json; the PRD asks for a
committed openapi.yml, which is what downstream codegen consumes.

**Usage**:

```console
$ gcpe openapi [OPTIONS]
```

**Options**:

* `--out <path>`: Where to write the specification  [default: openapi.yml]
* `--help`: Show this message and exit.

## `gcpe types`

List the friendly resource-type names this tool understands.

**Usage**:

```console
$ gcpe types [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `gcpe serve`

Run the FastAPI server.

**Usage**:

```console
$ gcpe serve [OPTIONS]
```

**Options**:

* `--host <str>`: Address to bind  [default: 127.0.0.1]
* `--port <int>`: Port to bind  [default: 8000]
* `--help`: Show this message and exit.
