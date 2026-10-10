"""Existing historical plan authority, exercised through real Mongo/ASGI routes."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_g47_component_lifecycle_integration as fixture
from review_local_policy import historical_assembly_evidence


class HistoricalEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixture.ComponentRouteTests()
        await self.f.asyncSetUp()
        self.f.context["permissions"].add("fulfillment.ready.read")
        self.db = self.f.db
        reply, _ = await self.f.accept()
        self.assertEqual(reply.status_code, 200, reply.text)
        await self.f.seed_physical()
        await self.db.unified_orders.update_one({"order_number": "order-1"},
            {"$set": {"raw_by_source.salla_direct.date": "invalid"}})
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        self.provider = AsyncMock(side_effect=AssertionError("No provider call allowed"))
        self.provider_patch = patch.object(fixture.pieces, "call_salla", self.provider)
        self.provider_patch.start()

    async def asyncTearDown(self):
        self.provider_patch.stop()
        self.provider.assert_not_awaited()
        await self.f.asyncTearDown()

    async def snapshot(self):
        return {name: rows for name in sorted(await self.db.list_collection_names())
                if (rows := await self.db[name].find({}).sort("_id", 1).to_list(None))}

    async def test_removed_configuration_consumes_existing_plan_once_concurrently(self):
        search = await self.f.client.get("/preparation-work-v1/assembly/search?q=order-1")
        self.assertEqual(search.status_code, 200, search.text)
        self.assertTrue(any(p["can_mark_ready"] for p in search.json()["pieces"]))
        replies = await asyncio.gather(*(self.f.mark_piece("piece-1") for _ in range(3)))
        self.assertTrue(any(r.status_code == 200 for r in replies))
        self.assertTrue(all(r.status_code in {200, 409} for r in replies))
        self.assertEqual((await self.f.mark_piece("piece-1")).status_code, 200)
        self.assertEqual(await self.f.on_hand(), 18)
        self.assertEqual(await self.db[fixture.UNITS].count_documents({"state": "consumed"}), 1)
        self.assertEqual(await self.db[fixture.pieces.PIECE_EVENTS].count_documents({"piece_id": "piece-1"}), 1)
        fixture.pieces.sync_completed_carrier_label.assert_not_awaited()

    async def test_negative_evidence_has_zero_document_side_effects(self):
        baseline = await self.snapshot()
        cases = [
            (fixture.PLANS, {"state": "cancelled"}),
            (fixture.PLANS, {"state": "unknown"}),
            (fixture.PLANS, {"user_id": "other"}),
            (fixture.PLANS, {"order_id": "other"}),
            (fixture.PLANS, {"source_version.value": -1}),
            (fixture.fulfillment.COMPONENT_LIFECYCLES, {"accepted": False}),
            ("unified_orders", {"g47_salla_snapshot.component_pending": True}),
            ("unified_orders", {"g47_salla_snapshot.requires_authoritative_refresh": True}),
            ("unified_orders", {"user_id": "other"}),
            ("unified_orders", {"order_number": "other"}),
            ("unified_orders", {"raw_by_source.salla_direct.reference_id": "other"}),
            ("unified_orders", {"raw_by_source.salla_direct.id": ""}),
            (fixture.fulfillment.WORKFLOWS, {"completion_mode": "unknown"}),
            (fixture.fulfillment.WORKFLOWS, {"completion_mode": "mezan_local_v1"}),
            (fixture.fulfillment.WORKFLOWS, {"stage": "reviewed"}),
            (fixture.pieces.PIECES, {"order_item_id": "other"}),
            (fixture.pieces.PIECES, {"unit_index": 99}),
            (fixture.pieces.PIECES, {"order_number": "other"}),
            (fixture.pieces.PIECES, {"user_id": "other"}),
            (fixture.pieces.PIECES, {"status": "in_progress", "supplier_dispatch_status": "sent"}),
            ("unified_orders", {"order_status": "delivered"}),
            ("unified_orders", {"order_status": "under_review"}),
        ]
        for status in ("", "unknown", "under_review", "processing", "جاري التنفيذ", "completed", "shipped", "delivered", "cancelled"):
            cases.append(("unified_orders", {"order_status": status, "order_status_slug": status,
                                             "raw_by_source.salla_direct.status": status,
                                             "raw_by_source.salla_direct.status_slug": status}))
        cases.append((fixture.PLANS, None))
        for collection, updates in cases:
            with self.subTest(collection=collection, updates=updates):
                for name in await self.db.list_collection_names():
                    await self.db[name].delete_many({})
                for name, rows in baseline.items():
                    await self.db[name].insert_many(deepcopy(rows))
                if updates is None:
                    await self.db[collection].delete_many({})
                else:
                    await self.db[collection].update_many({}, {"$set": updates})
                before = await self.snapshot()
                response = await self.f.mark_piece("piece-1")
                self.assertIn(response.status_code, (404, 409), response.text)
                self.assertEqual(await self.snapshot(), before)
        fixture.pieces.sync_completed_carrier_label.assert_not_awaited()

    async def test_virtual_uses_same_historical_evidence_and_unit_plan(self):
        await self.db[fixture.pieces.PIECES].delete_many({})
        await self.db[fixture.fulfillment.WORKFLOWS].update_one({"order_number": "order-1"},
            {"$set": {"items": [{"order_item_id": "line-1", "quantity": 2,
                "preparation_route": "direct_assembly", "direct_assembly_piece_ids": ["virtual-1", "virtual-2"]}]}})
        response = await self.f.mark_piece("virtual-1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.f.on_hand(), 18)
        await self.db.unified_orders.update_one({"order_number": "order-1"},
            {"$set": {"order_status": "delivered"}})
        before = await self.snapshot()
        response = await self.f.mark_piece("virtual-2")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_failure_after_consumption_rolls_back_every_document(self):
        await self.db.create_collection(fixture.pieces.PIECE_EVENTS)
        await self.db.command({"collMod": fixture.pieces.PIECE_EVENTS,
            "validator": {"event_type": {"$ne": "assembly_piece_marked_ready"}}})
        before = await self.snapshot()
        response = await self.f.mark_piece("piece-1")
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(await self.snapshot(), before)


def test_evidence_is_not_dto_failure_authority():
    workflow = {"user_id": "owner", "order_number": "n", "stage": "ready_to_ship"}
    plan = {"_id": "plan", "user_id": "owner", "order_id": "n", "state": "accepted"}
    canonical = {"user_id": "owner", "order_number": "n", "order_status": "under_review",
                 "raw_by_source": {"salla_direct": {"id": "i", "reference_id": "n", "status": "under_review", "date": "invalid"}}}
    assert historical_assembly_evidence(user_id="owner", workflow=workflow, canonical=canonical, plan=plan)
    for mode in ("mezan_local_v1", "unknown"):
        assert historical_assembly_evidence(user_id="owner", workflow={**workflow, "completion_mode": mode}, canonical=canonical, plan=plan) is None
    assert historical_assembly_evidence(user_id="owner", workflow=workflow, canonical=canonical, plan={}) is None
