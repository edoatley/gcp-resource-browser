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
from fastapi.responses import JSONResponse

from app import core
from app.models import ErrorResponse, ResourceList

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
    core.ScopeAccessDenied: 403,
    core.ScopeNotFound: 404,
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
        400: {"model": ErrorResponse, "description": "Unknown resource type or malformed scope"},
        403: {
            "model": ErrorResponse,
            "description": "Caller lacks Cloud Asset Viewer on the scope",
        },
        404: {"model": ErrorResponse, "description": "Scope not found"},
        502: {"model": ErrorResponse, "description": "Cloud Asset Inventory call failed"},
    },
    summary="Search resources in a scope",
)
def search_resources(
    scope: str = Query(
        description="CAI scope: organizations/<id>, folders/<id>, or projects/<id>",
        examples=["projects/my-project"],
    ),
    type: str = Query(
        description=f"Resource type. One of: {', '.join(sorted(core.ASSET_TYPES))}",
        examples=["bucket"],
    ),
    q: str = Query(default="", description="Free-text CAI query filter"),
    limit: int = Query(
        default=core.DEFAULT_LIMIT, ge=1, le=10_000, description="Maximum resources to return"
    ),
) -> ResourceList:
    resources, truncated = core.search_resources(
        scope=scope, resource_type=type, query=q, limit=limit
    )
    return ResourceList(
        scope=scope,
        asset_type=core.ASSET_TYPES[type],
        count=len(resources),
        truncated=truncated,
        data=resources,
    )


@app.get("/healthz", summary="Liveness probe", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
