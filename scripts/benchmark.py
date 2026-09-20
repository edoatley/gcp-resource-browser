"""Record a latency baseline. Run this against a real, large estate.

Phase 4's exit criterion is a measured number, not an adjective. The dev estate
used during development is ~515 resources in a single CAI page, which exercises
none of the behaviour that matters at scale: pagination, fan-out breadth, or
quota pressure. Run this where those are real, and paste the table into
docs/DELIVERY_PLAN.md.

    uv run python -m scripts.benchmark --scope organizations/123456789
    uv run python -m scripts.benchmark --scope projects/a --scope projects/b ...

Every measurement is read-only.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Callable

from app import core
from app.fanout import search_scopes
from app.params import SearchFilters


def timed(fn: Callable[[], object], repeats: int) -> tuple[float, float, object]:
    """Return median and best elapsed milliseconds, plus the last result.

    Median rather than mean: one slow call from a cold connection or a retry
    would drag a mean and misrepresent the typical case.
    """
    timings = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        timings.append((time.perf_counter() - start) * 1000)
    return statistics.median(timings), min(timings), result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", action="append", required=True, help="Repeatable")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    scopes: list[str] = args.scope
    primary = scopes[0]

    def filters(**kw) -> SearchFilters:
        return SearchFilters(scope=primary, resource_types=[], **kw)

    print(f"Scope(s): {', '.join(scopes)}")
    print(f"Repeats : {args.repeats} (median of)\n")
    rows: list[tuple[str, float, float, str]] = []

    # Cold: includes client construction, which is paid once per process.
    core.get_client.cache_clear()
    median, best, result = timed(lambda: core.search_resources(filters(limit=args.limit)), 1)
    rows.append(
        ("Cold (client construction included)", median, best, f"{len(result.resources)} shown")
    )

    median, best, result = timed(
        lambda: core.search_resources(filters(limit=args.limit)), args.repeats
    )
    rows.append(
        (
            "Warm, all types, noise reduced",
            median,
            best,
            f"{len(result.resources)} shown, {result.suppressed} hidden",
        )
    )

    median, best, result = timed(
        lambda: core.search_resources(filters(limit=args.limit, show_all=True)), args.repeats
    )
    rows.append(("Warm, all types, --show-all", median, best, f"{len(result.resources)} shown"))

    median, best, result = timed(
        lambda: core.search_resources(
            SearchFilters(scope=primary, resource_types=["bucket"], limit=args.limit)
        ),
        args.repeats,
    )
    rows.append(("Single asset type", median, best, f"{len(result.resources)} shown"))

    # Does an early stop actually save network, or is it all one page?
    median, best, result = timed(lambda: core.search_resources(filters(limit=10)), args.repeats)
    rows.append(("All types, --limit 10", median, best, f"{len(result.resources)} shown"))

    # Streaming: time to the FIRST row, which is the point of it.
    def first_row() -> object:
        return next(core.stream_resources(filters(limit=args.limit)), None)

    median, best, _ = timed(first_row, args.repeats)
    rows.append(("Streaming: time to first resource", median, best, "first row only"))

    if len(scopes) > 1:

        def sequential() -> int:
            total = 0
            for scope in scopes:
                total += len(
                    core.search_resources(
                        SearchFilters(scope=scope, resource_types=[], limit=args.limit)
                    ).resources
                )
            return total

        median_seq, _, count = timed(sequential, 1)
        rows.append(
            (
                f"Sequential across {len(scopes)} scopes",
                median_seq,
                median_seq,
                f"{count} resources",
            )
        )

        median_par, best_par, merged = timed(
            lambda: search_scopes(scopes, filters(limit=args.limit)), 1
        )
        speedup = median_seq / median_par if median_par else 0
        rows.append(
            (
                f"Concurrent across {len(scopes)} scopes",
                median_par,
                best_par,
                f"{len(merged.resources)} resources, {speedup:.1f}x",
            )
        )
        if merged.failures:
            print(f"WARNING: {len(merged.failures)} scope(s) failed:", file=sys.stderr)
            for scope, reason in merged.failures.items():
                print(f"  {scope}: {reason}", file=sys.stderr)

    # Cache: only meaningful as a second identical call.
    core._RESPONSE_CACHE.clear()
    cached = filters(limit=args.limit, cache_ttl=60)
    core.search_resources(cached)
    median, best, _ = timed(lambda: core.search_resources(cached), args.repeats)
    rows.append(("Cached repeat (--cache-ttl 60)", median, best, "cache hit"))

    print(f"| {'Measurement':44} | {'Median':>9} | {'Best':>9} | Result |")
    print(f"|:{'-' * 44}-|{'-' * 10}:|{'-' * 10}:|:-------|")
    for label, median, best, note in rows:
        print(f"| {label:44} | {median:7.0f}ms | {best:7.0f}ms | {note} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
