"""Component lifecycle tests; transactional cases require an isolated local replica set.

No fake Mongo transaction, no monkeypatched atomic_owner, no Preview database.
MZ2_TEST_MONGO_URI is deliberately restricted to a loopback test server.
"""
import asyncio
from decimal import Decimal
import os
import sys
from urllib.parse import urlsplit
import unittest
import uuid

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_atomic import atomic_owner
import stock_component_consumption_service as service


CUTOFF = "2040-01-01T00:00:00+00:00"
CREATED = "2040-01-02T00:00:00+00:00"

WORKER = r'''
import asyncio, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_atomic import atomic_owner
from stock_component_consumption_service import consume_component_stock
async def main():
    assert sys.argv[1].startswith("g47_component_test_")
    client = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
    db = client[sys.argv[1]]
    async def work(scoped):
        await consume_component_stock(scoped, merchant_id="owner", order_id="order")
        if sys.argv[2] == "before_commit":
            os._exit(77)
    await atomic_owner(db, "owner", work)
    os._exit(78)  # committed response intentionally lost
asyncio.run(main())
'''


class ComponentRulesTests(unittest.TestCase):
    def test_finite_six_decimal_quantity_contract(self):
        self.assertEqual(service.quantity_decimal("0.000001"), Decimal("0.000001"))
        self.assertEqual(service._stored(Decimal("0.123456")), 0.123456)
        for value in (None, True, "NaN", "Infinity", -1, "0.0000001", "1000000001"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                service.quantity_decimal(value)

    def test_selected_ids_override_conflicting_display_names(self):
        tokens = service.selection_tokens({"options_raw": [{"option_id": "colour", "name": "Colour",
            "value": {"id": "red", "name": "Blue"}}]})
        self.assertTrue(service.binding_selected({"option_id": "colour", "value_id": "red"}, tokens))
        self.assertFalse(service.binding_selected({"option_id": "colour", "value_id": "blue",
            "option_name": "Colour", "value_name": "Blue"}, tokens))
        self.assertFalse(service.binding_selected({"option_id": "other", "value_id": "blue",
            "option_name": "Colour", "value_name": "Blue"}, tokens))

    def test_name_only_and_filled_options(self):
        tokens = service.selection_tokens({"options_raw": [{"option_id": "colour", "name": "Colour", "value": "Gold"}],
            "custom_fields": [{"field_id": "engraving", "value": 0}, {"field_id": "empty", "value": False}]})
        self.assertTrue(service.binding_selected({"option_id": "colour", "value_id": "gold", "option_name": "Colour", "value_name": "Gold"}, tokens))
        self.assertTrue(service.binding_selected({"option_id": "field:engraving", "value_id": "filled"}, tokens))
        self.assertFalse(service.binding_selected({"option_id": "field:empty", "value_id": "filled"}, tokens))
        self.assertTrue(service.binding_selected({"option_id": "colour", "value_id": "__option__"}, tokens))

    def test_rejects_ambiguous_line_and_noninteger_units(self):
        for rows in ([{"product_id": "p", "quantity": 1}], [{"order_line_id": "l", "product_id": "p", "quantity": 1.5}],
                     [{"order_line_id": "l", "product_id": "p", "quantity": 1}] * 2):
            with self.assertRaises(HTTPException):
                service._lines(rows)

    def test_version_and_cohort_timestamps_require_explicit_identity(self):
        for version in (None, True, -1, "1", "2040-01-01"):
            with self.assertRaises(HTTPException):
                service._version(version)
        with self.assertRaises(HTTPException):
            service._timestamp("2040-01-01T01:00:00")
        self.assertEqual(service._version(7), {"kind": "revision", "value": 7})
        self.assertEqual(service._version("2040-01-01T03:00:00+03:00")["value"], CUTOFF)


@unittest.skipUnless(os.environ.get("MZ2_TEST_MONGO_URI"), "requires isolated local Mongo replica set")
class ComponentMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ["MZ2_TEST_MONGO_URI"]
        if urlsplit(uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
            self.fail("component integration tests require a loopback Mongo fixture")
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        self.db = self.client["g47_component_test_" + uuid.uuid4().hex]
        hello = await self.db.command("hello")
        self.assertTrue(hello.get("setName"), "test fixture must be a replica set")
        await service.ensure_component_consumption_indexes(self.db)
        await self.db.settings.insert_one({"user_id": "owner", "g47_inventory": {"component_lifecycle_starts_at": CUTOFF}})
        await self.db[service.PRODUCTS].insert_one({"user_id": "owner", "id": "p", "salla_product_id": "p"})
        await self.db[service.RESOURCES].insert_one({"user_id": "owner", "id": "r", "kind": "stock_component", "track_inventory": True})
        await self.db[service.PRODUCT_BINDINGS].insert_one({"id": "b", "user_id": "owner", "salla_product_id": "p", "resource_id": "r", "quantity": 2})
        await self.db[service.LOCATIONS].insert_one({"user_id": "owner", "id": "loc", "warehouse_id": "warehouse", "state": "occupied",
            "occupancy": {"items": [
                {"item_type": "stock_component", "resource_id": "r", "receipt_id": "lot", "quantity": 10},
                {"item_type": "product", "product_id": "r", "sku": "r", "receipt_id": "product-lot", "quantity": 4}],
                "total_quantity": 14}})

    async def asyncTearDown(self):
        # Only the unique database created by this case may be deleted.
        self.assertTrue(self.db.name.startswith("g47_component_test_"))
        await self.client.drop_database(self.db.name)
        self.client.close()

    def line(self, qty=3, **patch):
        return {"order_line_id": "line", "product_id": "p", "quantity": qty, **patch}

    async def reserve(self, order="order", qty=3, **kwargs):
        return await service.reserve_component_stock(self.db, merchant_id="owner", order_id=order,
            lines=kwargs.pop("lines", [self.line(qty)]), source_version=kwargs.pop("source_version", 1),
            source_created_at=kwargs.pop("source_created_at", CREATED), **kwargs)

    async def consume(self, order="order", **kwargs):
        return await service.consume_component_stock(self.db, merchant_id="owner", order_id=order, **kwargs)

    async def cancel(self, order="order", version=2, **kwargs):
        return await service.release_component_stock(self.db, merchant_id="owner", order_id=order, source_version=version, **kwargs)

    async def occupancy(self):
        return (await self.db[service.LOCATIONS].find_one({"id": "loc"}))["occupancy"]

    async def stock(self):
        return (await self.occupancy())["items"][0]["quantity"]

    async def assertCode(self, code, awaitable):
        with self.assertRaises(HTTPException) as error:
            await awaitable
        self.assertEqual(error.exception.detail["code"], "component_" + code)

    async def crash_worker(self, point):
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", WORKER, self.db.name, point)
        return await asyncio.wait_for(process.wait(), 30)

    async def test_multiplication_units_replay_and_product_stock_exclusion(self):
        plan = await self.reserve()
        self.assertEqual(await self.stock(), 10)
        allocations = await self.db[service.UNITS].find({}).to_list(20)
        self.assertEqual(sum(Decimal(a["quantity"]) for r in allocations for a in r["allocations"]), 6)
        first = await self.consume(units={"line": [1]})
        self.assertEqual(await self.stock(), 8)
        self.assertEqual(len(first["component_provenance"]["units"]), 1)
        again = await self.consume(units={"line": [1]})
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["component_provenance"], again["component_provenance"])
        await self.consume(units={"line": [2, 3]})
        self.assertEqual(await self.stock(), 4)
        self.assertTrue((await self.consume())["duplicate"])
        self.assertEqual((await self.occupancy())["items"][1]["quantity"], 4)
        self.assertEqual((await self.occupancy())["total_quantity"], 8)
        self.assertEqual(plan["plan_id"], (await self.reserve())["plan_id"])

    async def test_recipe_is_frozen_and_changed_payload_fails(self):
        prior = await self.reserve()
        await self.db[service.PRODUCT_BINDINGS].update_one({"id": "b"}, {"$set": {"quantity": 99}})
        replay = await self.reserve(source_version=2)
        self.assertEqual(prior["units"], replay["units"])
        await self.consume()
        self.assertEqual(await self.stock(), 4)
        await self.assertCode("plan_conflict", self.reserve(qty=2, source_version=3))

    async def test_option_quantities_and_services_are_excluded(self):
        await self.db[service.RESOURCES].insert_one({"user_id": "owner", "id": "service", "kind": "service", "track_inventory": True})
        await self.db[service.PRODUCT_BINDINGS].insert_one({"id": "service-binding", "user_id": "owner", "salla_product_id": "p", "resource_id": "service", "quantity": 99})
        await self.db[service.OPTION_BINDINGS].insert_many([
            {"id": "red", "user_id": "owner", "salla_product_id": "p", "resource_id": "r", "quantity": 1, "mode": "resource", "option_id": "c", "value_id": "red"},
            {"id": "blue", "user_id": "owner", "salla_product_id": "p", "resource_id": "r", "quantity": 99, "mode": "resource", "option_id": "c", "value_id": "blue"}])
        await self.reserve(qty=2, lines=[self.line(2, options_raw=[{"option_id": "c", "value_id": "red"}])])
        await self.consume()
        self.assertEqual(await self.stock(), 4)
        self.assertEqual(await self.db[service.UNITS].count_documents({"resource_demands.resource_id": "service"}), 0)

    async def test_zero_demand_plan_does_not_require_physical_stock(self):
        await self.db[service.RESOURCES].update_one({"id": "r"}, {"$set": {"kind": "service"}})
        await self.reserve(qty=2)
        result = await self.consume()
        self.assertEqual(await self.stock(), 10)
        self.assertTrue(all(not row["resource_demands"] for row in result["units"]))

    async def test_shortage_rolls_back_all_reservations(self):
        await self.assertCode("insufficient_stock", self.reserve(qty=6))
        self.assertEqual(await self.db[service.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[service.UNITS].count_documents({}), 0)
        self.assertEqual(await self.stock(), 10)

    async def test_parallel_reservations_never_overreserve(self):
        results = await asyncio.gather(self.reserve("one", 3), self.reserve("two", 3), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        error = next(result for result in results if isinstance(result, Exception))
        self.assertIsInstance(error, HTTPException)
        self.assertEqual(error.detail["code"], "component_insufficient_stock")
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "reserved"}), 3)

    async def test_cancel_tombstone_dominates_accept_race(self):
        await asyncio.gather(self.reserve(), self.cancel(version=1), return_exceptions=True)
        plan = await self.db[service.PLANS].find_one({})
        self.assertEqual(plan["state"], "cancelled")
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "reserved"}), 0)
        await self.assertCode("reaccept_review_required", self.reserve(source_version=0))
        await self.assertCode("reaccept_review_required", self.reserve(source_version=2))
        self.assertEqual(await self.stock(), 10)

    async def test_cancel_before_accept_and_stale_cancel(self):
        await self.cancel("cancelled", version=2)
        await self.assertCode("reaccept_review_required", self.reserve("cancelled", source_version=1))
        await self.reserve(source_version=3)
        old = await self.cancel(version=2)
        self.assertTrue(old["stale_event"])
        self.assertEqual(old["state"], "accepted")
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "reserved"}), 3)

    async def test_cancel_consumed_records_reconciliation_without_restock(self):
        await self.reserve()
        await self.consume(units={"line": [1]})
        result = await self.cancel()
        self.assertTrue(result["reconciliation_required"])
        self.assertEqual(await self.stock(), 8)
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "released"}), 2)
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "consumed"}), 1)
        await self.assertCode("order_cancelled", self.consume())

    async def test_owner_isolation_and_missing_reservation(self):
        await self.db[service.RESOURCES].update_one({"id": "r"}, {"$set": {"user_id": "other"}})
        await self.assertCode("resource_missing", self.reserve())
        self.assertEqual(await self.db[service.PLANS].count_documents({}), 0)
        await self.assertCode("reservation_missing", self.consume())
        await self.db[service.RESOURCES].update_one({"id": "r"}, {"$set": {"user_id": "owner"}})
        await self.db[service.LOCATIONS].update_one({"id": "loc"}, {"$set": {"user_id": "other"}})
        await self.assertCode("insufficient_stock", self.reserve())

    async def test_outer_workflow_failure_rolls_back_real_transaction(self):
        await self.reserve()
        async def operation(scoped):
            await service.consume_component_stock(scoped, merchant_id="owner", order_id="order")
            await scoped["synthetic_workflow"].insert_one({"state": "ready"})
            raise RuntimeError("synthetic interruption after stock and workflow writes")
        with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            await atomic_owner(self.db, "owner", operation)
        self.assertEqual(await self.stock(), 10)
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "consumed"}), 0)
        self.assertEqual(await self.db.synthetic_workflow.count_documents({}), 0)
        await self.consume()
        self.assertEqual(await self.stock(), 4)

    async def test_process_death_before_commit_leaves_no_partial_stock(self):
        await self.reserve()
        self.assertEqual(await self.crash_worker("before_commit"), 77)
        self.assertEqual(await self.stock(), 10)
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "consumed"}), 0)
        # Recovery uses Mongo's dead-session expiry, never deletes an owner lock.
        await asyncio.wait_for(self.consume(), 150)
        self.assertEqual(await self.stock(), 4)
        self.assertEqual(await self.db[service.UNITS].count_documents({"state": "consumed"}), 3)

    async def test_committed_response_lost_replays_same_consumption(self):
        plan = await self.reserve()
        self.assertEqual(await self.crash_worker("after_commit"), 78)
        replay = await self.consume()
        self.assertTrue(replay["duplicate"])
        self.assertEqual([r["consumption_id"] for r in plan["units"]], [r["consumption_id"] for r in replay["units"]])
        self.assertEqual(await self.stock(), 4)

    async def test_cutoff_no_historical_backfill_and_existing_replay(self):
        await self.db.settings.delete_many({})
        await self.assertCode("configuration_required", self.reserve())
        await self.db.settings.insert_one({"user_id": "owner", "g47_inventory": {"component_lifecycle_starts_at": CUTOFF}})
        await self.assertCode("historical_backfill_forbidden", self.reserve(source_created_at="2039-12-31T23:59:59Z"))
        await self.assertCode("source_created_at_required", self.reserve(source_created_at=None))
        await self.reserve()
        await self.db.settings.delete_many({})
        self.assertTrue((await self.reserve(source_created_at=None))["duplicate"])
        await self.consume()

    async def test_fractional_component_quantity_never_negative(self):
        await self.db[service.PRODUCT_BINDINGS].update_one({"id": "b"}, {"$set": {"quantity": "0.000001"}})
        await self.db[service.LOCATIONS].update_one({"id": "loc"}, {"$set": {"occupancy.items.0.quantity": 0.000003, "occupancy.total_quantity": 4.000003}})
        await self.reserve()
        await self.consume()
        self.assertEqual(await self.stock(), 0)
        self.assertEqual((await self.occupancy())["total_quantity"], 4)

    async def test_outside_stock_change_cannot_consume_another_orders_reserve(self):
        await self.reserve("one", 2)
        await self.reserve("two", 2)
        await self.db[service.LOCATIONS].update_one({"id": "loc"}, {"$set": {"occupancy.items.0.quantity": 6, "occupancy.total_quantity": 10}})
        await self.assertCode("reservation_exceeds_stock", self.consume("one"))
        self.assertEqual(await self.stock(), 6)

    async def test_prebuilt_proof_single_use_release_and_no_second_deduction(self):
        source_order = "stock-preparation:manufacture"
        await self.reserve(source_order, 2)
        produced = await self.consume(source_order, units={"line": [1, 2]})
        proof = produced["component_provenance"]
        self.assertEqual(await self.stock(), 6)
        await self.db[service.RECEIPTS].insert_one({"user_id": "owner", "id": "finished-receipt", "status": "posted",
            "source_type": "stock_preparation_order", "source_id": "manufacture", "source_line_id": "line", "quantity": 2,
            "component_provenance": proof})
        line = self.line(2, prebuilt_receipts=[{"receipt_id": "finished-receipt", "quantity": 2}])
        # Later recipe changes do not charge a manufactured item again.
        await self.db[service.PRODUCT_BINDINGS].update_one({"id": "b"}, {"$set": {"quantity": 50}})
        await self.reserve("customer-one", lines=[line])
        await self.assertCode("provenance_already_claimed", self.reserve("customer-two", lines=[line]))
        await self.cancel("customer-one")
        accepted = await self.reserve("customer-two", lines=[line])
        self.assertTrue(all(row["prebuilt"] for row in accepted["units"]))
        await self.consume("customer-two")
        self.assertEqual(await self.stock(), 6)
        await self.cancel("customer-two")
        self.assertEqual(await self.db[service.CLAIMS].count_documents({}), 2)
        await self.assertCode("provenance_already_claimed", self.reserve("customer-three", lines=[line]))


if __name__ == "__main__":
    unittest.main()
