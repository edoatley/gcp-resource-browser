"""Cloud Asset Inventory access, plus the IAM role catalogue and billing IAM.

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

import google.auth
from google.api_core import exceptions as gcp_exceptions
from google.cloud import asset_v1
from google.rpc.error_details_pb2 import ErrorInfo

from app import role_risk
from app.aggregate import ATTACHED_IAM_NOTE, attach_iam, fetch_iam_bindings, group_grants
from app.cache import TTLCache
from app.config import load_config
from app.models import (
    BillingGrantList,
    Grant,
    GrantList,
    Resource,
    RiskSource,
    RiskyPermission,
    RiskyRole,
    RoleList,
    SessionContext,
    VisibleProject,
)
from app.noise import NoiseFilter
from app.params import (
    DEFAULT_PAGE_SIZE,
    SORTABLE_FIELDS,
    BillingGrantFilters,
    GrantFilters,
    SearchFilters,
)
from app.query import QueryError, build_query, grant_query

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
    # None unless effective IAM was requested; then resource -> why its
    # inherited bindings could not be read ({} when all were).
    iam_errors: dict[str, str] | None = None


@lru_cache(maxsize=1)
def adc_quota_project() -> str | None:
    """The project ADC bills CAI calls to, or None if it cannot be read.

    Reads local credentials only; no network call. Used to make a rendered
    gcloud command bill where this tool does: the gcloud CLI keeps its own
    config, whose project often lacks the Cloud Asset API, and the resulting
    SERVICE_DISABLED 403 looks like the command is wrong when it is not.
    """
    try:
        credentials, _ = google.auth.default()
    except Exception:  # noqa: BLE001 - best-effort display detail, never fatal
        return None
    return getattr(credentials, "quota_project_id", None)


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
    return _api_not_enabled(service, consumer)


def _api_not_enabled(service: str, consumer: str) -> ApiNotEnabledError:
    consumer = consumer.removeprefix("projects/")
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
            filters.effective_iam,
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
    if isinstance(exc, gcp_exceptions.InvalidArgument) and "invalid scope" in exc.message.lower():
        # CAI's message blanks the scope ("Invalid scope . Please provide..."),
        # and reports a nonexistent project this way rather than as NotFound.
        # Name the scope, or with --also-scope the reader cannot tell which
        # of several it means.
        return InvalidScopeError(
            f"Cloud Asset Inventory does not recognise scope {scope}: check that it exists "
            f"and is spelled correctly. Upstream said: {exc.message}"
        )
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

    Deliberately not itself a generator: validation and query compilation run
    here, at call time, so a bad scope or type raises before a caller has
    committed to a streamed 200. As a generator, they ran on first iteration --
    after the response had started -- and a malformed request came back as an
    empty 200, indistinguishable from "nothing matched".
    """
    return _prepare(filters, client).iterate()


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
    if filters.effective_iam and not filters.include_iam:
        # Rejected rather than ignored: a caller asking for effective IAM and
        # getting no IAM at all might read the absence as "nothing granted".
        raise InvalidFilterError("effective requires include_iam: it extends the IAM attached.")
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

    iam_errors = None
    if filters.effective_iam:
        from app.effective import attach_inherited  # imported here: it calls back into core

        results, iam_errors = attach_inherited(results, filters.scope, prepared.client)
        iam_note = effective_iam_note(filters.scope, iam_errors)

    result = SearchResult(
        resources=results,
        truncated=prepared.truncated,
        query=prepared.query,
        asset_types=prepared.asset_types,
        suppressed=prepared.noise.suppressed,
        suppressed_summary=prepared.noise.summary(),
        iam_note=iam_note,
        iam_errors=iam_errors,
    )
    if filters.cache_ttl > 0:
        _RESPONSE_CACHE.put(_cache_key(filters), result)
    return result


# --- Effective (inherited) IAM ------------------------------------------------
#
# CAI's batchGetEffectiveIamPolicies returns, for each named resource, the
# policy attached to it and those attached to its ancestors -- but only "up to
# the scope" (the API's own words). A project scope therefore shows what a
# resource inherits from its project, never what the project inherits from a
# folder or organization. That bound is the API's, and every note says so.

# The API's per-call cap on names. Verified live: 21 names is InvalidArgument.
EFFECTIVE_IAM_BATCH = 20

_EFFECTIVE_REMEDY = (
    "Reading effective IAM needs cloudasset.assets.analyzeIamPolicy, searchAllResources and "
    "searchAllIamPolicies on the scope; roles/cloudasset.viewer holds all three."
)


def effective_iam_policies(
    scope: str, names: Sequence[str], client: asset_v1.AssetServiceClient | None = None
) -> list[asset_v1.BatchGetEffectiveIamPoliciesResponse.EffectiveIamPolicy]:
    """Each resource's own policy and its ancestors' policies, up to `scope`.

    One call, at most `EFFECTIVE_IAM_BATCH` names. Every name must be inside
    the scope and known to CAI's resource search; one that is not fails the
    whole call with NotFound (verified live), which callers split on.
    """
    if len(names) > EFFECTIVE_IAM_BATCH:
        raise ValueError(f"At most {EFFECTIVE_IAM_BATCH} names per call, got {len(names)}")
    request = asset_v1.BatchGetEffectiveIamPoliciesRequest(scope=scope, names=list(names))
    try:
        response = (client or get_client()).batch_get_effective_iam_policies(request=request)
    except gcp_exceptions.NotFound as exc:
        # Not the scope: _translate's "Scope not found" would blame the wrong thing.
        raise ScopeNotFound(
            f"Cloud Asset Inventory could not find a resource in {scope}. "
            f"Upstream said: {exc.message}"
        ) from exc
    except gcp_exceptions.PermissionDenied as exc:
        if (disabled := _service_disabled_error(exc)) is not None:
            raise disabled from exc
        raise ScopeAccessDenied(
            f"Permission denied reading effective IAM in {scope}. {_EFFECTIVE_REMEDY} "
            f"Upstream said: {exc.message}"
        ) from exc
    except gcp_exceptions.GoogleAPICallError as exc:
        raise _translate(exc, scope) from exc
    return list(response.policy_results)


def effective_iam_note(scope: str, errors: dict[str, str]) -> str:
    """What `--include-iam --effective` returned. Stated on every such response."""
    if scope.startswith("organizations/"):
        reach = f"its ancestors up to and including {scope}, the top of the hierarchy"
        above = ""
    else:
        reach = f"its ancestors up to and including {scope}"
        above = (
            f" Grants made above {scope}, on its folders or organization, are NOT included: "
            "Cloud Asset Inventory stops at the search scope, so search a wider one to see them."
        )
    note = (
        f"Bindings attached to each resource, plus those it inherits from {reach}. Inherited "
        f"bindings carry `inherited_from`, the resource they are attached to.{above}"
    )
    if errors:
        note += (
            f" Inherited bindings could not be read for {len(errors)} resource(s), which show "
            "attached bindings only; they are listed in `iam_errors`."
        )
    return note


# --- Role risk -------------------------------------------------------------

IAM_API_URL = "https://iam.googleapis.com/v1"
IAM_ROLES_URL = f"{IAM_API_URL}/roles"

# The predefined role catalogue: ~2,400 roles, ~10 MB, 3 pages. Cached for a
# day. This is the ONE cache that is on by default, and deliberately so: it is
# Google's reference data about what roles contain, not the customer's estate,
# so a day-old copy cannot hide a fix someone is verifying. Search results stay
# uncached by default (see app/cache.py).
ROLE_CATALOGUE_TTL = 24 * 60 * 60
_ROLE_CATALOGUE = TTLCache(ttl_seconds=ROLE_CATALOGUE_TTL)


@lru_cache(maxsize=1)
def get_rest_session():
    """An authorised HTTP session for Google's REST APIs, on the same ADC as CAI.

    Shared by the IAM role catalogue and the Cloud Billing API: both are plain
    REST, both authenticate from the same credentials, and building one session
    means one place where the auth scope is decided.
    """
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(credentials)


def _rest_error(response, what: str, service: str, remedy: str = "") -> ResourceExplorerError:
    """Translate a failed REST response, keeping Google's own message.

    Mirrors `_translate` for the gRPC path, including the SERVICE_DISABLED
    case: Google returns 403 both for "you lack the role" and for "the API is
    off on the project this bills to", and the two have different fixes on
    often-different projects.
    """
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    message = error.get("message") or f"HTTP {response.status_code}"
    for detail in error.get("details", []):
        if detail.get("reason") == "SERVICE_DISABLED":
            metadata = detail.get("metadata", {})
            return _api_not_enabled(
                metadata.get("service", service),
                metadata.get("consumer", "your quota project"),
            )
    if response.status_code == 403:
        return ScopeAccessDenied(f"{what} was denied: {message}{remedy}")
    if response.status_code == 404:
        return ScopeNotFound(f"{what} found nothing: {message}")
    return UpstreamError(f"{what} failed: {message}")


def _paginate(
    session, url: str, params: dict, key: str, what: str, service: str, remedy: str = ""
) -> list[dict]:
    """Collect every page of a Google REST list call into one list."""
    items: list[dict] = []
    page_token = None
    while True:
        page_params = dict(params)
        if page_token:
            page_params["pageToken"] = page_token
        response = session.get(url, params=page_params)
        if not response.ok:
            raise _rest_error(response, what, service, remedy)
        body = response.json()
        items.extend(body.get(key, []))
        page_token = body.get("nextPageToken")
        if not page_token:
            break
    return items


def fetch_role_catalogue(session=None) -> list[dict]:
    """Every predefined role with its permissions, from the IAM API (cached)."""
    if (cached := _ROLE_CATALOGUE.get("predefined")) is not None:
        return cached

    roles = _paginate(
        session or get_rest_session(),
        IAM_ROLES_URL,
        # view=FULL is load-bearing: the default BASIC view omits
        # includedPermissions, which would classify every role as safe.
        {"view": "FULL", "pageSize": 1000},
        key="roles",
        what="IAM role listing",
        service="iam.googleapis.com",
    )
    _ROLE_CATALOGUE.put("predefined", roles)
    return roles


# IAM supports custom roles on an organization and on a project, and nowhere
# else -- notably NOT on a folder. A folder scope is therefore rejected rather
# than answered with predefined roles only, which would read as "this folder
# has no risky custom roles" when the question was never asked.
CUSTOM_ROLE_LEVELS = ("organizations", "projects")


def validate_role_scope(scope: str) -> str:
    validate_scope(scope)
    level = scope.split("/", 1)[0]
    if level not in CUSTOM_ROLE_LEVELS:
        raise InvalidFilterError(
            f"Custom roles cannot be defined on a folder, so {scope} has none to list. "
            "Pass the organization, or a project, instead."
        )
    return scope


def fetch_custom_roles(scope: str, session=None) -> list[dict]:
    """Custom roles defined ON `scope` itself, with their permissions.

    Deliberately uncached, unlike the predefined catalogue: these are the
    customer's own definitions, edited by the customer, so a day-old copy
    could hide exactly the change someone is verifying. Deleted roles are
    excluded -- `showDeleted` is left off.
    """
    return _paginate(
        session or get_rest_session(),
        f"{IAM_API_URL}/{scope}/roles",
        {"view": "FULL", "pageSize": 1000},
        key="roles",
        what=f"Custom role listing on {scope}",
        service="iam.googleapis.com",
    )


def _with_downgrades(note: str) -> str:
    """A coverage note, plus any site downgrade of the risk rules."""
    downgrades = role_risk.downgrade_note()
    return f"{note} {downgrades}" if downgrades else note


def _roles_coverage_note(scope: str | None) -> str:
    """Which roles the listing could see. Stated on every response."""
    if scope is None:
        return (
            "Predefined roles only. Custom roles are not listed; pass a scope (an "
            "organization or a project) to include the custom roles defined on it. "
            "`gcpe grants` matches custom roles either way, because it matches on "
            "permissions rather than on names."
        )
    if scope.startswith("organizations/"):
        return (
            f"Predefined roles, plus the custom roles defined on {scope}. Custom roles "
            "defined inside the projects beneath it are NOT listed: that would take one "
            "call per project, which this tool does not do. Pass a project scope to list "
            "its own."
        )
    return (
        f"Predefined roles, plus the custom roles defined on {scope}. Custom roles defined "
        "on its organization are not listed here, though they can be granted here; pass "
        "the organization to list those."
    )


def _parse_min_risk(value: str) -> role_risk.Risk:
    try:
        return role_risk.parse_risk(value)
    except role_risk.RiskRuleError as exc:
        raise InvalidFilterError(str(exc)) from exc


def _risky_permission(rule: role_risk.RiskRule) -> RiskyPermission:
    return RiskyPermission(
        permission=rule.permission,
        risk=rule.risk.value,
        reason=rule.reason,
        sources=list(rule.sources),
    )


def list_risky_roles(
    min_risk: str = "high",
    show_all: bool = False,
    scope: str | None = None,
    session=None,
) -> RoleList:
    """Roles containing at least one permission at or above `min_risk`.

    Predefined roles always; the custom roles defined on `scope` as well when
    one is given. Both are classified by the same rules against the same
    permissions, so a custom role is judged on what it can do rather than on
    the fact that someone wrote it locally.

    Service-agent roles are hidden unless `show_all`, and always counted: they
    are granted by Google, not by the customer, and are most of the matches.
    The rule is not applied to custom roles -- a customer-written role named
    `...serviceAgent` is still the customer's to review.
    """
    minimum = _parse_min_risk(min_risk)
    if scope is not None:
        validate_role_scope(scope)

    candidates = [(raw, False) for raw in fetch_role_catalogue(session)]
    if scope is not None:
        candidates += [(raw, True) for raw in fetch_custom_roles(scope, session)]

    roles: list[RiskyRole] = []
    hidden = 0
    for raw, custom in candidates:
        matched = role_risk.classify(raw.get("includedPermissions", []), minimum)
        if not matched:
            continue
        if not show_all and not custom and role_risk.is_service_agent_role(raw["name"]):
            hidden += 1
            continue
        roles.append(
            RiskyRole(
                name=raw["name"],
                title=raw.get("title"),
                stage=raw.get("stage"),
                risk=role_risk.highest(matched).value,
                permissions=[_risky_permission(r) for r in matched],
                custom=custom,
            )
        )

    roles.sort(key=lambda r: (-role_risk.Risk(r.risk).rank, r.name))
    cited = {source for role in roles for perm in role.permissions for source in perm.sources}
    sources = role_risk.load_sources()
    return RoleList(
        min_risk=minimum.value,
        scope=scope,
        coverage_note=_with_downgrades(_roles_coverage_note(scope)),
        count=len(roles),
        suppressed=hidden,
        suppressed_summary=(
            f"{hidden} Google service-agent role(s) hidden. Use --show-all to include them."
            if hidden
            else ""
        ),
        data=roles,
        sources={
            key: RiskSource(title=src.title, url=src.url, quote=src.quote)
            for key, src in sorted(sources.items())
            if key in cited
        },
    )


# Stated on every grant response unless expansion was asked for, when
# `_expansion_note` replaces it with what was actually expanded.
MEMBERS_NOT_EXPANDED = (
    "Group membership is not expanded: a group grant applies to every member. Pass "
    "expand_members to see who is behind groups and projectOwner/Editor/Viewer members."
)


def _coverage_note(scope: str) -> str:
    """What a grant search at this scope cannot see. Stated on every response."""
    kind = scope.split("/", 1)[0]
    above = {
        "projects": (
            "Grants made on this project's folders or organization are not visible from a "
            "project scope, though they apply here; search the organization to include them."
        ),
        "folders": (
            "Grants made on parent folders or the organization are not visible from a folder "
            "scope, though they apply here; search the organization to include them."
        ),
        "organizations": "Covers the organization and everything beneath it.",
    }[kind]
    return (
        f"{above} Billing account IAM is not indexed by Cloud Asset Inventory and is not "
        "included here; run `gcpe billing-grants` (GET /v1/billing-grants) for it. "
        + MEMBERS_NOT_EXPANDED
    )


def _validate_grant_filters(
    min_risk: str, member_types: Sequence[str], group_by: str | None
) -> tuple[role_risk.Risk, list[str]]:
    """Validate what the two grant searches share. Both reject, neither ignores."""
    minimum = _parse_min_risk(min_risk)
    try:
        types = role_risk.validate_member_types(member_types)
    except role_risk.RiskRuleError as exc:
        raise InvalidFilterError(str(exc)) from exc
    if group_by not in (None, "member", "role"):
        raise InvalidFilterError(f"Cannot group by {group_by!r}. Choose from: member, role.")
    return minimum, types


def grant_queries(minimum: role_risk.Risk, member_types: Sequence[str]) -> list[str]:
    """The CAI IAM-policy queries a grant search sends, one per permission batch.

    Pure, so the capability catalogue can render the gcloud form of a grant
    search without running it, from the same compilation the search uses.
    """
    permissions = [r.permission for r in role_risk.rules_at_least(minimum)]
    batches = role_risk.permission_batches(permissions, len(member_types))
    return [grant_query(batch, member_types) for batch in batches]


def search_grants(
    filters: GrantFilters, client: asset_v1.AssetServiceClient | None = None
) -> GrantList:
    """Principals holding roles at or above `filters.min_risk`, across a scope.

    Matching is done by CAI (`policy.role.permissions:`), which covers custom
    roles too. CAI returns whole policies, so bindings are narrowed to the
    roles CAI reports as matched, and members to the requested types -- a
    narrowing of rows already fetched, never a broader fetch. Service agents
    are hidden client-side because IAM policy search has no negation.
    """
    validate_scope(filters.scope)
    minimum, member_types = _validate_grant_filters(
        filters.min_risk, filters.member_types, filters.group_by
    )

    rules = {r.permission: r for r in role_risk.rules_at_least(minimum)}
    queries = grant_queries(minimum, member_types)
    client = client or get_client()

    # (resource, role, member, condition) -> grant; batches overlap on roles
    # that hold permissions from several batches, so merge rather than repeat.
    found: dict[tuple[str, str, str, str], Grant] = {}
    hidden: set[tuple[str, str, str, str]] = set()
    for query in queries:
        request = asset_v1.SearchAllIamPoliciesRequest(scope=filters.scope, query=query)
        try:
            for result in client.search_all_iam_policies(request=request):
                matched = {
                    role: list(perms.permissions)
                    for role, perms in result.explanation.matched_permissions.items()
                }
                for binding in result.policy.bindings:
                    if binding.role not in matched:
                        continue
                    condition = binding.condition.expression or None
                    for member in binding.members:
                        kind = role_risk.member_type(member)
                        if member_types and kind not in member_types:
                            continue
                        key = (result.resource, binding.role, member, condition or "")
                        if not filters.show_all and role_risk.is_service_agent_grant(
                            member, binding.role
                        ):
                            hidden.add(key)
                            continue
                        perms = set(matched[binding.role])
                        if key in found:
                            perms |= set(found[key].matched_permissions)
                        hit = [rules[p] for p in perms if p in rules]
                        found[key] = Grant(
                            resource=result.resource,
                            asset_type=result.asset_type or None,
                            role=binding.role,
                            member=member,
                            member_type=kind,
                            risk=role_risk.highest(hit).value,
                            matched_permissions=sorted(perms),
                            condition=condition,
                        )
        except gcp_exceptions.GoogleAPICallError as exc:
            raise _translate(exc, filters.scope) from exc

    grants = sorted(
        found.values(),
        key=lambda g: (-role_risk.Risk(g.risk).rank, g.member, g.role, g.resource),
    )
    truncated = filters.limit is not None and len(grants) > filters.limit
    if truncated:
        grants = grants[: filters.limit]

    expansions = _expand(grants, filters.expand_members, client=client)

    return GrantList(
        scope=filters.scope,
        min_risk=minimum.value,
        member_types=member_types,
        queries=queries,
        count=len(grants),
        truncated=truncated,
        suppressed=len(hidden),
        suppressed_summary=(
            f"{len(hidden)} grant(s) to Google service agents hidden. "
            "Use --show-all to include them."
            if hidden
            else ""
        ),
        coverage_note=_with_downgrades(_expansion_note(_coverage_note(filters.scope), expansions)),
        grants=grants,
        groups=group_grants(grants, filters.group_by) if filters.group_by else None,
        expansions=expansions,
    )


# --- Billing account IAM ----------------------------------------------------
#
# Billing accounts are the one place a maximally dangerous role lives that
# `gcpe grants` cannot reach: Cloud Asset Inventory does not index them, and
# `billingAccounts/...` is not a valid CAI scope (verified: "Invalid scope").
# Billing Account Administrator can move an account, change who pays, and
# close billing on every project attached to it -- one of the two motivating
# examples for this phase -- so leaving it out would leave the obvious hole.
#
# The Cloud Billing API is the route, and it is bounded: one list call, then
# one getIamPolicy per account a caller can see. That is a handful of calls,
# not a per-project walk, so it does not run into what the PRD rejects.

BILLING_API_URL = "https://cloudbilling.googleapis.com/v1"
BILLING_SERVICE = "cloudbilling.googleapis.com"
BILLING_ASSET_TYPE = "cloudbilling.googleapis.com/BillingAccount"

BILLING_COVERAGE_NOTE = (
    "Every billing account this caller can see. Billing accounts are not under an "
    "organization in the IAM hierarchy, so no scope bounds them and this may be more, or "
    "fewer, accounts than the organization you searched for resources. Roles are classified "
    "from the predefined catalogue; a role that is not in it is reported in "
    "`unclassified_roles` rather than assumed safe. " + MEMBERS_NOT_EXPANDED
)

_BILLING_REMEDY = (
    ". Listing billing accounts and reading their IAM needs roles/billing.viewer (or "
    "Billing Account Viewer on each account), and cloudbilling.googleapis.com enabled on "
    "the project this request bills to."
)


def list_billing_accounts(session=None) -> list[dict]:
    """Every billing account the caller can see, newest API page order preserved."""
    return _paginate(
        session or get_rest_session(),
        f"{BILLING_API_URL}/billingAccounts",
        {"pageSize": 100},
        key="billingAccounts",
        what="Billing account listing",
        service=BILLING_SERVICE,
        remedy=_BILLING_REMEDY,
    )


def billing_account_policy(name: str, session=None) -> dict:
    """The IAM policy attached to one billing account (`billingAccounts/XXXXXX-...`)."""
    session = session or get_rest_session()
    response = session.get(f"{BILLING_API_URL}/{name}:getIamPolicy")
    if not response.ok:
        raise _rest_error(
            response, f"Reading IAM on {name}", BILLING_SERVICE, remedy=_BILLING_REMEDY
        )
    return response.json()


def _catalogue_permissions(session=None) -> dict[str, set[str]]:
    """Role name -> the permissions it contains, from the predefined catalogue.

    The billing path needs this because the Cloud Billing API returns role
    names only. CAI does the equivalent server-side for `gcpe grants`, which
    is why that path never needs the catalogue.
    """
    return {
        raw["name"]: set(raw.get("includedPermissions", []))
        for raw in fetch_role_catalogue(session)
    }


def search_billing_grants(filters: BillingGrantFilters, session=None) -> BillingGrantList:
    """Principals holding roles at or above `filters.min_risk` on a billing account.

    The same rules as `search_grants`, evaluated locally rather than by CAI:
    the Cloud Billing API reports role names, so risk comes from looking each
    role up in the predefined catalogue. A role that is not there -- a custom
    role granted on the account -- is counted and named, never dropped.
    """
    minimum, member_types = _validate_grant_filters(
        filters.min_risk, filters.member_types, filters.group_by
    )
    rules = {r.permission: r for r in role_risk.rules_at_least(minimum)}

    session = session or get_rest_session()
    accounts = list_billing_accounts(session)
    permissions_by_role = _catalogue_permissions(session) if accounts else {}

    found: list[Grant] = []
    hidden = 0
    unclassified: set[str] = set()
    for account in accounts:
        name = account["name"]
        policy = billing_account_policy(name, session)
        for binding in policy.get("bindings", []):
            role = binding.get("role", "")
            held = permissions_by_role.get(role)
            if held is None:
                unclassified.add(role)
                continue
            matched = sorted(held & set(rules))
            if not matched:
                continue
            risk = role_risk.highest(rules[p] for p in matched)
            condition = (binding.get("condition") or {}).get("expression") or None
            for member in binding.get("members", []):
                kind = role_risk.member_type(member)
                if member_types and kind not in member_types:
                    continue
                if not filters.show_all and role_risk.is_service_agent_grant(member, role):
                    hidden += 1
                    continue
                found.append(
                    Grant(
                        resource=f"//{BILLING_SERVICE}/{name}",
                        asset_type=BILLING_ASSET_TYPE,
                        display_name=account.get("displayName"),
                        role=role,
                        member=member,
                        member_type=kind,
                        risk=risk.value,
                        matched_permissions=matched,
                        condition=condition,
                    )
                )

    grants = sorted(
        found, key=lambda g: (-role_risk.Risk(g.risk).rank, g.member, g.role, g.resource)
    )
    truncated = filters.limit is not None and len(grants) > filters.limit
    if truncated:
        grants = grants[: filters.limit]

    expansions = _expand(grants, filters.expand_members, session=session)

    return BillingGrantList(
        min_risk=minimum.value,
        member_types=member_types,
        accounts=[a["name"] for a in accounts],
        count=len(grants),
        truncated=truncated,
        suppressed=hidden,
        suppressed_summary=(
            f"{hidden} grant(s) to Google service agents hidden. Use --show-all to include them."
            if hidden
            else ""
        ),
        unclassified_roles=sorted(unclassified),
        coverage_note=_with_downgrades(_expansion_note(BILLING_COVERAGE_NOTE, expansions)),
        grants=grants,
        groups=group_grants(grants, filters.group_by) if filters.group_by else None,
        expansions=expansions,
    )


# --- Session context and visible projects (the `gcpe docs` browser) ---------
#
# Both exist so the browser can show the logged-in session's real scope rather
# than a placeholder. Neither is reachable from `gcpe serve`.

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

# Credential class module -> how to describe it to a person.
_CREDENTIAL_TYPES = {
    "google.oauth2.credentials": "user",
    "google.oauth2.service_account": "service account",
    "google.auth.impersonated_credentials": "impersonated service account",
    "google.auth.compute_engine.credentials": "compute engine metadata server",
    "google.auth.identity_pool": "workload identity federation",
    "google.auth.external_account": "workload identity federation",
}


def _user_email(credentials) -> str | None:
    """A user credential's email, from one tokeninfo call. Raises on failure.

    User credentials carry no email locally. tokeninfo is an OAuth endpoint,
    not a Google Cloud API: it costs no quota and needs no API enabled.
    """
    from google.auth.transport.requests import AuthorizedSession, Request

    credentials.refresh(Request())
    response = AuthorizedSession(credentials).get(
        TOKENINFO_URL, params={"access_token": credentials.token}, timeout=5
    )
    response.raise_for_status()
    return response.json().get("email")


@lru_cache(maxsize=1)
def session_context() -> SessionContext:
    """The identity, quota project and default project this process runs as.

    Never raises: the browser must still load, and say how to log in, when ADC
    is missing. Everything is read locally except a user credential's email,
    which costs one best-effort tokeninfo call. Cached for the process, since
    the credentials cannot change underneath it.
    """
    try:
        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        return SessionContext(error=f"{type(exc).__name__}: {exc}")

    kind = _CREDENTIAL_TYPES.get(type(credentials).__module__, type(credentials).__name__)
    principal = getattr(credentials, "service_account_email", None)
    note = None
    if principal in (None, "default"):
        principal = None
        try:
            principal = _user_email(credentials)
        except Exception as exc:  # noqa: BLE001 - best-effort display detail
            note = f"Could not read the principal: {type(exc).__name__}: {exc}"
        if principal is None and note is None:
            note = "The credential does not expose an email address."

    return SessionContext(
        credential_type=kind,
        principal=principal,
        principal_note=note,
        quota_project=getattr(credentials, "quota_project_id", None),
        default_project=project,
    )


RESOURCE_MANAGER_URL = "https://cloudresourcemanager.googleapis.com/v3"
RESOURCE_MANAGER_SERVICE = "cloudresourcemanager.googleapis.com"

# The scope picker's list, cached briefly. A deliberate, narrow exception to
# "cache off": it fills a dropdown, it never answers an audit question, and a
# page that re-listed every project on each keystroke would be slow for a
# caller who can see thousands.
_VISIBLE_PROJECTS = TTLCache(ttl_seconds=300)

_PROJECTS_REMEDY = (
    ". Listing projects needs resourcemanager.projects.get on them (roles/browser grants "
    "it), and cloudresourcemanager.googleapis.com enabled on the project this request "
    "bills to."
)


def list_visible_projects(session=None) -> list[VisibleProject]:
    """Every active project the caller can see, for the browser's scope picker.

    One paginated Resource Manager search, not a per-project walk. CAI cannot
    answer this without a scope above the projects, and an estate with no
    organization has none.
    """
    if (cached := _VISIBLE_PROJECTS.get("projects")) is not None:
        return cached

    raw = _paginate(
        session or get_rest_session(),
        f"{RESOURCE_MANAGER_URL}/projects:search",
        {},
        key="projects",
        what="Project listing",
        service=RESOURCE_MANAGER_SERVICE,
        remedy=_PROJECTS_REMEDY,
    )
    projects = sorted(
        (
            VisibleProject(
                project_id=p["projectId"],
                number=p["name"].removeprefix("projects/"),
                display_name=p.get("displayName"),
            )
            for p in raw
            # Projects pending deletion are listed too; they cannot be searched.
            if p.get("state", "ACTIVE") == "ACTIVE"
        ),
        key=lambda p: p.project_id,
    )
    _VISIBLE_PROJECTS.put("projects", projects)
    return projects


# --- Who is behind a member (expand_members) ----------------------------------
#
# A grant to `group:ops@x` or `projectOwner:p` names one member that stands for
# many principals, and the headcount is usually the finding. These read who
# they are, one level deep, from attached policy only -- the same attached-only
# rule as every other IAM answer here. Opt-in: it turns an infrastructure
# audit into a list of named people.

CLOUD_IDENTITY_URL = "https://cloudidentity.googleapis.com/v1"
CLOUD_IDENTITY_SERVICE = "cloudidentity.googleapis.com"
PROJECT_ASSET_TYPE = "cloudresourcemanager.googleapis.com/Project"

_GROUPS_REMEDY = (
    ". Reading a group's members needs cloudidentity.googleapis.com enabled on the project "
    "this request bills to, and a Groups Reader role in the Cloud Identity or Workspace "
    "directory that owns the group -- a directory role, not a GCP one. Groups belonging to a "
    "personal Google account cannot be read through this API."
)


def group_members(email: str, session=None) -> list[str]:
    """Every principal in a group, nested groups flattened (Cloud Identity).

    Two calls: a lookup from email to the group's resource name, then a
    transitive membership search, which flattens nesting server-side so no
    cycle-safe walk is needed here.
    """
    session = session or get_rest_session()
    response = session.get(f"{CLOUD_IDENTITY_URL}/groups:lookup", params={"groupKey.id": email})
    if not response.ok:
        raise _rest_error(
            response, f"Looking up group {email}", CLOUD_IDENTITY_SERVICE, _GROUPS_REMEDY
        )
    name = response.json()["name"]
    memberships = _paginate(
        session,
        f"{CLOUD_IDENTITY_URL}/{name}/memberships:searchTransitiveMemberships",
        {},
        key="memberships",
        what=f"Reading the members of group {email}",
        service=CLOUD_IDENTITY_SERVICE,
        remedy=_GROUPS_REMEDY,
    )
    return sorted(
        {key["id"] for m in memberships for key in m.get("preferredMemberKey", []) if "id" in key}
    )


def project_role_members(
    project: str, role: str, client: asset_v1.AssetServiceClient | None = None
) -> list[str]:
    """Members holding `role` in the policy attached to the project itself.

    What `projectOwner:<project>` and its siblings stand for. Read through CAI,
    scoped to the project and to its own asset type, so it needs only the
    Cloud Asset Viewer this tool already requires. Inherited grants of the
    role, from a folder or organization, are not included.
    """
    scope = f"projects/{project}"
    request = asset_v1.SearchAllIamPoliciesRequest(scope=scope, asset_types=[PROJECT_ASSET_TYPE])
    try:
        results = list((client or get_client()).search_all_iam_policies(request=request))
    except gcp_exceptions.GoogleAPICallError as exc:
        raise _translate(exc, scope) from exc
    return sorted(
        {
            member
            for result in results
            if result.resource.endswith(f"/projects/{project}")
            for binding in result.policy.bindings
            if binding.role == role
            for member in binding.members
        }
    )


def _expand(grants: list[Grant], requested: bool, client=None, session=None):
    """Expansions for the members in `grants`, or None when not requested."""
    if not requested:
        return None
    from app.expansion import expand_members  # imported here: it calls back into core

    expansions = expand_members([g.member for g in grants], client=client, session=session)
    for grant in grants:
        found = expansions.get(grant.member)
        if found is not None:
            grant.member_count = found.count
    return expansions


def _expansion_note(note: str, expansions) -> str:
    """Replace the "not expanded" caveat with what expansion actually did."""
    if expansions is None:
        return note
    listed = [e for e in expansions.values() if e.members is not None]
    failed = sorted(m for m, e in expansions.items() if e.error)
    said = (
        f"Expanded {len(listed)} member(s) that stand for other principals, one level deep and "
        "from attached policy only: a group or project role inside one is listed, not expanded "
        "again, and owners inherited from a folder or organization are not included. Names "
        "are in `expansions`."
    )
    if failed:
        said += f" {len(failed)} could not be read and still apply in full: {', '.join(failed)}."
    return note.replace(MEMBERS_NOT_EXPANDED, said)
