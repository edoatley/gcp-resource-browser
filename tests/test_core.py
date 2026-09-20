"""Tests for the CAI access layer."""

from __future__ import annotations

import pytest
from google.api_core import exceptions as gcp_exceptions

from app import core
from tests.conftest import FakeAssetClient, make_search_result


def test_maps_cai_fields_onto_the_model(fake_client: FakeAssetClient) -> None:
    result = core.search_resources("projects/my-project", ["bucket"], client=fake_client)

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
    result = core.search_resources("projects/my-project", ["bucket"], client=fake_client)
    assert result.resources[0].create_time is None


def test_sends_the_resolved_asset_type_upstream(fake_client: FakeAssetClient) -> None:
    core.search_resources("organizations/123", ["cloudrun"], client=fake_client)

    request = fake_client.last_request
    assert request.scope == "organizations/123"
    assert list(request.asset_types) == ["run.googleapis.com/Service"]


@pytest.mark.parametrize("scope", ["projects/p", "folders/123", "organizations/123"])
def test_accepts_every_cai_scope_kind(scope: str, fake_client: FakeAssetClient) -> None:
    core.search_resources(scope, ["bucket"], client=fake_client)
    assert fake_client.last_request.scope == scope


@pytest.mark.parametrize(
    "scope",
    ["my-project", "project/my-project", "buckets/x", "", "organizations/"],
)
def test_rejects_malformed_scope_without_calling_cai(scope: str) -> None:
    client = FakeAssetClient()
    with pytest.raises(core.InvalidScopeError):
        core.search_resources(scope, ["bucket"], client=client)
    # Validation must happen before the call, not after a confusing upstream error.
    assert client.last_request is None


def test_rejects_unknown_resource_type() -> None:
    client = FakeAssetClient()
    with pytest.raises(core.UnknownResourceTypeError):
        core.search_resources("projects/p", ["nonsense"], client=client)
    assert client.last_request is None


def test_truncates_and_flags_when_limit_is_hit() -> None:
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(10)])

    result = core.search_resources("projects/p", ["bucket"], limit=3, client=client)

    assert result.truncated is True
    assert len(result.resources) == 3


def test_not_truncated_when_results_fit_exactly() -> None:
    """Off-by-one guard: exactly `limit` results is not truncation."""
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(3)])

    result = core.search_resources("projects/p", ["bucket"], limit=3, client=client)

    assert result.truncated is False
    assert len(result.resources) == 3


def test_limit_none_returns_everything() -> None:
    client = FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(50)])

    result = core.search_resources("projects/p", ["bucket"], limit=None, client=client)

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
        core.search_resources("projects/p", ["bucket"], client=client)


def test_rejects_a_bare_string_of_types() -> None:
    """A str satisfies Sequence[str] and would iterate character by character."""
    client = FakeAssetClient()
    with pytest.raises(core.UnknownResourceTypeError, match="not the string"):
        core.search_resources("projects/p", "bucket", client=client)
    assert client.last_request is None


def test_searches_several_asset_types_in_one_call(fake_client: FakeAssetClient) -> None:
    """Multiple types are one upstream request, not one request per type."""
    core.search_resources("organizations/123", ["bucket", "cloudrun"], client=fake_client)

    assert list(fake_client.last_request.asset_types) == [
        "storage.googleapis.com/Bucket",
        "run.googleapis.com/Service",
    ]


def test_duplicate_types_are_collapsed(fake_client: FakeAssetClient) -> None:
    core.search_resources(
        "projects/p", ["bucket", "storage.googleapis.com/Bucket"], client=fake_client
    )
    assert list(fake_client.last_request.asset_types) == ["storage.googleapis.com/Bucket"]


def test_raw_asset_type_passes_through(fake_client: FakeAssetClient) -> None:
    core.search_resources("projects/p", ["dns.googleapis.com/ManagedZone"], client=fake_client)
    assert list(fake_client.last_request.asset_types) == ["dns.googleapis.com/ManagedZone"]


def test_asset_type_regex_passes_through(fake_client: FakeAssetClient) -> None:
    """CAI accepts RE2 patterns for asset types."""
    core.search_resources("projects/p", ["compute.googleapis.com/.*"], client=fake_client)
    assert list(fake_client.last_request.asset_types) == ["compute.googleapis.com/.*"]


def test_filters_are_compiled_into_the_upstream_query(fake_client: FakeAssetClient) -> None:
    """The whole point of Phase 2: filtering happens server-side, not locally."""
    core.search_resources(
        "organizations/123",
        ["bucket"],
        free_text="backup",
        labels=["env=prod"],
        locations=["europe-west2", "europe-west1"],
        client=fake_client,
    )

    assert fake_client.last_request.query == (
        "backup labels.env:prod location:(europe-west2 OR europe-west1)"
    )


def test_result_echoes_the_query_that_was_sent(fake_client: FakeAssetClient) -> None:
    result = core.search_resources(
        "projects/p", ["bucket"], labels=["env=prod"], client=fake_client
    )
    assert result.query == "labels.env:prod"


def test_bad_filter_raises_before_calling_cai() -> None:
    client = FakeAssetClient()
    with pytest.raises(core.InvalidFilterError):
        core.search_resources("projects/p", ["bucket"], labels=["NotAValidKey=x"], client=client)
    assert client.last_request is None


def test_empty_type_list_is_rejected() -> None:
    client = FakeAssetClient()
    with pytest.raises(core.UnknownResourceTypeError):
        core.search_resources("projects/p", [], client=client)
    assert client.last_request is None
