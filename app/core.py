"""Cloud Asset Inventory access.

The only module that talks to GCP. The CLI and the API are both thin wrappers
over `search_resources`, so any new capability belongs here rather than in
either surface.

Architectural rule: never iterate per-project service APIs to *find* resources.
Discovery is always a single scope-wide CAI search. See docs/PRD.md.
"""

from __future__ import annotations

import itertools
import re
from functools import lru_cache

from google.api_core import exceptions as gcp_exceptions
from google.cloud import asset_v1

from app.models import Resource

# Friendly names mapped to CAI asset types. One entry lights up both the CLI
# and the API, since both validate against this dict.
ASSET_TYPES: dict[str, str] = {
    "bucket": "storage.googleapis.com/Bucket",
    "cloudrun": "run.googleapis.com/Service",
}

# CAI accepts an organization, folder, or project as a search scope.
SCOPE_PATTERN = re.compile(r"^(organizations|folders|projects)/[a-zA-Z0-9][a-zA-Z0-9\-_.]*$")

# Results returned per upstream page. Tuning knob, not a cap on total results.
DEFAULT_PAGE_SIZE = 500

# Default ceiling on total results, so an unqualified org-wide search cannot
# stream an unbounded set into memory. Callers can raise or remove it; when it
# bites, the result is flagged `truncated` rather than silently shortened.
DEFAULT_LIMIT = 1000


class ResourceExplorerError(Exception):
    """Base class for errors this tool raises deliberately."""

    code = "internal_error"


class UnknownResourceTypeError(ResourceExplorerError):
    code = "unknown_resource_type"


class InvalidScopeError(ResourceExplorerError):
    code = "invalid_scope"


class ScopeAccessDenied(ResourceExplorerError):
    code = "permission_denied"


class ScopeNotFound(ResourceExplorerError):
    code = "scope_not_found"


class UpstreamError(ResourceExplorerError):
    code = "upstream_error"


@lru_cache(maxsize=1)
def get_client() -> asset_v1.AssetServiceClient:
    """Return a shared CAI client.

    Constructing a client pays channel and TLS setup, so it is built once and
    reused rather than per call. The client is thread-safe, which matters
    because FastAPI runs the synchronous endpoints in a threadpool.
    """
    return asset_v1.AssetServiceClient()


def resolve_asset_type(resource_type: str) -> str:
    """Map a friendly name to a CAI asset type, or raise."""
    try:
        return ASSET_TYPES[resource_type]
    except KeyError:
        raise UnknownResourceTypeError(
            f"Unsupported resource type {resource_type!r}. "
            f"Choose from: {', '.join(sorted(ASSET_TYPES))}"
        ) from None


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
    scope: str,
    resource_type: str,
    query: str = "",
    limit: int | None = DEFAULT_LIMIT,
    page_size: int = DEFAULT_PAGE_SIZE,
    client: asset_v1.AssetServiceClient | None = None,
) -> tuple[list[Resource], bool]:
    """Search a scope for resources of one type.

    Returns the resources found and whether `limit` truncated the result set.
    Relies on locally authenticated ADC (`gcloud auth application-default login`).
    """
    validate_scope(scope)
    asset_type = resolve_asset_type(resource_type)
    client = client or get_client()

    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        asset_types=[asset_type],
        query=query,
        page_size=page_size,
    )

    try:
        pager = client.search_all_resources(request=request)
        # Take one more than asked for, so truncation is detected without
        # walking the remainder of a potentially very large result set.
        window = itertools.islice(pager, limit + 1) if limit is not None else pager
        results = [_to_resource(item) for item in window]
    except gcp_exceptions.PermissionDenied as exc:
        raise ScopeAccessDenied(
            f"Permission denied on {scope}. The cloudasset.assets.searchAllResources "
            "permission (roles/cloudasset.viewer) is required on that scope."
        ) from exc
    except gcp_exceptions.NotFound as exc:
        raise ScopeNotFound(f"Scope {scope} was not found.") from exc
    except gcp_exceptions.InvalidArgument as exc:
        raise InvalidScopeError(
            f"Cloud Asset Inventory rejected the request: {exc.message}"
        ) from exc
    except gcp_exceptions.GoogleAPICallError as exc:
        raise UpstreamError(f"Cloud Asset Inventory call failed: {exc.message}") from exc

    truncated = limit is not None and len(results) > limit
    if truncated:
        results = results[:limit]
    return results, truncated
