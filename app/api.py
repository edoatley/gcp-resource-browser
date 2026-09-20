"""FastAPI surface.

A thin wrapper over `app.core`. Endpoints translate HTTP into a core call and
core errors into status codes; they contain no query logic of their own.

Endpoints are deliberately plain `def`, not `async def`. Starlette runs
non-async endpoints in a threadpool, so these already serve concurrent requests
without blocking the event loop, and the core stays synchronous. See Phase 7 of
docs/DELIVERY_PLAN.md before changing this.
"""

from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.requests import Request
from fastapi.responses import JSONResponse, StreamingResponse

from app import core
from app.aggregate import summarise
from app.models import ErrorResponse, ResourceList, Summary
from app.params import DEFAULT_LIMIT, Help, SearchFilters

# Precomputed for the OpenAPI descriptions below, which are evaluated at import.
_TYPE_NAMES = ", ".join(sorted(core.ASSET_TYPES))

app = FastAPI(
    title="GCP Resource Explorer API",
    description=(
        "Search Google Cloud resources across a project, folder, or organization "
        "via Cloud Asset Inventory."
    ),
    version="0.1.0",
)

# Core error -> HTTP status. Anything unmapped is a 500.
_STATUS_BY_ERROR: dict[type[core.ResourceExplorerError], int] = {
    core.UnknownResourceTypeError: 400,
    core.InvalidScopeError: 400,
    core.InvalidFilterError: 400,
    core.ScopeAccessDenied: 403,
    core.ScopeNotFound: 404,
    core.ApiNotEnabledError: 503,
    core.UpstreamError: 502,
}


@app.exception_handler(core.ResourceExplorerError)
def handle_explorer_error(request: Request, exc: core.ResourceExplorerError) -> JSONResponse:
    """Render deliberate errors as a typed body with a meaningful status."""
    status = _STATUS_BY_ERROR.get(type(exc), 500)
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(error=exc.code, detail=str(exc)).model_dump(),
    )


@app.get(
    "/v1/resources",
    response_model=ResourceList,
    responses={
        400: {"model": ErrorResponse, "description": "Unknown type, malformed scope or filter"},
        403: {"model": ErrorResponse, "description": "Caller lacks Cloud Asset Viewer"},
        404: {"model": ErrorResponse, "description": "Scope not found"},
        502: {"model": ErrorResponse, "description": "Cloud Asset Inventory call failed"},
        503: {"model": ErrorResponse, "description": "Cloud Asset API not enabled"},
    },
    summary="Search resources in a scope",
)
def search_resources(
    scope: str = Query(description=Help.SCOPE, examples=["projects/my-project"]),
    type: list[str] = Query(
        default_factory=list,
        # The surface adds what only it can say: the concrete name list, which
        # belongs in the schema but would bloat `--help`.
        description=f"{Help.TYPE} Friendly names: {_TYPE_NAMES}.",
        examples=[["bucket"]],
    ),
    q: str = Query(default="", description=Help.TERM),
    label: list[str] = Query(default_factory=list, description=Help.LABEL, examples=[["env=prod"]]),
    location: list[str] = Query(
        default_factory=list, description=Help.LOCATION, examples=[["europe-west2"]]
    ),
    project: list[str] = Query(default_factory=list, description=Help.PROJECT),
    raw_query: str = Query(default="", description=Help.RAW_QUERY),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=10_000, description=Help.LIMIT),
    show_all: bool = Query(default=False, description=Help.SHOW_ALL),
    sort: list[str] = Query(default_factory=list, description=Help.SORT, examples=[["name"]]),
    include_iam: bool = Query(default=False, description=Help.INCLUDE_IAM),
    cache_ttl: float = Query(default=0.0, ge=0, le=3600, description=Help.CACHE_TTL),
) -> ResourceList:
    result = core.search_resources(
        SearchFilters(
            scope=scope,
            resource_types=type,
            free_text=q,
            labels=label,
            locations=location,
            projects=project,
            raw_query=raw_query,
            limit=limit,
            show_all=show_all,
            sort=sort,
            include_iam=include_iam,
            cache_ttl=cache_ttl,
        )
    )
    return ResourceList(
        scope=scope,
        asset_types=result.asset_types,
        query=result.query,
        count=len(result.resources),
        truncated=result.truncated,
        suppressed=result.suppressed,
        suppressed_summary=result.suppressed_summary,
        iam_note=result.iam_note,
        data=result.resources,
    )


@app.get(
    "/v1/resources/stream",
    summary="Stream resources as newline-delimited JSON",
    response_class=StreamingResponse,
)
def stream_resources(
    scope: str = Query(description=Help.SCOPE, examples=["organizations/123"]),
    type: list[str] = Query(default_factory=list, description=Help.TYPE),
    q: str = Query(default="", description=Help.TERM),
    label: list[str] = Query(default_factory=list, description=Help.LABEL),
    location: list[str] = Query(default_factory=list, description=Help.LOCATION),
    project: list[str] = Query(default_factory=list, description=Help.PROJECT),
    limit: int | None = Query(default=None, ge=1, description=Help.LIMIT),
    show_all: bool = Query(default=False, description=Help.SHOW_ALL),
    sort: list[str] = Query(default_factory=list, description=Help.SORT),
) -> StreamingResponse:
    """One JSON object per line, emitted as CAI returns them.

    For result sets large enough that waiting for the whole payload is worse
    than parsing incrementally. Unlike `/v1/resources` this does not report
    suppression counts -- they are not known until the stream ends, and a
    trailer would be missed by most clients. Use `/v1/summary` for totals.
    """
    filters = SearchFilters(
        scope=scope,
        resource_types=type,
        free_text=q,
        labels=label,
        locations=location,
        projects=project,
        limit=limit,
        show_all=show_all,
        sort=sort,
    )

    def lines():
        for resource in core.stream_resources(filters):
            yield resource.model_dump_json(exclude_none=True) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.get(
    "/v1/summary",
    response_model=Summary,
    responses={
        400: {"model": ErrorResponse, "description": "Unknown type, malformed scope or filter"},
        403: {"model": ErrorResponse, "description": "Caller lacks Cloud Asset Viewer"},
        404: {"model": ErrorResponse, "description": "Scope not found"},
        503: {"model": ErrorResponse, "description": "Cloud Asset API not enabled"},
    },
    summary="Counts by type, project and location for a scope",
)
def summary(
    scope: str = Query(description=Help.SCOPE, examples=["organizations/123"]),
    type: list[str] = Query(default_factory=list, description=Help.TYPE),
    q: str = Query(default="", description=Help.TERM),
    label: list[str] = Query(default_factory=list, description=Help.LABEL),
    location: list[str] = Query(default_factory=list, description=Help.LOCATION),
    project: list[str] = Query(default_factory=list, description=Help.PROJECT),
    show_all: bool = Query(default=False, description=Help.SHOW_ALL),
) -> Summary:
    """Aggregate a whole scope into one payload.

    No `limit`: a summary of a truncated result set would be a lie, so this
    always counts everything matching.
    """
    result = core.search_resources(
        SearchFilters(
            scope=scope,
            resource_types=type,
            free_text=q,
            labels=label,
            locations=location,
            projects=project,
            show_all=show_all,
            limit=None,
        )
    )
    return summarise(scope, result.resources, result.suppressed)


@app.get("/v1/types", summary="List supported resource-type names")
def list_types() -> dict[str, str]:
    """Friendly names mapped to CAI asset types.

    Raw CAI asset types are accepted anywhere a friendly name is, so this is a
    convenience listing rather than the set of what can be searched.
    """
    return dict(sorted(core.ASSET_TYPES.items()))


@app.get("/healthz", summary="Liveness probe", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Is the process up. Deliberately does no I/O.

    A liveness probe that called GCP would restart the container whenever
    Google had a bad minute, turning an upstream blip into an outage.
    """
    return {"status": "ok"}


@app.get("/readyz", summary="Readiness probe", include_in_schema=False)
def readyz() -> JSONResponse:
    """Can this instance serve: are credentials resolvable and the client built.

    Checks that ADC resolves and the CAI client constructs -- the two things
    that make a deployment useless rather than slow. It does NOT call Cloud
    Asset Inventory: a probe on every pod every few seconds would burn quota to
    tell us something a real request reports anyway.
    """
    try:
        core.get_client()
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "detail": f"{type(exc).__name__}: {exc}"},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})
