import typer
import uvicorn
from fastapi import FastAPI
from rich.console import Console
from rich.table import Table
from google.cloud import asset_v1

# Initialize our frameworks
app = FastAPI(title="GCP Resource Explorer API")
cli = typer.Typer(help="GCP Resource Explorer CLI")
console = Console()

# Mapping friendly names to CAI asset types
ASSET_TYPES = {
    "bucket": "storage.googleapis.com/Bucket",
    "cloudrun": "run.googleapis.com/Service"
}


def search_cai_resources(scope: str, asset_type: str, query: str = ""):
    """
    Core function to query Cloud Asset Inventory.
    Relies on locally authenticated gcloud ADC (gcloud auth application-default login).
    """
    client = asset_v1.AssetServiceClient()

    # Scope can be an organization, folder, or project.
    # E.g., "organizations/123456789" or "projects/my-hub-project"
    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        asset_types=[asset_type],
        query=query,
    )

    # Iterate through the paginated response
    resources = []
    for resource in client.search_all_resources(request=request):
        resources.append({
            "name": resource.display_name,
            "project": resource.project.split("/")[-1],
            "location": resource.location,
            "state": resource.state
        })
    return resources


# ==========================================
# 1. FastAPI Endpoints
# ==========================================
@app.get("/projects/{project_id}/resources/{resource_type}")
def get_resources_api(project_id: str, resource_type: str):
    if resource_type not in ASSET_TYPES:
        return {"error": f"Unsupported resource type. Choose from {list(ASSET_TYPES.keys())}"}

    scope = f"projects/{project_id}"
    results = search_cai_resources(scope, ASSET_TYPES[resource_type])
    return {"project": project_id, "resource_type": resource_type, "count": len(results), "data": results}


# ==========================================
# 2. Typer CLI Commands
# ==========================================
@cli.command()
def list_resources(
        scope: str = typer.Argument(..., help="Scope e.g. projects/my-project or organizations/123"),
        resource_type: str = typer.Argument(..., help="Resource type: 'bucket' or 'cloudrun'")
):
    """
    Query GCP resources and print them in a formatted table.
    """
    if resource_type not in ASSET_TYPES:
        console.print(f"[red]Error:[/red] Invalid resource type. Use 'bucket' or 'cloudrun'.")
        raise typer.Exit()

    with console.status(f"Searching for {resource_type}s in {scope}..."):
        results = search_cai_resources(scope, ASSET_TYPES[resource_type])

    if not results:
        console.print("[yellow]No resources found.[/yellow]")
        return

    # Build the Rich Table
    table = Table(title=f"GCP {resource_type.capitalize()}s in {scope}")
    table.add_column("Resource Name", style="cyan", no_wrap=True)
    table.add_column("Project", style="magenta")
    table.add_column("Location", style="green")

    for item in results:
        table.add_row(item["name"], item["project"], item["location"])

    console.print(table)


@cli.command()
def serve():
    """Run the FastAPI server."""
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    cli()