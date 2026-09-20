"""Machine-readable output formats."""

from __future__ import annotations

import csv
import io
import json

from app.models import IamBinding, Resource
from app.output import CSV_COLUMNS, to_csv, to_json


def sample() -> list[Resource]:
    return [
        Resource(
            full_name="//storage.googleapis.com/b1",
            asset_type="storage.googleapis.com/Bucket",
            display_name="b1",
            project="734077548565",
            project_id="my-project",
            location="europe-west2",
            labels={"env": "prod", "team": "platform"},
        )
    ]


def test_json_is_parseable_and_keeps_nested_data() -> None:
    parsed = json.loads(to_json(sample()))

    assert parsed[0]["labels"] == {"env": "prod", "team": "platform"}
    assert parsed[0]["project_id"] == "my-project"


def test_json_omits_unset_fields_rather_than_emitting_nulls() -> None:
    parsed = json.loads(to_json(sample()))

    assert "iam_bindings" not in parsed[0], "not requested, so should not appear"


def test_json_includes_iam_when_present() -> None:
    resources = sample()
    resources[0].iam_bindings = [IamBinding(role="roles/storage.admin", members=["user:a"])]

    parsed = json.loads(to_json(resources))

    assert parsed[0]["iam_bindings"][0]["role"] == "roles/storage.admin"


def test_csv_round_trips_through_a_reader() -> None:
    rows = list(csv.DictReader(io.StringIO(to_csv(sample()))))

    assert len(rows) == 1
    assert rows[0]["display_name"] == "b1"
    assert rows[0]["project_id"] == "my-project"


def test_csv_header_matches_the_declared_columns() -> None:
    header = to_csv(sample()).splitlines()[0]

    assert header.split(",") == list(CSV_COLUMNS)


def test_csv_keeps_labels_as_json_rather_than_dropping_them() -> None:
    """Silently omitting nested data would misrepresent the resource."""
    rows = list(csv.DictReader(io.StringIO(to_csv(sample()))))

    assert json.loads(rows[0]["labels"]) == {"env": "prod", "team": "platform"}


def test_csv_handles_an_empty_result() -> None:
    output = to_csv([])

    assert output.strip().split(",") == list(CSV_COLUMNS), "header only"
