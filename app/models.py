"""Pydantic models shared by the CLI and the API.

These define the public shape of a result. They are also what gives the
auto-generated OpenAPI schema real content, which Phase 5's `openapi.yml`
export depends on.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class IamBinding(BaseModel):
    """One role granted to a set of members on a resource."""

    role: str = Field(description="IAM role, e.g. roles/storage.admin")
    members: list[str] = Field(description="Principals granted the role")


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
        default=None, description="Project NUMBER owning the resource, as CAI reports it"
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "Human-readable project ID, derived from the parent resource path where CAI "
            "exposes it. None for resources whose parent is not a project."
        ),
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
    iam_bindings: list[IamBinding] | None = Field(
        default=None,
        description=(
            "IAM bindings ATTACHED to this resource, when include=iam was requested. "
            "None means IAM was not requested; an empty list means it was, and none are "
            "attached. Inherited bindings are NOT included -- see `iam_note` on the "
            "response envelope."
        ),
    )


class ResourceList(BaseModel):
    """Envelope returned by a resource search."""

    scope: str = Field(description="Scope searched, e.g. projects/my-project")
    asset_types: list[str] = Field(description="CAI asset types that were searched")
    query: str = Field(
        description=(
            "The Cloud Asset Inventory query the filters compiled to. Echoed back so a "
            "filter that compiles to something unintended is visible rather than silent."
        )
    )
    count: int = Field(description="Number of resources in `data`")
    truncated: bool = Field(
        description="True when `limit` cut the result set short and more resources exist"
    )
    suppressed: int = Field(
        default=0,
        description=(
            "Resources hidden as low-signal. Always reported, never silently dropped; "
            "pass show_all=true to include them."
        ),
    )
    suppressed_summary: str = Field(
        default="", description="Human-readable explanation of what was hidden, and why"
    )
    iam_note: str | None = Field(
        default=None,
        description=(
            "Present when IAM was requested. States the semantics of what was returned, "
            "since attached-only bindings are easy to mistake for effective access."
        ),
    )
    data: list[Resource]


class Summary(BaseModel):
    """Aggregated counts for a scope, in one round trip."""

    scope: str = Field(description="Scope summarised")
    total: int = Field(description="Resources counted, after noise reduction")
    suppressed: int = Field(description="Resources hidden as low-signal")
    by_asset_type: dict[str, int] = Field(description="Counts per CAI asset type, descending")
    by_project: dict[str, int] = Field(description="Counts per project, descending")
    by_location: dict[str, int] = Field(description="Counts per location, descending")


class ErrorResponse(BaseModel):
    """Body returned with any non-2xx status."""

    error: str = Field(description="Machine-readable error code")
    detail: str = Field(description="Human-readable explanation")


class RiskSource(BaseModel):
    """A published source a risk rule relies on, quoted so it can be checked."""

    title: str
    url: str
    quote: str = Field(description="The passage the rule relies on")


class RiskyPermission(BaseModel):
    """One permission that makes a role risky, and why."""

    permission: str = Field(description="IAM permission, e.g. iam.serviceAccounts.actAs")
    risk: str = Field(description="high or medium")
    reason: str = Field(description="What a holder of this permission can do")
    sources: list[str] = Field(description="Ids of the sources supporting this rule")


class RiskyRole(BaseModel):
    """An IAM role classified by the permissions it contains."""

    name: str = Field(description="Role name, e.g. roles/owner")
    title: str | None = Field(default=None, description="Human-readable title")
    stage: str | None = Field(default=None, description="Launch stage, e.g. GA or DEPRECATED")
    risk: str = Field(description="Highest risk of any permission the role contains")
    permissions: list[RiskyPermission] = Field(description="The risky permissions it contains")


class RoleList(BaseModel):
    """Roles at or above a risk level."""

    min_risk: str = Field(description="Lowest risk level included")
    count: int = Field(description="Number of roles in `data`")
    suppressed: int = Field(
        default=0,
        description="Service-agent roles hidden by default; show_all=true includes them",
    )
    suppressed_summary: str = Field(default="", description="What was hidden, and why")
    data: list[RiskyRole]
    sources: dict[str, RiskSource] = Field(
        description="Every source cited by the returned roles, keyed by id"
    )


class Grant(BaseModel):
    """One principal holding one risky role on one resource."""

    resource: str = Field(description="Full resource name the policy is attached to")
    asset_type: str | None = Field(default=None, description="CAI asset type of that resource")
    role: str = Field(description="Role granted")
    member: str = Field(description="Principal, e.g. user:a@example.com or group:ops@example.com")
    member_type: str = Field(description="user, group, serviceAccount, domain, allUsers, ...")
    risk: str = Field(description="Highest risk among the matched permissions")
    matched_permissions: list[str] = Field(
        description="Risky permissions in this role that the search matched"
    )
    condition: str | None = Field(
        default=None, description="IAM condition expression, when the binding is conditional"
    )


class GrantGroup(BaseModel):
    """Grants rolled up by member or by role, for an over-permissioning review."""

    key: str = Field(description="The member or role grouped on")
    highest_risk: str
    grant_count: int
    resource_count: int = Field(description="Distinct resources involved")
    roles: list[str] | None = Field(default=None, description="Roles held (grouping by member)")
    members: list[str] | None = Field(default=None, description="Holders (grouping by role)")


class GrantList(BaseModel):
    """Envelope returned by a high-risk grant search."""

    scope: str
    min_risk: str = Field(description="Lowest role risk included")
    member_types: list[str] = Field(description="Member types searched; empty means all")
    queries: list[str] = Field(
        description=(
            "The CAI queries sent, one per batch of permissions. CAI caps the alternations "
            "in a single query, so a long permission list is searched in batches."
        )
    )
    count: int
    truncated: bool
    suppressed: int = Field(
        default=0,
        description="Service-agent grants hidden by default; show_all=true includes them",
    )
    suppressed_summary: str = ""
    coverage_note: str = Field(
        description="What this search cannot see. Read it before treating the list as complete."
    )
    grants: list[Grant]
    groups: list[GrantGroup] | None = Field(
        default=None, description="Present when group_by was requested"
    )
