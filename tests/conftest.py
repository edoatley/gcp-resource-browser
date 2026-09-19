"""Shared test fixtures.

The CAI client is faked throughout. No test makes a network call or needs
credentials, so the suite runs anywhere.
"""

from __future__ import annotations

import pytest
from google.api_core import exceptions as gcp_exceptions
from google.cloud import asset_v1


def make_search_result(
    name: str = "//storage.googleapis.com/buckets/example",
    asset_type: str = "storage.googleapis.com/Bucket",
    display_name: str = "example",
    project: str = "projects/123456",
    location: str = "europe-west2",
    labels: dict[str, str] | None = None,
) -> asset_v1.ResourceSearchResult:
    """Build a real CAI result proto, so field coercion is exercised for real."""
    return asset_v1.ResourceSearchResult(
        name=name,
        asset_type=asset_type,
        display_name=display_name,
        project=project,
        location=location,
        labels=labels or {},
    )


class FakeAssetClient:
    """Stands in for AssetServiceClient.

    Returns `results` from search_all_resources, or raises `raises`. Records the
    request it was given so tests can assert on what was actually sent to CAI.
    """

    def __init__(
        self,
        results: list[asset_v1.ResourceSearchResult] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.raises = raises
        self.last_request: asset_v1.SearchAllResourcesRequest | None = None

    def search_all_resources(self, request: asset_v1.SearchAllResourcesRequest):
        self.last_request = request
        if self.raises is not None:
            raise self.raises
        return iter(self.results)


@pytest.fixture
def fake_client() -> FakeAssetClient:
    return FakeAssetClient(results=[make_search_result()])


@pytest.fixture
def permission_denied() -> gcp_exceptions.PermissionDenied:
    return gcp_exceptions.PermissionDenied("caller lacks permission")
