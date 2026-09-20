"""Concurrent search across several scopes.

Two situations need this, and the second is the common one:

1. A search genuinely spanning sibling folders or projects.
2. **No organisation-level access.** Cloud Asset Inventory checks
   `cloudasset.assets.searchAllResources` on the scope *itself*, not on its
   children, so holding viewer on 50 projects individually does not make
   `organizations/X` work. Searching those 50 scopes is then the only route --
   and doing it sequentially is exactly the latency the PRD rejects.

This is not the per-project iteration the PRD forbids. That rule is about
*discovery*: never walk service APIs resource-by-resource to find what exists.
Each scope here is still answered by a single scope-wide CAI search; only the
scopes themselves are parallel, because the caller has no broader scope
available.

Concurrency is bounded. An unbounded fan-out over 2000 projects trades a
latency problem for a quota problem, which is worse -- a 429 fails the whole
search, where slowness merely annoys.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from google.cloud import asset_v1

from app import core
from app.models import Resource
from app.params import SearchFilters

# Concurrent in-flight CAI calls. Sized well below any plausible quota, and
# below anyio's 40-thread default so a fan-out inside an API request cannot
# starve the request threadpool it is running on.
DEFAULT_MAX_CONCURRENCY = 8


@dataclass
class FanoutResult:
    """Merged results from several scopes, and what failed."""

    resources: list[Resource] = field(default_factory=list)
    truncated: bool = False
    suppressed: int = 0
    query: str = ""
    asset_types: list[str] = field(default_factory=list)
    # Scope -> error message. A partial answer is useful, but only if the gaps
    # are named: silently omitting a scope the caller asked for would
    # under-report the estate, the failure this tool exists to prevent.
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        parts = []
        if self.suppressed:
            parts.append(f"{self.suppressed} hidden")
        if self.failures:
            parts.append(f"{len(self.failures)} scope(s) failed")
        return "; ".join(parts)


def search_scopes(
    scopes: list[str],
    filters: SearchFilters,
    client: asset_v1.AssetServiceClient | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> FanoutResult:
    """Search several scopes concurrently and merge the results.

    A scope that fails is recorded rather than aborting the whole search: with
    50 projects, one lacking permission should not lose the other 49. Failures
    are always reported.
    """
    merged = FanoutResult()
    if not scopes:
        return merged

    client = client or core.get_client()
    workers = max(1, min(max_concurrency, len(scopes)))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cai") as pool:
        futures = {
            pool.submit(
                core.search_resources,
                SearchFilters(**{**filters.__dict__, "scope": scope}),
                client,
            ): scope
            for scope in scopes
        }
        for future in as_completed(futures):
            scope = futures[future]
            try:
                result = future.result()
            except core.ResourceExplorerError as exc:
                merged.failures[scope] = str(exc)
                continue
            merged.resources.extend(result.resources)
            merged.truncated = merged.truncated or result.truncated
            merged.suppressed += result.suppressed
            merged.query = merged.query or result.query
            merged.asset_types = merged.asset_types or result.asset_types

    # as_completed yields in completion order, which varies run to run. Sort so
    # output is reproducible and diffable.
    merged.resources.sort(key=lambda r: (r.project_id or "", r.asset_type, r.full_name))
    return merged
