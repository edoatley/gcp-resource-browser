"""Typer CLI surface.

A thin wrapper over `app.core`, rendering results as a `rich` table. Deliberate
errors print a readable message and exit non-zero so the CLI composes in shells
and CI.
"""

from __future__ import annotations

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from app import core

cli = typer.Typer(help="GCP Resource Explorer CLI", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)

# Exit code per error class, so callers can branch on the failure.
EXIT_USAGE = 2
EXIT_PERMISSION = 3
EXIT_NOT_FOUND = 4
EXIT_UPSTREAM = 5

_EXIT_BY_ERROR: dict[type[core.ResourceExplorerError], int] = {
    core.UnknownResourceTypeError: EXIT_USAGE,
    core.InvalidScopeError: EXIT_USAGE,
    core.InvalidFilterError: EXIT_USAGE,
    core.ScopeAccessDenied: EXIT_PERMISSION,
    core.ScopeNotFound: EXIT_NOT_FOUND,
    core.UpstreamError: EXIT_UPSTREAM,
}


def _fail(exc: core.ResourceExplorerError) -> typer.Exit:
    err_console.print(f"[red]Error:[/red] {exc}")
    return typer.Exit(code=_EXIT_BY_ERROR.get(type(exc), 1))


def _render(result: core.SearchResult, scope: str, title: str, show_query: bool) -> None:
    """Print a search result as a table, or explain why it is empty."""
    if show_query and result.query:
        console.print(f"[dim]CAI query: {result.query}[/dim]")

    if not result.resources:
        console.print("[yellow]No resources found.[/yellow]")
        # An empty result with filters applied is ambiguous -- nothing matched,
        # or the filters compiled to something unintended. Show the query so
        # the user can tell which.
        if result.query and not show_query:
            console.print(f"[dim]Query sent was: {result.query}[/dim]")
        return

    table = Table(title=title)
    table.add_column("Resource Name", style="cyan", no_wrap=True)
    table.add_column("Type", style="blue")
    table.add_column("Project", style="magenta")
    table.add_column("Location", style="green")

    for item in result.resources:
        table.add_row(
            item.display_name or item.full_name,
            # The asset type's short half; the domain is noise in a table.
            item.asset_type.split("/")[-1],
            item.project,
            item.location,
        )

    console.print(table)


@cli.command()
def search(
    scope: str = typer.Argument(
        ..., help="Scope: organizations/<id>, folders/<id>, or projects/<id>"
    ),
    term: str = typer.Argument("", help="Free-text term, matched across all searchable fields"),
    resource_type: list[str] = typer.Option(
        ...,
        "--type",
        "-t",
        help="Resource type; repeat for several. Friendly name or raw CAI type.",
    ),
    label: list[str] = typer.Option(
        [], "--label", "-l", help="Label filter: key=value, or key for any value. Repeatable."
    ),
    location: list[str] = typer.Option(
        [], "--location", help="Location filter; repeat to OR. Supports * wildcards."
    ),
    project: list[str] = typer.Option([], "--project", help="Project filter; repeat to OR."),
    raw_query: str = typer.Option(
        "", "--raw-query", help="Raw CAI query syntax, ANDed with the other filters"
    ),
    limit: int = typer.Option(
        core.DEFAULT_LIMIT, "--limit", "-n", min=1, help="Maximum resources to return"
    ),
    show_query: bool = typer.Option(
        False, "--show-query", help="Print the CAI query the filters compiled to"
    ),
) -> None:
    """Search resources across a scope, with filters applied server-side."""
    try:
        with console.status(f"Searching {scope}..."):
            result = core.search_resources(
                scope=scope,
                resource_types=resource_type,
                free_text=term,
                labels=label,
                locations=location,
                projects=project,
                raw_query=raw_query,
                limit=limit,
            )
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    _render(result, scope, title=f"GCP resources in {scope}", show_query=show_query)

    if result.truncated:
        console.print(
            f"[yellow]Showing the first {limit} results; more exist. "
            f"Use --limit to raise the cap.[/yellow]"
        )


@cli.command("list-resources")
def list_resources(
    scope: str = typer.Argument(
        ..., help="Scope: organizations/<id>, folders/<id>, or projects/<id>"
    ),
    resource_type: str = typer.Argument(..., help="Resource type, e.g. 'bucket'"),
    query: str = typer.Option("", "--query", "-q", help="Free-text CAI query filter"),
    limit: int = typer.Option(
        core.DEFAULT_LIMIT, "--limit", "-n", min=1, help="Maximum resources to return"
    ),
) -> None:
    """Query one resource type and print a table (single-type form of `search`)."""
    try:
        with console.status(f"Searching for {resource_type}s in {scope}..."):
            result = core.search_resources(
                scope=scope, resource_types=[resource_type], free_text=query, limit=limit
            )
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    _render(
        result,
        scope,
        title=f"GCP {resource_type.capitalize()}s in {scope}",
        show_query=False,
    )

    if result.truncated:
        console.print(
            f"[yellow]Showing the first {limit} results; more exist. "
            f"Use --limit to raise the cap.[/yellow]"
        )


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
