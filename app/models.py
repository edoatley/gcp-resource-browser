"""Pydantic models shared by the CLI and the API.

These define the public shape of a result. They are also what gives the
auto-generated OpenAPI schema real content, which Phase 5's `openapi.yml`
export depends on.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class Resource(BaseModel):
    """A single resource returned by Cloud Asset Inventory."""

    full_name: str = Field(
        description=(
            "CAI full resource name, e.g. //storage.googleapis.com/buckets/my-bucket. "
            "This is the join key against IAM policy search."
        )
    )
    asset_type: str = Field(description="CAI asset type, e.g. storage.googleapis.com/Bucket")
    display_name: str | None = Field(default=None, description="Short name of the resource")
    project: str | None = Field(
        default=None, description="Project number or ID owning the resource"
    )
    location: str | None = Field(default=None, description="Region, zone, or multi-region")
    state: str | None = Field(
        default=None, description="Resource state, where the type reports one"
    )
    labels: dict[str, str] = Field(default_factory=dict, description="User-applied labels")
    create_time: datetime | None = Field(default=None, description="Resource creation time")
    parent_full_resource_name: str | None = Field(
        default=None, description="Full resource name of the parent, where CAI reports one"
    )


class ResourceList(BaseModel):
    """Envelope returned by a resource search."""

    scope: str = Field(description="Scope searched, e.g. projects/my-project")
    asset_type: str = Field(description="CAI asset type that was searched for")
    count: int = Field(description="Number of resources in `data`")
    truncated: bool = Field(
        description="True when `limit` cut the result set short and more resources exist"
    )
    data: list[Resource]


class ErrorResponse(BaseModel):
    """Body returned with any non-2xx status."""

    error: str = Field(description="Machine-readable error code")
    detail: str = Field(description="Human-readable explanation")
