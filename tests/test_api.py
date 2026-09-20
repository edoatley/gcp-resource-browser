"""Tests for the HTTP surface: status codes, response shape, and schema."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from google.api_core import exceptions as gcp_exceptions

from app import core
from app.api import app
from tests.conftest import (
    FakeAssetClient,
    make_iam_result,
    make_search_result,
    service_disabled_error,
)


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


def test_type_is_optional_and_searches_everything(client: TestClient, use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[make_search_result()]))

    response = client.get("/v1/resources", params={"scope": "projects/p"})

    assert response.status_code == 200
    assert list(fake.last_request.asset_types) == []


def test_suppression_is_reported_in_the_body(client: TestClient, use_fake) -> None:
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(asset_type="serviceusage.googleapis.com/Service", name="//x/a"),
                make_search_result(name="//x/b"),
            ]
        )
    )

    body = client.get("/v1/resources", params={"scope": "projects/p"}).json()

    assert body["count"] == 1
    assert body["suppressed"] == 1
    assert "enabled API services" in body["suppressed_summary"]


def test_show_all_includes_suppressed_resources(client: TestClient, use_fake) -> None:
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(asset_type="serviceusage.googleapis.com/Service", name="//x/a"),
                make_search_result(name="//x/b"),
            ]
        )
    )

    body = client.get(
        "/v1/resources", params={"scope": "projects/p", "show_all": "true"}
    ).json()

    assert body["count"] == 2
    assert body["suppressed"] == 0


def test_summary_counts_a_scope(client: TestClient, use_fake) -> None:
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(name="//x/1", location="europe-west2"),
                make_search_result(name="//x/2", location="europe-west2"),
                make_search_result(
                    name="//x/3", asset_type="compute.googleapis.com/Instance",
                    location="us-central1",
                ),
            ]
        )
    )

    body = client.get("/v1/summary", params={"scope": "projects/p"}).json()

    assert body["total"] == 3
    assert body["by_asset_type"]["storage.googleapis.com/Bucket"] == 2
    assert body["by_location"] == {"europe-west2": 2, "us-central1": 1}


def test_summary_counts_everything_not_a_page(client: TestClient, use_fake) -> None:
    """A summary of a truncated result set would be a lie, so there is no limit."""
    fake = use_fake(
        FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(2500)])
    )

    body = client.get("/v1/summary", params={"scope": "projects/p"}).json()

    assert body["total"] == 2500
    assert fake.last_request is not None


def test_include_iam_attaches_bindings_and_the_caveat(client: TestClient, use_fake) -> None:
    use_fake(
        FakeAssetClient(
            results=[make_search_result(name="//x/1")],
            iam_policies=[make_iam_result(resource="//x/1")],
        )
    )

    body = client.get(
        "/v1/resources", params={"scope": "projects/p", "include_iam": "true"}
    ).json()

    assert body["data"][0]["iam_bindings"][0]["role"] == "roles/storage.admin"
    assert "Inherited" in body["iam_note"]


def test_iam_note_is_absent_when_iam_was_not_requested(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient(results=[make_search_result()]))

    assert client.get("/v1/resources", params={"scope": "projects/p"}).json()["iam_note"] is None


def test_bad_sort_field_is_400(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient())

    response = client.get("/v1/resources", params={"scope": "projects/p", "sort": "bogus"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_filter"


def test_openapi_documents_every_endpoint(client: TestClient) -> None:
    """The exported openapi.yml is only useful if the schema is complete."""
    schema = client.get("/openapi.json").json()

    assert set(schema["paths"]) >= {"/v1/resources", "/v1/summary", "/v1/types"}
    assert "Summary" in schema["components"]["schemas"]
    assert "IamBinding" in schema["components"]["schemas"]


def test_liveness_does_no_io(client: TestClient, monkeypatch) -> None:
    """A liveness probe that called GCP would turn an upstream blip into an
    outage, restarting containers whenever Google had a bad minute."""

    def explode():
        raise AssertionError("healthz must not build a client or call GCP")

    monkeypatch.setattr(core, "get_client", explode)

    assert client.get("/healthz").status_code == 200


def test_readiness_reports_ok_when_credentials_resolve(client: TestClient, use_fake) -> None:
    use_fake(FakeAssetClient())

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_is_503_when_credentials_are_missing(client: TestClient, monkeypatch) -> None:
    """Not ready is a 503, not a crash: a probe must never raise."""

    def no_credentials():
        raise RuntimeError("could not determine Application Default Credentials")

    monkeypatch.setattr(core, "get_client", no_credentials)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert "Application Default Credentials" in response.json()["detail"]


def test_stream_emits_newline_delimited_json(client: TestClient, use_fake) -> None:
    import json

    use_fake(
        FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(3)])
    )

    response = client.get("/v1/resources/stream", params={"scope": "projects/p"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in response.text.splitlines() if line]
    assert len(lines) == 3
    assert json.loads(lines[0])["full_name"] == "//x/0"


def test_stream_applies_noise_reduction(client: TestClient, use_fake) -> None:
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(
                    name="//x/api", asset_type="serviceusage.googleapis.com/Service"
                ),
                make_search_result(name="//x/bucket"),
            ]
        )
    )

    response = client.get("/v1/resources/stream", params={"scope": "projects/p"})

    lines = [line for line in response.text.splitlines() if line]
    assert len(lines) == 1


def test_cache_ttl_is_accepted_and_off_by_default(client: TestClient, use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[make_search_result()]))
    core._RESPONSE_CACHE.clear()

    client.get("/v1/resources", params={"scope": "projects/p"})
    client.get("/v1/resources", params={"scope": "projects/p"})

    assert len(fake.requests) == 2, "no caching without an explicit ttl"
