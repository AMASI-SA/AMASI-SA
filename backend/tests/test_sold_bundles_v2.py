"""Frozen bundle costs remain one sold parent line; no financial writes."""
import os
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
from sold_products_report_v2 import aggregate_sold_products, load_sold_products
from stock_component_consumption_service import PLANS, UNITS, PRODUCTS


def calculate(actual=None, expected="30", quantity=2, line="line"):
    return aggregate_sold_products(
        [{"order_number": "order", "products": [{"product_id": "bundle", "order_item_id": line, "quantity": quantity}]}],
        [{"salla_product_id": "bundle", "cost_price": 999}], [],
        {("order", "line"): {"quantity": 2, "actual_cost": actual, "expected_cost": expected},
         ("order", "product:bundle"): None})[0]


def test_frozen_expected_then_actual_replaces_parent_cost_without_extra_units():
    expected = calculate()
    assert expected["total_cost"] == 30
    assert expected["units_sold"] == 2
    assert expected["unit_cost"] == 15
    assert expected["cost_source"] == "bundle_expected"
    actual = calculate(actual="22")
    assert actual["total_cost"] == 22
    assert actual["unit_cost"] == 11
    assert actual["cost_source"] == "bundle_actual"
    assert "revenue" not in actual and "profit" not in actual


def test_unknown_or_unmatched_bundle_never_falls_back_to_parent_cost():
    for row in [calculate(expected=None), calculate(quantity=3), calculate(line="missing")]:
        assert row["total_cost"] is None
        assert row["cost_source"] == "missing"


def test_multiple_bundle_sales_use_weighted_frozen_unit_cost():
    orders = [{"order_number": number, "products": [{"product_id": "bundle", "order_item_id": "line", "quantity": qty}]}
              for number, qty in [("one", 1), ("two", 2)]]
    costs = {("one", "line"): {"quantity": 1, "actual_cost": "10"},
             ("two", "line"): {"quantity": 2, "actual_cost": "50"}}
    row = aggregate_sold_products(orders, [{"salla_product_id": "bundle", "cost_price": 999}], [], costs)[0]
    assert row["units_sold"] == 3
    assert row["total_cost"] == 60
    assert row["unit_cost"] == 20


@unittest.skipUnless(os.environ.get("MZ2_TEST_MONGO_URI"), "requires loopback fixture")
class SoldBundleMongoTests(unittest.IsolatedAsyncioTestCase):
    async def test_loader_reads_existing_frozen_authority_without_mutation(self):
        uri = os.environ["MZ2_TEST_MONGO_URI"]
        self.assertIn(urlsplit(uri).hostname, {"localhost", "127.0.0.1", "::1"})
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = client["mz2_sold_bundle_test_" + uuid4().hex]
        try:
            await db[PRODUCTS].insert_one({"user_id": "owner", "salla_product_id": "bundle", "cost_price": 999})
            plan = {"_id": "plan", "user_id": "owner", "order_id": "order", "state": "accepted", "bundle_snapshots": {
                "line": {"product_id": "bundle", "revision": 1, "estimated_cost": "15"}}}
            await db[PLANS].insert_one(plan)
            for index in [1, 2]:
                await db[UNITS].insert_one({"_id": str(index), "user_id": "owner", "plan_id": "plan",
                    "order_line_id": "line", "unit_index": index, "state": "consumed", "bundle_leaves": [
                        {"state": "consumed", "actual_cost": "11", "required": True}]})
            orders = [{"order_number": "order", "products": [{"product_id": "bundle", "order_item_id": "line", "quantity": 2}]}]
            rows = await load_sold_products(db, "owner", orders)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["total_cost"], 22)
            self.assertEqual(rows[0]["units_sold"], 2)
            self.assertEqual(await db[PLANS].find_one({"_id": "plan"}), plan)
            other = await load_sold_products(db, "other", orders)
            self.assertIsNone(other[0]["total_cost"])
            self.assertEqual(await db[UNITS].count_documents({}), 2)
            # Pending payment intake has a frozen recipe, but no stock plan.
            await db["mezan_component_order_lifecycle_v1"].insert_one({"user_id": "owner",
                "order_number": "pending", "bundle_intake_snapshots": {"pending-line": {
                    "product_id": "bundle", "estimated_cost": "13"}}})
            pending_orders = [{"order_number": "pending", "products": [{"product_id": "bundle",
                "order_item_id": "pending-line", "quantity": 3}]}]
            pending = await load_sold_products(db, "owner", pending_orders)
            self.assertEqual(pending[0]["total_cost"], 39)
            self.assertEqual(pending[0]["cost_source"], "bundle_expected")
            self.assertEqual(await db[PLANS].count_documents({"order_id": "pending"}), 0)
            # A current bundle without any sale snapshot must not use parent999.
            from bundle_catalog_v2 import BUNDLES
            await db[BUNDLES].insert_one({"user_id": "owner", "product_id": "bundle", "product_type": "composite"})
            unknown = await load_sold_products(db, "owner", [{"order_number": "missing", "products": [
                {"product_id": "bundle", "order_item_id": "unknown", "quantity": 1}]}])
            self.assertIsNone(unknown[0]["total_cost"])
            self.assertEqual(unknown[0]["cost_source"], "missing")
        finally:
            self.assertTrue(db.name.startswith("mz2_sold_bundle_test_"))
            await client.drop_database(db.name)
            client.close()
