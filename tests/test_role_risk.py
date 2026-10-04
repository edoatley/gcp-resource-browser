"""Role risk: the rules, the role catalogue, and the grant search.

The grant search is an audit control, so the properties pinned here are the
ones whose failure would under-report: every queried batch is searched, a
binding's risk comes from what CAI matched, hidden service agents are counted,
and every response states what it cannot see.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app import cli as cli_module
from app import core, role_risk
from app.api import app
from app.params import GrantFilters
from app.query import grant_query
from tests.conftest import FakeAssetClient, make_iam_result

OWNER_PERMS = [
    "resourcemanager.projects.setIamPolicy",
    "iam.serviceAccounts.actAs",
    "iam.serviceAccountKeys.create",
]


# --- The rules file -----------------------------------------------------------


def test_every_rule_cites_a_source_that_exists() -> None:
    sources = role_risk.load_sources()
    for rule in role_risk.load_rules():
        assert rule.sources, f"{rule.permission} cites no source"
        for source in rule.sources:
            assert source in sources, f"{rule.permission} cites unknown source {source}"


def test_every_source_is_checkable() -> None:
    """A source without a URL and a quote cannot be validated, which is its whole point."""
    for source in role_risk.load_sources().values():
        assert source.url.startswith("https://")
        assert len(source.quote) > 40


def test_each_permission_has_one_rule() -> None:
    with role_risk.RULES_FILE.open() as handle:
        permissions = [r["permission"] for r in json.load(handle)["rules"]]
    assert len(permissions) == len(set(permissions))


def test_the_named_examples_are_high_risk() -> None:
    """The roles that motivated the feature must classify as high via their permissions."""
    for permission in (
        "billing.accounts.setIamPolicy",  # roles/billing.admin
        "resourcemanager.organizations.setIamPolicy",  # roles/resourcemanager.organizationAdmin
        "resourcemanager.projects.setIamPolicy",  # roles/owner
    ):
        assert role_risk.highest(role_risk.classify([permission])) is role_risk.Risk.HIGH


# --- Classification -----------------------------------------------------------


def test_classify_returns_highest_first() -> None:
    matched = role_risk.classify(["storage.buckets.setIamPolicy", "iam.serviceAccounts.actAs"])
    assert [r.risk for r in matched] == [role_risk.Risk.HIGH, role_risk.Risk.MEDIUM]


def test_minimum_risk_excludes_lower_rules() -> None:
    assert role_risk.classify(["storage.buckets.setIamPolicy"], role_risk.Risk.HIGH) == []


def test_a_harmless_role_matches_nothing() -> None:
    assert role_risk.classify(["storage.objects.get", "compute.instances.list"]) == []


@pytest.mark.parametrize(
    ("member", "role", "agent"),
    [
        ("serviceAccount:service-123@gcp-sa-aiplatform.iam.gserviceaccount.com", "roles/x", True),
        ("serviceAccount:service-123@compute-system.iam.gserviceaccount.com", "roles/x", True),
        ("serviceAccount:123@cloudservices.gserviceaccount.com", "roles/editor", True),
        ("serviceAccount:anything@example.com", "roles/run.serviceAgent", True),
        # Default service accounts are customer-managed: a classic over-grant, never hidden.
        ("serviceAccount:123-compute@developer.gserviceaccount.com", "roles/editor", False),
        ("serviceAccount:my-proj@appspot.gserviceaccount.com", "roles/editor", False),
        ("serviceAccount:deploy@my-proj.iam.gserviceaccount.com", "roles/owner", False),
        ("user:someone@example.com", "roles/owner", False),
    ],
)
def test_service_agent_detection(member: str, role: str, agent: bool) -> None:
    assert role_risk.is_service_agent_grant(member, role) is agent


# --- Query construction ---------------------------------------------------------


def test_grant_query_is_pinned() -> None:
    assert grant_query(["a.b.c", "d.e.f"], ["user", "group"]) == (
        "policy.role.permissions:(a.b.c OR d.e.f) memberTypes:(user OR group)"
    )
    assert grant_query(["a.b.c"]) == "policy.role.permissions:a.b.c"


@pytest.mark.parametrize(
    ("perms", "types", "sizes"),
    [(32, 0, [32]), (33, 0, [32, 1]), (22, 2, [16, 6]), (22, 3, [10, 10, 2])],
)
def test_batches_respect_the_alternation_limit(perms: int, types: int, sizes: list[int]) -> None:
    """CAI rejects more than 32 alternations, counted as permissions x member types."""
    batches = role_risk.permission_batches([f"p{i}" for i in range(perms)], types)
    assert [len(b) for b in batches] == sizes
    assert all(len(b) * max(1, types) <= role_risk.MAX_QUERY_ALTERNATIONS for b in batches)


# --- The role catalogue -------------------------------------------------------


class FakeResponse:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body, self.status_code, self.ok = body, status, status < 400

    def json(self) -> dict:
        return self._body


class FakeSession:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url, params):
        self.calls.append(params)
        return self.pages[len(self.calls) - 1]


@pytest.fixture(autouse=True)
def fresh_catalogue():
    core._ROLE_CATALOGUE.clear()
    yield
    core._ROLE_CATALOGUE.clear()


def catalogue(*roles: tuple[str, list[str]]) -> FakeSession:
    raw = [{"name": n, "title": n, "stage": "GA", "includedPermissions": p} for n, p in roles]
    # Two pages, so pagination is exercised.
    return FakeSession(
        [
            FakeResponse({"roles": raw[:1], "nextPageToken": "next"}),
            FakeResponse({"roles": raw[1:]}),
        ]
    )


def test_roles_are_classified_across_pages() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", ["storage.objects.get"]),
        ("roles/storage.admin", ["storage.buckets.setIamPolicy"]),
    )

    high = core.list_risky_roles("high", session=session)

    assert [r.name for r in high.data] == ["roles/owner"]
    assert session.calls[1]["pageToken"] == "next"
    assert high.data[0].risk == "high"
    assert set(high.sources) == {
        "rhino-part1",
        "rhino-part2",
        "gcp-roles-overview",
        "cis-gcp-1.6",
        "gcp-sa-best-practices",
    }


def test_medium_includes_high() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS), ("roles/storage.admin", ["storage.buckets.setIamPolicy"])
    )
    assert [r.name for r in core.list_risky_roles("medium", session=session).data] == [
        "roles/owner",
        "roles/storage.admin",
    ]


def test_service_agent_roles_are_hidden_and_counted() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS), ("roles/run.serviceAgent", ["iam.serviceAccounts.actAs"])
    )
    result = core.list_risky_roles("high", session=session)

    assert [r.name for r in result.data] == ["roles/owner"]
    assert result.suppressed == 1
    assert "--show-all" in result.suppressed_summary


def test_show_all_includes_service_agent_roles() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS), ("roles/run.serviceAgent", ["iam.serviceAccounts.actAs"])
    )
    assert core.list_risky_roles("high", show_all=True, session=session).count == 2


def test_catalogue_is_cached() -> None:
    session = catalogue(("roles/owner", OWNER_PERMS), ("roles/viewer", []))
    core.list_risky_roles("high", session=session)
    core.list_risky_roles("medium", session=session)
    assert len(session.calls) == 2  # one fetch of two pages, not two fetches


def test_disabled_iam_api_names_the_billing_project() -> None:
    error = {
        "error": {
            "code": 403,
            "message": "IAM API has not been used in project quota-proj",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "SERVICE_DISABLED",
                    "metadata": {
                        "service": "iam.googleapis.com",
                        "consumer": "projects/quota-proj",
                    },
                }
            ],
        }
    }
    with pytest.raises(core.ApiNotEnabledError, match="--project=quota-proj"):
        core.list_risky_roles("high", session=FakeSession([FakeResponse(error, 403)]))


def test_unknown_risk_level_is_rejected() -> None:
    with pytest.raises(core.InvalidFilterError, match="Choose from"):
        core.list_risky_roles("extreme", session=catalogue(("roles/x", []), ("roles/y", [])))


# --- The grant search ---------------------------------------------------------


def grants(client: FakeAssetClient, **kwargs):
    return core.search_grants(GrantFilters(scope="projects/p", **kwargs), client=client)


def owner_policy(**kwargs):
    return make_iam_result(
        resource="//cloudresourcemanager.googleapis.com/projects/p",
        role="roles/owner",
        members=("user:alice@example.com",),
        matched={"roles/owner": OWNER_PERMS},
        **kwargs,
    )


def test_every_batch_is_searched() -> None:
    client = FakeAssetClient(iam_policies=[owner_policy()])

    result = grants(client, min_risk="medium", member_types=["user", "group"])

    assert len(client.iam_requests) == len(result.queries) > 1
    assert [r.query for r in client.iam_requests] == result.queries


def test_bindings_are_narrowed_to_matched_roles() -> None:
    """CAI returns whole policies; a non-matching binding must not be reported."""
    client = FakeAssetClient(
        iam_policies=[owner_policy(extra_bindings={"roles/viewer": ("user:bob@example.com",)})]
    )

    result = grants(client)

    assert [(g.member, g.role) for g in result.grants] == [
        ("user:alice@example.com", "roles/owner")
    ]
    assert result.grants[0].risk == "high"
    assert result.grants[0].matched_permissions == sorted(OWNER_PERMS)


def test_member_types_narrow_within_a_binding() -> None:
    client = FakeAssetClient(
        iam_policies=[
            make_iam_result(
                role="roles/owner",
                members=("user:alice@example.com", "serviceAccount:ci@p.iam.gserviceaccount.com"),
                matched={"roles/owner": OWNER_PERMS},
            )
        ]
    )

    result = grants(client, member_types=["user"])

    assert [g.member for g in result.grants] == ["user:alice@example.com"]


def test_service_agent_grants_are_hidden_and_counted() -> None:
    agent = "serviceAccount:service-1@gcp-sa-run.iam.gserviceaccount.com"
    client = FakeAssetClient(
        iam_policies=[
            make_iam_result(
                role="roles/run.serviceAgent",
                members=(agent,),
                matched={"roles/run.serviceAgent": ["iam.serviceAccounts.actAs"]},
            ),
            owner_policy(),
        ]
    )

    hidden = grants(client)
    shown = grants(client, show_all=True)

    assert hidden.count == 1 and hidden.suppressed == 1
    assert "service agents hidden" in hidden.suppressed_summary
    assert agent in {g.member for g in shown.grants}


def test_a_grant_found_by_two_batches_appears_once_with_merged_permissions() -> None:
    class PerBatch(FakeAssetClient):
        def search_all_iam_policies(self, request):
            self.iam_requests.append(request)
            first = "resourcemanager.projects.setIamPolicy" in request.query
            perms = (
                ["resourcemanager.projects.setIamPolicy"] if first else ["storage.hmacKeys.create"]
            )
            return iter([owner_policy_with(perms)])

    def owner_policy_with(perms):
        return make_iam_result(
            role="roles/owner", members=("user:a@example.com",), matched={"roles/owner": perms}
        )

    result = grants(PerBatch(), min_risk="medium", member_types=["user", "group"])

    assert result.count == 1
    assert result.grants[0].matched_permissions == [
        "resourcemanager.projects.setIamPolicy",
        "storage.hmacKeys.create",
    ]


def test_conditional_bindings_keep_their_condition() -> None:
    client = FakeAssetClient(iam_policies=[owner_policy(condition="request.time < ts")])
    assert grants(client).grants[0].condition == "request.time < ts"


@pytest.mark.parametrize(
    ("scope", "phrase"),
    [
        ("projects/p", "not visible from a project scope"),
        ("folders/1", "not visible from a folder scope"),
        ("organizations/1", "Covers the organization"),
    ],
)
def test_coverage_note_is_always_stated(scope: str, phrase: str) -> None:
    result = core.search_grants(GrantFilters(scope=scope), client=FakeAssetClient())
    assert phrase in result.coverage_note
    assert "Billing account IAM" in result.coverage_note
    assert "not expanded" in result.coverage_note


def test_limit_flags_truncation() -> None:
    client = FakeAssetClient(
        iam_policies=[
            make_iam_result(
                role="roles/owner",
                members=tuple(f"user:{i}@x.com" for i in range(5)),
                matched={"roles/owner": OWNER_PERMS},
            )
        ]
    )
    result = grants(client, limit=2)
    assert result.count == 2 and result.truncated


def test_group_by_member_and_role() -> None:
    client = FakeAssetClient(
        iam_policies=[
            make_iam_result(
                resource="//r/1",
                role="roles/owner",
                members=("user:a@x.com",),
                matched={"roles/owner": OWNER_PERMS},
            ),
            make_iam_result(
                resource="//r/2",
                role="roles/iam.serviceAccountUser",
                members=("user:a@x.com", "user:b@x.com"),
                matched={"roles/iam.serviceAccountUser": ["iam.serviceAccounts.actAs"]},
            ),
        ]
    )

    by_member = grants(client, group_by="member").groups
    by_role = grants(client, group_by="role").groups

    assert by_member[0].key == "user:a@x.com"
    assert by_member[0].roles == ["roles/iam.serviceAccountUser", "roles/owner"]
    assert by_member[0].resource_count == 2
    sa_user = next(g for g in by_role if g.key == "roles/iam.serviceAccountUser")
    assert sa_user.members == ["user:a@x.com", "user:b@x.com"]


@pytest.mark.parametrize(
    "kwargs", [{"min_risk": "extreme"}, {"member_types": ["robot"]}, {"group_by": "colour"}]
)
def test_bad_inputs_are_rejected(kwargs: dict) -> None:
    with pytest.raises(core.InvalidFilterError):
        grants(FakeAssetClient(), **kwargs)


# --- Surfaces -----------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    client = FakeAssetClient(iam_policies=[owner_policy()])
    monkeypatch.setattr(core, "get_client", lambda: client)
    monkeypatch.setattr(
        core, "get_iam_session", lambda: catalogue(("roles/owner", OWNER_PERMS), ("roles/v", []))
    )
    return TestClient(app)


def test_api_roles(api) -> None:
    body = api.get("/v1/roles", params={"risk": "high"}).json()
    assert body["data"][0]["name"] == "roles/owner"
    assert body["sources"]["rhino-part2"]["url"].startswith("https://")


def test_api_grants_grouped(api) -> None:
    body = api.get(
        "/v1/grants",
        params={
            "scope": "projects/p",
            "role_risk": "high",
            "member_type": ["user", "group"],
            "group_by": "member",
        },
    ).json()
    assert body["groups"][0] == {
        "key": "user:alice@example.com",
        "highest_risk": "high",
        "grant_count": 1,
        "resource_count": 1,
        "roles": ["roles/owner"],
    }
    assert body["coverage_note"]


def test_api_rejects_a_bad_risk(api) -> None:
    response = api.get("/v1/grants", params={"scope": "projects/p", "role_risk": "extreme"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_filter"


def test_cli_grants_json_keeps_stdout_parseable(api) -> None:
    result = CliRunner().invoke(cli_module.cli, ["grants", "projects/p", "-o", "json"])
    assert json.loads(result.stdout)["grants"][0]["role"] == "roles/owner"
    assert "not visible from a project scope" in " ".join(result.stderr.split())


def test_cli_roles_table(api) -> None:
    result = CliRunner().invoke(cli_module.cli, ["roles"])
    assert result.exit_code == 0
    assert "roles/owner" in result.output


def test_cli_grants_show_gcloud(api) -> None:
    result = CliRunner().invoke(cli_module.cli, ["grants", "projects/p", "--show-gcloud"])
    assert "gcloud asset search-all-iam-policies" in result.output
    assert "--query='policy.role.permissions:(" in result.output


def test_the_evidence_page_lists_every_rule_and_source() -> None:
    """docs/ROLE_RISK.md is where a reviewer validates the rules; it must not drift."""
    from pathlib import Path

    page = (Path(__file__).parent.parent / "docs" / "ROLE_RISK.md").read_text()
    for rule in role_risk.load_rules():
        assert f"`{rule.permission}`" in page, f"{rule.permission} missing from ROLE_RISK.md"
    for source in role_risk.load_sources().values():
        assert source.url in page, f"{source.id} missing from ROLE_RISK.md"
