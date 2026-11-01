"""Shared test fixtures.

The CAI client is faked throughout. No test makes a network call or needs
credentials, so the suite runs anywhere.
"""

from __future__ import annotations

import pytest
from google.api_core import exceptions as gcp_exceptions
from google.cloud import asset_v1
from google.iam.v1.policy_pb2 import Binding, Policy
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
        effective: dict[str, list[tuple[str, dict[str, tuple[str, ...]]]]] | None = None,
        effective_raises: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.raises = raises
        # Answers the project ID -> number lookup, which is a separate CAI call.
        self.projects = projects or []
        self.iam_policies = iam_policies or []
        self.last_request: asset_v1.SearchAllResourcesRequest | None = None
        self.requests: list[asset_v1.SearchAllResourcesRequest] = []
        self.iam_request: asset_v1.SearchAllIamPoliciesRequest | None = None
        self.iam_requests: list[asset_v1.SearchAllIamPoliciesRequest] = []
        # name -> [(attached_resource, {role: members})], resource first, then
        # ancestors, as the API orders them. A name absent from it is unknown.
        self.effective = effective or {}
        self.effective_raises = effective_raises
        self.effective_requests: list[asset_v1.BatchGetEffectiveIamPoliciesRequest] = []

    def batch_get_effective_iam_policies(
        self, request: asset_v1.BatchGetEffectiveIamPoliciesRequest
    ) -> asset_v1.BatchGetEffectiveIamPoliciesResponse:
        """Behaves as the live API was observed to (2026-10-18).

        More than 20 names is InvalidArgument, and one unknown name fails the
        whole call with NotFound rather than being skipped.
        """
        self.effective_requests.append(request)
        if self.effective_raises is not None:
            raise self.effective_raises
        if len(request.names) > 20:
            raise gcp_exceptions.InvalidArgument("Some specified value(s) are invalid.")
        unknown = [n for n in request.names if n not in self.effective]
        if unknown:
            raise gcp_exceptions.NotFound(
                f"No resource found in resource search for <{unknown[0]}> in requested scope "
                f"<{request.scope}>"
            )
        effective = asset_v1.BatchGetEffectiveIamPoliciesResponse.EffectiveIamPolicy
        response = asset_v1.BatchGetEffectiveIamPoliciesResponse(
            policy_results=[
                effective(
                    full_resource_name=name,
                    policies=[
                        effective.PolicyInfo(
                            attached_resource=attached,
                            policy=Policy(
                                bindings=[
                                    Binding(role=role, members=list(members))
                                    for role, members in bindings.items()
                                ]
                            ),
                        )
                        for attached, bindings in self.effective[name]
                    ],
                )
                for name in request.names
            ]
        )
        return response

    def search_all_iam_policies(self, request: asset_v1.SearchAllIamPoliciesRequest):
        self.iam_request = request
        self.iam_requests.append(request)
        if self.raises is not None:
            raise self.raises
        return iter(self.iam_policies)

    def search_all_resources(self, request: asset_v1.SearchAllResourcesRequest):
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        # Two internal lookups search Project assets: ID -> number (with a
        # name: query) and number -> ID (no query). Both are answered from
        # `projects`. A test that wants to search Project assets as a user
        # simply leaves `projects` unset, so this falls through to `results`.
        is_project_lookup = (
            list(request.asset_types) == ["cloudresourcemanager.googleapis.com/Project"]
            and self.projects
        )
        if is_project_lookup:
            return iter(self.projects)
        self.last_request = request
        return iter(self.results)


def make_iam_result(
    resource: str = "//storage.googleapis.com/buckets/example",
    role: str = "roles/storage.admin",
    members: tuple[str, ...] = ("user:someone@example.com",),
    matched: dict[str, list[str]] | None = None,
    extra_bindings: dict[str, tuple[str, ...]] | None = None,
    condition: str = "",
) -> asset_v1.IamPolicySearchResult:
    """A CAI IAM policy result; `resource` is the join key.

    `matched` mimics `explanation.matched_permissions`, which CAI fills only
    for permission queries: role -> the queried permissions that role holds.
    """
    result = asset_v1.IamPolicySearchResult(resource=resource)
    binding = result.policy.bindings.add()
    binding.role = role
    binding.members.extend(members)
    if condition:
        binding.condition.expression = condition
    for extra_role, extra_members in (extra_bindings or {}).items():
        extra = result.policy.bindings.add()
        extra.role = extra_role
        extra.members.extend(extra_members)
    for matched_role, permissions in (matched or {}).items():
        result.explanation.matched_permissions[matched_role] = (
            asset_v1.IamPolicySearchResult.Explanation.Permissions(permissions=permissions)
        )
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


# --- REST fakes (IAM role catalogue, Cloud Billing) --------------------------
#
# Both live behind an AuthorizedSession rather than a generated client, so a
# fake only has to answer `get`. Routing by URL rather than by call order lets
# one session serve a billing search, which interleaves a role-catalogue read,
# an account list and a getIamPolicy per account.

PREDEFINED_ROLES_URL = "iam.googleapis.com/v1/roles"
BILLING_ACCOUNTS_URL = "cloudbilling.googleapis.com/v1/billingAccounts"


class FakeResponse:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body, self.status_code, self.ok = body, status, status < 400

    def json(self) -> dict:
        return self._body


class FakeRestSession:
    """Answers `get` from URL-matched responses; records every call made.

    A route's value may be a list, which is consumed one response per call so
    pagination can be exercised; the last entry answers any further calls.
    """

    def __init__(self, routes: dict[str, FakeResponse | list[FakeResponse]]) -> None:
        self._routes = {
            pattern: list(value) if isinstance(value, list) else [value]
            for pattern, value in routes.items()
        }
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.calls.append((url, dict(params or {})))
        # Longest pattern first, so ".../billingAccounts/X:getIamPolicy" wins
        # over the ".../billingAccounts" list route it contains.
        for pattern in sorted(self._routes, key=len, reverse=True):
            if pattern in url:
                queue = self._routes[pattern]
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"FakeRestSession got an unexpected request: {url}")

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


def role_pages(*roles: tuple[str, list[str]]) -> list[FakeResponse]:
    """Role definitions split across two pages, so pagination is exercised."""
    raw = [{"name": n, "title": n, "stage": "GA", "includedPermissions": p} for n, p in roles]
    return [
        FakeResponse({"roles": raw[:1], "nextPageToken": "next"}),
        FakeResponse({"roles": raw[1:]}),
    ]


def catalogue(*roles: tuple[str, list[str]], **routes) -> FakeRestSession:
    """A session serving the predefined catalogue, plus any extra routes."""
    return FakeRestSession({PREDEFINED_ROLES_URL: role_pages(*roles), **routes})
