"""Aggregation across Cloud Asset Inventory surfaces.

Implements the PRD's "aggregate data from multiple GCP sources into a single
optimized payload". Multiple sources means multiple *CAI* surfaces, not a
second data engine -- the decision that CAI is the sole engine rejects
iterating per-project service APIs to *discover* resources, which this does not
do. See docs/DELIVERY_PLAN.md.

The rule, precisely: never iterate APIs to **find** resources; additional APIs
may be called to **enrich** a bounded set already narrowed by a search.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from google.cloud import asset_v1

from app.models import IamBinding, Resource, Summary

# Stated on every response carrying IAM, because "who can access this bucket"
# is exactly what a reader will assume, and attached-only bindings do not
# answer it. A binding inherited from the parent project or folder grants real
# access and does not appear here.
ATTACHED_IAM_NOTE = (
    "Bindings attached directly to each resource. Inherited bindings from a parent "
    "project, folder or organization are NOT included, so this is not a complete "
    "picture of who can access a resource."
)


def fetch_iam_bindings(
    scope: str,
    asset_types: list[str],
    client: asset_v1.AssetServiceClient,
) -> dict[str, list[IamBinding]]:
    """Map full resource name -> attached IAM bindings, for a whole scope.

    One extra call for the scope, not one per resource -- which is what makes
    this an aggregation rather than a fan-out. `IamPolicySearchResult.resource`
    is the same full resource name CAI reports as `name` on a resource search,
    so it is the join key.
    """
    request = asset_v1.SearchAllIamPoliciesRequest(scope=scope, asset_types=asset_types)

    bindings: dict[str, list[IamBinding]] = defaultdict(list)
    for result in client.search_all_iam_policies(request=request):
        for binding in result.policy.bindings:
            bindings[result.resource].append(
                IamBinding(role=binding.role, members=list(binding.members))
            )
    return dict(bindings)


def attach_iam(
    resources: list[Resource], bindings: dict[str, list[IamBinding]]
) -> list[Resource]:
    """Join bindings onto resources by full resource name.

    A resource with no attached policy gets an empty list rather than None:
    None means IAM was never requested, and conflating the two would let a
    reader mistake "not asked" for "nothing granted".
    """
    return [
        resource.model_copy(update={"iam_bindings": bindings.get(resource.full_name, [])})
        for resource in resources
    ]


def summarise(scope: str, resources: list[Resource], suppressed: int) -> Summary:
    """Count a result set by type, project and location.

    The cheapest useful aggregation: no second CAI surface, one pass over
    results the caller was fetching anyway.
    """

    def ranked(counter: Counter[str]) -> dict[str, int]:
        # Descending by count, then by key, so equal counts order predictably
        # rather than by dict insertion.
        return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))

    return Summary(
        scope=scope,
        total=len(resources),
        suppressed=suppressed,
        by_asset_type=ranked(Counter(r.asset_type for r in resources)),
        by_project=ranked(Counter(r.project_id or r.project or "<unknown>" for r in resources)),
        by_location=ranked(Counter(r.location or "<none>" for r in resources)),
    )
