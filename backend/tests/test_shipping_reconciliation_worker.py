"""Worker recovery on disposable real Mongo; provider transport is GET-only fake."""
import asyncio
from contextvars import Context
from datetime import timedelta
import unittest
from unittest.mock import AsyncMock, patch

import assembly_completion_delivery as delivery
import test_assembly_completion_delivery as fixture


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.DeliveryTests.asyncSetUp
    asyncTearDown = fixture.DeliveryTests.asyncTearDown
    source_payload = fixture.DeliveryTests.source_payload
    webhook = fixture.DeliveryTests.webhook
    mark_piece = fixture.DeliveryTests.mark_piece
    on_hand = fixture.DeliveryTests.on_hand
    complete_review = fixture.DeliveryTests.complete_review
    workflow = fixture.DeliveryTests.workflow
    finish = fixture.DeliveryTests.finish

    async def prepare(self):
        await self.finish()
        self.calls = []
        self.provider_status = "completed"
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            f"{delivery.FIELD}.state": "requires_attention",
            f"{delivery.FIELD}.status_attempted": True,
            f"{delivery.FIELD}.awb_attempted": True,
            f"{delivery.FIELD}.due_at": ""}})

    async def provider(self, db, owner, method, path, **kwargs):
        self.assertEqual(method, "GET", "Worker must never send a provider POST")
        self.calls.append(path)
        order = {"id": "internal", "reference_id": "local-assembly",
                 "status": {"slug": self.provider_status}}
        if path == "/orders":
            return {"data": [order]}
        if path == "/orders/internal":
            return {"data": order}
        self.fail(f"Unexpected provider GET: {path}")

    def label(self):
        return AsyncMock(return_value={"ready": True, "order_status_completed": True,
            "shipment_id": "existing", "tracking_number": "EXISTING-AWB",
            "label_url": "https://example.test/verified-existing.pdf"})

    async def assert_preserved(self):
        row = await self.workflow()
        self.assertTrue(row[delivery.FIELD]["status_attempted"])
        self.assertTrue(row[delivery.FIELD]["awb_attempted"])
        self.assertEqual(await self.on_hand(), 16)

    async def expire_slots(self):
        expired = (delivery.now() - timedelta(seconds=1)).isoformat()
        await self.db[delivery.LIMITS].update_many({}, {"$set": {"available_at": expired}})
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            f"{delivery.FIELD}.lease_until": expired, f"{delivery.FIELD}.due_at": expired}})

    async def test_attention_later_completed_recovers_read_only(self):
        await self.prepare()
        self.provider_status = "in_progress"
        with patch.object(delivery.shipping, "call_salla", self.provider), \
             patch.object(delivery.shipping, "refresh_shipping_label", self.label()) as label:
            self.assertEqual(await delivery.run_once(self.db), 1)
            self.assertEqual((await self.workflow())[delivery.FIELD]["state"], "requires_attention")
            label.assert_not_awaited()
            await self.expire_slots()
            self.provider_status = "completed"
            self.assertEqual(await delivery.run_once(self.db), 1)
        row = await self.workflow()
        self.assertEqual(row[delivery.FIELD]["state"], "confirmed")
        self.assertEqual(row[delivery.FIELD]["read_attempts"], 2)
        self.assertTrue(row["carrier_label_ready"])
        await self.assert_preserved()

    async def test_two_workers_share_global_gate_and_cooldown(self):
        await self.prepare()
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
            return await self.provider(*args, **kwargs)
        with patch.object(delivery.shipping, "call_salla", held), \
             patch.object(delivery.shipping, "refresh_shipping_label", self.label()):
            first = asyncio.create_task(delivery.run_once(self.db), context=Context())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                self.assertEqual(await delivery.run_once(self.db), 0)
            finally:
                release.set()
                self.assertEqual(await asyncio.wait_for(first, 5), 1)
            self.assertEqual(await delivery.run_once(self.db), 0)
        self.assertEqual((await self.workflow())[delivery.FIELD]["read_attempts"], 1)
        for key, seconds in (("global", delivery.GLOBAL_READ_INTERVAL), ("owner:owner", delivery.OWNER_READ_INTERVAL)):
            row = await self.db[delivery.LIMITS].find_one({"_id": key})
            self.assertIsNone(row["claim"])
            self.assertGreater(row["available_at"], (delivery.now() + timedelta(seconds=seconds - 5)).isoformat())
        await self.assert_preserved()

    async def test_crashed_claim_recovers_only_after_expiry(self):
        await self.prepare()
        future = (delivery.now() + timedelta(seconds=120)).isoformat()
        for key in ("global", "owner:owner"):
            await self.db[delivery.LIMITS].insert_one({"_id": key, "claim": "dead", "available_at": future})
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            f"{delivery.FIELD}.claim": "dead", f"{delivery.FIELD}.lease_until": future}})
        with patch.object(delivery.shipping, "call_salla", self.provider), \
             patch.object(delivery.shipping, "refresh_shipping_label", self.label()):
            self.assertEqual(await delivery.run_once(self.db), 0)
            self.assertEqual(self.calls, [])
            await self.expire_slots()
            self.assertEqual(await delivery.run_once(self.db), 1)
        self.assertEqual((await self.workflow())[delivery.FIELD]["state"], "confirmed")
        await self.assert_preserved()

    async def test_stale_worker_cannot_publish_or_release_successor(self):
        await self.prepare()
        entered, release = asyncio.Event(), asyncio.Event()
        first_get = True
        async def held(*args, **kwargs):
            nonlocal first_get
            if first_get:
                first_get = False
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return await self.provider(*args, **kwargs)
        with patch.object(delivery.shipping, "call_salla", held), \
             patch.object(delivery.shipping, "refresh_shipping_label", self.label()) as label:
            old = asyncio.create_task(delivery.run_once(self.db), context=Context())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await self.expire_slots()
                self.assertEqual(await delivery.run_once(self.db), 1)
                successor = await self.workflow()
                slots = await self.db[delivery.LIMITS].find({}).sort("_id", 1).to_list(2)
            finally:
                release.set()
                await asyncio.wait_for(old, 5)
            self.assertEqual(await self.workflow(), successor)
            self.assertEqual(await self.db[delivery.LIMITS].find({}).sort("_id", 1).to_list(2), slots)
            self.assertEqual(label.await_count, 1)
        await self.assert_preserved()

    async def test_backoff_bounds_and_read_budget_exhaustion(self):
        await self.prepare()
        instant = delivery.now()
        with patch.object(delivery, "now", return_value=instant):
            for count, seconds in ((1, 60), (2, 120), (6, 1920), (7, 3600), (16, 3600)):
                self.assertEqual(delivery.next_read_at({delivery.FIELD: {"read_attempts": count}}),
                                 (instant + timedelta(seconds=seconds)).isoformat())
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            f"{delivery.FIELD}.read_attempts": delivery.MAX_READ_ATTEMPTS}})
        with patch.object(delivery.shipping, "call_salla", self.provider), \
             patch.object(delivery.shipping, "refresh_shipping_label", self.label()) as label:
            self.assertEqual(await delivery.run_once(self.db), 0)
            label.assert_not_awaited()
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.workflow())[delivery.FIELD]["read_attempts"], delivery.MAX_READ_ATTEMPTS)
        await self.assert_preserved()

    async def test_terminal_writer_during_read_prevents_publication(self):
        await self.prepare()
        from orders_db import upsert_order
        changed = False
        async def terminal(*args, **kwargs):
            nonlocal changed
            result = await self.provider(*args, **kwargs)
            if not changed:
                changed = True
                await upsert_order(self.db, "owner", "local-assembly",
                    {"order_status": "delivered", "order_status_slug": "delivered"},
                    "salla_direct", raw={"status": {"slug": "delivered"}})
            return result
        with patch.object(delivery.shipping, "call_salla", terminal), \
             patch.object(delivery.shipping, "refresh_shipping_label", self.label()) as label:
            self.assertEqual(await delivery.run_once(self.db), 1)
            label.assert_not_awaited()
        row = await self.workflow()
        self.assertFalse(row["carrier_label_ready"])
        self.assertFalse(row[delivery.FIELD]["order_confirmed"])
        self.assertEqual(row[delivery.FIELD]["state"], "requires_attention")
        await self.assert_preserved()
