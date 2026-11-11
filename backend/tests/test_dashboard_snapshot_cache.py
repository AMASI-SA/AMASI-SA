"""Behavioral bounds for dashboard saved-result reads; no external services."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dashboard_snapshot_cache import DashboardSnapshotBusy, DashboardSnapshotCache, DashboardSnapshotTimeout


class Clock:
    seconds = 0.0
    start = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)

    def monotonic(self):
        return self.seconds

    def now(self):
        return self.start + timedelta(seconds=self.seconds)


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [60, 1800, 18000])
async def test_exact_expiry_metadata_and_independent_results(ttl):
    clock = Clock()
    cache = DashboardSnapshotCache(monotonic=clock.monotonic, now=clock.now)
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return {"items": [{"amount": calls}], "unknown_profit": None}

    first = await cache.get(("owner", "section"), ttl_seconds=ttl, build=build)
    assert first["snapshot"] == {
        "generated_at": clock.start.isoformat(),
        "next_refresh_at": (clock.start + timedelta(seconds=ttl)).isoformat(),
        "refresh_interval_seconds": ttl,
    }
    first["items"][0]["amount"] = 999
    clock.seconds = ttl - 0.001
    saved = await cache.get(("owner", "section"), ttl_seconds=ttl, build=build)
    assert saved["items"][0]["amount"] == 1
    assert saved["unknown_profit"] is None
    assert calls == 1
    clock.seconds = ttl
    fresh = await cache.get(("owner", "section"), ttl_seconds=ttl, build=build)
    assert fresh["items"][0]["amount"] == 2
    assert fresh["snapshot"]["generated_at"] != first["snapshot"]["generated_at"]


@pytest.mark.asyncio
async def test_manual_refresh_bypasses_value_and_all_concurrent_waiters_join():
    cache = DashboardSnapshotCache()
    calls = 0
    started, release = asyncio.Event(), asyncio.Event()

    async def initial():
        return {"version": 1}

    async def rebuild():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"version": 2}

    await cache.get("same", ttl_seconds=1800, build=initial)
    one = asyncio.create_task(cache.get("same", ttl_seconds=1800, build=rebuild, refresh=True))
    await started.wait()
    two = asyncio.create_task(cache.get("same", ttl_seconds=1800, build=rebuild, refresh=True))
    three = asyncio.create_task(cache.get("same", ttl_seconds=1800, build=rebuild))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(one, two, three)
    assert calls == 1
    assert [value["version"] for value in results] == [2, 2, 2]
    results[0]["version"] = 8
    assert results[1]["version"] == 2


@pytest.mark.asyncio
async def test_error_is_not_cached_and_disconnected_waiter_does_not_cancel_shared_work():
    cache = DashboardSnapshotCache(max_inflight=1)
    started, release = asyncio.Event(), asyncio.Event()

    async def build():
        started.set()
        await release.wait()
        return {"complete": True}

    first = asyncio.create_task(cache.get("same", ttl_seconds=60, build=build))
    await started.wait()
    second = asyncio.create_task(cache.get("same", ttl_seconds=60, build=build))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(DashboardSnapshotBusy):
        await cache.get("other", ttl_seconds=60, build=build)
    release.set()
    assert (await second)["complete"] is True

    async def fail():
        raise ValueError("synthetic_failure")

    with pytest.raises(ValueError, match="synthetic_failure"):
        await cache.get("failed", ttl_seconds=60, build=fail)
    assert (await cache.get("failed", ttl_seconds=60, build=build))["complete"] is True


@pytest.mark.asyncio
async def test_lru_and_payload_bounds_do_not_retain_oversized_or_cross_key_results():
    cache = DashboardSnapshotCache(max_entries=2, max_bytes=2000, max_entry_bytes=800)
    calls = {}

    async def read(key, *, refresh=False, large=False):
        async def build():
            calls[key] = calls.get(key, 0) + 1
            return {"key": key, "sequence": calls[key], "body": "x" * (3000 if large else 1)}
        return await cache.get(key, ttl_seconds=60, build=build, refresh=refresh)

    await read("a")
    await read("b")
    await read("a")
    await read("c")
    assert (await read("a"))["sequence"] == 1
    assert (await read("b"))["sequence"] == 2
    assert (await read("b", refresh=True, large=True))["sequence"] == 3
    # An oversized successful refresh invalidates the old value too.
    assert (await read("b"))["sequence"] == 4
    assert cache._bytes <= cache.max_bytes


@pytest.mark.asyncio
async def test_total_payload_budget_evicts_even_before_entry_limit():
    cache = DashboardSnapshotCache(max_entries=20, max_bytes=500, max_entry_bytes=500)
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return {"body": "x" * 100, "version": calls}

    await cache.get("a", ttl_seconds=60, build=build)
    await cache.get("b", ttl_seconds=60, build=build)
    assert (await cache.get("a", ttl_seconds=60, build=build))["version"] == 3
    assert cache._bytes <= 500


@pytest.mark.asyncio
async def test_timeout_cancels_stuck_build_and_releases_admission_for_retry():
    cache = DashboardSnapshotCache(max_inflight=1, build_timeout_seconds=0.01)
    cancelled = asyncio.Event()

    async def stuck():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(DashboardSnapshotTimeout):
        await cache.get("a", ttl_seconds=60, build=stuck)
    assert cancelled.is_set()

    async def success():
        return {"complete": True}

    assert (await cache.get("a", ttl_seconds=60, build=success))["complete"] is True
    assert (await cache.get("b", ttl_seconds=60, build=success))["complete"] is True
