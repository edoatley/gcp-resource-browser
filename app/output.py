"""Machine-readable output formats for the CLI.

A CLI that only prints a table is only usable by a human. These make it
composable with jq, spreadsheets and pipelines -- the PRD asks for a tool
operators can build on, not only read.
"""

from __future__ import annotations

import csv
import io
import json
from enum import StrEnum

from app.models import Resource


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    CSV = "csv"


# Flat columns for CSV. Nested data (labels, IAM bindings) is rendered as
# compact JSON in its cell rather than being dropped: a spreadsheet user can
# still read it, and silently omitting it would misrepresent the resource.
CSV_COLUMNS = (
    "full_name",
    "asset_type",
    "display_name",
    "project_id",
    "project",
    "location",
    "state",
    "create_time",
    "labels",
)


def to_json(resources: list[Resource]) -> str:
    return json.dumps([r.model_dump(mode="json", exclude_none=True) for r in resources], indent=2)


def to_csv(resources: list[Resource]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for resource in resources:
        row = resource.model_dump(mode="json")
        row["labels"] = json.dumps(row.get("labels") or {}, separators=(",", ":"))
        writer.writerow({column: row.get(column) for column in CSV_COLUMNS})
    return buffer.getvalue()
