"""The `gcpe docs` capability browser: a local page over `app/capabilities.py`.

A separate app that routes its own pages first and passes everything else to
the unchanged API app, so "Call API" calls the real `/v1` routes exactly as any
client would. That is the whole safety model: the page only fetches URLs the
catalogue rendered, and the API validates them as it validates anything else.
There is no endpoint that runs a command, and nothing here shells out.

Only `gcpe docs` builds this app. `gcpe serve` serves `app.api:app` alone, where
`/docs` redirects to Swagger, so a deployment never exposes the session's
identity or a page that runs searches as it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import api, core
from app import capabilities as caps

STATIC_DIR = Path(__file__).with_name("static") / "docs"

# The hand-written guides are not shipped in the package or the image, so the
# browser links to them on GitHub. Links only: the page loads nothing external.
REPO_DOCS_URL = "https://github.com/edoatley/gcp-resource-browser/blob/main/"

# Shown, visibly, when no default project can be read from ADC.
PLACEHOLDER_SCOPE = "projects/YOUR_PROJECT"

router = APIRouter(include_in_schema=False)


def default_scope() -> str:
    project = core.session_context().default_project
    return f"projects/{project}" if project else PLACEHOLDER_SCOPE


def catalogue_payload(scope: str, billing_project: str | None) -> dict:
    """The catalogue with every example rendered for `scope`."""
    capabilities = []
    for capability in caps.CAPABILITIES:
        route = caps.route_for(capability.route)
        examples = []
        for example in capability.examples:
            unavailable = example.unavailable_for(scope)
            examples.append(
                {
                    "id": example.id,
                    "title": example.title,
                    "unavailable": unavailable,
                    "cli": caps.to_cli(capability, example, scope),
                    "url": caps.to_url(capability, example, scope),
                    "gcloud": None
                    if unavailable
                    else caps.to_gcloud(capability, example, scope, billing_project),
                    "streams": capability.route.endswith("/stream"),
                }
            )
        capabilities.append(
            {
                "id": capability.id,
                "title": capability.title,
                "theme": capability.theme,
                "purpose": caps.purpose(capability),
                "route": capability.route,
                "cli_command": capability.cli_command,
                "cli_aliases": list(capability.cli_aliases),
                # FastAPI's Swagger UI deep-links operations by tag and id.
                "swagger": f"/api/docs#/default/{route.unique_id}",
                "docs": [{"label": d, "url": REPO_DOCS_URL + d} for d in capability.docs],
                "params": [
                    {
                        "api": p.api,
                        "cli": "(positional)" if p.cli_positional else p.cli_flag,
                        "help": p.help,
                        "repeatable": p.is_list,
                    }
                    for p in caps.params(capability.id)
                ],
                "examples": examples,
            }
        )
    return {"scope": scope, "themes": list(caps.THEMES), "capabilities": capabilities}


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/docs", status_code=307)


@router.get("/docs")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/docs/_/session")
def session() -> dict:
    context = core.session_context()
    return {**context.model_dump(), "default_scope": default_scope()}


@router.get("/docs/_/catalogue")
def catalogue(scope: str | None = Query(default=None)) -> dict:
    scope = core.validate_scope(scope or default_scope())
    return catalogue_payload(scope, core.session_context().quota_project)


@router.get("/docs/_/projects")
def projects() -> dict:
    return {"projects": [p.model_dump() for p in core.list_visible_projects()]}


def create_docs_app() -> FastAPI:
    """The browser's routes, then the API app for everything else."""
    docs = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    docs.add_exception_handler(core.ResourceExplorerError, api.handle_explorer_error)
    docs.include_router(router)
    docs.mount("/docs/static", StaticFiles(directory=STATIC_DIR), name="docs-static")
    docs.mount("/", api.app)
    return docs
