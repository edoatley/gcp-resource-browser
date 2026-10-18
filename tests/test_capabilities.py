"""The capability catalogue must describe the tool as it is, not as it was.

These are the staleness guards for `app/capabilities.py`, in the same spirit as
the CI diffs on `openapi.yml` and `CLI_REFERENCE.md`: adding a command or route
without cataloguing it fails here, and so does an example that no longer runs
on either surface or whose gcloud form has drifted from `--show-gcloud`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app import api, cli, core
from app import capabilities as caps
from app.params import Help
from tests.conftest import (
    BILLING_ACCOUNTS_URL,
    FakeAssetClient,
    FakeResponse,
    catalogue,
    make_search_result,
)

SCOPE = "projects/p"
REPO = Path(__file__).resolve().parent.parent

EXAMPLES = [
    pytest.param(capability, example, id=f"{capability.id}:{example.id}")
    for capability in caps.CAPABILITIES
    for example in capability.examples
]


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    """Answer every surface from fakes: CAI, the IAM catalogue and Cloud Billing."""
    core._ROLE_CATALOGUE.clear()
    account = "billingAccounts/01AAAA-BBBBBB-CCCCCC"
    session = catalogue(
        ("roles/owner", ["resourcemanager.projects.setIamPolicy"]),
        ("roles/viewer", ["storage.objects.get"]),
        **{
            f"iam.googleapis.com/v1/{SCOPE}/roles": FakeResponse({"roles": []}),
            BILLING_ACCOUNTS_URL: FakeResponse({"billingAccounts": [{"name": account}]}),
            f"{account}:getIamPolicy": FakeResponse(
                {"bindings": [{"role": "roles/owner", "members": ["user:a@example.com"]}]}
            ),
        },
    )
    bucket = make_search_result()
    project = "//cloudresourcemanager.googleapis.com/projects/p"
    effective = {bucket.name: [(project, {"roles/owner": ("user:a@example.com",)})]}
    monkeypatch.setattr(core, "get_client", lambda: FakeAssetClient([bucket], effective=effective))
    monkeypatch.setattr(core, "get_rest_session", lambda: session)
    monkeypatch.setattr(core, "adc_quota_project", lambda: None)
    yield
    core._ROLE_CATALOGUE.clear()


# --- Coverage: nothing can be added without being catalogued -------------------


def _cli_command_names() -> set[str]:
    return {
        command.name or command.callback.__name__.replace("_", "-")
        for command in cli.cli.registered_commands
    }


def test_every_cli_command_is_catalogued_or_excluded() -> None:
    listed = {c.cli_command for c in caps.CAPABILITIES if c.cli_command}
    listed |= {alias for c in caps.CAPABILITIES for alias in c.cli_aliases}
    missing = _cli_command_names() - listed - set(caps.EXCLUDED_CLI_COMMANDS)
    assert not missing, f"CLI commands missing from app/capabilities.py: {sorted(missing)}"


def test_catalogue_names_only_real_cli_commands() -> None:
    named = {c.cli_command for c in caps.CAPABILITIES if c.cli_command}
    named |= {alias for c in caps.CAPABILITIES for alias in c.cli_aliases}
    named |= set(caps.EXCLUDED_CLI_COMMANDS)
    assert named <= _cli_command_names()


def test_every_v1_route_is_catalogued() -> None:
    routes = {
        r.path for r in api.app.routes if isinstance(r, APIRoute) and r.path.startswith("/v1/")
    }
    assert routes == {c.route for c in caps.CAPABILITIES}


def test_ids_are_unique_and_themes_are_known() -> None:
    assert len(caps.BY_ID) == len(caps.CAPABILITIES)
    for capability in caps.CAPABILITIES:
        assert capability.theme in caps.THEMES
        assert capability.examples, f"{capability.id} has no example"
        ids = [e.id for e in capability.examples]
        assert len(ids) == len(set(ids)), f"{capability.id} repeats an example id"


# --- Derivation: descriptions come from the code, not from here ---------------


def test_every_capability_has_a_purpose_from_its_route() -> None:
    for capability in caps.CAPABILITIES:
        assert caps.purpose(capability), f"{capability.route} has no summary"


@pytest.mark.parametrize("capability", caps.CAPABILITIES, ids=lambda c: c.id)
def test_every_param_is_described_by_help(capability) -> None:
    """A parameter with no shared Help text would appear undocumented in the browser."""
    for param in caps.params(capability.id):
        assert param.help_name is not None, f"{capability.route} {param.api} has no Help text"
        assert param.help == getattr(Help, param.help_name)


def test_search_params_pair_across_surfaces() -> None:
    """Pinned: the pairing that turns an API example into CLI flags."""
    pairs = {p.api: (p.cli_flag, p.cli_positional) for p in caps.params("search")}
    assert pairs["scope"] == (None, True)
    assert pairs["q"] == (None, True)
    assert pairs["type"] == ("--type", False)
    assert pairs["include_iam"] == ("--include-iam", False)


def test_grants_params_pair_differently_spelled_names() -> None:
    pairs = {p.api: p.cli_flag for p in caps.params("grants")}
    assert pairs["group_by"] == "--by"
    assert pairs["role_risk"] == "--role-risk"


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to dashes."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


@pytest.mark.parametrize("capability", caps.CAPABILITIES, ids=lambda c: c.id)
def test_docs_links_resolve(capability) -> None:
    for link in capability.docs:
        path, _, anchor = link.partition("#")
        text = (REPO / path).read_text()
        if anchor:
            headings = {_slug(h) for h in re.findall(r"^#+ (.+)$", text, flags=re.M)}
            assert anchor in headings, f"{link}: no such heading"


# --- Examples: each must run on every surface it claims -----------------------


@pytest.mark.parametrize(("capability", "example"), EXAMPLES)
def test_example_runs_through_the_api(capability, example) -> None:
    response = TestClient(api.app).get(caps.to_url(capability, example, SCOPE))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(("capability", "example"), EXAMPLES)
def test_example_runs_through_the_cli(capability, example) -> None:
    argv = caps.to_argv(capability, example, SCOPE)
    if argv is None:
        pytest.skip(f"{capability.id} has no CLI form")
    result = CliRunner().invoke(cli.cli, argv)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("capability", "example"),
    [p for p in EXAMPLES if p.values[0].id in ("search", "grants", "roles", "billing-grants")],
)
def test_gcloud_form_matches_show_gcloud(capability, example) -> None:
    """The catalogue's gcloud form must be what the CLI itself prints."""
    result = CliRunner().invoke(
        cli.cli, [*caps.to_argv(capability, example, SCOPE), "--show-gcloud", "-o", "json"]
    )
    assert result.exit_code == 0, result.output
    rendered = caps.to_gcloud(capability, example, SCOPE)
    assert rendered is not None
    if capability.id == "billing-grants":
        # The CLI names the accounts it actually read; the catalogue cannot.
        assert rendered.splitlines()[:4] == result.stderr.splitlines()[:4]
    else:
        # `_print_gcloud` writes the command(s) and a blank line before any note,
        # so nothing gcloud-shaped may follow: a missing trailing command is drift.
        assert result.stderr.startswith(rendered + "\n\n")
        rest = result.stderr[len(rendered) :].splitlines()
        assert not [line for line in rest if line.startswith(("gcloud ", "# "))]


def test_examples_never_use_the_project_filter() -> None:
    """Resolving a project ID is a CAI call, which a rendered example cannot make."""
    for capability in caps.CAPABILITIES:
        for example in capability.examples:
            assert "project" not in example.args


# --- Rendering, pinned ---------------------------------------------------------


def _example(capability_id: str, example_id: str):
    capability = caps.BY_ID[capability_id]
    return capability, next(e for e in capability.examples if e.id == example_id)


def test_renders_a_search_three_ways() -> None:
    capability, example = _example("search", "prod-vms-in-london")

    assert caps.to_cli(capability, example, SCOPE) == (
        "gcpe search projects/p --type vm --label env=prod --location europe-west2"
    )
    assert caps.to_url(capability, example, SCOPE) == (
        "/v1/resources?scope=projects%2Fp&type=vm&label=env%3Dprod&location=europe-west2"
    )
    assert caps.to_gcloud(capability, example, SCOPE, billing_project="quota-p") == (
        "gcloud asset search-all-resources \\\n"
        "    --scope=projects/p \\\n"
        "    --asset-types=compute.googleapis.com/Instance \\\n"
        "    --query='labels.env:prod location:europe-west2' \\\n"
        "    --billing-project=quota-p"
    )


def test_quotes_values_for_the_shell() -> None:
    capability, example = _example("search", "newest-first")
    assert caps.to_cli(capability, example, SCOPE) == (
        "gcpe search projects/p --sort 'createTime DESC' --limit 20"
    )


def test_scope_is_substituted_everywhere() -> None:
    capability, example = _example("grants", "users-by-member")
    scope = "folders/123"

    assert caps.to_cli(capability, example, scope) == (
        "gcpe grants folders/123 --member-type user --by member"
    )
    assert caps.to_url(capability, example, scope) == (
        "/v1/grants?scope=folders%2F123&member_type=user&group_by=member"
    )
    assert "--scope=folders/123" in caps.to_gcloud(capability, example, scope)


def test_capabilities_without_a_form_say_so() -> None:
    stream, example = _example("stream", "everything")
    assert caps.to_cli(stream, example, SCOPE) is None
    types, example = _example("types", "all")
    assert caps.to_gcloud(types, example, SCOPE) is None
    assert caps.to_url(types, example, SCOPE) == "/v1/types"


def test_scope_levels_say_why_an_example_cannot_run() -> None:
    _, example = _example("roles", "custom")

    assert example.unavailable_for("projects/p") is None
    assert example.unavailable_for("organizations/1") is None
    assert example.unavailable_for("folders/1") == (
        "Runs only against organizations/ or projects/ scopes, not folders/."
    )


def test_examples_with_no_scope_restriction_run_anywhere() -> None:
    for capability in caps.CAPABILITIES:
        for example in capability.examples:
            if not example.scope_levels:
                assert example.unavailable_for("folders/1") is None
