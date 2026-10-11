"""Bounded synthetic local-Mongo measurements; not a production SLO benchmark.

Run from backend with MZ2_TEST_MONGO_URI and PYTHONPATH=.;tests configured.
Only a UUID-named fixture database is created and removed. No provider IO.
"""
import asyncio
from contextvars import Context
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from time import perf_counter
from unittest.mock import AsyncMock, patch

import assembly_completion_delivery as delivery
from operational_atomic import operational_owner
from orders_db import upsert_order
from test_assembly_completion_delivery import DeliveryTests

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/operations/READY-SHIPPING-PHASE1/PERFORMANCE.json"
URI = "mongodb://127.0.0.1:27944/?replicaSet=readyPhase1"


def task(awaitable):
    return asyncio.create_task(awaitable, context=Context())


async def measured(awaitable):
    started = perf_counter()
    result = await asyncio.wait_for(awaitable, 5)
    return round((perf_counter() - started) * 1000, 3), result


async def benchmark(fixture):
    ready = []
    for piece_id in fixture.ids:
        elapsed, response = await measured(fixture.mark_piece(piece_id))
        assert response.status_code == 200, response.text
        assert response.json()["piece_ready_confirmed"] is True
        ready.append(elapsed)
    fixture.external.assert_not_awaited()
    import preparation_piece_operations as pieces
    pieces.sync_completed_carrier_label.assert_not_awaited()
    assert await fixture.on_hand() == 16
    workflow = await fixture.workflow()
    assert workflow["assembly_status"] == "completed"
    assert workflow[delivery.FIELD]["state"] == "pending"

    async def writer(owner, number):
        raw = {"id": number, "reference_id": number, "status": {"slug": "in_progress"}}
        result = await upsert_order(fixture.db, owner, number,
            {"status": "in_progress", "order_status": "in_progress"}, "salla_direct", raw=raw)
        assert result["doc"]["order_number"] == number
        return result

    entered, release = asyncio.Event(), asyncio.Event()
    async def provider_get(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 5)
        return "internal", {"id": "internal", "reference_id": "local-assembly",
                            "status": {"slug": "in_progress"}}

    no_post = AsyncMock(side_effect=AssertionError("Unexpected provider POST"))
    with patch.object(delivery.shipping, "_resolve_order", provider_get), \
         patch.object(delivery.shipping, "call_salla", no_post):
        outbox = task(delivery.resume(fixture.db, user_id="owner", order_number="local-assembly", manual=True))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            network_wait_writer_ms, _ = await measured(writer("owner", "network-wait-unrelated"))
            assert not release.is_set() and not outbox.done()
        finally:
            release.set()
            await asyncio.wait_for(outbox, 5)
        no_post.assert_not_awaited()
    assert (await fixture.workflow())[delivery.FIELD]["state"] == "requires_attention"

    # A real worker cycle includes Mongo global/owner admission and claim/CAS.
    # Synthetic GET transport avoids any provider or PDF network dependency.
    provider_reads = []
    async def worker_get(db, owner, method, path, **kwargs):
        assert method == "GET", "Background reconciliation attempted a write"
        provider_reads.append(path)
        current = {"id": "internal", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}
        if path == "/orders":
            return {"data": [current]}
        assert path == "/orders/internal", path
        return {"data": current}
    await fixture.db[delivery.WORKFLOWS].update_one({"user_id": "owner"},
        {"$set": {f"{delivery.FIELD}.due_at": ""}})
    with patch.object(delivery.shipping, "call_salla", worker_get):
        worker_ms, processed = await measured(delivery.run_once(fixture.db))
        assert processed == 1
        cooldown_ms, processed = await measured(delivery.run_once(fixture.db))
        assert processed == 0
    assert len(provider_reads) == 2
    assert await fixture.on_hand() == 16

    held, unlock = asyncio.Event(), asyncio.Event()
    hold_started = None
    async def hold_owner(scoped):
        nonlocal hold_started
        hold_started = perf_counter()
        held.set()
        await asyncio.wait_for(unlock.wait(), 5)

    holder = task(operational_owner(fixture.db, "owner", hold_owner))
    same = different = None
    try:
        await asyncio.wait_for(held.wait(), 5)
        same = task(measured(writer("owner", "held-same-owner")))
        different = task(measured(writer("different-owner", "held-different-owner")))
        different_ms, _ = await asyncio.wait_for(different, 5)
        await asyncio.sleep(max(0, 0.2 - (perf_counter() - hold_started)))
        assert not same.done(), "Same-owner writer bypassed owner serialization"
        hold_ms = round((perf_counter() - hold_started) * 1000, 3)
    finally:
        unlock.set()
        await asyncio.wait_for(holder, 5)
        if different is not None:
            await asyncio.wait_for(different, 5)
        if same is not None:
            same_ms, _ = await asyncio.wait_for(same, 5)
    return {
        "ready_two_pieces_ms": ready,
        "ready_provider_io_calls": 0,
        "same_owner_writer_during_held_provider_get_ms": network_wait_writer_ms,
        "same_owner_writer_finished_before_provider_get_release": True,
        "provider_post_calls": no_post.await_count,
        "explicit_owner_hold_ms": hold_ms,
        "same_owner_writer_during_explicit_hold_ms": same_ms,
        "same_owner_writer_waited_for_release": True,
        "different_owner_writer_during_explicit_hold_ms": different_ms,
        "different_owner_writer_finished_before_release": True,
        "background_worker_cycle_ms": worker_ms,
        "background_worker_provider_get_calls": len(provider_reads),
        "background_worker_cooldown_skip_ms": cooldown_ms,
        "background_worker_skipped_while_cooling_down": True,
    }


async def main():
    assert os.environ.get("MZ2_TEST_MONGO_URI") == URI, "Explicit isolated local URI required"
    started = perf_counter()
    fixture = DeliveryTests("test_atomic_ready_concurrent_and_response_loss_readback")
    try:
        await asyncio.wait_for(fixture.asyncSetUp(), 8)
        results = await asyncio.wait_for(benchmark(fixture), 8)
    finally:
        await asyncio.wait_for(fixture.asyncTearDown(), 8)
    tracked = ["backend/operational_atomic.py", "backend/orders_db.py",
               "backend/assembly_status_policy.py", "backend/assembly_completion_delivery.py",
               "backend/preparation_piece_operations.py", "backend/shipping_read_budget.py",
               "backend/shipping_print_document.py", "backend/order_engine/shipping_label_service.py"]
    report = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5).strip(),
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in tracked},
        "python": platform.python_version(), "asyncio_debug": asyncio.get_running_loop().get_debug(),
        "mongo": "loopback:27944, readyPhase1 replica set", "fixture_database_removed": fixture.db.name,
        "elapsed_seconds": round(perf_counter() - started, 3), "measurements": results,
        "limits": ["One synthetic run, two Ready samples and one sample per concurrency scenario; no percentiles or production SLO claim.",
                   "Real local Mongo transactions, ASGI routes, and stock; synthetic authentication, catalog, and provider transport.",
                   "Provider GET is an asyncio Event; no provider network latency or production load measured.",
                   "Background worker uses synthetic in_progress GET replies; PDF download and parsing are not measured.",
                   "Same-owner timing includes deliberate contention and Mongo transaction retry overhead.",
                   "HEAD may have uncommitted integration changes; listed source hashes identify measured files."]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
