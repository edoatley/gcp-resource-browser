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
from app.params import BillingGrantFilters, GrantFilters
from app.query import grant_query
from tests.conftest import (
    BILLING_ACCOUNTS_URL,
    PREDEFINED_ROLES_URL,
    FakeAssetClient,
    FakeResponse,
    FakeRestSession,
    catalogue,
    make_iam_result,
)

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


@pytest.fixture(autouse=True)
def fresh_catalogue():
    core._ROLE_CATALOGUE.clear()
    yield
    core._ROLE_CATALOGUE.clear()


def test_roles_are_classified_across_pages() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", ["storage.objects.get"]),
        ("roles/storage.admin", ["storage.buckets.setIamPolicy"]),
    )

    high = core.list_risky_roles("high", session=session)

    assert [r.name for r in high.data] == ["roles/owner"]
    assert session.calls[1][1]["pageToken"] == "next"
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
        core.list_risky_roles(
            "high", session=FakeRestSession({PREDEFINED_ROLES_URL: FakeResponse(error, 403)})
        )


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
        core, "get_rest_session", lambda: catalogue(("roles/owner", OWNER_PERMS), ("roles/v", []))
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


# --- Custom roles -------------------------------------------------------------
#
# The point of including them: `gcpe grants` already matches custom roles,
# because it matches on permissions. Until now `gcpe roles` could not list
# them, so the two surfaces disagreed about what exists.

CUSTOM_ROLES_URL = "iam.googleapis.com/v1/projects/p/roles"
ORG_CUSTOM_ROLES_URL = "iam.googleapis.com/v1/organizations/123/roles"


def custom(*roles: tuple[str, list[str]], url: str = CUSTOM_ROLES_URL) -> dict:
    """A route serving custom role definitions for one scope."""
    return {
        url: FakeResponse(
            {
                "roles": [
                    {"name": n, "title": n, "stage": "GA", "includedPermissions": p}
                    for n, p in roles
                ]
            }
        )
    }


def test_custom_roles_are_classified_by_the_same_rules() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", ["storage.objects.get"]),
        **custom(
            ("projects/p/roles/deployer", ["iam.serviceAccounts.actAs"]),
            ("projects/p/roles/reader", ["storage.objects.get"]),
        ),
    )

    result = core.list_risky_roles("high", scope="projects/p", session=session)

    names = [r.name for r in result.data]
    assert names == ["projects/p/roles/deployer", "roles/owner"]
    deployer = result.data[0]
    assert deployer.custom is True
    assert deployer.risk == "high"
    assert [p.permission for p in deployer.permissions] == ["iam.serviceAccounts.actAs"]
    # The predefined role is still reported, and still marked predefined.
    assert result.data[1].custom is False
    assert result.scope == "projects/p"


def test_custom_roles_are_read_from_the_scope_given() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", []),
        **custom(("organizations/123/roles/breakglass", OWNER_PERMS), url=ORG_CUSTOM_ROLES_URL),
    )

    result = core.list_risky_roles("high", scope="organizations/123", session=session)

    assert any(ORG_CUSTOM_ROLES_URL in url for url in session.urls())
    assert "organizations/123/roles/breakglass" in [r.name for r in result.data]


def test_no_scope_lists_predefined_roles_only() -> None:
    session = catalogue(("roles/owner", OWNER_PERMS), ("roles/viewer", []))

    result = core.list_risky_roles("high", session=session)

    assert result.scope is None
    assert all(not r.custom for r in result.data)
    # Nothing but the predefined catalogue was fetched.
    assert all(PREDEFINED_ROLES_URL in url for url in session.urls())


def test_a_folder_scope_is_rejected_rather_than_answered() -> None:
    """IAM has no folder-level custom roles, so answering would imply we looked."""
    with pytest.raises(core.InvalidFilterError, match="cannot be defined on a folder"):
        core.list_risky_roles("high", scope="folders/456", session=catalogue())


def test_a_malformed_role_scope_is_rejected() -> None:
    with pytest.raises(core.InvalidScopeError):
        core.list_risky_roles("high", scope="not-a-scope", session=catalogue())


def test_service_agent_suppression_does_not_apply_to_custom_roles() -> None:
    """A customer-written role named like an agent is still the customer's to review."""
    session = catalogue(
        ("roles/firebase.managementServiceAgent", OWNER_PERMS),
        ("roles/viewer", []),
        **custom(("projects/p/roles/myServiceAgent", OWNER_PERMS)),
    )

    result = core.list_risky_roles("high", scope="projects/p", session=session)

    assert [r.name for r in result.data] == ["projects/p/roles/myServiceAgent"]
    assert result.suppressed == 1  # the predefined agent role, counted as always


def test_custom_roles_are_not_cached_while_the_catalogue_is() -> None:
    """Custom roles are the customer's own data: a stale copy could hide a fix."""
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", []),
        **custom(("projects/p/roles/deployer", ["iam.serviceAccounts.actAs"])),
    )

    core.list_risky_roles("high", scope="projects/p", session=session)
    first = list(session.urls())
    core.list_risky_roles("high", scope="projects/p", session=session)
    second = session.urls()[len(first) :]

    assert second == [f"https://{CUSTOM_ROLES_URL}"]  # catalogue cached, custom roles re-read


def test_an_org_scope_says_it_cannot_see_project_custom_roles() -> None:
    """Listing those would be the per-project walk the PRD rejects, so it says so."""
    session = catalogue(
        ("roles/owner", OWNER_PERMS), ("roles/viewer", []), **custom(url=ORG_CUSTOM_ROLES_URL)
    )

    note = core.list_risky_roles("high", scope="organizations/123", session=session).coverage_note

    assert "projects beneath it are NOT listed" in note
    assert "one call per project" in note


def test_no_scope_says_custom_roles_are_missing() -> None:
    note = core.list_risky_roles(
        "high", session=catalogue(("roles/owner", OWNER_PERMS), ("roles/v", []))
    ).coverage_note
    assert "Custom roles are not listed" in note


def test_denied_custom_role_listing_is_a_permission_error() -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", []),
        **{CUSTOM_ROLES_URL: FakeResponse({"error": {"message": "iam.roles.list denied"}}, 403)},
    )
    with pytest.raises(core.ScopeAccessDenied, match="iam.roles.list denied"):
        core.list_risky_roles("high", scope="projects/p", session=session)


# --- Billing account IAM ------------------------------------------------------
#
# The one risky grant `gcpe grants` cannot reach at any scope: CAI does not
# index billing accounts. The properties pinned are the under-reporting ones --
# a role that cannot be classified is named rather than dropped, and the call
# count stays bounded.

BILLING_PERMS = ["billing.accounts.setIamPolicy"]
ACCOUNT_A = "billingAccounts/01AAAA-BBBBBB-CCCCCC"
ACCOUNT_B = "billingAccounts/02DDDD-EEEEEE-FFFFFF"


def billing_session(
    accounts: list[dict] | None = None,
    policies: dict[str, list[dict]] | None = None,
    roles: tuple[tuple[str, list[str]], ...] = (
        ("roles/billing.admin", BILLING_PERMS),
        ("roles/billing.viewer", ["billing.accounts.get"]),
    ),
    **extra,
) -> FakeRestSession:
    """A session answering the catalogue, the account list, and each policy."""
    accounts = (
        [{"name": ACCOUNT_A, "displayName": "Engineering", "open": True}]
        if accounts is None
        else accounts
    )
    routes = {
        BILLING_ACCOUNTS_URL: FakeResponse({"billingAccounts": accounts}),
        **{
            f"{name}:getIamPolicy": FakeResponse({"bindings": bindings})
            for name, bindings in (policies or {}).items()
        },
        **extra,
    }
    return catalogue(*roles, **routes)


def billing(session: FakeRestSession, **kwargs) -> object:
    return core.search_billing_grants(BillingGrantFilters(**kwargs), session=session)


def test_a_billing_admin_is_reported_as_high_risk() -> None:
    session = billing_session(
        policies={
            ACCOUNT_A: [
                {"role": "roles/billing.admin", "members": ["user:finance@example.com"]},
                {"role": "roles/billing.viewer", "members": ["user:analyst@example.com"]},
            ]
        }
    )

    result = billing(session)

    assert result.count == 1
    grant = result.grants[0]
    assert grant.member == "user:finance@example.com"
    assert grant.role == "roles/billing.admin"
    assert grant.risk == "high"
    assert grant.matched_permissions == BILLING_PERMS
    assert grant.resource == f"//cloudbilling.googleapis.com/{ACCOUNT_A}"
    assert grant.asset_type == core.BILLING_ASSET_TYPE
    assert grant.display_name == "Engineering"  # the ID alone is unreadable
    assert result.accounts == [ACCOUNT_A]


def test_one_policy_call_per_account_and_no_more() -> None:
    """Bounded by accounts, not by projects -- the reason this phase allows it."""
    session = billing_session(
        accounts=[{"name": ACCOUNT_A}, {"name": ACCOUNT_B}],
        policies={
            ACCOUNT_A: [{"role": "roles/billing.admin", "members": ["user:a@example.com"]}],
            ACCOUNT_B: [{"role": "roles/billing.admin", "members": ["user:b@example.com"]}],
        },
    )

    result = billing(session)

    policy_calls = [u for u in session.urls() if u.endswith(":getIamPolicy")]
    assert len(policy_calls) == 2
    assert {g.member for g in result.grants} == {"user:a@example.com", "user:b@example.com"}


def test_an_unclassifiable_role_is_named_not_dropped() -> None:
    """A custom role granted on an account is not in the catalogue; silence would hide it."""
    session = billing_session(
        policies={
            ACCOUNT_A: [
                {"role": "organizations/123/roles/billingBreakglass", "members": ["user:x@e.com"]},
                {"role": "roles/billing.admin", "members": ["user:finance@example.com"]},
            ]
        }
    )

    result = billing(session)

    assert result.unclassified_roles == ["organizations/123/roles/billingBreakglass"]
    assert [g.role for g in result.grants] == ["roles/billing.admin"]


def test_service_agents_are_hidden_counted_and_recoverable() -> None:
    agent = "serviceAccount:service-123@gcp-sa-billing.iam.gserviceaccount.com"
    policies = {
        ACCOUNT_A: [{"role": "roles/billing.admin", "members": [agent, "user:finance@example.com"]}]
    }

    hidden = billing(billing_session(policies=policies))
    shown = billing(billing_session(policies=policies), show_all=True)

    assert [g.member for g in hidden.grants] == ["user:finance@example.com"]
    assert hidden.suppressed == 1
    assert "1 grant(s) to Google service agents hidden" in hidden.suppressed_summary
    assert {g.member for g in shown.grants} == {agent, "user:finance@example.com"}
    assert shown.suppressed == 0


def test_member_types_narrow_the_rows() -> None:
    session = billing_session(
        policies={
            ACCOUNT_A: [
                {
                    "role": "roles/billing.admin",
                    "members": ["user:a@example.com", "group:ops@example.com"],
                }
            ]
        }
    )

    result = billing(session, member_types=["group"])

    assert [g.member for g in result.grants] == ["group:ops@example.com"]
    assert result.member_types == ["group"]


def test_conditions_are_preserved() -> None:
    session = billing_session(
        policies={
            ACCOUNT_A: [
                {
                    "role": "roles/billing.admin",
                    "members": ["user:a@example.com"],
                    "condition": {"expression": "request.time < timestamp('2030-01-01T00:00:00Z')"},
                }
            ]
        }
    )

    assert billing(session).grants[0].condition.startswith("request.time <")


def test_grants_can_be_grouped_by_member() -> None:
    session = billing_session(
        accounts=[{"name": ACCOUNT_A}, {"name": ACCOUNT_B}],
        policies={
            ACCOUNT_A: [{"role": "roles/billing.admin", "members": ["user:a@example.com"]}],
            ACCOUNT_B: [{"role": "roles/billing.admin", "members": ["user:a@example.com"]}],
        },
    )

    groups = billing(session, group_by="member").groups

    assert groups[0].key == "user:a@example.com"
    assert groups[0].resource_count == 2


def test_the_limit_marks_the_result_truncated() -> None:
    session = billing_session(
        policies={
            ACCOUNT_A: [
                {
                    "role": "roles/billing.admin",
                    "members": ["user:a@example.com", "user:b@example.com"],
                }
            ]
        }
    )

    result = billing(session, limit=1)

    assert result.truncated is True
    assert result.count == 1


def test_no_visible_accounts_reads_no_policies() -> None:
    session = billing_session(accounts=[])

    result = billing(session)

    assert result.accounts == [] and result.grants == []
    assert not [u for u in session.urls() if u.endswith(":getIamPolicy")]


def test_denied_billing_access_names_the_role_needed() -> None:
    session = catalogue(
        ("roles/billing.admin", BILLING_PERMS),
        ("roles/v", []),
        **{
            BILLING_ACCOUNTS_URL: FakeResponse(
                {"error": {"message": "The caller does not have permission"}}, 403
            )
        },
    )
    with pytest.raises(core.ScopeAccessDenied, match="roles/billing.viewer"):
        billing(session)


def test_a_disabled_billing_api_names_the_quota_project() -> None:
    error = {
        "error": {
            "message": "Cloud Billing API has not been used in project quota-proj before",
            "details": [
                {
                    "reason": "SERVICE_DISABLED",
                    "metadata": {
                        "service": "cloudbilling.googleapis.com",
                        "consumer": "projects/quota-proj",
                    },
                }
            ],
        }
    }
    session = catalogue(
        ("roles/billing.admin", BILLING_PERMS),
        ("roles/v", []),
        **{BILLING_ACCOUNTS_URL: FakeResponse(error, 403)},
    )
    with pytest.raises(core.ApiNotEnabledError, match="cloudbilling.googleapis.com"):
        billing(session)


def test_every_billing_response_states_what_it_cannot_see() -> None:
    note = billing(billing_session()).coverage_note
    assert "no scope bounds them" in note
    assert "Group membership is not expanded" in note


@pytest.mark.parametrize(
    "kwargs", [{"min_risk": "extreme"}, {"member_types": ["robot"]}, {"group_by": "colour"}]
)
def test_bad_billing_inputs_are_rejected(kwargs: dict) -> None:
    with pytest.raises(core.InvalidFilterError):
        billing(billing_session(), **kwargs)


# --- Surfaces for the new capabilities ----------------------------------------


@pytest.fixture
def roles_api(monkeypatch):
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", []),
        **custom(("projects/p/roles/deployer", ["iam.serviceAccounts.actAs"])),
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)
    return TestClient(app)


def test_api_roles_with_a_scope_includes_custom_roles(roles_api) -> None:
    body = roles_api.get("/v1/roles", params={"risk": "high", "scope": "projects/p"}).json()

    custom_roles = [r for r in body["data"] if r["custom"]]
    assert [r["name"] for r in custom_roles] == ["projects/p/roles/deployer"]
    assert body["scope"] == "projects/p"
    assert "custom roles defined on projects/p" in body["coverage_note"]


def test_api_roles_rejects_a_folder_scope(roles_api) -> None:
    response = roles_api.get("/v1/roles", params={"scope": "folders/456"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_filter"


@pytest.fixture
def billing_api(monkeypatch):
    session = billing_session(
        policies={
            ACCOUNT_A: [{"role": "roles/billing.admin", "members": ["user:finance@example.com"]}]
        }
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)
    return TestClient(app)


def test_api_billing_grants(billing_api) -> None:
    body = billing_api.get("/v1/billing-grants", params={"role_risk": "high"}).json()

    assert body["count"] == 1
    assert body["grants"][0]["member"] == "user:finance@example.com"
    assert body["accounts"] == [ACCOUNT_A]
    assert body["coverage_note"]


def test_api_billing_grants_rejects_a_bad_group_by(billing_api) -> None:
    response = billing_api.get("/v1/billing-grants", params={"group_by": "colour"})
    assert response.status_code == 400


def test_cli_roles_marks_custom_roles(monkeypatch) -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", []),
        **custom(("projects/p/roles/deployer", ["iam.serviceAccounts.actAs"])),
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)

    result = CliRunner().invoke(cli_module.cli, ["roles", "--scope", "projects/p"])

    assert result.exit_code == 0
    assert "custom" in result.stdout
    assert "deployer" in result.stdout


def test_cli_roles_csv_carries_the_custom_column(monkeypatch) -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS),
        ("roles/viewer", []),
        **custom(("projects/p/roles/deployer", ["iam.serviceAccounts.actAs"])),
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)

    result = CliRunner().invoke(cli_module.cli, ["roles", "--scope", "projects/p", "-o", "csv"])

    assert result.exit_code == 0
    rows = result.stdout.splitlines()
    assert rows[0].split(",")[:4] == ["name", "title", "risk", "custom"]
    assert any(row.startswith("projects/p/roles/deployer,") and ",true," in row for row in rows)


def test_cli_roles_folder_scope_exits_usage(monkeypatch) -> None:
    monkeypatch.setattr(core, "get_rest_session", lambda: catalogue())
    result = CliRunner().invoke(cli_module.cli, ["roles", "--scope", "folders/456"])
    assert result.exit_code == cli_module.EXIT_USAGE


def test_cli_billing_grants_json_keeps_stdout_parseable(monkeypatch) -> None:
    session = billing_session(
        policies={
            ACCOUNT_A: [
                {"role": "roles/billing.admin", "members": ["user:finance@example.com"]},
                {"role": "organizations/123/roles/odd", "members": ["user:x@example.com"]},
            ]
        }
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)

    result = CliRunner().invoke(cli_module.cli, ["billing-grants", "-o", "json"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["grants"][0]["member"] == "user:finance@example.com"
    # The warning about what could not be classified goes to stderr, not stdout.
    assert "could not be classified" in result.stderr
    assert "organizations/123/roles/odd" in result.stderr


def test_cli_billing_grants_distinguishes_no_accounts_from_no_grants(monkeypatch) -> None:
    """ "No accounts visible" is a permissions answer, not a clean bill of health."""
    monkeypatch.setattr(core, "get_rest_session", lambda: billing_session(accounts=[]))

    result = CliRunner().invoke(cli_module.cli, ["billing-grants"])

    assert result.exit_code == 0
    assert "No billing accounts visible" in result.stdout
    assert "roles/billing.viewer" in result.stdout


def test_cli_billing_grants_show_gcloud(monkeypatch) -> None:
    session = billing_session(
        policies={ACCOUNT_A: [{"role": "roles/billing.admin", "members": ["user:a@example.com"]}]}
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)

    result = CliRunner().invoke(cli_module.cli, ["billing-grants", "--show-gcloud"])

    assert "gcloud billing accounts list --format='value(name)'" in result.stdout
    assert "gcloud billing accounts get-iam-policy 01AAAA-BBBBBB-CCCCCC" in result.stdout


def test_cli_roles_show_gcloud(monkeypatch) -> None:
    session = catalogue(
        ("roles/owner", OWNER_PERMS), ("roles/viewer", []), **custom(url=ORG_CUSTOM_ROLES_URL)
    )
    monkeypatch.setattr(core, "get_rest_session", lambda: session)

    result = CliRunner().invoke(
        cli_module.cli, ["roles", "--scope", "organizations/123", "--show-gcloud"]
    )

    assert "gcloud iam roles list --organization=123" in result.stdout
