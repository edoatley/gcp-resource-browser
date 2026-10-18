"""Who is behind a member: groups, project roles, and members that can only be explained.

Expansion is opt-in and must never change an answer it was not asked for, must
read each distinct member once, and must name anything it could not read rather
than dropping it -- the same rules as fan-out and suppression.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app import cli, core, expansion, role_risk
from app.api import app
from app.gcloud import grants_command
from app.params import BillingGrantFilters, GrantFilters
from app.query import grant_query
from tests.conftest import (
    BILLING_ACCOUNTS_URL,
    FakeAssetClient,
    FakeResponse,
    FakeRestSession,
    catalogue,
    make_iam_result,
)

BUCKET = "//storage.googleapis.com/buckets/b"
PROJECT = "//cloudresourcemanager.googleapis.com/projects/p"
LOOKUP = "cloudidentity.googleapis.com/v1/groups:lookup"
MEMBERS = "groups/abc/memberships:searchTransitiveMemberships"
POOL_SET = (
    "principalSet://iam.googleapis.com/projects/123/locations/global/"
    "workloadIdentityPools/github-pool/attribute.repository/org/app"
)


def _project_policy(*owners: str, editors: tuple[str, ...] = ()) -> object:
    """The policy attached to project p itself: what projectOwner:p stands for."""
    return make_iam_result(
        resource=PROJECT,
        role="roles/owner",
        members=owners,
        extra_bindings={"roles/editor": editors},
    )


def _bucket_grant(*members: str) -> object:
    """A medium-risk legacy bucket binding, the usual home of projectOwner:p."""
    return make_iam_result(
        resource=BUCKET,
        role="roles/storage.legacyBucketOwner",
        members=members,
        matched={"roles/storage.legacyBucketOwner": ["storage.buckets.setIamPolicy"]},
    )


def _groups(*pages: list[str], status: int = 200, body: dict | None = None) -> FakeRestSession:
    """A Cloud Identity fake: one group, its members across pages."""
    if status != 200:
        return FakeRestSession({LOOKUP: FakeResponse(body or {}, status)})
    responses = [
        FakeResponse(
            {
                "memberships": [{"preferredMemberKey": [{"id": m}]} for m in page],
                **({"nextPageToken": "t"} if i < len(pages) - 1 else {}),
            }
        )
        for i, page in enumerate(pages)
    ]
    return FakeRestSession({LOOKUP: FakeResponse({"name": "groups/abc"}), MEMBERS: responses})


# --- Reading who is behind a member --------------------------------------------


def test_a_project_role_member_is_read_from_the_project_policy() -> None:
    client = FakeAssetClient(iam_policies=[_project_policy("user:a@x.com", "user:b@x.com")])

    found = expansion.expand_members(["projectOwner:p"], client=client)

    assert found["projectOwner:p"].members == ["user:a@x.com", "user:b@x.com"]
    assert found["projectOwner:p"].count == 2
    request = client.iam_requests[0]
    assert request.scope == "projects/p"
    assert list(request.asset_types) == [core.PROJECT_ASSET_TYPE]


def test_each_project_role_reads_its_own_role() -> None:
    client = FakeAssetClient(
        iam_policies=[_project_policy("user:o@x.com", editors=("user:e@x.com",))]
    )

    found = expansion.expand_members(["projectEditor:p", "projectViewer:p"], client=client)

    assert found["projectEditor:p"].members == ["user:e@x.com"]
    assert found["projectViewer:p"].members == []
    assert found["projectViewer:p"].count == 0


def test_a_policy_on_another_resource_does_not_count_as_the_project() -> None:
    """Only the policy attached to the project itself defines projectOwner:p."""
    client = FakeAssetClient(iam_policies=[_bucket_grant("user:not-an-owner@x.com")])
    assert (
        expansion.expand_members(["projectOwner:p"], client=client)["projectOwner:p"].members == []
    )


def test_a_group_is_read_transitively_across_pages() -> None:
    session = _groups(["a@x.com", "b@x.com"], ["c@x.com"])

    found = expansion.expand_members(["group:ops@x.com"], session=session)

    assert found["group:ops@x.com"].members == ["a@x.com", "b@x.com", "c@x.com"]
    assert session.calls[0][1] == {"groupKey.id": "ops@x.com"}
    assert all(MEMBERS in url for url in session.urls()[1:])


def test_an_unreadable_group_is_named_and_the_rest_still_expand() -> None:
    session = _groups(status=403, body={"error": {"message": "caller lacks access"}})
    client = FakeAssetClient(iam_policies=[_project_policy("user:a@x.com")])

    found = expansion.expand_members(
        ["group:ops@x.com", "projectOwner:p"], client=client, session=session
    )

    group = found["group:ops@x.com"]
    assert group.members is None and group.count is None
    assert "caller lacks access" in group.error and "Groups Reader" in group.error
    assert found["projectOwner:p"].count == 1


def test_a_disabled_cloud_identity_api_names_the_quota_project() -> None:
    body = {
        "error": {
            "message": "disabled",
            "details": [
                {
                    "reason": "SERVICE_DISABLED",
                    "metadata": {
                        "service": "cloudidentity.googleapis.com",
                        "consumer": "projects/q",
                    },
                }
            ],
        }
    }
    found = expansion.expand_members(["group:ops@x.com"], session=_groups(status=403, body=body))
    assert "cloudidentity.googleapis.com is not enabled on q" in found["group:ops@x.com"].error


def test_each_distinct_member_is_read_once() -> None:
    client = FakeAssetClient(iam_policies=[_project_policy("user:a@x.com")])
    expansion.expand_members(["projectOwner:p", "projectOwner:p", "projectOwner:p"], client=client)
    assert len(client.iam_requests) == 1


def test_single_principals_are_left_out() -> None:
    found = expansion.expand_members(["user:a@x.com", "serviceAccount:s@p.iam.gserviceaccount.com"])
    assert found == {}


def test_lookups_are_concurrent_but_bounded(monkeypatch) -> None:
    lock, live, peak = threading.Lock(), [0], [0]

    def slow(project, role, client=None):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.02)
        with lock:
            live[0] -= 1
        return []

    monkeypatch.setattr(core, "project_role_members", slow)
    members = [f"projectOwner:p{i}" for i in range(12)]

    expansion.expand_members(members, max_concurrency=3)

    assert 1 < peak[0] <= 3


# --- Members that can only be explained -------------------------------------------


@pytest.mark.parametrize(
    ("member", "phrase"),
    [
        (POOL_SET, "whose repository is 'org/app'"),
        (POOL_SET.rsplit("/", 3)[0] + "/*", "Every identity from workload identity pool"),
        (POOL_SET.replace("attribute.repository/org/app", "group/eng"), "external group 'eng'"),
        ("principalSet://something/else", "cannot be listed"),
        ("domain:example.com", "Every account in the Google Workspace or Cloud Identity domain"),
        ("allUsers", "Anyone on the internet"),
        ("allAuthenticatedUsers", "Anyone signed in to any Google account"),
    ],
)
def test_unlistable_members_are_explained(member, phrase) -> None:
    found = expansion.expand_members([member])[member]
    assert phrase in found.note
    assert found.members is None and found.count is None


# --- In a grant search -------------------------------------------------------------


def _search(expand: bool, **kwargs):
    client = FakeAssetClient(
        iam_policies=[
            _bucket_grant("projectOwner:p", "user:u@x.com"),
            _project_policy("user:a@x.com"),
        ]
    )
    return core.search_grants(
        GrantFilters(scope="projects/p", min_risk="medium", expand_members=expand, **kwargs),
        client=client,
    )


def test_expansion_adds_counts_and_names_without_changing_rows() -> None:
    plain, expanded = _search(False), _search(True)

    assert [(g.member, g.role) for g in plain.grants] == [
        (g.member, g.role) for g in expanded.grants
    ]
    counts = {g.member: g.member_count for g in expanded.grants}
    assert counts == {"projectOwner:p": 1, "user:u@x.com": None}
    assert expanded.expansions["projectOwner:p"].members == ["user:a@x.com"]


def test_without_the_flag_the_response_is_unchanged() -> None:
    result = _search(False)

    assert result.expansions is None
    assert all(g.member_count is None for g in result.grants)
    body = json.loads(result.model_dump_json(exclude_none=True))
    assert "expansions" not in body
    assert all("member_count" not in g for g in body["grants"])
    assert core.MEMBERS_NOT_EXPANDED in result.coverage_note


def test_the_coverage_note_says_what_was_expanded() -> None:
    note = _search(True).coverage_note

    assert core.MEMBERS_NOT_EXPANDED not in note
    assert "Expanded 1 member(s)" in note
    assert "one level deep and from attached policy only" in note


def test_the_coverage_note_names_what_could_not_be_read(monkeypatch) -> None:
    def denied(project, role, client=None):
        raise core.ScopeAccessDenied("no")

    monkeypatch.setattr(core, "project_role_members", denied)

    note = _search(True).coverage_note

    assert "1 could not be read and still apply in full: projectOwner:p." in note


def test_billing_grants_expand_groups_too() -> None:
    account = "billingAccounts/01AAAA-BBBBBB-CCCCCC"
    session = catalogue(
        ("roles/billing.admin", ["billing.accounts.setIamPolicy"]),
        **{
            BILLING_ACCOUNTS_URL: FakeResponse({"billingAccounts": [{"name": account}]}),
            f"{account}:getIamPolicy": FakeResponse(
                {"bindings": [{"role": "roles/billing.admin", "members": ["group:fin@x.com"]}]}
            ),
            LOOKUP: FakeResponse({"name": "groups/abc"}),
            MEMBERS: FakeResponse({"memberships": [{"preferredMemberKey": [{"id": "cfo@x.com"}]}]}),
        },
    )
    core._ROLE_CATALOGUE.clear()

    result = core.search_billing_grants(BillingGrantFilters(expand_members=True), session=session)

    assert result.grants[0].member_count == 1
    assert result.expansions["group:fin@x.com"].members == ["cfo@x.com"]
    assert "Expanded 1 member(s)" in result.coverage_note
    core._ROLE_CATALOGUE.clear()


# --- Project member types ------------------------------------------------------------


@pytest.mark.parametrize("kind", ["projectOwner", "projectEditor", "projectViewer"])
def test_project_member_types_can_be_filtered(kind) -> None:
    """CAI accepts these in memberTypes: (verified live), so they must not be rejected."""
    assert role_risk.validate_member_types([kind]) == [kind]
    assert grant_query(["storage.buckets.setIamPolicy"], [kind]) == (
        f"policy.role.permissions:storage.buckets.setIamPolicy memberTypes:{kind}"
    )


# --- Surfaces ------------------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch):
    client = FakeAssetClient(
        iam_policies=[
            _bucket_grant("projectOwner:p", "user:u@x.com"),
            _project_policy("user:a@x.com"),
        ]
    )
    monkeypatch.setattr(core, "get_client", lambda: client)
    monkeypatch.setattr(core, "adc_quota_project", lambda: None)
    return client


def test_cli_table_shows_counts_not_names(fake) -> None:
    result = CliRunner().invoke(
        cli.cli,
        ["grants", "projects/p", "--role-risk", "medium", "--expand-members"],
        env={"COLUMNS": "200"},  # so rich does not wrap the member column mid-name
    )

    assert result.exit_code == 0, result.output
    assert "p (1)" in result.output  # the type prefix moves to a line of its own
    assert "project owner" in result.output
    assert "user:a@x.com" not in result.output
    assert "-o json lists who they are" in " ".join(result.output.split())


def test_cli_json_lists_names(fake) -> None:
    result = CliRunner().invoke(
        cli.cli, ["grants", "projects/p", "--role-risk", "medium", "--expand-members", "-o", "json"]
    )
    body = json.loads(result.stdout)
    assert body["expansions"]["projectOwner:p"]["members"] == ["user:a@x.com"]


def test_cli_csv_adds_a_count_column_only_when_asked(fake) -> None:
    plain = CliRunner().invoke(cli.cli, ["grants", "projects/p", "-r", "medium", "-o", "csv"])
    expanded = CliRunner().invoke(
        cli.cli, ["grants", "projects/p", "-r", "medium", "-o", "csv", "--expand-members"]
    )

    assert "member_count" not in plain.stdout.splitlines()[0]
    assert expanded.stdout.splitlines()[0].endswith(",member_count")
    assert "user:a@x.com" not in expanded.stdout


def test_api_expand_members(fake) -> None:
    body = (
        TestClient(app)
        .get(
            "/v1/grants",
            params={"scope": "projects/p", "role_risk": "medium", "expand_members": "true"},
        )
        .json()
    )
    assert body["expansions"]["projectOwner:p"]["count"] == 1
    assert {g["member"]: g.get("member_count") for g in body["grants"]}["projectOwner:p"] == 1


def test_api_without_the_flag_has_no_expansion_fields(fake) -> None:
    body = (
        TestClient(app)
        .get("/v1/grants", params={"scope": "projects/p", "role_risk": "medium"})
        .json()
    )
    assert "expansions" not in body
    assert all("member_count" not in g for g in body["grants"])


def test_gcloud_form_shows_how_to_expand_by_hand() -> None:
    plain = grants_command("projects/p", ["q"])
    expanded = grants_command("projects/p", ["q"], expand=True)

    assert expanded.startswith(plain)
    assert "gcloud identity groups memberships search-transitive-memberships" in expanded
    assert "gcloud projects get-iam-policy PROJECT" in expanded


@pytest.mark.parametrize(
    ("member", "label"),
    [
        (POOL_SET, "github-pool: repository = org/app"),
        (POOL_SET.rsplit("/", 3)[0] + "/*", "github-pool: every identity"),
        (POOL_SET.replace("attribute.repository/org/app", "group/eng"), "github-pool: group = eng"),
        (
            POOL_SET.replace("principalSet", "principal").replace(
                "attribute.repository/org/app", "subject/repo:org/app:ref:main"
            ),
            "github-pool: subject = repo:org/app:ref:main",
        ),
        ("principalSet://something/else", None),
        ("user:a@x.com", None),
    ],
)
def test_federated_members_get_a_readable_label(member, label) -> None:
    assert expansion.short_label(member) == label


def test_table_shortens_a_federated_member_and_prints_it_in_full(monkeypatch) -> None:
    grant = make_iam_result(
        resource=BUCKET,
        role="roles/storage.admin",
        members=(POOL_SET, "serviceAccount:deploy@p.iam.gserviceaccount.com"),
        matched={"roles/storage.admin": ["storage.buckets.setIamPolicy"]},
    )
    monkeypatch.setattr(core, "get_client", lambda: FakeAssetClient(iam_policies=[grant]))

    result = CliRunner().invoke(
        cli.cli, ["grants", "projects/p", "-r", "medium"], env={"COLUMNS": "250"}
    )
    out = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "github-pool: repository = org/app" in out
    assert f"github-pool: repository = org/app is {POOL_SET}" in out  # nothing lost
    assert "deploy@p.iam.gserviceaccount.com" in out and "service account" in out
    assert "serviceAccount:deploy" not in out  # the prefix became the type line


def test_json_keeps_exact_member_strings(monkeypatch) -> None:
    grant = make_iam_result(
        resource=BUCKET,
        role="roles/storage.admin",
        members=(POOL_SET,),
        matched={"roles/storage.admin": ["storage.buckets.setIamPolicy"]},
    )
    monkeypatch.setattr(core, "get_client", lambda: FakeAssetClient(iam_policies=[grant]))

    result = CliRunner().invoke(cli.cli, ["grants", "projects/p", "-r", "medium", "-o", "json"])

    assert json.loads(result.stdout)["grants"][0]["member"] == POOL_SET
