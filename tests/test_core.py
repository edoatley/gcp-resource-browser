"""Tests for the CAI access layer."""

from __future__ import annotations

import pytest
from google.api_core import exceptions as gcp_exceptions

from app import core
from app.params import SearchFilters
from tests.conftest import (
    FakeAssetClient,
    filters,
    make_iam_result,
    make_project_result,
    make_search_result,
    service_disabled_error,
)


def test_maps_cai_fields_onto_the_model(fake_client: FakeAssetClient) -> None:
    result = core.search_resources(filters(scope="projects/my-project"), client=fake_client)

    assert not result.truncated
    assert len(result.resources) == 1
    resource = result.resources[0]
    assert resource.full_name == "//storage.googleapis.com/buckets/example"
    assert resource.asset_type == "storage.googleapis.com/Bucket"
    assert resource.display_name == "example"
    assert resource.location == "europe-west2"
    # CAI reports "projects/<number>"; only the trailing segment is kept.
    assert resource.project == "123456"


def test_unset_timestamp_is_none_not_epoch(fake_client: FakeAssetClient) -> None:
    """An unset protobuf Timestamp must not surface as 1970-01-01."""
    result = core.search_resources(filters(scope="projects/my-project"), client=fake_client)
    assert result.resources[0].create_time is None


def test_sends_the_resolved_asset_type_upstream(fake_client: FakeAssetClient) -> None:
    core.search_resources(
        filters(scope="organizations/123", types=("cloudrun",)), client=fake_client
    )

    request = fake_client.last_request
    assert request.scope == "organizations/123"
    assert list(request.asset_types) == ["run.googleapis.com/Service"]


@pytest.mark.parametrize("scope", ["projects/p", "folders/123", "organizations/123"])
def test_accepts_every_cai_scope_kind(scope: str, fake_client: FakeAssetClient) -> None:
    core.search_resources(filters(scope=scope), client=fake_client)
    assert fake_client.last_request.scope == scope


@pytest.mark.parametrize(
    "scope",
    ["my-project", "project/my-project", "buckets/x", "", "organizations/"],
)
def test_rejects_malformed_scope_without_calling_cai(scope: str) -> None:
    client = FakeAssetClient()
    with pytest.raises(core.InvalidScopeError):
        core.search_resources(filters(scope=scope), client=client)
    # Validation must happen before the call, not after a confusing upstream error.
    assert client.last_request is None


def test_rejects_unknown_resource_type() -> None:
    client = FakeAssetClient()
    with pytest.raises(core.UnknownResourceTypeError):
        core.search_resources(filters(types=("nonsense",)), client=client)
    assert client.last_request is None


def test_truncates_and_flags_when_limit_is_hit() -> None:
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(10)])

    result = core.search_resources(filters(limit=3), client=client)

    assert result.truncated is True
    assert len(result.resources) == 3


def test_not_truncated_when_results_fit_exactly() -> None:
    """Off-by-one guard: exactly `limit` results is not truncation."""
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(3)])

    result = core.search_resources(filters(limit=3), client=client)

    assert result.truncated is False
    assert len(result.resources) == 3


def test_limit_none_returns_everything() -> None:
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(50)])

    result = core.search_resources(filters(limit=None), client=client)

    assert result.truncated is False
    assert len(result.resources) == 50


@pytest.mark.parametrize(
    ("upstream", "expected"),
    [
        (gcp_exceptions.PermissionDenied("nope"), core.ScopeAccessDenied),
        (gcp_exceptions.NotFound("gone"), core.ScopeNotFound),
        (gcp_exceptions.InvalidArgument("bad"), core.InvalidFilterError),
        (gcp_exceptions.ResourceExhausted("quota"), core.UpstreamError),
    ],
)
def test_translates_google_errors_into_domain_errors(
    upstream: Exception, expected: type[Exception]
) -> None:
    client = FakeAssetClient(raises=upstream)
    with pytest.raises(expected):
        core.search_resources(filters(), client=client)


def test_rejects_a_bare_string_of_types() -> None:
    """A str satisfies Sequence[str] and would iterate character by character."""
    client = FakeAssetClient()
    # Constructed directly, not via the filters() helper, which would coerce it.
    bare = SearchFilters(scope="projects/p", resource_types="bucket")
    with pytest.raises(core.UnknownResourceTypeError, match="not the string"):
        core.search_resources(bare, client=client)
    assert client.last_request is None


def test_searches_several_asset_types_in_one_call(fake_client: FakeAssetClient) -> None:
    """Multiple types are one upstream request, not one request per type."""
    core.search_resources(
        filters(scope="organizations/123", types=("bucket", "cloudrun")), client=fake_client
    )

    assert list(fake_client.last_request.asset_types) == [
        "storage.googleapis.com/Bucket",
        "run.googleapis.com/Service",
    ]


def test_duplicate_types_are_collapsed(fake_client: FakeAssetClient) -> None:
    core.search_resources(
        filters(types=("bucket", "storage.googleapis.com/Bucket")), client=fake_client
    )
    assert list(fake_client.last_request.asset_types) == ["storage.googleapis.com/Bucket"]


def test_raw_asset_type_passes_through(fake_client: FakeAssetClient) -> None:
    core.search_resources(filters(types=("dns.googleapis.com/ManagedZone",)), client=fake_client)
    assert list(fake_client.last_request.asset_types) == ["dns.googleapis.com/ManagedZone"]


def test_asset_type_regex_passes_through(fake_client: FakeAssetClient) -> None:
    """CAI accepts RE2 patterns for asset types."""
    core.search_resources(filters(types=("compute.googleapis.com/.*",)), client=fake_client)
    assert list(fake_client.last_request.asset_types) == ["compute.googleapis.com/.*"]


def test_filters_are_compiled_into_the_upstream_query(fake_client: FakeAssetClient) -> None:
    """The whole point of Phase 2: filtering happens server-side, not locally."""
    core.search_resources(
        filters(
            scope="organizations/123",
            free_text="backup",
            labels=["env=prod"],
            locations=["europe-west2", "europe-west1"],
        ),
        client=fake_client,
    )

    assert fake_client.last_request.query == (
        "backup labels.env:prod location:(europe-west2 OR europe-west1)"
    )


def test_result_echoes_the_query_that_was_sent(fake_client: FakeAssetClient) -> None:
    result = core.search_resources(filters(labels=["env=prod"]), client=fake_client)
    assert result.query == "labels.env:prod"


def test_bad_filter_raises_before_calling_cai() -> None:
    client = FakeAssetClient()
    with pytest.raises(core.InvalidFilterError):
        core.search_resources(filters(labels=["NotAValidKey=x"]), client=client)
    assert client.last_request is None


def test_empty_type_list_searches_every_type() -> None:
    """Phase 3 reverses Phase 2 here: no type means all types.

    CAI searches every supported asset type when `asset_types` is empty. Only
    usable because noise reduction makes the result readable.
    """
    client = FakeAssetClient(results=[make_search_result()])

    result = core.search_resources(filters(types=()), client=client)

    assert list(client.last_request.asset_types) == []
    assert result.asset_types == []
    assert len(result.resources) == 1


def test_disabled_api_is_not_reported_as_a_permission_problem() -> None:
    """Google returns 403 for both; the remedies are completely different.

    Reported by a user whose search failed against projects/idp-prototype-edo
    while the actual problem was an unenabled API on their quota project. The
    old message named the wrong cause, the wrong project and the wrong fix.
    """
    client = FakeAssetClient(raises=service_disabled_error(quota_project="billing-project"))

    with pytest.raises(core.ApiNotEnabledError) as caught:
        core.search_resources(filters(scope="projects/searched-project"), client=client)

    message = str(caught.value)
    assert "billing-project" in message
    assert "gcloud services enable cloudasset.googleapis.com" in message
    # It must not blame the scope being searched, which is a different project.
    assert "searched-project" not in message
    assert "roles/cloudasset.viewer" not in message


def test_genuine_permission_denial_still_reports_the_scope() -> None:
    client = FakeAssetClient(raises=gcp_exceptions.PermissionDenied("caller lacks permission"))

    with pytest.raises(core.ScopeAccessDenied) as caught:
        core.search_resources(filters(scope="projects/locked-down"), client=client)

    message = str(caught.value)
    assert "projects/locked-down" in message
    assert "roles/cloudasset.viewer" in message
    # The upstream detail is preserved rather than replaced by our guess.
    assert "caller lacks permission" in message


# --- project identity -------------------------------------------------------
# CAI reports `project` as a NUMBER and never reports the ID directly. Verified
# against the live API: `project:my-project-id` matches nothing while
# `project:123456789` matches, so an unresolved ID is a silent empty result.


def test_project_id_is_recovered_from_the_parent_path(fake_client: FakeAssetClient) -> None:
    result = core.search_resources(filters(), client=fake_client)

    resource = result.resources[0]
    assert resource.project == "123456", "the raw CAI number is preserved"
    assert resource.project_id == "my-project", "the readable ID is recovered"


def test_project_asset_takes_its_id_from_additional_attributes() -> None:
    """A Project asset has no parent path, but carries projectId directly."""
    client = FakeAssetClient(results=[make_project_result(project_id="edo-thing", number="99")])

    result = core.search_resources(filters(types=("project",)), client=client)

    assert result.resources[0].project_id == "edo-thing"
    assert result.resources[0].project == "99"


def test_project_id_is_none_when_the_parent_is_not_a_project() -> None:
    """A BigQuery table's parent is its dataset. Report nothing rather than guess."""
    client = FakeAssetClient(
        results=[
            make_search_result(
                parent_full_resource_name="//bigquery.googleapis.com/projects/p/datasets/d"
            )
        ]
    )

    result = core.search_resources(filters(), client=client)

    assert result.resources[0].project_id is None
    assert result.resources[0].project == "123456"


def test_project_filter_resolves_an_id_to_a_number() -> None:
    """The bug this fixes: an ID passed through returns nothing, silently."""
    core._PROJECT_NUMBERS.clear()
    client = FakeAssetClient(
        results=[make_search_result()],
        projects=[make_project_result(project_id="edo-thing", number="734077548565")],
    )

    result = core.search_resources(filters(projects=["edo-thing"]), client=client)

    assert result.query == "project:734077548565"
    assert client.last_request.query == "project:734077548565"


def test_project_filter_passes_a_number_through_without_a_lookup() -> None:
    core._PROJECT_NUMBERS.clear()
    client = FakeAssetClient(results=[])

    result = core.search_resources(filters(projects=["734077548565"]), client=client)

    assert result.query == "project:734077548565"
    # One call only: no resolution lookup for something already numeric.
    assert len(client.requests) == 1


def test_resolved_project_numbers_are_cached() -> None:
    """The ID -> number mapping is immutable in GCP, so caching cannot go stale."""
    core._PROJECT_NUMBERS.clear()
    client = FakeAssetClient(
        results=[], projects=[make_project_result(project_id="edo-thing", number="42")]
    )

    core.search_resources(filters(projects=["edo-thing"]), client=client)
    first = len(client.requests)
    core.search_resources(filters(projects=["edo-thing"]), client=client)

    # The second search adds one search call, not a search plus a lookup.
    assert len(client.requests) == first + 1


def test_unresolvable_project_fails_loudly() -> None:
    """Better a clear error than an empty result that looks like 'nothing matched'."""
    core._PROJECT_NUMBERS.clear()
    client = FakeAssetClient(results=[], projects=[])

    with pytest.raises(core.InvalidFilterError, match="Could not resolve project"):
        core.search_resources(filters(projects=["no-such-project"]), client=client)


def test_project_lookup_ignores_a_near_miss() -> None:
    """`name:` is a word match, so the exact projectId must be confirmed."""
    core._PROJECT_NUMBERS.clear()
    client = FakeAssetClient(
        results=[], projects=[make_project_result(project_id="edo-thing-staging", number="7")]
    )

    with pytest.raises(core.InvalidFilterError):
        core.search_resources(filters(projects=["edo-thing"]), client=client)


# --- sorting ----------------------------------------------------------------
# CAI sorts server-side via order_by. Sorting locally would order one page of
# an arbitrary selection, which looks right and is wrong.


def test_sort_is_passed_to_cai(fake_client: FakeAssetClient) -> None:
    core.search_resources(filters(sort=["location", "name DESC"]), client=fake_client)

    assert fake_client.last_request.order_by == "location, name DESC"


def test_unsorted_search_sends_no_order_by(fake_client: FakeAssetClient) -> None:
    core.search_resources(filters(), client=fake_client)

    assert fake_client.last_request.order_by == ""


def test_unknown_sort_field_is_rejected_not_ignored() -> None:
    """A silently dropped sort produces plausible output in the wrong order."""
    client = FakeAssetClient()

    with pytest.raises(core.InvalidFilterError, match="Cannot sort by"):
        core.search_resources(filters(sort=["bogus"]), client=client)
    assert client.last_request is None


def test_invalid_sort_direction_is_rejected() -> None:
    with pytest.raises(core.InvalidFilterError, match="sort direction"):
        core.search_resources(filters(sort=["name SIDEWAYS"]), client=FakeAssetClient())


def test_sort_direction_is_normalised() -> None:
    client = FakeAssetClient(results=[])

    core.search_resources(filters(sort=["name desc"]), client=client)

    assert client.last_request.order_by == "name DESC"


# --- IAM enrichment ---------------------------------------------------------


def test_iam_is_not_fetched_unless_asked(fake_client: FakeAssetClient) -> None:
    result = core.search_resources(filters(), client=fake_client)

    assert fake_client.iam_request is None
    assert result.resources[0].iam_bindings is None
    assert result.iam_note is None


def test_iam_is_one_extra_call_for_the_whole_scope() -> None:
    """Enrichment, not fan-out: a call per resource would be the rejected pattern."""
    client = FakeAssetClient(
        results=[make_search_result(name=f"//x/{i}") for i in range(10)],
        iam_policies=[make_iam_result(resource="//x/3")],
    )

    result = core.search_resources(filters(include_iam=True), client=client)

    assert client.iam_request is not None
    assert client.iam_request.scope == "projects/p"
    assert len(result.resources) == 10
    joined = {r.full_name: r.iam_bindings for r in result.resources}
    assert joined["//x/3"][0].role == "roles/storage.admin"
    assert joined["//x/4"] == [], "no policy attached, but IAM was requested"


def test_iam_request_is_scoped_to_the_same_asset_types() -> None:
    client = FakeAssetClient(results=[], iam_policies=[])

    core.search_resources(filters(types=("bucket",), include_iam=True), client=client)

    assert list(client.iam_request.asset_types) == ["storage.googleapis.com/Bucket"]


def test_iam_response_carries_the_attached_only_caveat() -> None:
    client = FakeAssetClient(results=[make_search_result()], iam_policies=[])

    result = core.search_resources(filters(include_iam=True), client=client)

    assert result.iam_note and "Inherited" in result.iam_note


def test_project_id_recovered_from_a_nested_resources_own_name() -> None:
    """A key's parent is a service account, not a project, so the parent path
    cannot supply the ID -- but the resource's own name carries it."""
    client = FakeAssetClient(
        results=[
            make_search_result(
                name="//iam.googleapis.com/projects/sudoku-eo-2026/serviceAccounts/1/keys/abc",
                asset_type="iam.googleapis.com/ServiceAccountKey",
                parent_full_resource_name=(
                    "//iam.googleapis.com/projects/sudoku-eo-2026/serviceAccounts/sa@x.com"
                ),
            )
        ]
    )

    result = core.search_resources(filters(), client=client)

    assert result.resources[0].project_id == "sudoku-eo-2026"


def test_number_only_resource_is_resolved_via_one_scope_lookup() -> None:
    """Some resources carry only the project number anywhere in their payload.

    Left unresolved they appear in a summary as a separate row from the same
    project's named resources, splitting one project in two.
    """
    core._PROJECT_IDS_BY_SCOPE.clear()
    client = FakeAssetClient(
        results=[
            make_search_result(
                name="//iam.googleapis.com/projects/633423842545/locations/global/pools/p",
                asset_type="iam.googleapis.com/WorkloadIdentityPool",
                project="projects/633423842545",
                parent_full_resource_name=(
                    "//cloudresourcemanager.googleapis.com/projects/633423842545"
                ),
            )
        ],
        projects=[make_project_result(project_id="sudoku-eo-2026", number="633423842545")],
    )

    result = core.search_resources(filters(), client=client)

    assert result.resources[0].project_id == "sudoku-eo-2026"
    assert result.resources[0].project == "633423842545", "the raw number is preserved"


def test_no_scope_lookup_when_every_id_is_already_known(fake_client: FakeAssetClient) -> None:
    """The extra call must only happen when it is needed."""
    core._PROJECT_IDS_BY_SCOPE.clear()

    core.search_resources(filters(), client=fake_client)

    assert len(fake_client.requests) == 1


def test_scope_lookup_is_one_call_for_any_number_of_projects() -> None:
    """One call per scope, never one per project -- that would be the fan-out
    the PRD rejects."""
    core._PROJECT_IDS_BY_SCOPE.clear()
    unresolvable = [
        make_search_result(
            name=f"//iam.googleapis.com/projects/{600 + i}/locations/global/pools/p{i}",
            project=f"projects/{600 + i}",
            parent_full_resource_name="",
        )
        for i in range(50)
    ]
    client = FakeAssetClient(results=unresolvable, projects=[])

    core.search_resources(filters(), client=client)

    # One resource search plus at most one Project lookup.
    assert len(client.requests) == 2


def test_failed_lookup_degrades_to_the_number() -> None:
    """A display nicety must never fail a search."""
    core._PROJECT_IDS_BY_SCOPE.clear()

    class FailsOnLookup(FakeAssetClient):
        def search_all_resources(self, request):
            if list(request.asset_types) == ["cloudresourcemanager.googleapis.com/Project"]:
                raise gcp_exceptions.ServiceUnavailable("nope")
            return super().search_all_resources(request)

    client = FailsOnLookup(
        results=[
            make_search_result(
                name="//iam.googleapis.com/projects/633423842545/locations/global/pools/p",
                project="projects/633423842545",
                parent_full_resource_name="",
            )
        ]
    )

    result = core.search_resources(filters(), client=client)

    assert result.resources[0].project_id is None
    assert result.resources[0].project == "633423842545"


# --- streaming --------------------------------------------------------------


def test_stream_yields_lazily_without_materialising() -> None:
    """The point of streaming: the first row before the last is fetched."""
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(500)])

    stream = core.stream_resources(filters(limit=None), client=client)
    first = next(stream)

    assert first.full_name == "//x/0"


def test_stream_applies_noise_reduction() -> None:
    client = FakeAssetClient(
        results=[
            make_search_result(name="//x/api", asset_type="serviceusage.googleapis.com/Service"),
            make_search_result(name="//x/bucket"),
        ]
    )

    streamed = list(core.stream_resources(filters(types=()), client=client))

    assert [r.full_name for r in streamed] == ["//x/bucket"]


def test_stream_respects_the_limit() -> None:
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(100)])

    assert len(list(core.stream_resources(filters(limit=7), client=client))) == 7


def test_stream_validates_before_yielding_anything() -> None:
    """A bad scope must fail at once, not part-way through a stream."""
    client = FakeAssetClient()

    with pytest.raises(core.InvalidScopeError):
        next(core.stream_resources(filters(scope="nope"), client=client))
