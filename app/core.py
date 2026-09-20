"""Cloud Asset Inventory access.

The only module that talks to GCP. The CLI and the API are both thin wrappers
over `search_resources`, so any new capability belongs here rather than in
either surface.

Architectural rule: never iterate per-project service APIs to *find* resources.
Discovery is always a single scope-wide CAI search. See docs/PRD.md.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from google.api_core import exceptions as gcp_exceptions
from google.cloud import asset_v1
from google.rpc.error_details_pb2 import ErrorInfo

from app.aggregate import ATTACHED_IAM_NOTE, attach_iam, fetch_iam_bindings
from app.cache import TTLCache
from app.config import load_config
from app.models import Resource
from app.noise import NoiseFilter
from app.params import DEFAULT_PAGE_SIZE, SORTABLE_FIELDS, SearchFilters
from app.query import QueryError, build_query

# Friendly names mapped to CAI asset types, loaded from data rather than
# written as a literal so scripts/ can read the same file with jq. The mapping
# previously existed twice -- here and in bash -- which is exactly the kind of
# duplication that drifts silently.
ASSET_TYPES_FILE = Path(__file__).with_name("asset_types.json")


def _load_asset_types() -> dict[str, str]:
    with ASSET_TYPES_FILE.open() as handle:
        types = dict(json.load(handle)["types"])
    # Site config adds names; it never needs to restate the defaults.
    types.update(load_config().extra_asset_types)
    return types


ASSET_TYPES: dict[str, str] = _load_asset_types()

# CAI reports a resource's parent as //cloudresourcemanager.googleapis.com/projects/<id>,
# which is the only place a human-readable project ID appears for most resources -- the
# `project` field holds the number.
_PROJECT_PARENT = re.compile(
    r"^//cloudresourcemanager\.googleapis\.com/projects/(?P<project_id>[a-z][a-z0-9-]{4,28}[a-z0-9])$"
)

# Project IDs are never all-digits; project numbers always are.
_PROJECT_NUMBER = re.compile(r"^\d+$")

# Nested resources (a key under a service account, a record set under a managed
# zone) have a non-project parent, so the ID cannot come from the parent path --
# but it usually appears in the resource's own name.
_PROJECT_IN_NAME = re.compile(r"/projects/(?P<project_id>[a-z][a-z0-9-]{4,28}[a-z0-9])(?:/|$)")

# Project ID -> number. Immutable in GCP, so caching cannot go stale.
_PROJECT_NUMBERS: dict[str, str] = {}

# A raw CAI asset type looks like "storage.googleapis.com/Bucket". Anything
# containing a dot or a slash is treated as a raw type or RE2 pattern and
# passed through untouched; bare words must resolve via ASSET_TYPES.
_RAW_ASSET_TYPE = re.compile(r"[./]")

# CAI accepts an organization, folder, or project as a search scope.
SCOPE_PATTERN = re.compile(r"^(organizations|folders|projects)/[a-zA-Z0-9][a-zA-Z0-9\-_.]*$")


class ResourceExplorerError(Exception):
    """Base class for errors this tool raises deliberately."""

    code = "internal_error"


class UnknownResourceTypeError(ResourceExplorerError):
    code = "unknown_resource_type"


class InvalidScopeError(ResourceExplorerError):
    code = "invalid_scope"


class InvalidFilterError(ResourceExplorerError):
    code = "invalid_filter"


class ScopeAccessDenied(ResourceExplorerError):
    code = "permission_denied"


class ApiNotEnabledError(ResourceExplorerError):
    """The Cloud Asset API is disabled on the project the request bills to.

    Google returns HTTP 403 for this as well as for genuine permission
    failures, and the two have completely different remedies -- and often
    concern different projects. Conflating them sends people to fix the wrong
    thing, so it gets its own class.
    """

    code = "api_not_enabled"


class ScopeNotFound(ResourceExplorerError):
    code = "scope_not_found"


class UpstreamError(ResourceExplorerError):
    code = "upstream_error"


@dataclass(frozen=True)
class SearchResult:
    """What a search returned, plus how it was asked."""

    resources: list[Resource]
    truncated: bool
    query: str
    asset_types: list[str]
    suppressed: int = 0
    suppressed_summary: str = ""
    iam_note: str | None = None


@lru_cache(maxsize=1)
def get_client() -> asset_v1.AssetServiceClient:
    """Return a shared CAI client.

    Constructing a client pays channel and TLS setup, so it is built once and
    reused rather than per call. The client is thread-safe, which matters
    because FastAPI runs the synchronous endpoints in a threadpool.
    """
    return asset_v1.AssetServiceClient()


def _error_info(exc: gcp_exceptions.GoogleAPICallError) -> ErrorInfo | None:
    """Pull Google's machine-readable ErrorInfo out of an exception, if present."""
    for detail in getattr(exc, "details", None) or []:
        if isinstance(detail, ErrorInfo):
            return detail
    return None


def _service_disabled_error(exc: gcp_exceptions.PermissionDenied) -> ApiNotEnabledError | None:
    """Recognise "API not enabled" hiding inside a 403."""
    info = _error_info(exc)
    if info is None or info.reason != "SERVICE_DISABLED":
        return None

    metadata = dict(info.metadata)
    service = metadata.get("service", "cloudasset.googleapis.com")
    # The consumer is the project the call bills to -- the ADC quota project,
    # which is frequently NOT the project being searched. Naming the wrong one
    # is the whole reason this case is separated out.
    consumer = metadata.get("containerInfo") or metadata.get("consumer", "your quota project")
    return ApiNotEnabledError(
        f"{service} is not enabled on {consumer}, the project this request bills to "
        f"(which may differ from the scope being searched). Enable it with:\n"
        f"    gcloud services enable {service} --project={consumer}\n"
        "Then wait a minute for it to propagate."
    )


def resolve_asset_type(resource_type: str) -> str:
    """Map a friendly name to a CAI asset type, or pass a raw type through."""
    if _RAW_ASSET_TYPE.search(resource_type):
        # Raw CAI type or RE2 pattern. CAI rejects a pattern matching nothing
        # with INVALID_ARGUMENT, which surfaces as InvalidScopeError.
        return resource_type
    try:
        return ASSET_TYPES[resource_type]
    except KeyError:
        raise UnknownResourceTypeError(
            f"Unsupported resource type {resource_type!r}. "
            f"Choose from: {', '.join(sorted(ASSET_TYPES))}, "
            "or pass a raw CAI asset type such as storage.googleapis.com/Bucket."
        ) from None


def resolve_asset_types(resource_types: Sequence[str]) -> list[str]:
    """Resolve several types, preserving order and dropping duplicates."""
    if isinstance(resource_types, str):
        # A bare str satisfies Sequence[str] and would iterate character by
        # character, searching for asset types "b", "u", "c"... Fail loudly.
        raise UnknownResourceTypeError(
            f"resource_types must be a sequence of types, not the string {resource_types!r}. "
            f"Pass [{resource_types!r}]."
        )
    if not resource_types:
        # No type means every type: CAI searches all supported asset types when
        # `asset_types` is empty. Only usable because noise reduction makes the
        # result readable -- the two ship together for that reason.
        return []
    resolved = [resolve_asset_type(t) for t in resource_types]
    return list(dict.fromkeys(resolved))


def validate_scope(scope: str) -> str:
    """Check the scope is well-formed before spending an API call on it."""
    if not SCOPE_PATTERN.match(scope):
        raise InvalidScopeError(
            f"Invalid scope {scope!r}. Expected organizations/<id>, folders/<id>, or projects/<id>."
        )
    return scope


def _project_id_of(result: asset_v1.ResourceSearchResult) -> str | None:
    """Recover the human-readable project ID, which CAI does not report directly.

    A Project asset carries it in additional_attributes; everything else carries
    it in the parent path. Resources nested below a project (a BigQuery table
    under a dataset, say) have a non-project parent, and get None rather than a
    guess.
    """
    attributes = dict(result.additional_attributes) if result.additional_attributes else {}
    if project_id := attributes.get("projectId"):
        return str(project_id)

    match = _PROJECT_PARENT.match(result.parent_full_resource_name or "")
    if match:
        return match.group("project_id")

    # Fall back to the resource's own name. Deliberately not the parent's: a
    # nested resource's parent is another resource, whose path also carries the
    # project, so either works -- but the resource's own name is always present.
    match = _PROJECT_IN_NAME.search(result.name or "")
    return match.group("project_id") if match else None


def _to_resource(result: asset_v1.ResourceSearchResult) -> Resource:
    """Flatten a CAI search result into our own model."""
    return Resource(
        project_id=_project_id_of(result),
        full_name=result.name,
        asset_type=result.asset_type,
        display_name=result.display_name or None,
        # CAI reports this as "projects/<number>"; keep the trailing segment.
        project=result.project.split("/")[-1] if result.project else None,
        location=result.location or None,
        state=result.state or None,
        labels=dict(result.labels),
        # Unset protobuf timestamps surface as None rather than epoch zero.
        create_time=result.create_time if "create_time" in result else None,
        parent_full_resource_name=result.parent_full_resource_name or None,
    )


# Shared response cache. Inert unless a caller sets a TTL.
_RESPONSE_CACHE = TTLCache()


def cache_stats() -> tuple[int, int]:
    """Hits and misses, for benchmarking."""
    return _RESPONSE_CACHE.hits, _RESPONSE_CACHE.misses


def _cache_key(filters: SearchFilters) -> str:
    """Everything that changes the answer, and nothing that does not."""
    return repr(
        (
            filters.scope,
            tuple(filters.resource_types),
            filters.free_text,
            tuple(filters.labels),
            tuple(filters.locations),
            tuple(filters.projects),
            filters.raw_query,
            filters.limit,
            filters.show_all,
            tuple(filters.sort),
            filters.include_iam,
        )
    )


# Project number -> ID, keyed by scope. Built with one CAI call per scope, not
# one per project: resolving 2000 projects individually is precisely the
# per-project fan-out the PRD rejects.
_PROJECT_IDS_BY_SCOPE: dict[str, dict[str, str]] = {}


def project_ids_for_scope(scope: str, client: asset_v1.AssetServiceClient) -> dict[str, str]:
    """Map project number -> project ID for every project visible in a scope.

    One call regardless of how many projects the scope holds, because a CAI
    Project asset carries both: the ID in `additional_attributes.projectId` and
    the number in `project`.
    """
    if scope in _PROJECT_IDS_BY_SCOPE:
        return _PROJECT_IDS_BY_SCOPE[scope]

    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        asset_types=["cloudresourcemanager.googleapis.com/Project"],
        page_size=DEFAULT_PAGE_SIZE,
    )
    mapping: dict[str, str] = {}
    for result in client.search_all_resources(request=request):
        attributes = dict(result.additional_attributes) if result.additional_attributes else {}
        project_id = attributes.get("projectId")
        if project_id and result.project:
            mapping[result.project.split("/")[-1]] = str(project_id)

    _PROJECT_IDS_BY_SCOPE[scope] = mapping
    return mapping


def _fill_project_ids(
    resources: list[Resource], scope: str, client: asset_v1.AssetServiceClient
) -> list[Resource]:
    """Recover project IDs that were not derivable from a resource's own paths.

    Some resources report only the project number anywhere in their payload
    (workload identity pools, for one). Left alone they appear in a summary as
    a separate row from the same project's named resources, splitting one
    project in two -- which is wrong, not merely ugly.
    """
    unresolved = [r for r in resources if not r.project_id and r.project]
    if not unresolved:
        return resources

    try:
        mapping = project_ids_for_scope(scope, client)
    except gcp_exceptions.GoogleAPICallError:
        # Best-effort enrichment: a display nicety must never fail a search.
        # The number is still shown, which is accurate if less readable.
        return resources

    return [
        r.model_copy(update={"project_id": mapping[r.project]})
        if (not r.project_id and r.project in mapping)
        else r
        for r in resources
    ]


def resolve_project_filter(
    scope: str,
    project: str,
    client: asset_v1.AssetServiceClient,
) -> str:
    """Turn a project ID into the project NUMBER that CAI's `project:` field matches.

    Verified against the live API: `project:my-project-id` matches nothing,
    while `project:123456789` matches. Passing an ID through unchanged returns
    an empty result rather than an error -- the silent-wrong-answer failure this
    tool exists to avoid -- so IDs are resolved rather than forwarded.

    Costs one extra CAI call per distinct ID. The mapping is immutable in GCP,
    so it is cached for the life of the process.
    """
    if _PROJECT_NUMBER.match(project):
        return project

    if cached := _PROJECT_NUMBERS.get(project):
        return cached

    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        asset_types=["cloudresourcemanager.googleapis.com/Project"],
        query=f'name:"{project}"',
    )
    for result in client.search_all_resources(request=request):
        # `name:` is a word match, so confirm the exact ID rather than trusting it.
        attributes = dict(result.additional_attributes) if result.additional_attributes else {}
        if attributes.get("projectId") == project and result.project:
            number = result.project.split("/")[-1]
            _PROJECT_NUMBERS[project] = number
            return number

    raise InvalidFilterError(
        f"Could not resolve project {project!r} to a project number within {scope}. "
        "Cloud Asset Inventory matches projects by number, and the project must be "
        "visible in the scope being searched. Pass the project number directly if you "
        "know it."
    )


def build_order_by(sort: Sequence[str]) -> str:
    """Compile sort terms into CAI's `order_by`, rejecting unknown fields.

    Sorting is done by CAI, not locally: a locally sorted page is only the
    first page in arbitrary order, then sorted -- which looks right and is
    wrong. Unknown fields are rejected rather than dropped, since a silently
    ignored sort produces plausible output in the wrong order.
    """
    terms = []
    for term in sort:
        field_name, _, direction = term.strip().partition(" ")
        if field_name not in SORTABLE_FIELDS:
            raise InvalidFilterError(
                f"Cannot sort by {field_name!r}. Cloud Asset Inventory sorts on: "
                f"{', '.join(SORTABLE_FIELDS)}."
            )
        direction = direction.strip().upper()
        if direction not in ("", "ASC", "DESC"):
            raise InvalidFilterError(
                f"Invalid sort direction {direction!r} in {term!r}. Use ASC or DESC."
            )
        terms.append(f"{field_name} {direction}".strip())
    return ", ".join(terms)


def _translate(exc: gcp_exceptions.GoogleAPICallError, scope: str) -> ResourceExplorerError:
    """Map a Google exception onto a domain error.

    Shared by every CAI call, so a failure during project resolution reports
    the same way as one during the search itself.
    """
    if isinstance(exc, gcp_exceptions.PermissionDenied):
        # Google returns 403 both for a disabled API and for a genuine IAM
        # failure. Check the structured reason before assuming which.
        disabled = _service_disabled_error(exc)
        if disabled is not None:
            return disabled
        return ScopeAccessDenied(
            f"Permission denied on {scope}. The cloudasset.assets.searchAllResources "
            "permission (roles/cloudasset.viewer) is required on that scope. "
            f"Upstream said: {exc.message}"
        )
    if isinstance(exc, gcp_exceptions.NotFound):
        return ScopeNotFound(f"Scope {scope} was not found.")
    if isinstance(exc, gcp_exceptions.InvalidArgument):
        # CAI also returns this for an asset-type pattern matching nothing.
        return InvalidFilterError(f"Cloud Asset Inventory rejected the request: {exc.message}")
    return UpstreamError(f"Cloud Asset Inventory call failed: {exc.message}")


def stream_resources(
    filters: SearchFilters,
    client: asset_v1.AssetServiceClient | None = None,
) -> Iterator[Resource]:
    """Yield matching resources one at a time.

    The generator form exists so a 2000-project result set need not be fully
    materialised before the first row is available. `search_resources` wraps
    this when a list is wanted; the streaming API endpoint consumes it directly.

    Note what is NOT yielded: noise-suppressed resources, and anything past
    `limit`. Callers needing the suppression counts use `search_resources`,
    which can report them once the stream is exhausted.
    """
    prepared = _prepare(filters, client)
    yield from prepared.iterate()


@dataclass
class _Prepared:
    """A validated, compiled search, ready to execute."""

    request: asset_v1.SearchAllResourcesRequest
    asset_types: list[str]
    query: str
    client: asset_v1.AssetServiceClient
    filters: SearchFilters
    noise: NoiseFilter

    def iterate(self) -> Iterator[Resource]:
        limit = self.filters.limit
        yielded = 0
        try:
            for item in self.client.search_all_resources(request=self.request):
                resource = _to_resource(item)
                if not self.noise.keep(resource):
                    continue
                if limit is not None and yielded == limit:
                    self.truncated = True
                    return
                yielded += 1
                yield resource
        except gcp_exceptions.GoogleAPICallError as exc:
            raise _translate(exc, self.filters.scope) from exc

    truncated: bool = False


def _prepare(filters: SearchFilters, client: asset_v1.AssetServiceClient | None) -> _Prepared:
    """Validate, resolve and compile everything before the first API call."""
    validate_scope(filters.scope)
    asset_types = resolve_asset_types(filters.resource_types)
    client = client or get_client()

    # Project IDs must become numbers before the query is built, so the query
    # echoed back to the caller is the one actually sent.
    try:
        projects = [
            resolve_project_filter(filters.scope, project, client) for project in filters.projects
        ]
    except gcp_exceptions.GoogleAPICallError as exc:
        raise _translate(exc, filters.scope) from exc

    try:
        query = build_query(
            free_text=filters.free_text,
            labels=filters.labels,
            locations=filters.locations,
            projects=projects,
            raw=filters.raw_query,
        )
    except QueryError as exc:
        raise InvalidFilterError(str(exc)) from exc

    return _Prepared(
        request=asset_v1.SearchAllResourcesRequest(
            scope=filters.scope,
            asset_types=asset_types,
            query=query,
            page_size=filters.page_size,
            order_by=build_order_by(filters.sort),
        ),
        asset_types=asset_types,
        query=query,
        client=client,
        filters=filters,
        noise=NoiseFilter(enabled=not filters.show_all),
    )


def search_resources(
    filters: SearchFilters,
    client: asset_v1.AssetServiceClient | None = None,
) -> SearchResult:
    """Search a scope for resources matching `filters`.

    Filters are compiled into a CAI query and evaluated upstream. Returns the
    resources found, whether `limit` truncated them, and the query that was
    actually sent -- the last so both surfaces can show it, since a filter that
    silently compiles to the wrong thing is otherwise invisible.

    Relies on locally authenticated ADC (`gcloud auth application-default login`).
    """
    if filters.cache_ttl > 0:
        _RESPONSE_CACHE.ttl = filters.cache_ttl
        if (cached := _RESPONSE_CACHE.get(_cache_key(filters))) is not None:
            return cached

    prepared = _prepare(filters, client)
    results = list(prepared.iterate())
    results = _fill_project_ids(results, filters.scope, prepared.client)

    iam_note = None
    if filters.include_iam:
        # Enrichment, not discovery: one extra call for the scope, joined onto
        # a set the search already narrowed.
        try:
            bindings = fetch_iam_bindings(filters.scope, prepared.asset_types, prepared.client)
        except gcp_exceptions.GoogleAPICallError as exc:
            raise _translate(exc, filters.scope) from exc
        results = attach_iam(results, bindings)
        iam_note = ATTACHED_IAM_NOTE

    result = SearchResult(
        resources=results,
        truncated=prepared.truncated,
        query=prepared.query,
        asset_types=prepared.asset_types,
        suppressed=prepared.noise.suppressed,
        suppressed_summary=prepared.noise.summary(),
        iam_note=iam_note,
    )
    if filters.cache_ttl > 0:
        _RESPONSE_CACHE.put(_cache_key(filters), result)
    return result
