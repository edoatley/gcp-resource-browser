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

from app.models import GrantList, Resource, RoleList, Summary


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


def summary_to_csv(summary: Summary) -> str:
    """One row per (dimension, key) count -- the long form, which pivots cleanly.

    Suppressed resources get their own row rather than being left out, for the
    same reason the table reports them: a total that hides what it excluded
    misstates the scope.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("dimension", "key", "count"))
    writer.writerow(("total", summary.scope, summary.total))
    writer.writerow(("suppressed", summary.scope, summary.suppressed))
    for dimension, counts in (
        ("asset_type", summary.by_asset_type),
        ("project", summary.by_project),
        ("location", summary.by_location),
    ):
        for key, count in counts.items():
            writer.writerow((dimension, key, count))
    return buffer.getvalue()


GRANT_CSV_COLUMNS = (
    "risk",
    "member",
    "member_type",
    "role",
    "resource",
    "asset_type",
    "matched_permissions",
    "condition",
)


def grants_to_csv(grants: GrantList) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=GRANT_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for grant in grants.grants:
        row = grant.model_dump(mode="json")
        row["matched_permissions"] = " ".join(row["matched_permissions"])
        writer.writerow(row)
    return buffer.getvalue()


def roles_to_csv(roles: RoleList) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("name", "title", "risk", "permissions", "sources"))
    for role in roles.data:
        writer.writerow(
            (
                role.name,
                role.title or "",
                role.risk,
                " ".join(p.permission for p in role.permissions),
                " ".join(sorted({s for p in role.permissions for s in p.sources})),
            )
        )
    return buffer.getvalue()
