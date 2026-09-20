"""Tests for the CLI surface: rendering and exit codes."""

from __future__ import annotations

import pytest
from google.api_core import exceptions as gcp_exceptions
from typer.testing import CliRunner

from app import cli as cli_module
from app import core
from tests.conftest import FakeAssetClient, make_search_result, service_disabled_error

runner = CliRunner()


@pytest.fixture
def use_fake(monkeypatch):
    def _use(fake: FakeAssetClient) -> FakeAssetClient:
        monkeypatch.setattr(core, "get_client", lambda: fake)
        return fake

    return _use


def test_renders_a_table_of_results(use_fake) -> None:
    use_fake(FakeAssetClient(results=[make_search_result(display_name="my-bucket")]))

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket"])

    assert result.exit_code == 0
    assert "my-bucket" in result.output
    assert "europe-west2" in result.output


def test_reports_an_empty_result_set(use_fake) -> None:
    use_fake(FakeAssetClient(results=[]))

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket"])

    assert result.exit_code == 0
    assert "No resources found" in result.output


def test_unknown_type_exits_with_usage_code(use_fake) -> None:
    use_fake(FakeAssetClient())

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "nonsense"])

    assert result.exit_code == cli_module.EXIT_USAGE


def test_malformed_scope_exits_with_usage_code(use_fake) -> None:
    use_fake(FakeAssetClient())

    result = runner.invoke(cli_module.cli, ["list-resources", "nope", "bucket"])

    assert result.exit_code == cli_module.EXIT_USAGE


def test_permission_denied_exits_with_permission_code(use_fake) -> None:
    use_fake(FakeAssetClient(raises=gcp_exceptions.PermissionDenied("nope")))

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket"])

    assert result.exit_code == cli_module.EXIT_PERMISSION


def test_warns_when_output_was_truncated(use_fake) -> None:
    """A cap that silently shortens results would misrepresent the estate."""
    use_fake(FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(10)]))

    result = runner.invoke(
        cli_module.cli, ["list-resources", "projects/p", "bucket", "--limit", "2"]
    )

    assert result.exit_code == 0
    assert "more exist" in result.output


def test_passes_the_query_filter_through(use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[]))

    runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket", "-q", "name:backup"])

    assert fake.last_request.query == "name:backup"


def test_search_compiles_filters_into_the_query(use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[]))

    result = runner.invoke(
        cli_module.cli,
        [
            "search",
            "organizations/123",
            "backup",
            "--type",
            "bucket",
            "--label",
            "env=prod",
            "--location",
            "europe-west2",
        ],
    )

    assert result.exit_code == 0
    assert fake.last_request.query == "backup labels.env:prod location:europe-west2"


def test_search_accepts_repeated_types(use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[]))

    runner.invoke(
        cli_module.cli,
        ["search", "projects/p", "--type", "bucket", "--type", "cloudrun"],
    )

    assert list(fake.last_request.asset_types) == [
        "storage.googleapis.com/Bucket",
        "run.googleapis.com/Service",
    ]


def test_show_query_prints_the_compiled_query(use_fake) -> None:
    use_fake(FakeAssetClient(results=[make_search_result()]))

    result = runner.invoke(
        cli_module.cli,
        ["search", "projects/p", "--type", "bucket", "--label", "env=prod", "--show-query"],
    )

    assert "labels.env:prod" in result.output


def test_empty_result_explains_which_query_ran(use_fake) -> None:
    """Nothing matched, or the filter compiled wrong? The user must be able to tell."""
    use_fake(FakeAssetClient(results=[]))

    result = runner.invoke(
        cli_module.cli,
        ["search", "projects/p", "--type", "bucket", "--label", "env=prod"],
    )

    assert "No resources found" in result.output
    assert "labels.env:prod" in result.output


def test_invalid_label_exits_with_usage_code(use_fake) -> None:
    use_fake(FakeAssetClient())

    result = runner.invoke(
        cli_module.cli,
        ["search", "projects/p", "--type", "bucket", "--label", "BadKey=x"],
    )

    assert result.exit_code == cli_module.EXIT_USAGE


def test_types_command_lists_the_mapping() -> None:
    result = runner.invoke(cli_module.cli, ["types"])

    assert result.exit_code == 0
    assert "bucket" in result.output
    assert "storage.googleapis.com/Bucket" in result.output


def test_disabled_api_exits_with_its_own_code(use_fake) -> None:
    """Distinct from EXIT_UPSTREAM: the remedy is to enable an API, not retry."""
    use_fake(FakeAssetClient(raises=service_disabled_error(quota_project="billing-project")))

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket"])

    assert result.exit_code == cli_module.EXIT_NOT_CONFIGURED
    assert "gcloud services enable" in result.output
