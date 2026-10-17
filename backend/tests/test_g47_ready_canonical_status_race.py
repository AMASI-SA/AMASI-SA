"""Actual Ready endpoints and canonical writers on a disposable replica set.

Only provider transport/authentication is replaced. No stock or transaction
double. Independent request contexts reproduce PR1300's snapshot interleave.
"""
import asyncio
from contextvars import Context
from unittest.mock import AsyncMock, patch
import unittest

import preparation_piece_operations as operations
import test_g47_component_lifecycle_integration as fixture
from orders_db import upsert_order
from operational_atomic import operational_owner
from salla_integration import auto_sync
from qoyod_auto_unified.live_source import _promote_snapshot_to_unified


class CanonicalStatusRaceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    order = fixture.ComponentRouteTests.order
    accept = fixture.ComponentRouteTests.accept
    source_payload = fixture.ComponentRouteTests.source_payload
    seed_physical = fixture.ComponentRouteTests.seed_physical
    mark_piece = fixture.ComponentRouteTests.mark_piece
    on_hand = fixture.ComponentRouteTests.on_hand
    # PR1305's older fixture models execution status in seed_physical; the
    # current fixture models it after acceptance. Retain each fixture contract.
    if hasattr(fixture.ComponentRouteTests, "seed_execution_status"):
        seed_execution_status = fixture.ComponentRouteTests.seed_execution_status

    async def seed(self, virtual=False):
        accepted, _ = await self.accept()
        self.assertEqual(accepted.status_code, 200, accepted.text)
        payload = self.source_payload(number="order-1", status="in_progress")
        await self.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
            "order_date": fixture.WHEN, "order_status": "in_progress",
            "order_status_slug": "in_progress", "raw_by_source.salla_direct": payload,
        }})
        await self.seed_physical()
        if virtual:
            await self.db[operations.PIECES].delete_many({})
            await self.db[operations.WORKFLOWS].update_one({"order_number": "order-1"}, {"$set": {
                "operational_items": [{"operational_item_id": "virtual-1", "name": "Synthetic note",
                    "assembly_status": "pending", "blocks_order_completion": True}],
            }})
        await operations.ensure_piece_operation_indexes(self.db)
        return "virtual-1" if virtual else "piece-1"

    async def write_status(self, writer, status="delivered", db=None):
        db = self.db if db is None else db
        payload = self.source_payload(number="order-1", status=status, version="2026-10-10T00:00:00+00:00")
        if writer == "upsert":
            return await upsert_order(db, "owner", "order-1", {
                "order_number": "order-1", "order_status": status, "order_status_slug": status,
                "order_date": fixture.WHEN}, source="salla_direct", raw=payload)
        if writer == "reconcile":
            with patch.object(auto_sync, "call_salla", AsyncMock(return_value={"data": [payload]})), \
                 patch.object(auto_sync, "_refresh_plan_b_status_snapshot", AsyncMock()):
                return await auto_sync._reconcile_status_page(db, "owner", page=1)
        if writer == "promotion":
            await self.db.integration_inbox.insert_one({"user_id": "owner",
                "connector_key": "salla_direct_status_resync", "salla_order_number": "order-1",
                "received_at": "2026-10-10T00:00:00+00:00",
                "canonical_payload": {"order_status": status, "order_status_native": status}})
            return await _promote_snapshot_to_unified(db, orders_user_id="owner", order_number="order-1")
        raise AssertionError(writer)

    async def business_snapshot(self):
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(1000)
                for name in (operations.PIECES, operations.WORKFLOWS, operations.PIECE_EVENTS,
                             fixture.UNITS, fixture.PLANS, fixture.LOCATIONS)}

    async def check_before(self, virtual, writer, status):
        piece = await self.seed(virtual)
        await self.write_status(writer, status)
        before = await self.business_snapshot()
        with patch.object(operations, "sync_completed_carrier_label", AsyncMock()) as shipping:
            response = await self.mark_piece(piece)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.business_snapshot(), before)
        self.assertEqual(await self.on_hand(), 20)
        shipping.assert_not_awaited()

    async def check_during(self, virtual, writer):
        piece = await self.seed(virtual)
        original = operations._current_assembly_order
        task = None
        observations = []
        async def interleave(db, **kwargs):
            nonlocal task
            order = await original(db, **kwargs)
            if task is None:
                self.assertEqual(order.status, "in_progress")
                task = asyncio.create_task(self.write_status(writer), context=Context())
                # The real writer must remain unable to commit while Ready owns
                # the transaction. A bypass commits and fails the fresh read.
                await asyncio.sleep(0.2)
                observations.append((await original(self.db, **kwargs)).status)
                self.assertFalse(task.done(), "status writer bypassed Ready owner")
                self.assertEqual((await original(db, **kwargs)).status, "in_progress")
            return order
        try:
            with patch.object(operations, "_current_assembly_order", interleave):
                response = await self.mark_piece(piece)
            self.assertEqual(response.status_code, 200, response.text)
            await asyncio.wait_for(task, 10)
            self.assertEqual(observations, ["in_progress"])
            canonical = await self.db.unified_orders.find_one({"order_number": "order-1"})
            self.assertEqual(canonical["order_status_slug"], "delivered")
            self.assertEqual(await self.on_hand(), 20 if virtual else 18)
            before = await self.business_snapshot()
            rejected = await self.mark_piece(piece)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertEqual(await self.business_snapshot(), before)
        finally:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    async def test_two_writers_wait_for_existing_owner(self):
        await self.seed()
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(scoped):
            entered.set()
            await release.wait()
        owner = asyncio.create_task(operational_owner(self.db, "owner", hold), context=Context())
        await entered.wait()
        writers = [asyncio.create_task(self.write_status(name), context=Context())
                   for name in ("upsert", "reconcile")]
        try:
            await asyncio.sleep(0.2)
            self.assertTrue(all(not task.done() for task in writers))
        finally:
            release.set()
            await asyncio.gather(owner, *writers)
        self.assertEqual((await self.db.unified_orders.find_one({"order_number": "order-1"}))["order_status_slug"], "delivered")

    async def check_writer_first(self, virtual):
        piece = await self.seed(virtual)
        before = await self.business_snapshot()
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(scoped):
            await self.write_status("upsert", db=scoped)
            entered.set()
            await release.wait()
        writer = asyncio.create_task(operational_owner(self.db, "owner", hold), context=Context())
        await entered.wait()
        ready = asyncio.create_task(self.mark_piece(piece), context=Context())
        try:
            await asyncio.sleep(0.2)
            self.assertFalse(ready.done())
        finally:
            release.set()
            _, response = await asyncio.gather(writer, ready)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.business_snapshot(), before)
        self.assertEqual(await self.on_hand(), 20)

    async def test_physical_writer_committing_during_ready_rejects(self):
        await self.check_writer_first(False)

    async def test_virtual_writer_committing_during_ready_rejects(self):
        await self.check_writer_first(True)


def add_cases():
    for virtual in (False, True):
        kind = "virtual" if virtual else "physical"
        for writer in ("upsert", "reconcile", "promotion"):
            async def during(self, v=virtual, w=writer):
                await self.check_during(v, w)
            setattr(CanonicalStatusRaceTests, f"test_{kind}_{writer}_during", during)
            for status in ("delivered", "cancelled"):
                async def before(self, v=virtual, w=writer, s=status):
                    await self.check_before(v, w, s)
                setattr(CanonicalStatusRaceTests, f"test_{kind}_{writer}_{status}_before", before)


add_cases()
