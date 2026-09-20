"""Cloud Asset Inventory access.

The only module that talks to GCP. The CLI and the API are both thin wrappers
over `search_resources`, so any new capability belongs here rather than in
either surface.

Architectural rule: never iterate per-project service APIs to *find* resources.
Discovery is always a single scope-wide CAI search. See docs/PRD.md.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from google.api_core import exceptions as gcp_exceptions
from google.cloud import asset_v1

from app.models import Resource
from app.params import SearchFilters
from app.query import QueryError, build_query

# Friendly names mapped to CAI asset types, loaded from data rather than
# written as a literal so scripts/ can read the same file with jq. The mapping
# previously existed twice -- here and in bash -- which is exactly the kind of
# duplication that drifts silently.
ASSET_TYPES_FILE = Path(__file__).with_name("asset_types.json")


def _load_asset_types() -> dict[str, str]:
    with ASSET_TYPES_FILE.open() as handle:
        return dict(json.load(handle)["types"])


ASSET_TYPES: dict[str, str] = _load_asset_types()

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


@lru_cache(maxsize=1)
def get_client() -> asset_v1.AssetServiceClient:
    """Return a shared CAI client.

    Constructing a client pays channel and TLS setup, so it is built once and
    reused rather than per call. The client is thread-safe, which matters
    because FastAPI runs the synchronous endpoints in a threadpool.
    """
    return asset_v1.AssetServiceClient()


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
        raise UnknownResourceTypeError(
            "At least one resource type is required. "
            f"Choose from: {', '.join(sorted(ASSET_TYPES))}."
        )
    resolved = [resolve_asset_type(t) for t in resource_types]
    return list(dict.fromkeys(resolved))


def validate_scope(scope: str) -> str:
    """Check the scope is well-formed before spending an API call on it."""
    if not SCOPE_PATTERN.match(scope):
        raise InvalidScopeError(
            f"Invalid scope {scope!r}. Expected organizations/<id>, folders/<id>, or projects/<id>."
        )
    return scope


def _to_resource(result: asset_v1.ResourceSearchResult) -> Resource:
    """Flatten a CAI search result into our own model."""
    return Resource(
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
    validate_scope(filters.scope)
    asset_types = resolve_asset_types(filters.resource_types)

    try:
        query = build_query(
            free_text=filters.free_text,
            labels=filters.labels,
            locations=filters.locations,
            projects=filters.projects,
            raw=filters.raw_query,
        )
    except QueryError as exc:
        raise InvalidFilterError(str(exc)) from exc

    client = client or get_client()

    request = asset_v1.SearchAllResourcesRequest(
        scope=filters.scope,
        asset_types=asset_types,
        query=query,
        page_size=filters.page_size,
    )

    limit = filters.limit
    try:
        pager = client.search_all_resources(request=request)
        # Take one more than asked for, so truncation is detected without
        # walking the remainder of a potentially very large result set.
        window = itertools.islice(pager, limit + 1) if limit is not None else pager
        results = [_to_resource(item) for item in window]
    except gcp_exceptions.PermissionDenied as exc:
        raise ScopeAccessDenied(
            f"Permission denied on {filters.scope}. The cloudasset.assets.searchAllResources "
            "permission (roles/cloudasset.viewer) is required on that scope."
        ) from exc
    except gcp_exceptions.NotFound as exc:
        raise ScopeNotFound(f"Scope {filters.scope} was not found.") from exc
    except gcp_exceptions.InvalidArgument as exc:
        # CAI also returns this for an asset-type pattern matching nothing.
        raise InvalidFilterError(
            f"Cloud Asset Inventory rejected the request: {exc.message}"
        ) from exc
    except gcp_exceptions.GoogleAPICallError as exc:
        raise UpstreamError(f"Cloud Asset Inventory call failed: {exc.message}") from exc

    truncated = limit is not None and len(results) > limit
    if truncated:
        results = results[:limit]

    return SearchResult(
        resources=results, truncated=truncated, query=query, asset_types=asset_types
    )
