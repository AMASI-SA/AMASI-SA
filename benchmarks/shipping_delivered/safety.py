"""Disposable real-Mongo ASGI safety experiment; never imported by runtime.

Adapters are process-local experiments, NOT implementations for deployment.
Provider transport and authentication are synthetic. Stock and transactions are real.
"""
import asyncio
import argparse
import contextvars
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "backend/tests")]

from pymongo import monitoring
from motor.motor_asyncio import AsyncIOMotorClient
import test_g47_component_lifecycle_integration as fixtures
from benchmarks.shipping_delivered.designs import design_adapter
import preparation_piece_operations as ops
from orders_db import upsert_order
from test_fulfillment_delivered_guard import DeliveredAssemblyTests
from test_g47_component_lifecycle_integration import WHEN
from test_g47_component_lifecycle_integration import ComponentRouteTests


PROVIDER = contextvars.ContextVar("safety_provider", default=False)


class ProviderDispatch(monitoring.CommandListener):
    """Observe actual wire-command dispatch, not elapsed sleep or task creation."""
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.dispatched = asyncio.Event()

    def started(self, event):
        if PROVIDER.get() and event.command_name == "update" and event.command.get("update") == "unified_orders":
            self.loop.call_soon_threadsafe(self.dispatched.set)

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


async def provider_write(case, writer, kind):
    payload = case.source_payload(number="order-1", status="delivered",
                                  version="2026-10-09T00:00:00+00:00")

    async def apply(db):
        if kind == "reconciliation":
            from salla_integration import auto_sync
            payload["reference_id"] = "order-1"
            with patch.object(auto_sync, "call_salla", AsyncMock(return_value={"data": [payload]})), \
                 patch.object(auto_sync, "_refresh_plan_b_status_snapshot", AsyncMock()):
                result = await auto_sync._reconcile_status_page(db, "owner", page=1)
                assert result[1] == 1, result
        else:
            incoming = {"order_number": "order-1", "order_status": "delivered",
                        "order_status_slug": "delivered", "order_date": payload["date"]}
            if kind == "enrichment":
                # Same upsert entrypoint used by enrichment, not its HTTP/catalog work.
                incoming["customer_name"] = "Synthetic enriched customer"
            with patch("orders_db._ensure_order_products_catalogued", AsyncMock()), \
                 patch("bnpl.billing_eligible.propagate_status_to_billing_eligible", AsyncMock()):
                await upsert_order(db, "owner", "order-1", incoming,
                                   source="salla_direct", raw=payload, shipping_snapshot={})
    await writer(case.db, "owner", apply)


async def business_snapshot(case):
    # Provider writes and serialization metadata are intentionally excluded.
    rows = {name: await case.db[name].find({}).sort("_id", 1).to_list(10000)
            for name in await case.db.list_collection_names()
            if name not in {"unified_orders", "mz2_atomic_owners"}}
    return {name: docs for name, docs in rows.items() if docs}


async def run_case(design, kind, virtual, phase):
    case = DeliveredAssemblyTests("test_physical_delivered_rejected_without_writes")
    dispatch = ProviderDispatch()
    with patch.object(fixtures, "AsyncIOMotorClient", lambda *a, **kw: AsyncIOMotorClient(*a, event_listeners=[dispatch], **kw)):
        await case.asyncSetUp()
    task = None
    try:
        if virtual:
            piece = await case.seed(status="in_progress", virtual=True)
            if phase == "concurrent_duplicates":
                await case.db[ops.WORKFLOWS].update_one({"order_number": "order-1"}, {"$push": {
                    "operational_items": {"operational_item_id": "virtual-2", "name": "Second pending annotation",
                        "assembly_status": "pending", "blocks_order_completion": True}}})
        else:
            # Reserve real inventory through the established acceptance fixture;
            # acceptance is fixture setup, outside the measured/tested request.
            case.order = ComponentRouteTests.order.__get__(case)
            accepted, _ = await ComponentRouteTests.accept(case)
            assert accepted.status_code == 200, accepted.text
            await case.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
                "order_date": WHEN, "order_status": "in_progress",
                "raw_by_source.salla_direct": case.source_payload(number="order-1", status="in_progress")}})
            await case.seed_physical()
            await ops.ensure_piece_operation_indexes(case.db)
            piece = "piece-1"
        before = await business_snapshot(case)
        stock_before = await case.on_hand()
        shipping = AsyncMock(return_value={"ok": True})
        salla = AsyncMock(side_effect=AssertionError("Salla transport forbidden"))
        fired = False
        read_attempts = 0
        consumption_attempts = 0
        trace = []
        original = ops._current_assembly_order
        consume_original = ops._consume_piece_components
        async def consume(*args, **kwargs):
            nonlocal consumption_attempts
            consumption_attempts += 1
            return await consume_original(*args, **kwargs)
        async with design_adapter(design) as writer:
            adapted = ops._current_assembly_order

            async def launch_writer():
                nonlocal task
                # Independent provider task must NOT inherit active transaction context.
                async def independent():
                    token = PROVIDER.set(True)
                    try:
                        return await provider_write(case, writer, kind)
                    finally:
                        PROVIDER.reset(token)
                task = asyncio.create_task(independent(), context=contextvars.Context())
                if phase == "after_pin" and design in {"canonical", "conditional"}:
                    await asyncio.wait_for(dispatch.dispatched.wait(), 15)
                    assert not task.done(), "writer unexpectedly bypassed serialization"
                    trace.append({"provider_update_command_dispatched_before_mark_ready_continued": True,
                                  "writer_not_completed_while_reservation_held": True})
                else:
                    await asyncio.wait_for(asyncio.shield(task), 15)
                    actual = await original(case.db, user_id="owner", order_number="order-1")
                    assert actual.status == "delivered", actual.status
                    trace.append({"delivered_committed_before_mark_ready_continued": True})

            async def interleave(db, **kwargs):
                nonlocal fired, read_attempts
                read_attempts += 1
                if fired:
                    return await adapted(db, **kwargs)
                fired = True
                if phase == "during_transaction_before_reservation":
                    trace.append({"transaction_route_reads_already_occurred_before_reservation": True})
                    await launch_writer()
                    return await adapted(db, **kwargs)
                if phase == "after_snapshot":
                    dto = await original(db, **kwargs)
                    trace.append({"snapshot_status": dto.status})
                    await launch_writer()
                    return await adapted(db, **kwargs)
                dto = await adapted(db, **kwargs)
                trace.append({"post_pin_status": dto.status})
                await launch_writer()
                return dto

            if phase == "concurrent_duplicates":
                reader = adapted
            elif phase == "abort":
                await case.db.command({"collMod": ops.PIECE_EVENTS, "validator": {
                    "event_type": {"$nin": ["assembly_piece_marked_ready", "operational_assembly_item_marked_ready"]}},
                    "validationLevel": "strict"})
                reader = adapted
            elif phase == "before_transaction":
                await provider_write(case, writer, kind)
                reader = adapted
            else:
                reader = interleave
            with patch.object(ops, "_current_assembly_order", reader), \
                 patch.object(ops, "sync_completed_carrier_label", shipping), \
                 patch.object(ops, "_consume_piece_components", consume), \
                 patch.object(ops, "call_salla", salla):
                if phase == "concurrent_duplicates":
                    concurrent = await asyncio.wait_for(asyncio.gather(case.mark_piece(piece), case.mark_piece(piece)), 40)
                    assert all(r.status_code == 200 for r in concurrent), [r.text for r in concurrent]
                    assert sum(not r.json().get("idempotent", False) for r in concurrent) == 1
                    response = await asyncio.wait_for(case.mark_piece(piece), 40)
                    assert response.json().get("idempotent") is True
                    trace.append({"concurrent_http_statuses": [r.status_code for r in concurrent],
                                  "concurrent_non_idempotent": 1, "sequential_duplicate_idempotent": True})
                else:
                    response = await asyncio.wait_for(case.mark_piece(piece), 40)
            if task:
                await asyncio.wait_for(task, 20)
                final_provider_order = await original(case.db, user_id="owner", order_number="order-1")
                assert final_provider_order.status == "delivered"
                trace.append({"provider_delivered_committed_after_request_finished": True})
        after = await business_snapshot(case)
        rejected = response.status_code == 409
        should_reject = phase == "before_transaction" or (design in {"canonical", "conditional"} and phase in {
            "after_snapshot", "during_transaction_before_reservation"})
        expected = 500 if phase == "abort" else 409 if should_reject else 200
        assert response.status_code == expected, response.text
        if rejected or phase == "abort":
            if rejected:
                assert response.json()["detail"]["code"] == "assembly_order_delivered", response.text
                assert consumption_attempts == 0, "delivered rejection reached consumption function"
            else:
                assert consumption_attempts > 0, "failure was before consumption/event attempt"
            assert before == after, "rejection left business side effects"
            assert await case.on_hand() == stock_before
            assert shipping.await_count == 0
            canonical = await case.db.unified_orders.find_one({"order_number": "order-1"})
            assert "benchmark_only_canonical_fence" not in canonical, "aborted fence persisted"
        assert salla.await_count == 0
        if design in {"canonical", "conditional"} and phase in {"after_snapshot", "during_transaction_before_reservation"}:
            assert read_attempts >= 2, "transaction callback did not retry stale snapshot"
        if phase == "concurrent_duplicates":
            assert shipping.await_count == 0
            if not virtual:
                assert await case.on_hand() == stock_before - 2
            assert await case.db[ops.PIECE_EVENTS].count_documents({"piece_id": piece,
                "event_type": {"$in": ["assembly_piece_marked_ready", "operational_assembly_item_marked_ready"]}}) == 1
        if design == "baseline" and phase in {"after_snapshot", "after_pin", "during_transaction_before_reservation"} and not virtual:
            assert await case.on_hand() == stock_before - 2, "positive control did not reproduce consumption"
        return {"design": design, "writer": kind, "virtual": virtual, "phase": phase,
                "http_status": response.status_code, "trace": trace,
                "rejected": rejected, "zero_business_side_effects_on_reject": before == after if rejected or phase == "abort" else None,
                "canonical_read_attempts": read_attempts, "consumption_attempts": consumption_attempts,
                "stock_before": stock_before, "stock_after": await case.on_hand(),
                "shipping_hook_calls": shipping.await_count, "real_salla_calls": 0,
                "baseline_race_observed": design == "baseline" and phase in {"after_snapshot", "after_pin", "during_transaction_before_reservation"},
                "ordering": "aborted" if phase == "abort" else "delivered_first" if should_reject else "stale_baseline" if design == "baseline" else "assembly_first"}
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await case.asyncTearDown()


async def main():
    from isolation import localhost_only
    localhost_only()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="benchmark-results/safety.json")
    args = parser.parse_args()
    from motor.motor_asyncio import AsyncIOMotorClient
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    assert uri.startswith("mongodb://127.0.0.1:"), "isolated local Mongo URI required; no skip/fallback"
    client = AsyncIOMotorClient(uri)
    try:
        hello = await client.admin.command("hello")
        version = (await client.admin.command("buildInfo"))["version"]
        assert hello.get("setName") and hello.get("isWritablePrimary")
        assert version == "8.0.12", version
    finally:
        client.close()
    rows = []
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    failure = None
    try:
        for design in ("baseline", "canonical", "conditional"):
            for kind in ("status_only_upsert", "reconciliation", "enrichment"):
                for virtual in (False, True):
                    for phase in ("before_transaction", "during_transaction_before_reservation", "after_snapshot", "after_pin"):
                        rows.append(await run_case(design, kind, virtual, phase))
            for virtual in (False, True):
                kind = "no_provider_write"
                for phase in ("abort", "concurrent_duplicates"):
                    rows.append(await run_case(design, kind, virtual, phase))
    except BaseException as exc:
        failure = {"case": [design, kind, virtual, phase], "type": type(exc).__name__, "error": str(exc)}
        raise
    finally:
        out.write_text(json.dumps({"mongo_version": version, "replica_set": hello["setName"],
        "primary": True, "cases": rows, "pass": len(rows), "skipped": 0,
        "failure": failure,
        "limitations": ["Enrichment exercises its actual upsert entrypoint, not external transport/catalog work.",
            "Virtual fixture is an operational annotation with no material demand; physical fixture tests real inventory consumption.",
            "Provider transport/auth are synthetic; shipping hook is recorded, never issues shipments.",
            "Reconciliation accounting snapshot refresh is stubbed to keep accounting outside scope.",
            "Provider upsert catalogue and billing propagation are stubbed; canonical status persistence is real.",
            "Assembly-first serialization permits assembly and delays provider commit; not retroactive delivered precedence."]}, indent=2), encoding="utf-8")
    print(json.dumps({"safety_pass": len(rows), "skipped": 0, "output": str(out)}))


if __name__ == "__main__":
    asyncio.run(main())
