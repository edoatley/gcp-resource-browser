"""Cover the differential-check helper.

It silently went stale when `search_resources` changed signature -- the code
was inlined in a bash heredoc where neither ruff nor pytest could see it, and
nothing noticed until the check was run by hand against real GCP. Now it is a
module, these tests keep it wired to the current API.
"""

from __future__ import annotations

import json

import pytest

from app import core
from scripts import _explorer_search
from tests.conftest import FakeAssetClient, make_search_result


@pytest.fixture
def use_fake(monkeypatch):
    def _use(fake: FakeAssetClient) -> FakeAssetClient:
        monkeypatch.setattr(core, "get_client", lambda: fake)
        return fake

    return _use


def test_emits_one_json_object_per_resource(use_fake, monkeypatch, capsys) -> None:
    use_fake(FakeAssetClient(results=[make_search_result(display_name="b1")]))
    monkeypatch.setattr(
        "sys.argv", ["_explorer_search", "--scope", "projects/p", "--type", "bucket"]
    )

    assert _explorer_search.main() == 0

    line = capsys.readouterr().out.strip()
    assert json.loads(line)["display_name"] == "b1"


def test_output_matches_jq_compact_formatting(use_fake, monkeypatch, capsys) -> None:
    """gcloud's side uses `jq -c`; a spacing difference would diff as a mismatch."""
    use_fake(FakeAssetClient(results=[make_search_result()]))
    monkeypatch.setattr(
        "sys.argv", ["_explorer_search", "--scope", "projects/p", "--type", "bucket"]
    )

    _explorer_search.main()

    line = capsys.readouterr().out.strip()
    assert ", " not in line
    assert ": " not in line


def test_filters_reach_the_query(use_fake, monkeypatch, capsys) -> None:
    fake = use_fake(FakeAssetClient(results=[]))
    monkeypatch.setattr(
        "sys.argv",
        [
            "_explorer_search",
            "--scope",
            "organizations/1",
            "--type",
            "bucket",
            "--label",
            "env=prod",
            "--location",
            "europe-west2",
            "--term",
            "backup",
        ],
    )

    _explorer_search.main()

    assert fake.last_request.query == "backup labels.env:prod location:europe-west2"


def test_reports_errors_without_traceback(use_fake, monkeypatch, capsys) -> None:
    use_fake(FakeAssetClient())
    monkeypatch.setattr(
        "sys.argv", ["_explorer_search", "--scope", "not-a-scope", "--type", "bucket"]
    )

    assert _explorer_search.main() == 1
    assert "explorer error" in capsys.readouterr().err


def test_grants_helper_emits_sorted_tsv_with_agents_included(use_fake, monkeypatch, capsys) -> None:
    """Agents must be included: gcloud cannot tell them apart, so both sides compare everything."""
    from scripts import _explorer_grants
    from tests.conftest import make_iam_result

    owner = ["resourcemanager.projects.setIamPolicy"]
    agent = "serviceAccount:service-1@gcp-sa-run.iam.gserviceaccount.com"
    use_fake(
        FakeAssetClient(
            iam_policies=[
                make_iam_result(
                    resource="//r",
                    role="roles/owner",
                    members=("user:b@x.com", agent),
                    matched={"roles/owner": owner},
                )
            ]
        )
    )
    monkeypatch.setattr("sys.argv", ["_explorer_grants", "--scope", "projects/p"])

    assert _explorer_grants.main() == 0

    assert capsys.readouterr().out.splitlines() == [
        f"//r\troles/owner\t{agent}",
        "//r\troles/owner\tuser:b@x.com",
    ]


def test_roles_helper_emits_sorted_tsv_with_agent_roles_included(monkeypatch, capsys) -> None:
    """Agent roles must be included: the gcloud side classifies the whole catalogue."""
    from scripts import _explorer_roles
    from tests.conftest import catalogue

    core._ROLE_CATALOGUE.clear()
    owner = ["resourcemanager.projects.setIamPolicy"]
    monkeypatch.setattr(
        core,
        "get_rest_session",
        lambda: catalogue(("roles/run.serviceAgent", owner), ("roles/owner", owner)),
    )
    monkeypatch.setattr("sys.argv", ["_explorer_roles", "--risk", "high"])

    assert _explorer_roles.main() == 0

    assert capsys.readouterr().out.splitlines() == [
        "high\troles/owner",
        "high\troles/run.serviceAgent",
    ]
    core._ROLE_CATALOGUE.clear()


def test_billing_helper_emits_sorted_tsv_and_warns_about_unclassified(monkeypatch, capsys) -> None:
    from scripts import _explorer_billing_grants
    from tests.conftest import BILLING_ACCOUNTS_URL, FakeResponse, catalogue

    core._ROLE_CATALOGUE.clear()
    account = "billingAccounts/01AAAA-BBBBBB-CCCCCC"
    agent = "serviceAccount:service-1@gcp-sa-billing.iam.gserviceaccount.com"
    session = catalogue(
        ("roles/billing.admin", ["billing.accounts.setIamPolicy"]),
        ("roles/billing.viewer", []),
        **{
            BILLING_ACCOUNTS_URL: FakeResponse({"billingAccounts": [{"name": account}]}),
            f"{account}:getIamPolicy": FakeResponse(
                {
                    "bindings": [
                        {"role": "roles/billing.admin", "members": ["user:a@x.com", agent]},
                        {"role": "organizations/1/roles/odd", "members": ["user:b@x.com"]},
                    ]
                }
            ),
        },
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)
    monkeypatch.setattr("sys.argv", ["_explorer_billing_grants"])

    assert _explorer_billing_grants.main() == 0

    captured = capsys.readouterr()
    prefix = f"//cloudbilling.googleapis.com/{account}\troles/billing.admin"
    assert captured.out.splitlines() == [f"{prefix}\t{agent}", f"{prefix}\tuser:a@x.com"]
    assert "organizations/1/roles/odd" in captured.err
    core._ROLE_CATALOGUE.clear()


def test_projects_helper_emits_sorted_tsv(monkeypatch, capsys) -> None:
    from scripts import _explorer_projects
    from tests.conftest import FakeResponse, FakeRestSession

    core._VISIBLE_PROJECTS.clear()
    session = FakeRestSession(
        {
            "projects:search": FakeResponse(
                {
                    "projects": [
                        {"name": "projects/2", "projectId": "zeta"},
                        {"name": "projects/1", "projectId": "alpha"},
                    ]
                }
            )
        }
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)

    assert _explorer_projects.main() == 0
    assert capsys.readouterr().out.splitlines() == ["alpha\t1", "zeta\t2"]
    core._VISIBLE_PROJECTS.clear()


def test_expansions_helper_emits_sorted_tsv_and_fails_on_unreadable(monkeypatch, capsys) -> None:
    from scripts import _explorer_expansions
    from tests.conftest import make_iam_result

    def client_with(project_policy_members):
        return FakeAssetClient(
            iam_policies=[
                make_iam_result(
                    resource="//storage.googleapis.com/buckets/b",
                    role="roles/storage.legacyBucketOwner",
                    members=("projectOwner:p", "projectEditor:p"),
                    matched={"roles/storage.legacyBucketOwner": ["storage.buckets.setIamPolicy"]},
                ),
                make_iam_result(
                    resource="//cloudresourcemanager.googleapis.com/projects/p",
                    role="roles/owner",
                    members=project_policy_members,
                ),
            ]
        )

    monkeypatch.setattr(core, "get_client", lambda: client_with(("user:b@x.com", "user:a@x.com")))
    monkeypatch.setattr("sys.argv", ["_explorer_expansions", "--scope", "projects/p"])

    assert _explorer_expansions.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "projectEditor:p\t-",
        "projectOwner:p\tuser:a@x.com",
        "projectOwner:p\tuser:b@x.com",
    ]

    def denied(project, role, client=None):
        raise core.ScopeAccessDenied("no")

    monkeypatch.setattr(core, "project_role_members", denied)
    assert _explorer_expansions.main() == 1
    assert "could not expand projectOwner:p" in capsys.readouterr().err


def test_effective_iam_helper_emits_inherited_bindings_and_gaps(
    use_fake, monkeypatch, capsys
) -> None:
    """Attached bindings are left out; an unreadable resource is a line, not a silence."""
    from scripts import _explorer_effective_iam
    from tests.conftest import make_iam_result

    project = "//cloudresourcemanager.googleapis.com/projects/p"
    names = ["//storage.googleapis.com/a", "//storage.googleapis.com/gone"]
    use_fake(
        FakeAssetClient(
            results=[make_search_result(name=n) for n in names],
            iam_policies=[make_iam_result(resource=n) for n in names],
            effective={names[0]: [(project, {"roles/owner": ("user:o@example.com",)})]},
        )
    )
    monkeypatch.setattr("sys.argv", ["_explorer_effective_iam", "--scope", "projects/p"])

    assert _explorer_effective_iam.main() == 0

    assert capsys.readouterr().out.splitlines() == [
        f"{names[0]}\t{project}\troles/owner\tuser:o@example.com",
        f"{names[1]}\tUNREADABLE",
    ]
