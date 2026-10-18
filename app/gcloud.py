"""Render a search as the equivalent `gcloud` command.

For users who want to see, reproduce, or hand someone a search in the tool
they already trust. It is rendered from what was actually sent to CAI -- the
compiled query (project IDs already resolved to numbers), the resolved asset
types and the compiled `order_by` -- so it reproduces this tool's request
exactly.

That is also why it is NOT a correctness check. `scripts/gcloud-search-resources.sh`
builds its query with independent logic precisely because comparing a query
against itself proves nothing; this reuses the tool's compiler by design.

Two behaviours have no gcloud equivalent, and the output says so rather than
pretending to be an exact replica:

- Noise reduction is client-side, so gcloud returns the suppressed rows too.
- `limit` here counts *visible* rows; gcloud's `--limit` counts every row. It
  is only emitted when the result was truncated, where it matters.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

# gcloud's alternate-delimiter syntax for a list flag whose values contain a
# comma (`gcloud topic escaping`), which an RE2 asset-type pattern can.
_ALT_DELIMITER = "|"


def _list_flag(name: str, values: Sequence[str]) -> str:
    if any("," in v for v in values):
        joined = f"^{_ALT_DELIMITER}^" + _ALT_DELIMITER.join(values)
    else:
        joined = ",".join(values)
    return f"--{name}={shlex.quote(joined)}"


def _command(verb: str, flags: list[str]) -> str:
    # One flag per line: long CAI queries stay readable, and it still pastes
    # into a shell as a single command.
    return " \\\n    ".join([f"gcloud asset {verb}", *flags])


def search_command(
    scope: str,
    asset_types: Sequence[str],
    query: str,
    order_by: str = "",
    limit: int | None = None,
    truncated: bool = False,
    suppressed: int = 0,
    include_iam: bool = False,
    billing_project: str | None = None,
) -> str:
    """The gcloud command(s) equivalent to one scope's search, with caveats.

    `billing_project` is the ADC quota project. Passing it makes gcloud bill
    where this tool does, rather than to whatever project the gcloud CLI is
    configured with -- which may not have the Cloud Asset API enabled.
    """
    billing = [f"--billing-project={shlex.quote(billing_project)}"] if billing_project else []
    flags = [f"--scope={shlex.quote(scope)}"]
    if asset_types:
        flags.append(_list_flag("asset-types", asset_types))
    if query:
        flags.append(f"--query={shlex.quote(query)}")
    if order_by:
        flags.append(f"--order-by={shlex.quote(order_by)}")
    if truncated and limit is not None:
        flags.append(f"--limit={limit}")
    flags.extend(billing)

    lines = []
    if suppressed:
        lines.append(
            f"# gcloud has no noise reduction: expect {suppressed} more row(s) than shown here."
        )
    if truncated and limit is not None and suppressed:
        lines.append(
            "# --limit counts every row in gcloud but only visible rows here, "
            "so the two may stop at different rows."
        )
    lines.append(_command("search-all-resources", flags))

    if include_iam:
        iam_flags = [f"--scope={shlex.quote(scope)}"]
        if asset_types:
            iam_flags.append(_list_flag("asset-types", asset_types))
        iam_flags.extend(billing)
        lines.append("")
        lines.append("# Attached IAM bindings, joined on the resource name:")
        lines.append(_command("search-all-iam-policies", iam_flags))

    return "\n".join(lines)


_EXPANSION_COMMANDS = [
    "",
    "# --expand-members: for each group: or projectOwner/Editor/Viewer: member above,",
    "gcloud identity groups memberships search-transitive-memberships --group-email=GROUP",
    "gcloud projects get-iam-policy PROJECT --flatten=bindings "
    "--filter=bindings.role=roles/owner --format='value(bindings.members)'",
]


def grants_command(
    scope: str,
    queries: Sequence[str],
    billing_project: str | None = None,
    expand: bool = False,
) -> str:
    """The gcloud command(s) for a risky-grant search: one per permission batch.

    gcloud returns whole policies, including bindings this tool narrows away
    and grants to service agents it hides; the comment says so.
    """
    billing = [f"--billing-project={shlex.quote(billing_project)}"] if billing_project else []
    lines = [
        "# gcloud returns whole IAM policies: expect bindings for non-matching roles and",
        "# Google service agents too, which this tool filters out of the rows it reports.",
    ]
    if len(queries) > 1:
        lines.append(
            f"# {len(queries)} commands: CAI caps alternations per query, "
            "so permissions are batched."
        )
    for i, query in enumerate(queries):
        if i:
            lines.append("")
        flags = [f"--scope={shlex.quote(scope)}", f"--query={shlex.quote(query)}", *billing]
        lines.append(_command("search-all-iam-policies", flags))
    if expand:
        lines.extend(_EXPANSION_COMMANDS)
    return "\n".join(lines)


def roles_command(scope: str | None = None, min_risk: str = "high") -> str:
    """The gcloud command(s) behind a risky-role listing.

    `gcloud iam roles list` has no notion of risk, so this lists the same
    roles the tool classified and says where the classification came from.
    The real check is `scripts/compare-roles.sh`, which reclassifies with jq.
    """
    lines = [
        "# gcloud cannot filter by risk: these list the catalogue the tool classified at",
        f"# {min_risk} risk or above, using app/role_risk_rules.json.",
        "gcloud iam roles list --format='table(name,title,stage)'",
    ]
    if scope:
        level, _, ident = scope.partition("/")
        flag = "--organization" if level == "organizations" else "--project"
        lines.append("")
        lines.append(f"# Custom roles defined on {scope}:")
        lines.append(
            f"gcloud iam roles list {flag}={shlex.quote(ident)} --format='table(name,title,stage)'"
        )
    return "\n".join(lines)


def billing_grants_command(accounts: Sequence[str] = (), expand: bool = False) -> str:
    """The gcloud command(s) for a billing-account grant search.

    Billing account IAM is not in Cloud Asset Inventory, so there is no
    `gcloud asset` equivalent -- this is the Cloud Billing API, one policy
    read per account, which is exactly what the tool does.
    """
    lines = [
        "# Billing account IAM is not indexed by Cloud Asset Inventory, so this uses the",
        "# Cloud Billing API. gcloud returns whole policies: expect non-risky roles and",
        "# Google service agents too, which this tool filters out of the rows it reports.",
        "gcloud billing accounts list --format='value(name)'",
    ]
    for account in accounts:
        ident = account.removeprefix("billingAccounts/")
        lines.append(f"gcloud billing accounts get-iam-policy {shlex.quote(ident)}")
    if not accounts:
        lines.append("")
        lines.append("# Then, for each account listed above:")
        lines.append("gcloud billing accounts get-iam-policy BILLING_ACCOUNT_ID")
    if expand:
        lines.extend(_EXPANSION_COMMANDS)
    return "\n".join(lines)
