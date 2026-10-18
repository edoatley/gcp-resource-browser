"""Effective IAM: inherited bindings attached alongside attached ones.

The fake answers as the live API was observed to: at most 20 names a call,
and one unknown name failing the whole call with NotFound.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from google.api_core import exceptions as gcp_exceptions
from typer.testing import CliRunner

from app import api, cli, core, effective
from app.fanout import search_scopes
from app.gcloud import search_command
from tests.conftest import (
    FakeAssetClient,
    filters,
    make_iam_result,
    make_search_result,
    service_disabled_error,
)

PROJECT = "//cloudresourcemanager.googleapis.com/projects/my-project"
BUCKET = "//storage.googleapis.com/example"


def _bucket(name: str = BUCKET):
    return make_search_result(name=name)


def _own_and_project(name: str) -> list:
    """What the API returns for a bucket in a project: its own policy, then the project's."""
    return [
        (name, {"roles/storage.legacyBucketOwner": ("projectOwner:my-project",)}),
        (PROJECT, {"roles/owner": ("user:owner@example.com",)}),
    ]


def _client(names: list[str], **kwargs) -> FakeAssetClient:
    return FakeAssetClient(
        results=[_bucket(n) for n in names],
        iam_policies=[make_iam_result(resource=n) for n in names],
        effective={n: _own_and_project(n) for n in names},
        **kwargs,
    )


def test_inherited_bindings_follow_the_attached_ones_marked_with_their_ancestor() -> None:
    client = _client([BUCKET])

    result = core.search_resources(filters(include_iam=True, effective_iam=True), client=client)

    [resource] = result.resources
    assert [(b.role, b.inherited_from) for b in resource.iam_bindings] == [
        ("roles/storage.admin", None),
        ("roles/owner", PROJECT),
    ]
    assert result.iam_errors == {}


def test_the_resources_own_policy_from_the_effective_call_is_not_repeated() -> None:
    """The attached path already supplies it; the legacy role must not appear."""
    client = _client([BUCKET])

    result = core.search_resources(filters(include_iam=True, effective_iam=True), client=client)

    roles = [b.role for b in result.resources[0].iam_bindings]
    assert "roles/storage.legacyBucketOwner" not in roles


def test_effective_without_include_iam_is_rejected_not_ignored() -> None:
    client = _client([BUCKET])

    with pytest.raises(core.InvalidFilterError, match="include_iam"):
        core.search_resources(filters(effective_iam=True), client=client)
    assert client.requests == [], "rejected before any call"


def test_names_are_batched_by_twenty() -> None:
    names = [f"//storage.googleapis.com/b{i}" for i in range(45)]
    client = _client(names)

    result = core.search_resources(filters(include_iam=True, effective_iam=True), client=client)

    assert sorted(len(r.names) for r in client.effective_requests) == [5, 20, 20]
    assert {r.scope for r in client.effective_requests} == {"projects/p"}
    assert all(any(b.inherited_from for b in r.iam_bindings) for r in result.resources)


def test_concurrency_is_bounded() -> None:
    names = [f"//storage.googleapis.com/b{i}" for i in range(20 * 12)]
    client = _client(names)
    in_flight, peak, lock = 0, 0, threading.Lock()
    original = client.batch_get_effective_iam_policies

    def slow(request):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.02)
        try:
            return original(request)
        finally:
            with lock:
                in_flight -= 1

    client.batch_get_effective_iam_policies = slow
    resources = [core._to_resource(_bucket(n)) for n in names]

    effective.attach_inherited(resources, "projects/p", client, max_concurrency=3)

    assert len(client.effective_requests) == 12
    assert peak <= 3


def test_one_unknown_name_loses_only_itself() -> None:
    """Verified live: one unknown name fails its whole batch with NotFound."""
    names = [f"//storage.googleapis.com/b{i}" for i in range(3)]
    client = _client(names)
    del client.effective[names[1]]

    result = core.search_resources(filters(include_iam=True, effective_iam=True), client=client)

    by_name = {r.full_name: r for r in result.resources}
    assert list(result.iam_errors) == [names[1]]
    assert "Upstream said" in result.iam_errors[names[1]]
    assert any(b.inherited_from for b in by_name[names[0]].iam_bindings)
    assert any(b.inherited_from for b in by_name[names[2]].iam_bindings)
    # Its attached binding is kept, never dropped.
    assert [b.inherited_from for b in by_name[names[1]].iam_bindings] == [None]
    # One batch, then one call per name in it.
    assert [len(r.names) for r in client.effective_requests] == [3, 1, 1, 1]
    assert "could not be read for 1 resource(s)" in result.iam_note


def test_every_name_unknown_is_reported_not_raised() -> None:
    """Not a systemic failure: each resource's absence is its own fact."""
    client = _client([BUCKET])
    client.effective = {}

    result = core.search_resources(filters(include_iam=True, effective_iam=True), client=client)

    assert list(result.iam_errors) == [BUCKET]


def test_a_denied_call_with_nothing_read_raises() -> None:
    client = _client([BUCKET], effective_raises=gcp_exceptions.PermissionDenied("no"))

    with pytest.raises(core.ScopeAccessDenied, match="analyzeIamPolicy"):
        core.search_resources(filters(include_iam=True, effective_iam=True), client=client)


def test_a_disabled_api_names_the_billing_project() -> None:
    client = _client([BUCKET], effective_raises=service_disabled_error())

    with pytest.raises(core.ApiNotEnabledError, match="my-quota-project"):
        core.search_resources(filters(include_iam=True, effective_iam=True), client=client)


def test_more_than_twenty_names_is_refused_before_calling() -> None:
    client = _client([])

    with pytest.raises(ValueError, match="20"):
        core.effective_iam_policies("projects/p", [f"//x/{i}" for i in range(21)], client)
    assert client.effective_requests == []


def test_note_states_the_scope_bound() -> None:
    project = core.effective_iam_note("projects/p", {})
    assert "up to and including projects/p" in project
    assert "above projects/p" in project and "NOT included" in project

    org = core.effective_iam_note("organizations/1", {})
    assert "top of the hierarchy" in org
    assert "NOT included" not in org


def test_cache_key_distinguishes_effective() -> None:
    plain = core._cache_key(filters(include_iam=True))
    with_effective = core._cache_key(filters(include_iam=True, effective_iam=True))
    assert plain != with_effective


def test_fanout_merges_iam_errors_across_scopes() -> None:
    names = ["//storage.googleapis.com/a", "//storage.googleapis.com/b"]
    client = _client(names)
    del client.effective[names[0]]

    merged = search_scopes(
        ["projects/p", "projects/q"], filters(include_iam=True, effective_iam=True), client
    )

    assert set(merged.iam_errors) == {names[0]}


# --- Byte-identical without the flag ------------------------------------------


def test_api_body_without_effective_has_no_new_keys() -> None:
    fake = _client([BUCKET])
    original = core.get_client
    core.get_client = lambda: fake
    try:
        body = (
            TestClient(api.app)
            .get("/v1/resources", params={"scope": "projects/p", "include_iam": "true"})
            .text
        )
    finally:
        core.get_client = original

    assert "inherited_from" not in body
    assert "iam_errors" not in body
    assert fake.effective_requests == []
    assert json.loads(body)["data"][0]["iam_bindings"] == [
        {"role": "roles/storage.admin", "members": ["user:someone@example.com"]}
    ]


def test_cli_json_without_effective_has_no_new_keys(monkeypatch) -> None:
    fake = _client([BUCKET])
    monkeypatch.setattr(core, "get_client", lambda: fake)

    result = CliRunner().invoke(cli.cli, ["search", "projects/p", "--include-iam", "-o", "json"])

    assert result.exit_code == 0, result.output
    assert "inherited_from" not in result.stdout
    assert fake.effective_requests == []


# --- Surfaces ------------------------------------------------------------------


def test_api_returns_inherited_bindings_and_iam_errors(monkeypatch) -> None:
    names = [BUCKET, "//storage.googleapis.com/gone"]
    fake = _client(names)
    del fake.effective[names[1]]
    monkeypatch.setattr(core, "get_client", lambda: fake)

    body = (
        TestClient(api.app)
        .get(
            "/v1/resources",
            params={"scope": "projects/p", "include_iam": "true", "effective": "true"},
        )
        .json()
    )

    inherited = [b for b in body["data"][0]["iam_bindings"] if "inherited_from" in b]
    assert inherited == [
        {"role": "roles/owner", "members": ["user:owner@example.com"], "inherited_from": PROJECT}
    ]
    assert list(body["iam_errors"]) == [names[1]]
    assert "inherits from its ancestors" in body["iam_note"]


def test_api_rejects_effective_without_include_iam(monkeypatch) -> None:
    monkeypatch.setattr(core, "get_client", lambda: _client([BUCKET]))

    response = TestClient(api.app).get(
        "/v1/resources", params={"scope": "projects/p", "effective": "true"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_filter"


def test_cli_exits_partial_and_names_each_unread_resource(monkeypatch) -> None:
    names = [BUCKET, "//storage.googleapis.com/gone"]
    fake = _client(names)
    del fake.effective[names[1]]
    monkeypatch.setattr(core, "get_client", lambda: fake)

    result = CliRunner().invoke(
        cli.cli, ["search", "projects/p", "--include-iam", "--effective", "-o", "json"]
    )

    assert result.exit_code == cli.EXIT_PARTIAL
    rows = json.loads(result.stdout)
    assert any(b.get("inherited_from") == PROJECT for b in rows[0]["iam_bindings"])
    assert f"Inherited IAM not read for {names[1]}" in " ".join(result.stderr.split())


def test_cli_also_scope_exits_partial_on_iam_errors(monkeypatch) -> None:
    fake = _client([BUCKET])
    fake.effective = {}
    monkeypatch.setattr(core, "get_client", lambda: fake)

    result = CliRunner().invoke(
        cli.cli,
        ["search", "projects/p", "--also-scope", "projects/q", "--include-iam", "--effective"],
    )

    assert result.exit_code == cli.EXIT_PARTIAL


def test_gcloud_rendering_adds_the_effective_command() -> None:
    command = search_command(
        "projects/p",
        ["storage.googleapis.com/Bucket"],
        "",
        include_iam=True,
        effective_iam=True,
        billing_project="quota-proj",
    )

    assert command.endswith(
        "# --effective: inherited bindings, for the names above, at most 20 per command.\n"
        "gcloud asset get-effective-iam-policy \\\n"
        "    --scope=projects/p \\\n"
        "    --names=NAME[,NAME...] \\\n"
        "    --billing-project=quota-proj"
    )


def test_gcloud_rendering_without_effective_is_unchanged() -> None:
    args = ("projects/p", ["storage.googleapis.com/Bucket"], "")
    assert search_command(*args, include_iam=True) == search_command(
        *args, include_iam=True, effective_iam=False
    )
    assert "get-effective-iam-policy" not in search_command(*args, include_iam=True)
