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


def test_table_shows_the_project_id_not_the_number(use_fake) -> None:
    """CAI reports a number; an operator needs the ID they recognise."""
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(
                    project="projects/734077548565",
                    parent_full_resource_name=(
                        "//cloudresourcemanager.googleapis.com/projects/idp-prototype-edo"
                    ),
                )
            ]
        )
    )

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket"])

    assert "idp-prototype-edo" in result.output
    assert "734077548565" not in result.output


def test_table_falls_back_to_the_number_when_no_id_is_available(use_fake) -> None:
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(
                    project="projects/734077548565",
                    parent_full_resource_name="//bigquery.googleapis.com/projects/p/datasets/d",
                )
            ]
        )
    )

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket"])

    assert "734077548565" in result.output


def test_long_names_do_not_starve_the_other_columns(use_fake) -> None:
    """A no_wrap name column used to squeeze every other column to an ellipsis."""
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(
                    display_name="a-very-long-resource-name-that-would-dominate-the-table-width",
                    asset_type="iam.googleapis.com/ServiceAccount",
                    location="europe-west2",
                )
            ]
        )
    )

    result = runner.invoke(cli_module.cli, ["list-resources", "projects/p", "bucket"])

    # Type and Location must survive intact even beside an overlong name.
    assert "ServiceAccount" in result.output
    assert "europe-west2" in result.output


def test_search_without_a_type_searches_everything(use_fake) -> None:
    fake = use_fake(FakeAssetClient(results=[make_search_result()]))

    result = runner.invoke(cli_module.cli, ["search", "projects/p"])

    assert result.exit_code == 0
    assert list(fake.last_request.asset_types) == []


def test_suppression_is_always_reported(use_fake) -> None:
    """Hidden rows must never go unmentioned in an audit tool."""
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(asset_type="serviceusage.googleapis.com/Service", name="//x/a"),
                make_search_result(name="//x/b", display_name="real-bucket"),
            ]
        )
    )

    result = runner.invoke(cli_module.cli, ["search", "projects/p"])

    assert "real-bucket" in result.output
    assert "1 hidden" in result.output
    assert "--show-all" in result.output


def test_show_all_flag_disables_suppression(use_fake) -> None:
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(
                    asset_type="serviceusage.googleapis.com/Service",
                    name="//x/a",
                    display_name="storage-api",
                )
            ]
        )
    )

    result = runner.invoke(cli_module.cli, ["search", "projects/p", "--show-all"])

    assert "storage-api" in result.output
    assert "hidden" not in result.output


def test_fully_suppressed_result_explains_itself(use_fake) -> None:
    """'No resources found' alone would be misleading when everything was hidden."""
    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(asset_type="serviceusage.googleapis.com/Service", name="//x/a")
            ]
        )
    )

    result = runner.invoke(cli_module.cli, ["search", "projects/p"])

    assert "after hiding 1" in result.output
    assert "--show-all" in result.output


def test_json_output_is_clean_on_stdout(use_fake) -> None:
    """A pipeline reading stdout must get valid JSON and nothing else."""
    import json

    use_fake(FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(25)]))

    result = runner.invoke(
        cli_module.cli, ["search", "projects/p", "--type", "bucket", "-o", "json", "-n", "5"]
    )

    parsed = json.loads(result.stdout)
    assert len(parsed) == 5


def test_warnings_go_to_stderr_not_into_the_payload(use_fake) -> None:
    import json

    use_fake(
        FakeAssetClient(
            results=[
                make_search_result(name="//x/a", asset_type="serviceusage.googleapis.com/Service"),
                make_search_result(name="//x/b"),
            ]
        )
    )

    result = runner.invoke(cli_module.cli, ["search", "projects/p", "-o", "json"])

    json.loads(result.stdout)  # must not raise
    assert "hidden" not in result.stdout


def test_csv_output_parses(use_fake) -> None:
    import csv
    import io

    use_fake(FakeAssetClient(results=[make_search_result(display_name="b1")]))

    result = runner.invoke(
        cli_module.cli, ["search", "projects/p", "--type", "bucket", "-o", "csv"]
    )

    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    assert rows[0]["display_name"] == "b1"


def test_summary_command_reports_totals(use_fake) -> None:
    use_fake(FakeAssetClient(results=[make_search_result(name=f"//x/{i}") for i in range(4)]))

    result = runner.invoke(cli_module.cli, ["summary", "projects/p"])

    assert result.exit_code == 0
    assert "4 resources" in result.output


def test_summary_json_output(use_fake) -> None:
    import json

    use_fake(FakeAssetClient(results=[make_search_result()]))

    result = runner.invoke(cli_module.cli, ["summary", "projects/p", "-o", "json"])

    assert json.loads(result.stdout)["total"] == 1


def test_openapi_export_writes_a_spec(tmp_path) -> None:
    import yaml

    out = tmp_path / "openapi.yml"
    result = runner.invoke(cli_module.cli, ["openapi", "--out", str(out)])

    assert result.exit_code == 0
    spec = yaml.safe_load(out.read_text())
    assert spec["openapi"].startswith("3.")
    assert "/v1/resources" in spec["paths"]
    assert "/v1/summary" in spec["paths"]


def test_bad_sort_exits_with_usage_code(use_fake) -> None:
    use_fake(FakeAssetClient())

    result = runner.invoke(
        cli_module.cli, ["search", "projects/p", "--type", "bucket", "--sort", "bogus"]
    )

    assert result.exit_code == cli_module.EXIT_USAGE
