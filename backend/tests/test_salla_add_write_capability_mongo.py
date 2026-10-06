"""The ADD capability cannot update old units or release unrelated barriers."""
import unittest
from uuid import uuid4

from fastapi import HTTPException
import fulfillment_lifecycle as controls
from operational_atomic import operational_owner
from tests import test_salla_add_application_mongo as fixture


class AddWriteCapabilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.SallaAddApplicationMongoTests.asyncSetUp
    cleanup = fixture.SallaAddApplicationMongoTests.cleanup
    replay = fixture.SallaAddApplicationMongoTests.replay
    state = fixture.SallaAddApplicationMongoTests.state

    async def denied(self, action):
        before = await self.state()
        async def mutate(scoped):
            # Catching the denial cannot commit any following write either.
            try:
                await action(scoped)
            except HTTPException:
                pass
            await scoped[controls.AUDIT].insert_one({"_id": uuid4().hex,
                "user_id": "owner", "event_type": "must_rollback"})
        with self.assertRaises(HTTPException):
            await operational_owner(self.db, "owner", mutate, profile="salla_add")
        self.assertEqual(await self.state(), before)

    async def test_old_piece_update_is_forbidden(self):
        await self.denied(lambda scoped: scoped[controls.PIECES].update_one(
            {"user_id": "owner", "piece_id": "old-piece"}, {"$set": {"status": "assigned"}}))

    async def test_existing_component_unit_update_is_forbidden(self):
        await self.denied(lambda scoped: scoped["mezan_component_consumption_units_v1"].update_one(
            {"user_id": "owner", "order_line_id": "old"}, {"$set": {"generation": 1}}))

    async def test_general_order_hold_release_is_forbidden(self):
        await self.db[controls.HOLDS].insert_one({"_id": "manual", "id": "manual", "user_id": "owner",
            "order_number": "order-1", "scope": "order", "status": "active", "authority": "manual"})
        await self.denied(lambda scoped: scoped[controls.HOLDS].update_one(
            {"user_id": "owner", "id": "manual", "status": "active"}, {"$set": {"status": "released"}}))

    async def test_old_plan_line_replacement_is_forbidden(self):
        await self.denied(lambda scoped: scoped["mezan_component_consumption_plans_v1"].update_one(
            {"user_id": "owner", "order_id": "order-1"}, {"$set": {"lines": []}}))

    async def test_invoice_journal_and_inventory_writes_are_forbidden(self):
        for name in ("qoyod_invoices", "journal_entries", "warehouse_locations", "unified_orders"):
            with self.subTest(collection=name):
                await self.denied(lambda scoped: scoped[name].insert_one({"user_id": "owner", "id": "forbidden"}))
                self.assertEqual(await self.db[name].count_documents({"id": "forbidden"}), 0)
