"""Optional short-TTL response cache.

**Off by default, deliberately.** Cloud Asset Inventory is already a
near-real-time index rather than live data, so caching costs little accuracy in
principle -- but this is an auditing tool, and silently answering from a stale
cache is the wrong default when someone is checking whether a fix landed. The
protection it offers is against repeated identical polling, which is a
deployment decision, so it is opt-in.

Enable with `--cache-ttl SECONDS` or `?cache_ttl=`, or set a default in site
config. A TTL of 0 disables it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# Bounded so a long-running API process cannot grow without limit. Small,
# because the value is in absorbing repeated identical polls, not in
# remembering every query ever asked.
MAX_ENTRIES = 256


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    """A tiny time-bounded cache. Not thread-safe by design of use.

    Reads and writes happen under FastAPI's threadpool, where a torn read would
    at worst recompute an entry -- never return a wrong one, since entries are
    replaced wholesale rather than mutated.
    """

    def __init__(self, ttl_seconds: float = 0.0) -> None:
        self.ttl = ttl_seconds
        self._entries: dict[str, _Entry] = {}
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.expires_at < time.monotonic():
            # Expired entries are dropped on read rather than swept, which
            # keeps this free of background work.
            del self._entries[key]
            self.misses += 1
            return None
        self.hits += 1
        return entry.value

    def put(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        if len(self._entries) >= MAX_ENTRIES:
            # Evict the soonest to expire. Not an LRU: with a uniform TTL this
            # is the entry with the least remaining value.
            oldest = min(self._entries, key=lambda k: self._entries[k].expires_at)
            del self._entries[oldest]
        self._entries[key] = _Entry(value=value, expires_at=time.monotonic() + self.ttl)

    def clear(self) -> None:
        self._entries.clear()
        self.hits = self.misses = 0
