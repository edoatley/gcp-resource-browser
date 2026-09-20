"""Shared test fixtures.

The CAI client is faked throughout. No test makes a network call or needs
credentials, so the suite runs anywhere.
"""

from __future__ import annotations

import pytest
from google.api_core import exceptions as gcp_exceptions
from google.cloud import asset_v1
from google.rpc.error_details_pb2 import ErrorInfo

from app.params import SearchFilters


def filters(scope: str = "projects/p", types: tuple[str, ...] = ("bucket",), **kwargs):
    """Build SearchFilters with defaults, so a test names only what it varies."""
    return SearchFilters(scope=scope, resource_types=list(types), **kwargs)


def make_search_result(
    name: str = "//storage.googleapis.com/buckets/example",
    asset_type: str = "storage.googleapis.com/Bucket",
    display_name: str = "example",
    project: str = "projects/123456",
    location: str = "europe-west2",
    labels: dict[str, str] | None = None,
    parent_full_resource_name: str = "//cloudresourcemanager.googleapis.com/projects/my-project",
    additional_attributes: dict[str, str] | None = None,
) -> asset_v1.ResourceSearchResult:
    """Build a real CAI result proto, so field coercion is exercised for real."""
    return asset_v1.ResourceSearchResult(
        name=name,
        asset_type=asset_type,
        display_name=display_name,
        project=project,
        location=location,
        labels=labels or {},
        parent_full_resource_name=parent_full_resource_name,
        # A protobuf Struct: must be set at construction, not mutated after.
        additional_attributes=additional_attributes or {},
    )


def make_project_result(
    project_id: str = "my-project", number: str = "123456"
) -> asset_v1.ResourceSearchResult:
    """A CAI Project asset, which carries its ID in additional_attributes."""
    return make_search_result(
        name=f"//cloudresourcemanager.googleapis.com/projects/{project_id}",
        asset_type="cloudresourcemanager.googleapis.com/Project",
        display_name="My Project",
        project=f"projects/{number}",
        parent_full_resource_name="",
        additional_attributes={"projectId": project_id},
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
        projects: list[asset_v1.ResourceSearchResult] | None = None,
        iam_policies: list[asset_v1.IamPolicySearchResult] | None = None,
    ) -> None:
        self.results = results or []
        self.raises = raises
        # Answers the project ID -> number lookup, which is a separate CAI call.
        self.projects = projects or []
        self.iam_policies = iam_policies or []
        self.last_request: asset_v1.SearchAllResourcesRequest | None = None
        self.requests: list[asset_v1.SearchAllResourcesRequest] = []
        self.iam_request: asset_v1.SearchAllIamPoliciesRequest | None = None

    def search_all_iam_policies(self, request: asset_v1.SearchAllIamPoliciesRequest):
        self.iam_request = request
        if self.raises is not None:
            raise self.raises
        return iter(self.iam_policies)

    def search_all_resources(self, request: asset_v1.SearchAllResourcesRequest):
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        # The project ID -> number lookup is a Project search with a name: query.
        # Discriminate on both, so a user legitimately searching for Project
        # assets still gets `results`.
        is_lookup = list(request.asset_types) == [
            "cloudresourcemanager.googleapis.com/Project"
        ] and request.query.startswith('name:"')
        if is_lookup:
            return iter(self.projects)
        self.last_request = request
        return iter(self.results)


def make_iam_result(
    resource: str = "//storage.googleapis.com/buckets/example",
    role: str = "roles/storage.admin",
    members: tuple[str, ...] = ("user:someone@example.com",),
) -> asset_v1.IamPolicySearchResult:
    """A CAI IAM policy result; `resource` is the join key."""
    result = asset_v1.IamPolicySearchResult(resource=resource)
    binding = result.policy.bindings.add()
    binding.role = role
    binding.members.extend(members)
    return result


@pytest.fixture
def fake_client() -> FakeAssetClient:
    return FakeAssetClient(results=[make_search_result()])


@pytest.fixture
def permission_denied() -> gcp_exceptions.PermissionDenied:
    return gcp_exceptions.PermissionDenied("caller lacks permission")


def service_disabled_error(
    service: str = "cloudasset.googleapis.com",
    quota_project: str = "my-quota-project",
) -> gcp_exceptions.PermissionDenied:
    """A 403 that is really "API not enabled".

    Mirrors the structure Google actually returns, captured from a live call:
    a PermissionDenied carrying an ErrorInfo with reason SERVICE_DISABLED, whose
    metadata names the *billing* project rather than the scope searched.
    """
    info = ErrorInfo(
        reason="SERVICE_DISABLED",
        domain="googleapis.com",
        metadata={
            "service": service,
            "containerInfo": quota_project,
            "consumer": f"projects/{quota_project}",
            "serviceTitle": "Cloud Asset API",
        },
    )
    return gcp_exceptions.PermissionDenied(
        f"{service} has not been used in project {quota_project} before or it is disabled.",
        details=[info],
    )
