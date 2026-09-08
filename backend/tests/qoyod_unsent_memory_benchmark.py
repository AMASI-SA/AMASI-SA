"""Isolated read-only benchmark for the Qoyod dashboard scan coordinator.

No MongoDB, provider, accounting, or network writes are performed. The
simulated scan allocates bounded scratch rows and returns only a compact,
contract-shaped response so the measurement focuses on duplicate audit work.
"""
from __future__ import annotations

import asyncio
import json
import resource
import time
import tracemalloc
from typing import Any

from qoyod_auto_unified import queue_api


SCAN_ROWS = 5_000
SCRATCH_BYTES_PER_ROW = 512


class ScanProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.peak_active = 0

    async def run(self, *, search: str | None = None) -> dict[str, Any]:
        self.calls += 1
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        scratch = [
            f"{index:06d}:" + ("x" * SCRATCH_BYTES_PER_ROW)
            for index in range(SCAN_ROWS)
        ]
        checksum = sum(len(row) for row in scratch)
        await asyncio.sleep(0.01)
        self.active -= 1
        return {
            "ok": True,
            "read_only": True,
            "orders": [],
            "counts": {"لم يُرسل": SCAN_ROWS},
            "scanned_rows": SCAN_ROWS,
            "checksum": checksum,
            "search": search,
        }


async def legacy_run(concurrency: int) -> dict[str, Any]:
    probe = ScanProbe()

    async def request() -> dict[str, Any]:
        first = await probe.run()
        second = await probe.run()
        assert first["counts"] == second["counts"]
        return second

    results = await asyncio.gather(*[
        asyncio.create_task(request()) for _ in range(concurrency)
    ])
    assert all(result["counts"]["لم يُرسل"] == SCAN_ROWS for result in results)
    return {
        "db_audit_scans": probe.calls,
        "peak_heavy_concurrency": probe.peak_active,
        "responses": len(results),
        "response_correct": True,
    }


async def bounded_run(concurrency: int) -> dict[str, Any]:
    probe = ScanProbe()
    original_execute = queue_api._execute_list
    queue_api._reset_query_coordinator_for_tests()

    async def execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return await probe.run(search=kwargs.get("search"))

    async def original(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("benchmark replaces the uncached executor")

    queue_api._execute_list = execute
    db = object()
    try:
        results = await asyncio.gather(*[
            asyncio.create_task(
                queue_api._list_unsent_orders_with_queue_counts(
                    original,
                    db,
                    user_id="benchmark-tenant",
                    from_date="2026-07-01",
                    limit=5000,
                )
            )
            for _ in range(concurrency)
        ])
    finally:
        queue_api._execute_list = original_execute
        queue_api._reset_query_coordinator_for_tests()
    assert all(result["counts"]["لم يُرسل"] == SCAN_ROWS for result in results)
    return {
        "db_audit_scans": probe.calls,
        "peak_heavy_concurrency": probe.peak_active,
        "responses": len(results),
        "response_correct": True,
    }


async def measure(label: str, concurrency: int, runner: Any) -> dict[str, Any]:
    tracemalloc.start()
    started = time.perf_counter()
    metrics = await runner(concurrency)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "mode": label,
        "concurrency": concurrency,
        "limit": SCAN_ROWS,
        "duration_ms": duration_ms,
        "tracemalloc_current_bytes": current,
        "tracemalloc_peak_bytes": peak,
        "process_peak_rss_kib": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss,
        **metrics,
    }


async def main() -> None:
    rows = []
    for concurrency in (1, 5, 20):
        rows.append(await measure("before_legacy_duplicate", concurrency, legacy_run))
        rows.append(await measure("after_bounded_coalesced", concurrency, bounded_run))
    print(json.dumps({
        "benchmark": "qoyod_unsent_orders_memory_bounds_v1",
        "read_only": True,
        "scan_rows_per_audit": SCAN_ROWS,
        "results": rows,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
