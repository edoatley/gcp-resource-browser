"""The `gcpe docs` browser: local-only routing, session context, and the picker.

The page itself is static; what matters is what the server lets it reach. The
browser must exist only under `gcpe docs`, must never take a command, and must
still load when credentials are missing.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app import api, cli, core, docs_site
from tests.conftest import FakeAssetClient, FakeResponse, FakeRestSession, make_search_result

PROJECTS_URL = "cloudresourcemanager.googleapis.com/v3/projects:search"


def _credentials(module: str, **attrs):
    """A stand-in credential whose class lives in `module`, as google.auth's do."""
    cls = type("Credentials", (), {"__module__": module})
    obj = cls()
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    core.session_context.cache_clear()
    core._VISIBLE_PROJECTS.clear()
    monkeypatch.setattr(core, "get_client", lambda: FakeAssetClient([make_search_result()]))
    yield
    core.session_context.cache_clear()
    core._VISIBLE_PROJECTS.clear()


@pytest.fixture
def adc(monkeypatch):
    """Make ADC resolve to the given credentials and default project."""

    def _use(credentials, project: str | None = "my-project") -> None:
        monkeypatch.setattr(core.google.auth, "default", lambda **_: (credentials, project))

    return _use


@pytest.fixture
def docs() -> TestClient:
    return TestClient(docs_site.create_docs_app())


@pytest.fixture
def serve() -> TestClient:
    return TestClient(api.app)


# --- Only `gcpe docs` has the browser ------------------------------------------


def test_serve_redirects_docs_to_swagger(serve) -> None:
    response = serve.get("/docs", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/api/docs"
    assert serve.get("/api/docs").status_code == 200


@pytest.mark.parametrize("path", ["/docs/_/session", "/docs/_/catalogue", "/docs/_/projects"])
def test_serve_never_exposes_the_browser_endpoints(serve, path) -> None:
    assert serve.get(path).status_code == 404


def test_browser_endpoints_are_not_in_the_api_schema() -> None:
    assert not [path for path in api.app.openapi()["paths"] if path.startswith("/docs")]


def test_docs_app_serves_the_page_and_still_serves_the_api(docs, adc) -> None:
    adc(_credentials("google.oauth2.service_account", service_account_email="sa@p.iam"))

    assert docs.get("/docs").headers["content-type"].startswith("text/html")
    assert docs.get("/docs/static/docs.js").status_code == 200
    assert docs.get("/v1/types").json()["bucket"] == "storage.googleapis.com/Bucket"
    assert docs.get("/api/docs").status_code == 200
    assert docs.get("/", follow_redirects=False).headers["location"] == "/docs"


def test_api_errors_keep_their_shape_through_the_docs_app(docs) -> None:
    response = docs.get("/v1/resources", params={"scope": "nope"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


# --- The catalogue, rendered for a scope ---------------------------------------


def test_catalogue_is_rendered_for_the_requested_scope(docs, adc) -> None:
    adc(_credentials("google.oauth2.service_account", service_account_email="sa@p.iam"))

    body = docs.get("/docs/_/catalogue", params={"scope": "folders/42"}).json()

    search = next(c for c in body["capabilities"] if c["id"] == "search")
    example = search["examples"][0]
    assert body["scope"] == "folders/42"
    assert example["cli"] == "gcpe search folders/42 --type bucket"
    assert example["url"] == "/v1/resources?scope=folders%2F42&type=bucket"
    assert "--scope=folders/42" in example["gcloud"]
    assert search["swagger"].startswith("/api/docs#/default/")
    assert search["docs"][0]["url"].startswith(docs_site.REPO_DOCS_URL)


def test_catalogue_defaults_to_the_adc_project(docs, adc) -> None:
    adc(_credentials("google.oauth2.service_account", service_account_email="sa@p.iam"), "dev-1")
    assert docs.get("/docs/_/catalogue").json()["scope"] == "projects/dev-1"


def test_catalogue_falls_back_to_a_visible_placeholder(docs, adc) -> None:
    adc(_credentials("google.oauth2.service_account", service_account_email="sa@p.iam"), None)
    assert docs.get("/docs/_/catalogue").json()["scope"] == docs_site.PLACEHOLDER_SCOPE


def test_gcloud_forms_bill_where_the_tool_bills(docs, adc) -> None:
    adc(
        _credentials(
            "google.oauth2.service_account", service_account_email="sa@p.iam", quota_project_id="q"
        )
    )
    body = docs.get("/docs/_/catalogue", params={"scope": "projects/p"}).json()
    grants = next(c for c in body["capabilities"] if c["id"] == "grants")
    assert "--billing-project=q" in grants["examples"][0]["gcloud"]


def test_folder_scope_marks_custom_roles_unavailable(docs, adc) -> None:
    adc(_credentials("google.oauth2.service_account", service_account_email="sa@p.iam"))

    body = docs.get("/docs/_/catalogue", params={"scope": "folders/42"}).json()

    roles = {e["id"]: e for c in body["capabilities"] if c["id"] == "roles" for e in c["examples"]}
    assert roles["custom"]["unavailable"] == (
        "Runs only against organizations/ or projects/ scopes, not folders/."
    )
    assert roles["custom"]["gcloud"] is None
    assert roles["high"]["unavailable"] is None


def test_malformed_scope_is_rejected_not_rendered(docs) -> None:
    response = docs.get("/docs/_/catalogue", params={"scope": "nope"})
    assert response.status_code == 400


# --- Session context --------------------------------------------------------------


def test_session_reads_a_service_account_locally(docs, adc) -> None:
    adc(
        _credentials(
            "google.oauth2.service_account",
            service_account_email="sa@p.iam.gserviceaccount.com",
            quota_project_id="billing-p",
        )
    )

    body = docs.get("/docs/_/session").json()

    assert body["credential_type"] == "service account"
    assert body["principal"] == "sa@p.iam.gserviceaccount.com"
    assert body["quota_project"] == "billing-p"
    assert body["default_scope"] == "projects/my-project"


def test_session_names_the_impersonated_account(adc) -> None:
    adc(_credentials("google.auth.impersonated_credentials", service_account_email="target@p.iam"))
    context = core.session_context()
    assert context.credential_type == "impersonated service account"
    assert context.principal == "target@p.iam"


def test_session_asks_tokeninfo_for_a_user_email(adc, monkeypatch) -> None:
    adc(_credentials("google.oauth2.credentials"))
    monkeypatch.setattr(core, "_user_email", lambda credentials: "me@example.com")

    context = core.session_context()

    assert context.credential_type == "user"
    assert context.principal == "me@example.com"


def test_an_unreadable_principal_is_explained_not_fatal(adc, monkeypatch) -> None:
    adc(_credentials("google.oauth2.credentials"))

    def boom(credentials):
        raise OSError("offline")

    monkeypatch.setattr(core, "_user_email", boom)

    context = core.session_context()

    assert context.principal is None
    assert "offline" in context.principal_note


def test_missing_adc_still_loads_the_page(docs, monkeypatch) -> None:
    def no_adc(**_):
        raise core.google.auth.exceptions.DefaultCredentialsError("no ADC")

    monkeypatch.setattr(core.google.auth, "default", no_adc)

    session = docs.get("/docs/_/session").json()
    catalogue = docs.get("/docs/_/catalogue")

    assert "no ADC" in session["error"]
    assert session["default_scope"] == docs_site.PLACEHOLDER_SCOPE
    assert catalogue.status_code == 200
    assert docs.get("/docs").status_code == 200


# --- The scope picker ----------------------------------------------------------------


def _projects_session(*pages: dict, status: int = 200) -> FakeRestSession:
    return FakeRestSession({PROJECTS_URL: [FakeResponse(p, status) for p in pages]})


def test_projects_are_listed_across_pages_active_only_and_sorted(monkeypatch, docs) -> None:
    session = _projects_session(
        {
            "projects": [
                {"name": "projects/2", "projectId": "zeta", "displayName": "Zeta"},
                {"name": "projects/3", "projectId": "gone", "state": "DELETE_REQUESTED"},
            ],
            "nextPageToken": "t",
        },
        {"projects": [{"name": "projects/1", "projectId": "alpha", "state": "ACTIVE"}]},
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)

    body = docs.get("/docs/_/projects").json()

    assert body["projects"] == [
        {"project_id": "alpha", "number": "1", "display_name": None},
        {"project_id": "zeta", "number": "2", "display_name": "Zeta"},
    ]
    assert len(session.calls) == 2


def test_projects_are_cached_briefly(monkeypatch) -> None:
    session = _projects_session({"projects": [{"name": "projects/1", "projectId": "a"}]})

    core.list_visible_projects(session)
    core.list_visible_projects(session)

    assert len(session.calls) == 1


def test_a_disabled_resource_manager_api_names_the_quota_project(monkeypatch, docs) -> None:
    error = {
        "error": {
            "message": "Cloud Resource Manager API has not been used",
            "details": [
                {
                    "reason": "SERVICE_DISABLED",
                    "metadata": {
                        "service": "cloudresourcemanager.googleapis.com",
                        "consumer": "projects/quota-p",
                    },
                }
            ],
        }
    }
    monkeypatch.setattr(core, "get_rest_session", lambda: _projects_session(error, status=403))

    response = docs.get("/docs/_/projects")

    assert response.status_code == 503
    assert "quota-p" in response.json()["detail"]


def test_a_denied_project_listing_names_the_role_needed(monkeypatch) -> None:
    session = _projects_session({"error": {"message": "denied"}}, status=403)
    with pytest.raises(core.ScopeAccessDenied, match="roles/browser"):
        core.list_visible_projects(session)


# --- The page loads nothing external ---------------------------------------------------


def test_static_assets_make_no_external_requests() -> None:
    for path in docs_site.STATIC_DIR.iterdir():
        text = path.read_text()
        assert not re.search(r"https?://", text), f"{path.name} references an external URL"
        assert "innerHTML" not in text, f"{path.name} writes HTML from data"


# --- gcpe docs ------------------------------------------------------------------------------


@pytest.fixture
def launched(monkeypatch):
    calls: dict = {"opened": []}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))

    class Timer:
        def __init__(self, delay, fn, args):
            self.args = args

        def start(self):
            calls["opened"].append(self.args[0])

    monkeypatch.setattr(cli.threading, "Timer", Timer)
    return calls


def test_docs_binds_loopback_and_opens_the_browser(launched) -> None:
    result = CliRunner().invoke(cli.cli, ["docs"])

    assert result.exit_code == 0, result.output
    assert launched["host"] == "127.0.0.1"
    assert launched["opened"] == ["http://127.0.0.1:8000/docs"]
    # The browser app, not the bare API app, whose /docs is a redirect.
    page = TestClient(launched["app"]).get("/docs", follow_redirects=False)
    assert page.status_code == 200


def test_docs_no_browser_and_a_warning_off_loopback(launched) -> None:
    result = CliRunner().invoke(cli.cli, ["docs", "--host", "0.0.0.0", "--no-browser"])

    assert result.exit_code == 0
    assert launched["opened"] == []
    assert "run searches as you" in " ".join(result.stderr.split())


def test_page_script_puts_nodes_through_one_flattening_helper() -> None:
    """A DOM method handed an array stringifies it ("a,b", "[object HTMLElement]").

    That shipped once: nested arrays reached replaceChildren, and the nav showed
    raw URLs. `put()` flattens to any depth, so it must be the only caller.
    """
    script = (docs_site.STATIC_DIR / "docs.js").read_text()
    assert script.count(".replaceChildren(") == 1
    assert "parent.replaceChildren(...nodes(children))" in script


# --- The parameter builder --------------------------------------------------------


def _render(docs, capability: str, **params):
    return docs.get("/docs/_/render", params={"capability": capability, **params}).json()


def _pairs(args: dict) -> list[tuple[str, str]]:
    pairs = []
    for key, value in args.items():
        if isinstance(value, bool):
            pairs.append((key, "true"))
        elif isinstance(value, list):
            pairs.extend((key, v) for v in value)
        else:
            pairs.append((key, value))
    return pairs


def test_every_preset_renders_exactly_as_the_catalogue_does(docs, adc) -> None:
    """The builder and the catalogue must be one renderer, not two that agree today."""
    adc(
        _credentials(
            "google.oauth2.service_account", service_account_email="sa@p.iam", quota_project_id="q"
        )
    )
    catalogue = docs.get("/docs/_/catalogue", params={"scope": "projects/p"}).json()

    for capability in catalogue["capabilities"]:
        for example in capability["examples"]:
            query = [("capability", capability["id"]), *_pairs(example["args"])]
            rendered = docs.get("/docs/_/render", params=query).json()

            where = f"{capability['id']}:{example['id']}"
            assert rendered["errors"] == [], where
            assert rendered["cli"] == example["cli"], where
            assert rendered["url"] == example["url"], where
            assert rendered["gcloud"] == example["gcloud"], where


def test_render_reports_every_problem_inline(docs) -> None:
    body = _render(
        docs, "search", scope="projects/p", type="nonsense", sort="bogus", label="=bad", limit="0"
    )

    errors = " ".join(body["errors"])
    assert "Unsupported resource type 'nonsense'" in errors
    assert "Cannot sort by 'bogus'" in errors
    assert "Invalid label filter" in errors
    assert "limit must be at least 1" in errors
    assert body["gcloud"] is None
    assert "Fix the errors" in body["notes"]["gcloud"]


@pytest.mark.parametrize(
    ("capability", "params", "message"),
    [
        ("search", {"nope": "1", "scope": "projects/p"}, "Unknown parameter 'nope'"),
        ("search", {}, "scope is required"),
        ("search", {"scope": "nope"}, "Invalid scope"),
        ("roles", {"scope": "folders/1"}, "cannot be defined on a folder"),
        ("roles", {"risk": "extreme"}, "Unknown risk level"),
        ("grants", {"scope": "projects/p", "member_type": "robot"}, "Unknown member type"),
        ("grants", {"scope": "projects/p", "group_by": "x"}, "Cannot group by 'x'"),
        ("grants", {"scope": "projects/p", "limit": "ten"}, "limit must be a number"),
    ],
)
def test_render_names_the_problem(docs, capability, params, message) -> None:
    body = _render(docs, capability, **params)
    assert any(message in error for error in body["errors"]), body["errors"]


def test_render_explains_a_missing_form(docs, adc) -> None:
    adc(_credentials("google.oauth2.service_account", service_account_email="sa@p.iam"))

    stream = _render(docs, "stream", scope="projects/p")
    project = _render(docs, "search", scope="projects/p", project="other")
    types = _render(docs, "types")

    assert stream["cli"] is None and "No CLI form" in stream["notes"]["cli"]
    assert project["gcloud"] is None and "--show-gcloud" in project["notes"]["gcloud"]
    assert project["cli"] == "gcpe search projects/p --project other"
    assert types["gcloud"] is None and types["url"] == "/v1/types"


def test_render_rejects_an_unknown_capability(docs) -> None:
    assert docs.get("/docs/_/render", params={"capability": "bogus"}).status_code == 400


def test_render_is_browser_only(serve) -> None:
    assert serve.get("/docs/_/render", params={"capability": "search"}).status_code == 404


def test_catalogue_describes_each_field(docs, adc) -> None:
    adc(_credentials("google.oauth2.service_account", service_account_email="sa@p.iam"))
    catalogue = docs.get("/docs/_/catalogue", params={"scope": "projects/p"}).json()
    search = {
        p["api"]: p for c in catalogue["capabilities"] if c["id"] == "search" for p in c["params"]
    }

    assert search["scope"]["required"] is True
    assert search["limit"]["kind"] == "int"
    assert search["show_all"]["kind"] == "bool"
    assert search["type"]["kind"] == "list"
    assert "bucket" in search["type"]["suggestions"]
    assert "createTime DESC" in search["sort"]["suggestions"]


def test_page_never_assembles_a_command_itself() -> None:
    """Every command string comes from /docs/_/render, so the page cannot drift from it."""
    script = (docs_site.STATIC_DIR / "docs.js").read_text()
    assert not re.search(r"""["'`]--""", script), "docs.js builds a CLI flag"
    assert "gcloud asset" not in script
