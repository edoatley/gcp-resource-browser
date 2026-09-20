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
from app.output import OutputFormat, to_csv, to_json
from app.params import DEFAULT_LIMIT, Help, SearchFilters

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
) -> None:
    """Search several scopes concurrently and render the merged result."""
    try:
        with console.status(f"Searching {len(scopes)} scopes..."):
            merged = search_scopes(scopes, filters, max_concurrency=max_concurrency)
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    if output is not OutputFormat.TABLE:
        _emit(merged.resources, output)
    else:
        if show_query and merged.query:
            console.print(f"[dim]CAI query: {merged.query}[/dim]")
        if merged.resources:
            _render_table(merged.resources, f"GCP resources across {len(scopes)} scopes")
        else:
            console.print("[yellow]No resources found.[/yellow]")
        if merged.suppressed:
            console.print(f"[dim]{merged.suppressed} hidden. Use --show-all.[/dim]")

    # Failures are never silent: a partial answer that looks complete would
    # under-report the estate, which is the failure this tool exists to prevent.
    for failed_scope, reason in sorted(merged.failures.items()):
        err_console.print(f"[red]FAILED[/red] {failed_scope}: {reason}")
    if merged.failures:
        raise typer.Exit(code=EXIT_PARTIAL)


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
        _search_many([scope, *also_scope], filters, max_concurrency, output, show_query)
        return

    try:
        with console.status(f"Searching {scope}..."):
            result = core.search_resources(
                SearchFilters(
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
                )
            )
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

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


@cli.command("summary")
def summary_command(
    scope: str = typer.Argument(..., help=Help.SCOPE),
    resource_type: list[str] = typer.Option([], "--type", "-t", help=Help.TYPE),
    label: list[str] = typer.Option([], "--label", "-l", help=Help.LABEL),
    location: list[str] = typer.Option([], "--location", help=Help.LOCATION),
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
                    labels=label,
                    locations=location,
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
