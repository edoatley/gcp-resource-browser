"""Typer CLI surface.

A thin wrapper over `app.core`, rendering results as a `rich` table. Deliberate
errors print a readable message and exit non-zero so the CLI composes in shells
and CI.
"""

from __future__ import annotations

from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from app import core
from app.aggregate import summarise
from app.fanout import DEFAULT_MAX_CONCURRENCY, search_scopes
from app.gcloud import grants_command, search_command
from app.output import (
    OutputFormat,
    grants_to_csv,
    roles_to_csv,
    summary_to_csv,
    to_csv,
    to_json,
)
from app.params import DEFAULT_LIMIT, GrantFilters, Help, SearchFilters

cli = typer.Typer(help="GCP Resource Explorer CLI", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)

# Exit code per error class, so callers can branch on the failure.
EXIT_USAGE = 2
EXIT_PERMISSION = 3
EXIT_NOT_FOUND = 4
EXIT_UPSTREAM = 5
EXIT_NOT_CONFIGURED = 6
# Some scopes answered and some failed. Distinct from success, because a
# partial answer must not be mistaken for a complete one by a script.
EXIT_PARTIAL = 7

_EXIT_BY_ERROR: dict[type[core.ResourceExplorerError], int] = {
    core.UnknownResourceTypeError: EXIT_USAGE,
    core.InvalidScopeError: EXIT_USAGE,
    core.InvalidFilterError: EXIT_USAGE,
    core.ScopeAccessDenied: EXIT_PERMISSION,
    core.ScopeNotFound: EXIT_NOT_FOUND,
    core.UpstreamError: EXIT_UPSTREAM,
    core.ApiNotEnabledError: EXIT_NOT_CONFIGURED,
}


def _fail(exc: core.ResourceExplorerError) -> typer.Exit:
    err_console.print(f"[red]Error:[/red] {exc}")
    return typer.Exit(code=_EXIT_BY_ERROR.get(type(exc), 1))


def _search_many(
    scopes: list[str],
    filters: SearchFilters,
    max_concurrency: int,
    output: OutputFormat,
    show_query: bool,
    show_gcloud: bool = False,
) -> None:
    """Search several scopes concurrently and render the merged result."""
    # Before the search: reading ADC may spawn `gcloud`, and forking after gRPC
    # has started prints a gRPC warning to stderr.
    billing_project = core.adc_quota_project() if show_gcloud else None
    try:
        with console.status(f"Searching {len(scopes)} scopes..."):
            merged = search_scopes(scopes, filters, max_concurrency=max_concurrency)
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    if show_gcloud:
        # One command per scope: gcloud has no multi-scope search. Per-scope
        # suppression counts are not kept, so none is claimed here.
        _print_gcloud(
            "\n\n".join(
                search_command(
                    scope=scope,
                    asset_types=merged.asset_types,
                    query=merged.query,
                    order_by=core.build_order_by(filters.sort),
                    include_iam=filters.include_iam,
                    billing_project=billing_project,
                )
                for scope in scopes
                if scope not in merged.failures
            ),
            output,
        )

    # The same notes a single-scope search gives, so adding --also-scope never
    # makes truncation, suppression or the IAM caveat disappear. In
    # machine-readable mode they go to stderr to keep stdout parseable.
    notes = []
    if merged.truncated:
        notes.append(
            f"Showing the first {filters.limit} results per scope; more exist. "
            "Use --limit to raise the cap."
        )
    if merged.suppressed:
        notes.append(f"{merged.suppressed} hidden. Use --show-all to include them.")
    if merged.iam_note:
        notes.append(merged.iam_note)

    if output is not OutputFormat.TABLE:
        _emit(merged.resources, output)
        for note in notes:
            err_console.print(note)
    else:
        if show_query and merged.query:
            console.print(f"[dim]CAI query: {merged.query}[/dim]")
        if merged.resources:
            _render_table(merged.resources, f"GCP resources across {len(scopes)} scopes")
        else:
            console.print("[yellow]No resources found.[/yellow]")
        for note in notes:
            console.print(f"[dim]{note}[/dim]")

    # Failures are never silent: a partial answer that looks complete would
    # under-report the estate, which is the failure this tool exists to prevent.
    for failed_scope, reason in sorted(merged.failures.items()):
        err_console.print(f"[red]FAILED[/red] {failed_scope}: {reason}")
    if merged.failures:
        raise typer.Exit(code=EXIT_PARTIAL)


def _print_gcloud(command: str, output: OutputFormat) -> None:
    """Show the gcloud equivalent without corrupting machine-readable stdout."""
    target = console if output is OutputFormat.TABLE else err_console
    # markup=False: a query or label value may contain `[...]`, which rich
    # would otherwise swallow as a style tag, printing a different command.
    target.print(command, markup=False, highlight=False, soft_wrap=True)
    target.print()


def _emit(resources: list, output: OutputFormat) -> None:
    """Write machine-readable output to stdout, unstyled and unwrapped."""
    text = to_json(resources) if output is OutputFormat.JSON else to_csv(resources)
    # print(), not console.print(): rich would wrap and colourise, corrupting
    # the payload for anything downstream.
    print(text, end="" if output is OutputFormat.CSV else "\n")


def _notes_to_stderr(result: core.SearchResult, limit: int) -> None:
    """Warnings that must not pollute a machine-readable stdout."""
    if result.truncated:
        err_console.print(f"Showing the first {limit} results; more exist.")
    if result.suppressed_summary:
        err_console.print(result.suppressed_summary)
    if result.iam_note:
        err_console.print(result.iam_note)


def _report_suppressed(result: core.SearchResult) -> None:
    """Never let hidden rows go unmentioned.

    An audit tool that quietly drops results is worse than one that shows too
    many, so the count and the reasons are always stated.
    """
    if result.suppressed_summary:
        console.print(f"[dim]{result.suppressed_summary}[/dim]")


def _warn_if_truncated(result: core.SearchResult, limit: int) -> None:
    """A cap that silently shortened the list would misreport the estate."""
    if result.truncated:
        console.print(
            f"[yellow]Showing the first {limit} results; more exist. "
            f"Use --limit to raise the cap.[/yellow]"
        )


def _render(result: core.SearchResult, scope: str, title: str, show_query: bool) -> None:
    """Print a search result as a table, or explain why it is empty."""
    if show_query and result.query:
        console.print(f"[dim]CAI query: {result.query}[/dim]")

    if not result.resources:
        if result.suppressed:
            console.print(
                f"[yellow]No resources found[/yellow] after hiding {result.suppressed}. "
                "Everything matched was low-signal; use --show-all to see it."
            )
            return
        console.print("[yellow]No resources found.[/yellow]")
        # An empty result with filters applied is ambiguous -- nothing matched,
        # or the filters compiled to something unintended. Show the query so
        # the user can tell which.
        if result.query and not show_query:
            console.print(f"[dim]Query sent was: {result.query}[/dim]")
        return

    _render_table(result.resources, title)


def _render_table(resources: list, title: str) -> None:
    table = Table(title=title)
    # Sized to content rather than fixed ratios. The previous no_wrap on the
    # name column starved the others down to ellipses ("7340775...", "Service...")
    # as soon as several asset types were in one result set.
    table.add_column("Resource Name", style="cyan")
    table.add_column("Type", style="blue")
    table.add_column("Project", style="magenta")
    table.add_column("Location", style="green")

    for item in resources:
        table.add_row(
            item.display_name or item.full_name,
            # The asset type's short half; the domain is noise in a table.
            item.asset_type.split("/")[-1],
            # Prefer the readable ID; CAI's own `project` field is a number.
            item.project_id or item.project,
            item.location,
        )

    console.print(table)


@cli.command()
def search(
    scope: str = typer.Argument(..., help=Help.SCOPE),
    term: str = typer.Argument("", help=Help.TERM),
    resource_type: list[str] = typer.Option([], "--type", "-t", help=Help.TYPE),
    label: list[str] = typer.Option([], "--label", "-l", help=Help.LABEL),
    location: list[str] = typer.Option([], "--location", help=Help.LOCATION),
    project: list[str] = typer.Option([], "--project", help=Help.PROJECT),
    raw_query: str = typer.Option("", "--raw-query", help=Help.RAW_QUERY),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", "-n", min=1, help=Help.LIMIT),
    # CLI-only: the API always returns `query` in the body, where it costs
    # nothing. On a terminal it is noise unless asked for.
    show_query: bool = typer.Option(
        False, "--show-query", help="Print the CAI query the filters compiled to"
    ),
    show_gcloud: bool = typer.Option(
        False,
        "--show-gcloud",
        help="Print the equivalent `gcloud asset search-all-resources` command",
    ),
    show_all: bool = typer.Option(False, "--show-all", help=Help.SHOW_ALL),
    sort: list[str] = typer.Option([], "--sort", help=Help.SORT),
    include_iam: bool = typer.Option(False, "--include-iam", help=Help.INCLUDE_IAM),
    output: OutputFormat = typer.Option(OutputFormat.TABLE, "--output", "-o", help=Help.OUTPUT),
    also_scope: list[str] = typer.Option(
        [],
        "--also-scope",
        help="Additional scope to search concurrently; repeatable",
    ),
    max_concurrency: int = typer.Option(
        DEFAULT_MAX_CONCURRENCY, "--max-concurrency", min=1, help=Help.MAX_CONCURRENCY
    ),
    cache_ttl: float = typer.Option(0.0, "--cache-ttl", min=0, help=Help.CACHE_TTL),
) -> None:
    """Search resources across a scope, with filters applied server-side.

    With no --type, searches every asset type and hides low-signal resources.
    """
    filters = SearchFilters(
        scope=scope,
        resource_types=resource_type,
        free_text=term,
        labels=label,
        locations=location,
        projects=project,
        raw_query=raw_query,
        limit=limit,
        show_all=show_all,
        sort=sort,
        include_iam=include_iam,
        cache_ttl=cache_ttl,
    )

    if also_scope:
        _search_many(
            [scope, *also_scope], filters, max_concurrency, output, show_query, show_gcloud
        )
        return

    # Before the search: reading ADC may spawn `gcloud`, and forking after gRPC
    # has started prints a gRPC warning to stderr.
    billing_project = core.adc_quota_project() if show_gcloud else None
    try:
        with console.status(f"Searching {scope}..."):
            result = core.search_resources(filters)
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    if show_gcloud:
        _print_gcloud(
            search_command(
                scope=scope,
                asset_types=result.asset_types,
                query=result.query,
                order_by=core.build_order_by(sort),
                limit=limit,
                truncated=result.truncated,
                suppressed=result.suppressed,
                include_iam=include_iam,
                billing_project=billing_project,
            ),
            output,
        )

    if output is not OutputFormat.TABLE:
        # Machine-readable output goes to stdout alone; notes go to stderr so a
        # pipeline reading stdout gets valid JSON or CSV and nothing else.
        _emit(result.resources, output)
        _notes_to_stderr(result, limit)
        return

    _render(result, scope, title=f"GCP resources in {scope}", show_query=show_query)
    _warn_if_truncated(result, limit)
    _report_suppressed(result)
    if result.iam_note:
        console.print(f"[dim]{result.iam_note}[/dim]")


@cli.command("list-resources")
def list_resources(
    scope: str = typer.Argument(..., help=Help.SCOPE),
    resource_type: str = typer.Argument(..., help="Resource type, e.g. 'bucket'"),
    query: str = typer.Option("", "--query", "-q", help=Help.TERM),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", "-n", min=1, help=Help.LIMIT),
    show_all: bool = typer.Option(False, "--show-all", help=Help.SHOW_ALL),
) -> None:
    """Query one resource type and print a table (single-type form of `search`)."""
    try:
        with console.status(f"Searching for {resource_type}s in {scope}..."):
            result = core.search_resources(
                SearchFilters(
                    scope=scope,
                    resource_types=[resource_type],
                    free_text=query,
                    limit=limit,
                    show_all=show_all,
                )
            )
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    _render(
        result,
        scope,
        title=f"GCP {resource_type.capitalize()}s in {scope}",
        show_query=False,
    )
    _warn_if_truncated(result, limit)
    # Noise rules apply to a named type too (default subnets, default routes),
    # so this path must report what it hid just as `search` does.
    _report_suppressed(result)


@cli.command("summary")
def summary_command(
    scope: str = typer.Argument(..., help=Help.SCOPE),
    term: str = typer.Argument("", help=Help.TERM),
    resource_type: list[str] = typer.Option([], "--type", "-t", help=Help.TYPE),
    label: list[str] = typer.Option([], "--label", "-l", help=Help.LABEL),
    location: list[str] = typer.Option([], "--location", help=Help.LOCATION),
    project: list[str] = typer.Option([], "--project", help=Help.PROJECT),
    show_all: bool = typer.Option(False, "--show-all", help=Help.SHOW_ALL),
    output: OutputFormat = typer.Option(OutputFormat.TABLE, "--output", "-o", help=Help.OUTPUT),
) -> None:
    """Count resources in a scope by type, project and location."""
    try:
        with console.status(f"Summarising {scope}..."):
            result = core.search_resources(
                SearchFilters(
                    scope=scope,
                    resource_types=resource_type,
                    free_text=term,
                    labels=label,
                    locations=location,
                    projects=project,
                    show_all=show_all,
                    # No limit: a summary of a truncated set would be a lie.
                    limit=None,
                )
            )
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    totals = summarise(scope, result.resources, result.suppressed)

    if output is OutputFormat.JSON:
        print(totals.model_dump_json(indent=2))
        return
    if output is OutputFormat.CSV:
        print(summary_to_csv(totals), end="")
        return

    console.print(
        f"[bold]{totals.total}[/bold] resources in {scope}"
        + (f" ([dim]{totals.suppressed} hidden[/dim])" if totals.suppressed else "")
    )
    for title, counts in (
        ("By type", totals.by_asset_type),
        ("By project", totals.by_project),
        ("By location", totals.by_location),
    ):
        if not counts:
            continue
        table = Table(title=title, show_header=False, box=None, padding=(0, 2))
        table.add_column(style="cyan")
        table.add_column(style="magenta", justify="right")
        for key, count in counts.items():
            table.add_row(key.split("/")[-1] if title == "By type" else key, str(count))
        console.print(table)


def _risk_style(risk: str) -> str:
    return {"high": "bold red", "medium": "yellow"}.get(risk, "")


@cli.command("roles")
def roles_command(
    risk: str = typer.Option("high", "--risk", "-r", help=Help.RISK),
    show_all: bool = typer.Option(False, "--show-all", help=Help.SHOW_ALL_AGENTS),
    output: OutputFormat = typer.Option(OutputFormat.TABLE, "--output", "-o", help=Help.OUTPUT),
) -> None:
    """List IAM roles classed as risky, and the permissions that make them so.

    A role's risk is the highest risk of any permission it contains. The rules
    and the sources behind them are in docs/ROLE_RISK.md.
    """
    try:
        with console.status("Reading the IAM role catalogue..."):
            roles = core.list_risky_roles(risk, show_all=show_all)
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    if output is OutputFormat.JSON:
        print(roles.model_dump_json(indent=2))
        return
    if output is OutputFormat.CSV:
        print(roles_to_csv(roles), end="")
        if roles.suppressed_summary:
            err_console.print(roles.suppressed_summary)
        return

    table = Table(title=f"IAM roles at {roles.min_risk} risk or above")
    table.add_column("Role", style="cyan", no_wrap=True)
    table.add_column("Risk")
    table.add_column("Because it can", overflow="fold")
    for role in roles.data:
        table.add_row(
            role.name,
            f"[{_risk_style(role.risk)}]{role.risk}[/]",
            "\n".join(f"{p.permission}" for p in role.permissions),
        )
    console.print(table)
    console.print(f"[dim]{roles.count} role(s). Sources: docs/ROLE_RISK.md[/dim]")
    if roles.suppressed_summary:
        console.print(f"[dim]{roles.suppressed_summary}[/dim]")


@cli.command("grants")
def grants_command_cli(
    scope: str = typer.Argument(..., help=Help.SCOPE),
    role_risk: str = typer.Option("high", "--role-risk", "-r", help=Help.RISK),
    member_type: list[str] = typer.Option([], "--member-type", "-m", help=Help.MEMBER_TYPE),
    by: str | None = typer.Option(None, "--by", help=Help.GROUP_BY),
    show_all: bool = typer.Option(False, "--show-all", help=Help.SHOW_ALL_AGENTS),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", "-n", min=1, help=Help.LIMIT),
    output: OutputFormat = typer.Option(OutputFormat.TABLE, "--output", "-o", help=Help.OUTPUT),
    show_gcloud: bool = typer.Option(
        False,
        "--show-gcloud",
        help="Print the equivalent `gcloud asset search-all-iam-policies` command(s)",
    ),
) -> None:
    """Find principals holding risky roles across a scope.

    Matching is by permission, so custom roles are covered. Search the
    organization to include grants made at every level.
    """
    billing_project = core.adc_quota_project() if show_gcloud else None
    try:
        with console.status(f"Searching {scope} for {role_risk}-risk grants..."):
            result = core.search_grants(
                GrantFilters(
                    scope=scope,
                    min_risk=role_risk,
                    member_types=member_type,
                    show_all=show_all,
                    limit=limit,
                    group_by=by,
                )
            )
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    if show_gcloud:
        _print_gcloud(grants_command(scope, result.queries, billing_project), output)

    notes = [result.coverage_note]
    if result.truncated:
        notes.append(f"Showing the first {limit} grants; more exist. Use --limit to raise the cap.")
    if result.suppressed_summary:
        notes.append(result.suppressed_summary)

    if output is not OutputFormat.TABLE:
        print(
            result.model_dump_json(indent=2, exclude_none=True)
            if output is OutputFormat.JSON
            else grants_to_csv(result),
            end="\n" if output is OutputFormat.JSON else "",
        )
        for note in notes:
            err_console.print(note)
        return

    if not result.grants:
        console.print(f"[green]No {result.min_risk}-risk grants found.[/green]")
    elif result.groups is not None:
        table = Table(title=f"{result.min_risk.capitalize()}-risk grants in {scope}, by {by}")
        table.add_column(by.capitalize(), style="cyan", overflow="fold")
        table.add_column("Risk")
        table.add_column("Roles" if by == "member" else "Members", no_wrap=by == "member")
        table.add_column("Resources", justify="right")
        for group in result.groups:
            table.add_row(
                group.key,
                f"[{_risk_style(group.highest_risk)}]{group.highest_risk}[/]",
                "\n".join(group.roles or group.members or []),
                str(group.resource_count),
            )
        console.print(table)
    else:
        table = Table(title=f"{result.min_risk.capitalize()}-risk grants in {scope}")
        table.add_column("Member", style="cyan", overflow="fold")
        table.add_column("Role", style="magenta", no_wrap=True)
        table.add_column("Risk")
        table.add_column("On", overflow="fold")
        for grant in result.grants:
            table.add_row(
                grant.member,
                grant.role + (" [dim](conditional)[/dim]" if grant.condition else ""),
                f"[{_risk_style(grant.risk)}]{grant.risk}[/]",
                grant.resource.removeprefix("//"),
            )
        console.print(table)
    for note in notes:
        console.print(f"[dim]{note}[/dim]")


@cli.command("openapi")
def openapi_command(
    out: Path = typer.Option(Path("openapi.yml"), "--out", help="Where to write the specification"),
) -> None:
    """Export the API's OpenAPI specification.

    FastAPI serves the schema live at /openapi.json; the PRD asks for a
    committed openapi.yml, which is what downstream codegen consumes.
    """
    import yaml

    from app.api import app as fastapi_app

    spec = fastapi_app.openapi()
    out.write_text(yaml.safe_dump(spec, sort_keys=False, default_flow_style=False))
    console.print(f"Wrote {out} ({len(spec.get('paths', {}))} paths)")


@cli.command("types")
def list_types() -> None:
    """List the friendly resource-type names this tool understands."""
    table = Table(title="Supported resource types")
    table.add_column("Name", style="cyan")
    table.add_column("CAI asset type", style="green")
    for name, asset_type in sorted(core.ASSET_TYPES.items()):
        table.add_row(name, asset_type)
    console.print(table)
    console.print(
        "[dim]Any raw CAI asset type or RE2 pattern is also accepted, "
        "e.g. 'compute.googleapis.com/.*'[/dim]"
    )


@cli.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Address to bind"),
    port: int = typer.Option(8000, help="Port to bind"),
) -> None:
    """Run the FastAPI server."""
    uvicorn.run("app.api:app", host=host, port=port)
