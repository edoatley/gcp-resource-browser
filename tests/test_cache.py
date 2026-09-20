"""The opt-in response cache.

Off by default on purpose: silently answering an audit from a stale cache is
the wrong default when someone is checking whether a fix landed.
"""

from __future__ import annotations

import time

from app import core
from app.cache import MAX_ENTRIES, TTLCache
from app.params import SearchFilters
from tests.conftest import FakeAssetClient, make_search_result


def test_disabled_by_default() -> None:
    cache = TTLCache()

    cache.put("k", "v")

    assert not cache.enabled
    assert cache.get("k") is None


def test_returns_a_value_within_its_ttl() -> None:
    cache = TTLCache(ttl_seconds=60)
    cache.put("k", "v")

    assert cache.get("k") == "v"
    assert cache.hits == 1


def test_expired_entries_are_not_returned() -> None:
    cache = TTLCache(ttl_seconds=0.01)
    cache.put("k", "v")
    time.sleep(0.05)

    assert cache.get("k") is None
    assert cache.misses == 1


def test_eviction_keeps_the_cache_bounded() -> None:
    """A long-running API process must not grow without limit."""
    cache = TTLCache(ttl_seconds=60)

    for i in range(MAX_ENTRIES + 20):
        cache.put(f"k{i}", i)

    assert len(cache._entries) <= MAX_ENTRIES


# --- integration ------------------------------------------------------------


def filters(**kw) -> SearchFilters:
    return SearchFilters(scope="projects/p", resource_types=["bucket"], **kw)


def test_search_does_not_cache_unless_asked() -> None:
    core._RESPONSE_CACHE.clear()
    client = FakeAssetClient(results=[make_search_result()])

    core.search_resources(filters(), client=client)
    core.search_resources(filters(), client=client)

    assert len(client.requests) == 2, "every search should hit CAI"


def test_identical_search_is_served_from_cache_when_enabled() -> None:
    core._RESPONSE_CACHE.clear()
    client = FakeAssetClient(results=[make_search_result()])

    first = core.search_resources(filters(cache_ttl=60), client=client)
    second = core.search_resources(filters(cache_ttl=60), client=client)

    assert len(client.requests) == 1, "second search should not reach CAI"
    assert [r.full_name for r in second.resources] == [r.full_name for r in first.resources]


def test_a_different_filter_is_a_different_cache_entry() -> None:
    """Returning one filter's results for another would be silently wrong."""
    core._RESPONSE_CACHE.clear()
    client = FakeAssetClient(results=[make_search_result()])

    core.search_resources(filters(cache_ttl=60), client=client)
    core.search_resources(filters(cache_ttl=60, labels=["env=prod"]), client=client)

    assert len(client.requests) == 2


def test_show_all_is_part_of_the_cache_key() -> None:
    """Suppressed and unsuppressed results must never be confused."""
    core._RESPONSE_CACHE.clear()
    client = FakeAssetClient(results=[make_search_result()])

    core.search_resources(filters(cache_ttl=60), client=client)
    core.search_resources(filters(cache_ttl=60, show_all=True), client=client)

    assert len(client.requests) == 2
