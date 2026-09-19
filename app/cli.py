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
    core.ScopeAccessDenied: EXIT_PERMISSION,
    core.ScopeNotFound: EXIT_NOT_FOUND,
    core.UpstreamError: EXIT_UPSTREAM,
}


def _fail(exc: core.ResourceExplorerError) -> typer.Exit:
    err_console.print(f"[red]Error:[/red] {exc}")
    return typer.Exit(code=_EXIT_BY_ERROR.get(type(exc), 1))


@cli.command("list-resources")
def list_resources(
    scope: str = typer.Argument(
        ..., help="Scope: organizations/<id>, folders/<id>, or projects/<id>"
    ),
    resource_type: str = typer.Argument(..., help="Resource type: 'bucket' or 'cloudrun'"),
    query: str = typer.Option("", "--query", "-q", help="Free-text CAI query filter"),
    limit: int = typer.Option(
        core.DEFAULT_LIMIT, "--limit", "-n", min=1, help="Maximum resources to return"
    ),
) -> None:
    """Query GCP resources and print them in a formatted table."""
    try:
        with console.status(f"Searching for {resource_type}s in {scope}..."):
            resources, truncated = core.search_resources(
                scope=scope, resource_type=resource_type, query=query, limit=limit
            )
    except core.ResourceExplorerError as exc:
        raise _fail(exc) from exc

    if not resources:
        console.print("[yellow]No resources found.[/yellow]")
        return

    table = Table(title=f"GCP {resource_type.capitalize()}s in {scope}")
    table.add_column("Resource Name", style="cyan", no_wrap=True)
    table.add_column("Project", style="magenta")
    table.add_column("Location", style="green")

    for item in resources:
        table.add_row(item.display_name or item.full_name, item.project, item.location)

    console.print(table)

    # Never let a cap silently misrepresent the size of the result set.
    if truncated:
        console.print(
            f"[yellow]Showing the first {limit} results; more exist. "
            f"Use --limit to raise the cap.[/yellow]"
        )


@cli.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Address to bind"),
    port: int = typer.Option(8000, help="Port to bind"),
) -> None:
    """Run the FastAPI server."""
    uvicorn.run("app.api:app", host=host, port=port)
