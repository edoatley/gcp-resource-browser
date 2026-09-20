"""Tests for the HTTP surface: status codes, response shape, and schema."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from google.api_core import exceptions as gcp_exceptions

from app import core
from app.api import app
from tests.conftest import FakeAssetClient, make_search_result, service_disabled_error


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def use_fake(monkeypatch):
    """Point the core at a fake CAI client for the duration of a test."""

    def _use(fake: FakeAssetClient) -> FakeAssetClient:
        monkeypatch.setattr(core, "get_client", lambda: fake)
        return fake

    return _use


def test_returns_resources_for_a_project_scope(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient(results=[make_search_result()]))

    response = client.get("/v1/resources", params={"scope": "projects/p", "type": "bucket"})

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "projects/p"
    assert body["asset_types"] == ["storage.googleapis.com/Bucket"]
    assert body["count"] == 1
    assert body["truncated"] is False
    assert body["data"][0]["display_name"] == "example"


def test_accepts_an_organization_scope(client: TestClient, use_fake) -> None:
    """The API must not be narrower than the CLI."""
    fake = use_fake(FakeAssetClient(results=[make_search_result()]))

    response = client.get("/v1/resources", params={"scope": "organizations/123", "type": "bucket"})

    assert response.status_code == 200
    assert fake.last_request.scope == "organizations/123"


def test_unknown_type_is_400_not_200(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient())

    response = client.get("/v1/resources", params={"scope": "projects/p", "type": "nonsense"})

    assert response.status_code == 400
    assert response.json()["error"] == "unknown_resource_type"


def test_malformed_scope_is_400(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient())

    response = client.get("/v1/resources", params={"scope": "not-a-scope", "type": "bucket"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


def test_permission_denied_is_403(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient(raises=gcp_exceptions.PermissionDenied("nope")))

    response = client.get("/v1/resources", params={"scope": "projects/p", "type": "bucket"})

    assert response.status_code == 403
    assert response.json()["error"] == "permission_denied"


def test_missing_scope_is_422(client: TestClient) -> None:
    """Required query params are validated by FastAPI before reaching the core."""
    assert client.get("/v1/resources", params={"type": "bucket"}).status_code == 422


def test_truncation_is_reported_in_the_body(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(10)]))

    response = client.get(
        "/v1/resources", params={"scope": "projects/p", "type": "bucket", "limit": 2}
    )

    body = response.json()
    assert body["count"] == 2
    assert body["truncated"] is True


def test_openapi_schema_describes_the_response(client: TestClient) -> None:
    """Phase 5's openapi.yml export is only worth anything if the schema is real."""
    schema = client.get("/openapi.json").json()

    assert "/v1/resources" in schema["paths"]
    assert "ResourceList" in schema["components"]["schemas"]
    properties = schema["components"]["schemas"]["Resource"]["properties"]
    assert "full_name" in properties
    assert "labels" in properties


def test_repeated_type_params_search_several_types(client: TestClient, use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[]))

    client.get(
        "/v1/resources", params=[("scope", "organizations/1"), ("type", "bucket"), ("type", "vm")]
    )

    assert list(fake.last_request.asset_types) == [
        "storage.googleapis.com/Bucket",
        "compute.googleapis.com/Instance",
    ]


def test_filters_reach_the_upstream_query(client: TestClient, use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[]))

    client.get(
        "/v1/resources",
        params=[
            ("scope", "organizations/1"),
            ("type", "bucket"),
            ("q", "backup"),
            ("label", "env=prod"),
            ("location", "europe-west2"),
            ("location", "global"),
        ],
    )

    assert fake.last_request.query == ("backup labels.env:prod location:(europe-west2 OR global)")


def test_response_echoes_the_compiled_query(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient(results=[]))

    response = client.get(
        "/v1/resources",
        params=[("scope", "projects/p"), ("type", "bucket"), ("label", "env=prod")],
    )

    assert response.json()["query"] == "labels.env:prod"


def test_invalid_label_filter_is_400(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient())

    response = client.get(
        "/v1/resources",
        params=[("scope", "projects/p"), ("type", "bucket"), ("label", "BadKey=x")],
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_filter"


def test_types_endpoint_lists_the_mapping(client: TestClient) -> None:
    body = client.get("/v1/types").json()
    assert body["bucket"] == "storage.googleapis.com/Bucket"
    assert body["vm"] == "compute.googleapis.com/Instance"


def test_disabled_api_is_503_not_403(client: TestClient, use_fake) -> None:
    """403 would tell a caller to fix their IAM; the deployment is misconfigured."""
    use_fake(FakeAssetClient(raises=service_disabled_error(quota_project="billing-project")))

    response = client.get("/v1/resources", params={"scope": "projects/p", "type": "bucket"})

    assert response.status_code == 503
    assert response.json()["error"] == "api_not_enabled"
