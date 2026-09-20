"""Concurrent multi-scope search.

The common use for this is not exotic: CAI checks permission on the scope
itself, so someone holding viewer on 50 projects but not the organization must
search 50 scopes. Doing that sequentially is the latency the PRD rejects.
"""

from __future__ import annotations

import threading
import time

from google.api_core import exceptions as gcp_exceptions

from app.fanout import DEFAULT_MAX_CONCURRENCY, search_scopes
from app.params import SearchFilters
from tests.conftest import FakeAssetClient, make_search_result


def base() -> SearchFilters:
    return SearchFilters(scope="", resource_types=[])


def test_merges_results_from_every_scope() -> None:
    client = FakeAssetClient(results=[make_search_result(name="//x/a")])

    merged = search_scopes(["projects/a", "projects/b", "projects/c"], base(), client=client)

    assert len(merged.resources) == 3
    assert not merged.failures


def test_no_scopes_is_not_an_error() -> None:
    assert search_scopes([], base(), client=FakeAssetClient()).resources == []


def test_a_failing_scope_does_not_lose_the_others() -> None:
    """With 50 projects, one lacking permission must not cost the other 49."""

    class OneScopeDenied(FakeAssetClient):
        def search_all_resources(self, request):
            if request.scope == "projects/denied":
                raise gcp_exceptions.PermissionDenied("nope")
            return super().search_all_resources(request)

    client = OneScopeDenied(results=[make_search_result()])

    merged = search_scopes(["projects/ok", "projects/denied"], base(), client=client)

    assert len(merged.resources) == 1
    assert "projects/denied" in merged.failures
    assert "Permission denied" in merged.failures["projects/denied"]


def test_failures_are_recorded_never_silent() -> None:
    """A partial answer that looks complete would under-report the estate."""

    class AllDenied(FakeAssetClient):
        def search_all_resources(self, request):
            raise gcp_exceptions.PermissionDenied("nope")

    merged = search_scopes(["projects/a", "projects/b"], base(), client=AllDenied())

    assert merged.resources == []
    assert set(merged.failures) == {"projects/a", "projects/b"}
    assert "2 scope(s) failed" in merged.summary


def test_results_are_deterministically_ordered() -> None:
    """Completion order varies run to run; output must not."""
    client = FakeAssetClient(results=[make_search_result(name="//x/a")])
    scopes = [f"projects/p{i}" for i in range(6)]

    first = search_scopes(scopes, base(), client=client).resources
    second = search_scopes(scopes, base(), client=client).resources

    assert [r.full_name for r in first] == [r.full_name for r in second]


def test_concurrency_is_bounded() -> None:
    """Unbounded fan-out over 2000 projects trades latency for quota errors,
    and a 429 fails the whole search where slowness merely annoys."""
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    class SlowClient(FakeAssetClient):
        def search_all_resources(self, request):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return iter([])

    search_scopes(
        [f"projects/p{i}" for i in range(40)], base(), client=SlowClient(), max_concurrency=4
    )

    assert peak <= 4, f"expected at most 4 concurrent calls, saw {peak}"


def test_concurrency_never_exceeds_the_scope_count() -> None:
    """Spinning up 8 threads for 2 scopes wastes them."""
    client = FakeAssetClient(results=[])

    merged = search_scopes(
        ["projects/a"], base(), client=client, max_concurrency=DEFAULT_MAX_CONCURRENCY
    )

    assert merged.failures == {}


def test_suppression_counts_are_summed_across_scopes() -> None:
    client = FakeAssetClient(
        results=[
            make_search_result(name="//x/api", asset_type="serviceusage.googleapis.com/Service")
        ]
    )

    merged = search_scopes(["projects/a", "projects/b"], base(), client=client)

    assert merged.suppressed == 2
    assert merged.resources == []
