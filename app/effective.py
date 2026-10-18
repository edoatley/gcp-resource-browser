"""Effective IAM: the bindings a resource inherits from its ancestors.

`--include-iam` attaches the bindings set on each resource itself. A bucket's
real access also includes Owner on its project, and anything granted on a
dataset reaches the tables in it. With `effective`, CAI's
batchGetEffectiveIamPolicies supplies those inherited bindings, each marked
`inherited_from` the resource it is attached to.

The API looks only as far up as the search scope. A project scope shows what a
resource inherits from its project, never what the project inherits from a
folder or organization. `core.effective_iam_note` states that bound on every
response.

This is enrichment, not discovery. The names come from a search result that
`limit` already caps, so a search of N resources costs ceil(N / 20) extra calls,
with at most 8 in flight, as in `fanout`. When a batch fails, its resources are
named in `iam_errors` and keep their attached bindings; they are never dropped.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from google.cloud import asset_v1

from app import core
from app.models import IamBinding, Resource

# Concurrent calls in flight; the same ceiling, for the same reasons, as
# `fanout.DEFAULT_MAX_CONCURRENCY`.
DEFAULT_MAX_CONCURRENCY = 8

_Read = tuple[dict[str, list[IamBinding]], dict[str, core.ResourceExplorerError]]


def _inherited(result) -> list[IamBinding]:
    """The bindings in `result` that are attached to an ancestor, nearest first.

    The resource's own policy is skipped: the attached path already supplies it,
    and reading it twice could disagree.
    """
    return [
        IamBinding(role=binding.role, members=list(binding.members), inherited_from=attached)
        for info in result.policies
        if (attached := info.attached_resource) != result.full_resource_name
        for binding in info.policy.bindings
    ]


def _read_batch(scope: str, names: list[str], client: asset_v1.AssetServiceClient) -> _Read:
    """One call's worth of names. Every failure is recorded against each name."""
    try:
        results = core.effective_iam_policies(scope, names, client)
    except core.ResourceExplorerError as exc:
        return {}, dict.fromkeys(names, exc)
    found = {result.full_resource_name: _inherited(result) for result in results}
    missing = core.UpstreamError("Cloud Asset Inventory returned no effective policy for it.")
    return found, {name: missing for name in names if name not in found}


def _read_all(
    scope: str,
    batches: list[list[str]],
    client: asset_v1.AssetServiceClient,
    max_concurrency: int,
) -> _Read:
    found: dict[str, list[IamBinding]] = {}
    failed: dict[str, core.ResourceExplorerError] = {}
    if not batches:
        return found, failed
    workers = max(1, min(max_concurrency, len(batches)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cai-effective") as pool:
        for batch_found, batch_failed in pool.map(
            lambda batch: _read_batch(scope, batch, client), batches
        ):
            found.update(batch_found)
            failed.update(batch_failed)
    return found, failed


def inherited_bindings(
    scope: str,
    names: list[str],
    client: asset_v1.AssetServiceClient,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> _Read:
    """Map each name to the bindings it inherits, and each unreadable name to why.

    Two passes. One unknown name fails its whole batch with NotFound (verified
    live), and CAI cannot resolve some resources it lists, such as workload
    identity pools, which it names by project number. So the names in a batch
    that failed that way are read again one per call, and only the unknown
    ones are lost. Both passes run under the same concurrency bound.
    """
    size = core.EFFECTIVE_IAM_BATCH
    batches = [names[i : i + size] for i in range(0, len(names), size)]
    found, failed = _read_all(scope, batches, client, max_concurrency)

    retry = [
        [name]
        for batch in batches
        if len(batch) > 1 and isinstance(failed.get(batch[0]), core.ScopeNotFound)
        for name in batch
    ]
    if retry:
        for [name] in retry:
            del failed[name]
        retried_found, retried_failed = _read_all(scope, retry, client, max_concurrency)
        found.update(retried_found)
        failed.update(retried_failed)
    return found, failed


def attach_inherited(
    resources: list[Resource],
    scope: str,
    client: asset_v1.AssetServiceClient,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> tuple[list[Resource], dict[str, str]]:
    """Append inherited bindings to each resource's attached ones.

    Returns the resources and the names whose inheritance could not be read.
    When nothing could be read and the cause is not one resource's own (a
    denied permission, a disabled API), that error is raised instead: listing
    every row as failed in a 200 would bury the real problem. A resource that
    is simply not found, for example one deleted since the search, is listed
    in the errors and does not fail the search.
    """
    names = list(dict.fromkeys(r.full_name for r in resources))
    found, failed = inherited_bindings(scope, names, client, max_concurrency)
    if not found:
        systemic = [e for e in failed.values() if not isinstance(e, core.ScopeNotFound)]
        if systemic:
            raise systemic[0]
    enriched = [
        resource.model_copy(
            update={
                "iam_bindings": [
                    *(resource.iam_bindings or []),
                    *found.get(resource.full_name, []),
                ]
            }
        )
        for resource in resources
    ]
    return enriched, {name: str(exc) for name, exc in sorted(failed.items())}
