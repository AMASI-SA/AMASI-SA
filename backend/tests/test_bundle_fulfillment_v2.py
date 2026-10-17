"""Real Mongo bundle lifecycle checks; loopback UUID fixtures only."""
import asyncio
import os
import unittest
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

import bundle_catalog_v2 as catalog
import bundle_fulfillment_v2 as bundle
import stock_component_consumption_service as stock
from purchase_receiving_service import COST_POLICY, COST_STATES, identity_key
from supplier_identity_service import SUPPLIERS_V2


class AutomaticAllocationTests(unittest.IsolatedAsyncioTestCase):
    def leaf(self, **updates):
        return {"leaf_id": "leaf", "kind": "product", "product_id": "a", "quantity": "4",
                "sourcing_mode": "auto", "unit_cost": "10", "sourcing_policy": {
                    "revision": 1, "supplier_id": "supplier-a", "allow_split": True}, **updates}

    def lots(self, quantity=2, receipt="stock"):
        return [{"location_id": "loc", "lot_id": receipt, "available": Decimal(quantity),
                 "item": {"item_type": "product", "product_id": "a", "salla_product_id": "a"}}]

    async def allocate(self, leaf, lots, previous=None):
        with patch("supplier_identity_service.require_supplier_v2", new=AsyncMock(return_value={"id": "supplier-a"})):
            return await bundle.allocate_leaves(None, "owner", [leaf], lots, unit_id="u", previous=previous)

    async def test_stock_two_supplier_two_and_receipt_retry(self):
        leaves, allocations = await self.allocate(self.leaf(), self.lots())
        self.assertEqual(leaves[0]["reserved_quantity"], "2")
        self.assertEqual(leaves[0]["supplier_quantity"], "2")
        self.assertEqual(leaves[0]["state"], "awaiting_supplier")
        old = {**leaves[0], "receipt_ids": ["purchase"]}
        leaves, allocations = await self.allocate(self.leaf(), self.lots() + self.lots(2, "purchase"), [old])
        self.assertEqual(leaves[0]["reserved_quantity"], "4")
        self.assertEqual(leaves[0]["received_quantity"], "2")
        self.assertEqual(len({a["id"] for a in allocations}), 2)
        again, replay = await self.allocate(self.leaf(), self.lots() + self.lots(2, "purchase"), leaves)
        self.assertEqual(again, leaves)
        self.assertEqual(replay, allocations)
        totals = bundle.report_totals([{"state": "reserved", "order_number": "o", "order_line_id": "l",
            "product_id": "root", "leaves": [{**leaves[0], "unit_state": "reserved", "unit_index": 1}]}])
        self.assertEqual(totals["supplier_requirements"], [])

    async def test_no_policy_keeps_stock_and_unresolved_shortage(self):
        leaves, _ = await self.allocate(self.leaf(sourcing_policy={}), self.lots())
        self.assertEqual(leaves[0]["state"], "needs_source")
        self.assertEqual(leaves[0]["unresolved_quantity"], "2")
        self.assertIsNone(leaves[0]["supplier_id"])

    async def test_full_stock_needs_no_supplier_and_wrong_variant_never_allocates(self):
        leaves, _ = await self.allocate(self.leaf(sourcing_policy={}), self.lots(4))
        self.assertEqual(leaves[0]["state"], "reserved")
        leaves, allocations = await self.allocate(self.leaf(variant_id="different", sourcing_policy={}), self.lots(4))
        self.assertEqual(leaves[0]["unresolved_quantity"], "4")
        self.assertEqual(allocations, [])

    async def test_refresh_does_not_replace_committed_supplier_need_with_new_stock(self):
        leaves, _ = await self.allocate(self.leaf(), self.lots())
        leaves, allocations = await self.allocate(self.leaf(), self.lots(20), leaves)
        self.assertEqual(leaves[0]["supplier_quantity"], "2")
        self.assertEqual(leaves[0]["reserved_quantity"], "2")
        self.assertEqual(sum(Decimal(a["quantity"]) for a in allocations), 2)

    async def test_changed_customization_reselects_supplier_without_old_receipt_claims(self):
        policy = {"rules": [
            {"rule_id": "ali", "customization": {"gift": "Ali"}, "supplier_id": "supplier-a"},
            {"rule_id": "sara", "customization": {"gift": "Sara"}, "supplier_id": "supplier-b"}]}
        leaves, _ = await self.allocate(self.leaf(customization={"gift": "Ali"}, sourcing_policy=policy), [])
        leaves[0]["receipt_ids"] = ["old-gift-receipt"]
        changed, _ = await self.allocate(self.leaf(customization={"gift": "Sara"}, sourcing_policy=policy), [], leaves)
        self.assertEqual(changed[0]["supplier_id"], "supplier-b")
        self.assertEqual(changed[0]["receipt_ids"], [])

    async def test_no_split_routes_whole_shortage_to_approved_supplier(self):
        leaves, allocations = await self.allocate(self.leaf(sourcing_policy={"supplier_id": "supplier-a", "allow_split": False}), self.lots())
        self.assertEqual(leaves[0]["supplier_quantity"], "4")
        self.assertEqual(allocations, [])

    async def test_rules_match_variant_and_exact_customization_and_conflicts(self):
        from bundle_sourcing_v2 import resolve_policy
        rule = {"rule_id": "r", "variant_id": "v", "customization": {"gift": "Ali"},
                "conditions": [{"option_id": "color", "value_id": "blue"}], "supplier_id": "supplier-b"}
        leaf = self.leaf(variant_id="v", customization={"gift": "Ali"}, selected_options=[{"option_id": "color", "value_id": "blue"}], sourcing_policy={"rules": [rule]})
        self.assertEqual(resolve_policy(leaf)["supplier_id"], "supplier-b")
        self.assertIsNone(resolve_policy({**leaf, "customization": {"gift": "ALI"}})["supplier_id"])
        leaf["sourcing_policy"]["rules"].append({**rule, "rule_id": "r2"})
        self.assertEqual(resolve_policy(leaf)["reason"], "conflicting_sourcing_rules")


@unittest.skipUnless(os.environ.get("MZ2_TEST_MONGO_URI"), "requires isolated loopback replica set")
class BundleMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ["MZ2_TEST_MONGO_URI"]
        self.assertIn(urlsplit(uri).hostname, {"127.0.0.1", "localhost", "::1"})
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        self.db = self.client["mz2_bundle_test_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        await self.db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0,
            "writes_paused": False, "control_revision": 0})
        await self.db.settings.insert_one({"user_id": "owner", "g47_inventory": {
            "component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
        await stock.ensure_component_consumption_indexes(self.db)
        for pid, cost in [("root", 999), ("nested", 999), ("a", 10), ("b", 20), ("c", 30), ("d", 40)]:
            await self.db[stock.PRODUCTS].insert_one({"user_id": "owner", "id": pid,
                "mezan_product_id": pid, "salla_product_id": pid, "name": pid, "cost_price": cost})
        for sid in ["supplier-a", "supplier-b"]:
            await self.db[SUPPLIERS_V2].insert_one({"user_id": "owner", "id": sid, "status": "active"})
        await self.db.users.insert_one({"id": "owner", "role": "owner", "is_active": True})
        await self.db[stock.LOCATIONS].insert_one({"user_id": "owner", "id": "loc",
            "warehouse_id": "warehouse", "state": "occupied", "occupancy": {"items": [], "total_quantity": 0}})

    async def asyncTearDown(self):
        self.assertTrue(self.db.name.startswith("mz2_bundle_test_"))
        await self.client.drop_database(self.db.name)
        self.client.close()

    def item(self, pid, mode="stock", supplier=None, qty=1):
        return {"kind": "product", "product_id": pid, "quantity": qty,
            "sourcing_mode": mode, **({"supplier_id": supplier} if supplier else {})}

    async def define(self, items, pid="root", revision=0, **extra):
        return await catalog.save_bundle(self.db, "owner", pid, {
            "product_type": "composite", "expected_revision": revision, "items": items, **extra}, "owner")

    async def lot(self, pid, qty=1, supplier=None, owner="owner", receipt=None, variant=None, specifications=None):
        rid = receipt or uuid4().hex
        item = {"item_type": "product", "product_id": pid, "mezan_product_id": pid,
            "salla_product_id": pid, "receipt_id": rid, "quantity": qty,
            "configuration_key": "product-" + pid}
        if variant:
            item.update(variant_id=variant, salla_variant_id=variant)
        if specifications:
            item["specifications"] = specifications
            from product_inventory_rules import build_inventory_configuration_key
            item["preparation_state"] = "ready_complete"
            item["configuration_key"] = build_inventory_configuration_key(sku=pid,
                preparation_state="ready_complete", specifications=specifications)
        await self.db[stock.RECEIPTS].insert_one({**item, "id": rid, "user_id": owner,
            "location_id": "loc", "warehouse_id": "warehouse", "status": "posted",
            "source_type": "purchase_invoice", "source_id": "purchase-" + rid,
            "source_line_id": pid, "supplier_id": supplier, "unit_purchase_cost": "12"})
        await self.db.purchase_invoices.insert_one({"id": "purchase-" + rid,
            "user_id": owner, "supplier_counterparty_id": supplier, "status": "approved"})
        if owner == "owner":
            await self.db[stock.LOCATIONS].update_one({"user_id": owner, "id": "loc"}, {
                "$push": {"occupancy.items": item}, "$inc": {"occupancy.total_quantity": qty}})
            identity = {"item_type": "product", "product_id": pid, "variant_id": variant}
            await self.db[COST_STATES].update_one({"_id": identity_key(owner, identity)}, {"$set": {
                "user_id": owner, "authoritative": True, "cost_policy_version": COST_POLICY,
                "average_cost": "11"}}, upsert=True)
        return rid

    async def reserve(self, order="order", qty=1, version=1, options=None):
        return await stock.reserve_component_stock(self.db, merchant_id="owner", order_id=order,
            lines=[{"order_line_id": "line", "product_id": "root", "quantity": qty,
                "selected_options": options or []}],
            source_version=version, source_created_at="2026-10-01T00:00:00+00:00")

    async def view(self, order="order"):
        return (await bundle.order_view(self.db, "owner", order))["bundles"][0]

    async def consume(self, order="order"):
        return await stock.consume_component_stock(self.db, merchant_id="owner", order_id=order)

    async def attach(self, pid, receipt, order="order"):
        leaf = next(r for r in (await self.view(order))["leaves"] if r.get("product_id") == pid)
        return await bundle.refresh_requirements(self.db, "owner", order, actor_id="owner",
            receipt={"order_line_id": "line", "leaf_id": leaf["leaf_id"], "receipt_id": receipt})

    async def assertCode(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            await action
        self.assertEqual(caught.exception.detail["code"], code)

    async def test_four_products_two_suppliers_wait_then_consume_once(self):
        await self.define([self.item("a"), self.item("b"), self.item("c", "supplier", "supplier-a"),
            self.item("d", "supplier", "supplier-b")])
        await self.lot("a")
        await self.lot("b")
        await self.reserve()
        view = await self.view()
        self.assertEqual(len(view["leaves"]), 4)
        self.assertEqual(Decimal(view["expected_cost"]), 100)
        self.assertFalse(view["ready"])
        await self.assertCode("bundle_components_incomplete", self.consume())
        await self.attach("c", await self.lot("c", supplier="supplier-a"))
        self.assertFalse((await self.view())["ready"])
        await self.attach("d", await self.lot("d", supplier="supplier-b"))
        self.assertTrue((await self.view())["ready"])
        await self.consume()
        self.assertTrue((await self.consume())["duplicate"])
        self.assertEqual(Decimal((await self.view())["actual_cost"]), 46)
        location = await self.db[stock.LOCATIONS].find_one({"id": "loc"})
        self.assertEqual(location["occupancy"]["total_quantity"], 0)
        result = await stock.release_component_stock(self.db, merchant_id="owner", order_id="order", source_version=2)
        self.assertTrue(result["reconciliation_required"])
        self.assertEqual((await self.db[stock.LOCATIONS].find_one({"id": "loc"}))["occupancy"]["total_quantity"], 0)

    async def test_automatic_split_native_receipt_cost_and_replay(self):
        from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES
        await self.db[PRODUCT_OPERATION_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "a",
            "bundle_sourcing": {"revision": 1, "supplier_id": "supplier-a", "allow_split": True}})
        await self.define([self.item("a", "auto", qty=4)])
        await self.lot("a", qty=2)
        await self.reserve()
        leaf = (await self.view())["leaves"][0]
        self.assertEqual(leaf["supplier_quantity"], "2")
        self.assertEqual(leaf["reserved_quantity"], "2")
        totals = bundle.report_totals([{**await self.view(), "order_number": "order"}])
        self.assertEqual(totals["supplier_requirements"][0]["requirements"][0]["quantity"], "2")
        receipt = await self.lot("a", qty=2, supplier="supplier-a")
        await self.attach("a", receipt)
        await self.attach("a", receipt)
        self.assertTrue((await self.view())["ready"])
        await self.consume()
        self.assertTrue((await self.consume())["duplicate"])
        self.assertEqual(Decimal((await self.view())["actual_cost"]), Decimal(46))
        self.assertEqual((await self.db[stock.LOCATIONS].find_one({"id": "loc"}))["occupancy"]["total_quantity"], 0)

    async def test_wrong_tenant_or_supplier_receipt_cannot_satisfy_requirement(self):
        await self.define([self.item("c", "supplier", "supplier-a")])
        await self.reserve()
        await self.assertCode("bundle_purchase_receipt_identity_mismatch",
            self.attach("c", await self.lot("c", owner="other", supplier="supplier-a")))
        await self.assertCode("bundle_purchase_supplier_mismatch",
            self.attach("c", await self.lot("c", supplier="supplier-b")))
        self.assertFalse((await self.view())["ready"])

    async def test_competing_orders_never_reserve_last_unit_twice(self):
        await self.define([self.item("a")])
        await self.lot("a")
        await asyncio.gather(self.reserve("one"), self.reserve("two"))
        views = [await self.view("one"), await self.view("two")]
        self.assertEqual(sum(v["ready"] for v in views), 1)
        self.assertEqual(sum(Decimal(r["reserved_quantity"]) for v in views for r in v["leaves"]), 1)
        winner = "one" if views[0]["ready"] else "two"
        loser = "two" if winner == "one" else "one"
        await stock.release_component_stock(self.db, merchant_id="owner", order_id=winner, source_version=2)
        await stock.release_component_stock(self.db, merchant_id="owner", order_id=winner, source_version=2)
        await bundle.refresh_requirements(self.db, "owner", loser, actor_id="owner")
        self.assertTrue((await self.view(loser))["ready"])
        await self.consume(loser)

    async def test_nested_snapshot_cost_survives_catalog_changes_and_replay(self):
        await self.define([self.item("a", qty=2)], pid="nested")
        await self.define([self.item("nested", qty=3)])
        await self.lot("a", qty=6)
        await self.reserve()
        before = await self.view()
        self.assertEqual(Decimal(before["expected_cost"]), 60)
        self.assertEqual(sum(Decimal(r["quantity"]) for r in before["leaves"]), 6)
        await self.db[stock.PRODUCTS].update_one({"id": "a"}, {"$set": {"cost_price": 999}})
        await self.define([self.item("b")], pid="nested", revision=1)
        await self.reserve()
        after = await self.view()
        self.assertEqual(after["expected_cost"], before["expected_cost"])
        self.assertEqual(after["leaves"], before["leaves"])
        await self.consume()
        self.assertEqual(Decimal((await self.view())["actual_cost"]), 66)

    async def test_nonstock_service_needs_no_inventory(self):
        await self.db[stock.RESOURCES].insert_one({"user_id": "owner", "id": "svc",
            "name": "Service", "kind": "service", "track_inventory": False, "unit_cost": 5})
        await self.define([{"kind": "resource", "resource_id": "svc", "quantity": 2, "sourcing_mode": "stock"}])
        await self.reserve()
        self.assertTrue((await self.view())["ready"])
        await self.consume()
        self.assertEqual(Decimal((await self.view())["actual_cost"]), 10)

    async def test_source_override_cannot_bypass_nonstock_and_direct_cost_policy(self):
        await self.db[catalog.RESOURCES].insert_one({"user_id": "owner", "id": "internal",
            "name": "internal work", "status": "active", "track_inventory": False, "unit_cost": 20})
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {
            "options": [{"id": "o", "values": [{"id": "v"}]}]}})
        await self.db[catalog.BINDINGS].insert_one({"id": "direct", "user_id": "owner", "salla_product_id": "root",
            "option_id": "o", "value_id": "v", "mode": "direct", "direct_amount": 4, "quantity": 1})
        await self.define([{"kind": "resource", "resource_id": "internal", "quantity": 1}])
        await self.reserve(options=[{"option_id": "o", "value_id": "v"}])
        before = await self.view()
        for leaf in before["leaves"]:
            expected = "bundle_cost_only_source_change_forbidden" if leaf.get("cost_only") else "bundle_nonstock_supplier_requires_service_receipt"
            await self.assertCode(expected, bundle.refresh_requirements(self.db, "owner", "order", actor_id="owner", source_change={
                "order_line_id": "line", "leaf_id": leaf["leaf_id"], "sourcing_mode": "supplier", "supplier_id": "supplier-a"}))
            self.assertEqual(await self.view(), before)

    async def test_receipt_api_resolves_invoice_supplier_and_fresh_actor(self):
        await self.define([self.item("c", "supplier", "supplier-a")])
        await self.reserve()
        rid = await self.lot("c", supplier="supplier-a")
        await self.db[stock.RECEIPTS].update_one({"id": rid}, {"$unset": {"supplier_id": ""}})
        leaf = (await self.view())["leaves"][0]
        actor = {"id": "owner"}

        async def current_user():
            return actor

        app = FastAPI()
        app.include_router(bundle.make_bundle_operations_router(self.db, current_user))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"order_line_id": "line", "leaf_id": leaf["leaf_id"], "receipt_id": rid}
            response = await client.post("/fulfillment-v2/orders/order/bundles/receipts", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["bundles"][0]["ready"])
            before = await self.db[stock.UNITS].find_one({"order_id": "order"})
            await self.db.users.insert_one({"id": "employee", "role": "employee", "created_by": "owner"})
            actor["id"] = "employee"
            denied = await client.post("/fulfillment-v2/orders/order/bundles/receipts", json=payload)
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertEqual(await self.db[stock.UNITS].find_one({"order_id": "order"}), before)
            actor["id"] = "owner"
            await self.db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
            denied = await client.get("/fulfillment-v2/orders/order/bundles")
            self.assertEqual(denied.status_code, 403, denied.text)

    async def test_quantity_amendment_replay_and_consumed_change_is_atomic(self):
        await self.define([self.item("a")])
        await self.lot("a", qty=3)
        await self.reserve()
        await self.reserve(qty=2, version=2)
        self.assertEqual((await self.view())["quantity"], 2)
        await self.reserve(qty=2, version=2)
        plan = await self.db[stock.PLANS].find_one({"order_id": "order"})
        self.assertEqual(len(plan["bundle_amendments"]), 1)
        self.assertEqual(await self.db[stock.UNITS].count_documents({"state": "reserved"}), 2)
        await self.consume()
        before = await self.db[stock.LOCATIONS].find_one({"id": "loc"})
        plan = await self.db[stock.PLANS].find_one({"order_id": "order"})
        await self.assertCode("bundle_consumed_amendment_requires_reconciliation",
            self.reserve(qty=3, version=3))
        self.assertEqual(await self.db[stock.LOCATIONS].find_one({"id": "loc"}), before)
        self.assertEqual(await self.db[stock.PLANS].find_one({"order_id": "order"}), plan)

    async def test_option_amendment_uses_original_alternative_and_cost_snapshot(self):
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"options": [
            {"id": "size", "type": "select", "required": True,
             "values": [{"id": "two", "name": "Two"}, {"id": "four", "name": "Four"}]}]}})
        items = [self.item("a"), self.item("b"),
            {**self.item("c"), "condition": {"option_id": "size", "value_id": "four"}},
            {**self.item("d"), "condition": {"option_id": "size", "value_id": "four"}}]
        await self.define(items)
        for pid in "abcd":
            await self.lot(pid)
        await self.reserve(options=[{"option_id": "size", "value_id": "two"}])
        self.assertEqual(len((await self.view())["leaves"]), 2)
        await self.define([self.item("a", qty=20)], revision=1)
        await self.db[stock.PRODUCTS].update_one({"id": "d"}, {"$set": {"cost_price": 500}})
        four = [{"option_id": "size", "value_id": "four"}]
        await self.reserve(options=four, version=2)
        view = await self.view()
        self.assertEqual(len(view["leaves"]), 4)
        self.assertEqual(Decimal(view["expected_cost"]), 100)
        self.assertTrue(view["ready"])
        await self.reserve(options=four, version=2)
        self.assertEqual((await self.view())["leaves"], view["leaves"])

    async def test_partial_supplier_receipt_replay_does_not_double_allocate(self):
        await self.define([self.item("c", "supplier", "supplier-a", qty=2)])
        await self.reserve()
        first = await self.lot("c", supplier="supplier-a")
        await self.attach("c", first)
        await self.attach("c", first)
        view = await self.view()
        self.assertFalse(view["ready"])
        self.assertEqual(Decimal(view["leaves"][0]["reserved_quantity"]), 1)
        await self.assertCode("bundle_components_incomplete", self.consume())
        await self.attach("c", await self.lot("c", supplier="supplier-a"))
        self.assertTrue((await self.view())["ready"])
        await self.consume()
        self.assertEqual(Decimal((await self.view())["actual_cost"]), 24)

    async def test_source_override_releases_stock_and_survives_quantity_amendment(self):
        await self.define([self.item("a")])
        await self.lot("a", qty=2)
        await self.reserve()
        leaf = (await self.view())["leaves"][0]
        await bundle.refresh_requirements(self.db, "owner", "order", actor_id="owner", source_change={
            "order_line_id": "line", "leaf_id": leaf["leaf_id"],
            "sourcing_mode": "supplier", "supplier_id": "supplier-a"})
        current = (await self.view())["leaves"][0]
        self.assertEqual(current["sourcing_mode"], "supplier")
        self.assertEqual(Decimal(current["reserved_quantity"]), 0)
        await self.reserve(order="other")
        self.assertTrue((await self.view("other"))["ready"])
        await self.reserve(qty=2, version=2)
        leaves = (await self.view())["leaves"]
        self.assertEqual({r["sourcing_mode"] for r in leaves}, {"supplier"})
        self.assertEqual({r["supplier_id"] for r in leaves}, {"supplier-a"})

    async def test_operational_view_hides_costs_without_cost_permission(self):
        from ai_store_access_contract import ROLE_ASSIGNMENTS, ROLE_ASSIGNMENT_OWNER_FIELD
        await self.define([self.item("a", "auto")])
        await self.lot("a")
        await self.reserve()
        await self.db.users.insert_one({"id": "viewer", "role": "employee", "created_by": "owner"})
        await self.db[ROLE_ASSIGNMENTS].insert_one({ROLE_ASSIGNMENT_OWNER_FIELD: "owner", "user_id": "viewer",
            "enabled": True, "extra_permissions": ["fulfillment.ready.read"]})

        async def current_user():
            return {"id": "viewer"}

        app = FastAPI()
        app.include_router(bundle.make_bundle_operations_router(self.db, current_user))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/fulfillment-v2/orders/order/bundles")
            self.assertEqual(response.status_code, 200, response.text)
            view = response.json()["bundles"][0]
            self.assertNotIn("actual_cost", view)
            self.assertNotIn("expected_cost", view)
            self.assertNotIn("unit_cost", view["leaves"][0])
            self.assertTrue(view["leaves"][0]["sourcing_segments"])
            for segment in view["leaves"][0]["sourcing_segments"]:
                self.assertNotIn("unit_cost", segment)
                self.assertNotIn("actual_cost", segment)
            self.assertEqual((await client.get("/fulfillment-v2/bundles/report")).status_code, 403)

    async def test_failure_after_allocation_rolls_back_and_retry_reserves_once(self):
        await self.define([self.item("a")])
        await self.lot("a")
        before = await self.db[stock.LOCATIONS].find_one({"id": "loc"})

        async def fail_after_writes(*args, **kwargs):
            raise RuntimeError("synthetic transaction boundary failure")

        with patch.object(stock, "_public", fail_after_writes):
            with self.assertRaisesRegex(RuntimeError, "synthetic transaction"):
                await self.reserve()
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.LOCATIONS].find_one({"id": "loc"}), before)
        await self.reserve()
        self.assertEqual(await self.db[stock.UNITS].count_documents({"state": "reserved"}), 1)
        self.assertTrue((await self.view())["ready"])

    async def test_source_change_history_limit_accounts_for_every_unit(self):
        await self.define([self.item("a")])
        await self.lot("a", qty=2)
        await self.reserve(qty=2)
        await self.db[stock.PLANS].update_one({"order_id": "order"}, {"$set": {
            "bundle_source_changes": [{"synthetic_previous_change": n} for n in range(99)]}})
        before = await self.db[stock.UNITS].find({"order_id": "order"}).sort("unit_index", 1).to_list(10)
        leaf = (await self.view())["leaves"][0]
        await self.assertCode("bundle_source_change_history_limit",
            bundle.refresh_requirements(self.db, "owner", "order", actor_id="owner", source_change={
                "order_line_id": "line", "leaf_id": leaf["leaf_id"],
                "sourcing_mode": "supplier", "supplier_id": "supplier-a"}))
        self.assertEqual(await self.db[stock.UNITS].find({"order_id": "order"}).sort("unit_index", 1).to_list(10), before)

    def order_dto(self, payment_status="pending"):
        from order_engine.models import OrderDTO, OrderItemDTO, OrderSourceDTO, PaymentDTO
        return OrderDTO(order_id="order", order_number="order", created_at=datetime.fromisoformat("2026-10-01T00:00:00+00:00"),
            source=OrderSourceDTO(source_order_id="order"), status="under_review",
            payment=PaymentDTO(method="credit_card", status=payment_status),
            items=[OrderItemDTO(order_item_id="line", product_id="root", name="Bundle", quantity=1)])

    async def test_intake_snapshot_freezes_before_reservation(self):
        await self.define([self.item("a")])
        snapshots = await bundle.capture_intake(self.db, "owner", [
            {"order_line_id": "line", "product_id": "root", "quantity": 1}], {})
        await self.db["mezan_component_order_lifecycle_v1"].insert_one({"user_id": "owner",
            "order_number": "order", "bundle_intake_snapshots": snapshots})
        await self.define([self.item("b")], revision=1)
        await self.db[stock.PRODUCTS].update_one({"id": "a"}, {"$set": {"cost_price": 500}})
        await self.lot("a")
        await self.reserve()
        view = await self.view()
        self.assertEqual(view["leaves"][0]["product_id"], "a")
        self.assertEqual(Decimal(view["expected_cost"]), 10)

    async def test_pending_payment_lifecycle_captures_intake_before_acceptance(self):
        from fulfillment_v2_routes import reconcile_component_order_lifecycle
        await self.define([self.item("a")])
        order = self.order_dto()
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "order",
            "order_date": order.created_at.isoformat(), "order_status_slug": order.status,
            "raw_by_source": {"salla_direct": {"date": order.created_at.isoformat(),
                "id": "order", "reference_id": "order", "status": order.status}}})
        result = await reconcile_component_order_lifecycle(self.db, user_id="owner", order=order,
            source_updated_at="2026-10-01T00:01:00+00:00", decision={})
        self.assertEqual(result["state"], "awaiting_eligibility", result)
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        lifecycle = await self.db["mezan_component_order_lifecycle_v1"].find_one({"order_number": "order"})
        self.assertIn("line", lifecycle["bundle_intake_snapshots"])
        await self.define([self.item("b")], revision=1)
        await self.lot("a")
        paid = self.order_dto("paid")
        result = await reconcile_component_order_lifecycle(self.db, user_id="owner", order=paid,
            source_updated_at="2026-10-01T00:02:00+00:00", decision={})
        self.assertTrue(result["accepted"], result)
        self.assertEqual((await self.view())["leaves"][0]["product_id"], "a")

    async def test_order_cost_snapshot_replaces_parent_cost_and_tracks_actual(self):
        from order_option_cost_snapshot_routes import calculate_order_cost_snapshot
        await self.define([self.item("a"), self.item("b")])
        await self.lot("a")
        await self.lot("b")
        await self.reserve()
        expected = await calculate_order_cost_snapshot(self.db, user_id="owner", order=self.order_dto("paid"))
        self.assertEqual(len(expected["items"]), 1)
        self.assertEqual(expected["total_cost"], 30)
        self.assertEqual(expected["items"][0]["cost_stage"], "expected")
        await self.consume()
        actual = await calculate_order_cost_snapshot(self.db, user_id="owner", order=self.order_dto("paid"))
        self.assertEqual(actual["total_cost"], 22)
        self.assertEqual(actual["items"][0]["cost_stage"], "actual")
        self.assertEqual(actual["items"][0]["base_unit_cost"], 0)

    async def test_actual_stock_and_supplier_cost_retain_direct_option_work(self):
        for pid in ("a", "b"):
            await self.db[stock.PRODUCTS].update_one({"id": pid, "user_id": "owner"}, {"$set": {
                "options": [{"id": "o", "values": [{"id": "v"}]}]}})
            await self.db[catalog.BINDINGS].insert_one({"user_id": "owner", "salla_product_id": pid,
                "option_id": "o", "value_id": "v", "mode": "direct", "direct_amount": 4, "quantity": 2})
        choice = {"selected_options": [{"option_id": "o", "value_id": "v"}]}
        await self.define([{**self.item("a"), **choice}, {**self.item("b", "supplier", "supplier-a"), **choice}])
        await self.lot("a")
        receipt = await self.lot("b", supplier="supplier-a")
        await self.reserve()
        self.assertEqual(Decimal((await self.view())["expected_cost"]), Decimal(46))
        await self.attach("b", receipt)
        await self.consume()
        self.assertEqual(Decimal((await self.view())["actual_cost"]), Decimal(39))

    async def test_report_groups_supplier_needs_without_extra_sales_lines(self):
        await self.define([self.item("a", "supplier", "supplier-a"),
            self.item("b", "supplier", "supplier-a"), self.item("c", "supplier", "supplier-b")])
        await self.reserve(qty=2)

        async def current_user():
            return {"id": "owner"}

        app = FastAPI()
        app.include_router(bundle.make_bundle_operations_router(self.db, current_user))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/fulfillment-v2/bundles/report")
            self.assertEqual(response.status_code, 200, response.text)
            report = response.json()
            self.assertEqual(report["sales_count"], 2)
            grouped = {r["supplier_id"]: r["requirements"] for r in report["supplier_requirements"]}
            self.assertEqual(set(grouped), {"supplier-a", "supplier-b"})
            self.assertEqual(sum(Decimal(r["quantity"]) for r in grouped["supplier-a"]), 4)
            self.assertEqual(sum(Decimal(r["quantity"]) for r in grouped["supplier-b"]), 2)
            self.assertEqual({r["order_number"] for r in grouped["supplier-a"]}, {"order"})

    async def test_verified_salla_webhook_replay_keeps_single_bundle_unit(self):
        import salla_integration.webhook_order_sync as webhook
        await self.define([self.item("a"), self.item("c", "supplier", "supplier-a")])
        await self.lot("a")
        when = "2026-10-01T00:00:00+00:00"
        payload = {"id": "order", "reference_id": "order", "date": when, "updated_at": when,
            "status": {"slug": "under_review", "name": "under_review"}, "payment_method": "cod",
            "amounts": {"total": {"amount": 100, "currency": "SAR"}},
            "shipping_address": {"city": "Synthetic city", "street": "Synthetic street"},
            "items": [{"id": "line", "product_id": "root", "name": "Synthetic bundle", "quantity": 1}]}
        with patch.object(webhook, "_resolve_user_id", AsyncMock(return_value="owner")), \
             patch("mezan_attribution_ledger_sync.safe_sync_order_to_attribution_ledger", AsyncMock(return_value={"synced": False})), \
             patch("first_party_attribution.link_order_attribution", AsyncMock(return_value={"linked": False})):
            for _ in range(2):
                result = await webhook.sync_order_from_verified_webhook(self.db, {
                    "event": "order.created", "merchant": "synthetic-store", "data": payload})
                self.assertTrue(result["synced"], result)
        self.assertEqual(await self.db[stock.PLANS].count_documents({"order_id": "order"}), 1)
        self.assertEqual(await self.db[stock.UNITS].count_documents({"order_id": "order"}), 1)
        view = await self.view()
        needs = bundle.report_totals([{**view, "order_number": "order"}])["supplier_requirements"]
        self.assertEqual(len(needs), 1)
        self.assertEqual(sum(Decimal(r["quantity"]) for r in needs[0]["requirements"]), 1)
        self.assertEqual((await self.db[stock.LOCATIONS].find_one({"id": "loc"}))["occupancy"]["total_quantity"], 1)

    async def current_fixture(self, mode="stock"):
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"cost_price": 50,
            "options": [{"id": "count", "type": "select", "required": True,
                "values": [{"id": str(n), "name": str(n)} for n in range(1, 5)]}]}})
        await self.db[stock.RESOURCES].insert_one({"user_id": "owner", "id": "pack", "name": "Packaging",
            "kind": "service", "track_inventory": False, "unit_cost": 20})
        await self.db[stock.PRODUCT_BINDINGS].insert_one({"user_id": "owner", "id": "pack-link",
            "salla_product_id": "root", "resource_id": "pack", "quantity": 1})
        items = [{"kind": "current_product", "quantity": n, "sourcing_mode": mode,
            **({"supplier_id": "supplier-a"} if mode == "supplier" else {}),
            "condition": {"option_id": "count", "value_id": str(n)}} for n in range(1, 5)]
        await self.define(items, component_scopes={"pack": "per_bundle"})
        return items

    async def test_current_product_one_through_four_and_sale_multiplier(self):
        await self.current_fixture()
        await self.lot("root", qty=20)
        for n in range(1, 5):
            order = "current-" + str(n)
            options = [{"option_id": "count", "value_id": str(n)}]
            await self.reserve(order=order, options=options)
            view = await self.view(order)
            self.assertEqual(Decimal(view["expected_cost"]), 50 * n + 20)
            self.assertEqual(Decimal(view["current_product_quantity"]), n)
            self.assertEqual(Decimal(view["fulfillment_quantity"]), n)
            self.assertEqual(view["sale_quantity"], 1)
            self.assertEqual(sum(Decimal(r["quantity"]) for r in view["leaves"] if r.get("resource_id") == "pack"), 1)
            await self.reserve(order=order, options=options)
            self.assertEqual(await self.db[stock.UNITS].count_documents({"order_id": order}), 1)
            await stock.release_component_stock(self.db, merchant_id="owner", order_id=order, source_version=2)
        await self.reserve(qty=3, options=[{"option_id": "count", "value_id": "2"}])
        view = await self.view()
        self.assertEqual(view["sale_quantity"], 3)
        self.assertEqual(Decimal(view["units_per_selection"]), 2)
        self.assertEqual(Decimal(view["fulfillment_quantity"]), 6)
        self.assertEqual(Decimal(view["expected_cost"]), 360)
        from sold_products_report_v2 import load_sold_products
        sold = await load_sold_products(self.db, "owner", [{"order_number": "order", "products": [
            {"product_id": "root", "order_item_id": "line", "quantity": 3}]}])
        self.assertEqual(sold[0]["units_sold"], 3)
        self.assertEqual(sold[0]["total_cost"], 360)
        await self.consume()
        self.assertEqual((await self.db[stock.LOCATIONS].find_one({"id": "loc"}))["occupancy"]["total_quantity"], 14)
        usage = bundle.report_totals([{**await self.view(), "order_number": "order"}])["component_usage"]
        self.assertEqual(Decimal(next(r for r in usage if r["reference_id"] == "root")["consumed_quantity"]), 6)

    async def test_current_product_amendment_keeps_packaging_and_catalog_frozen(self):
        await self.current_fixture()
        await self.lot("root", qty=4)
        await self.reserve(options=[{"option_id": "count", "value_id": "1"}])
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"cost_price": 500}})
        await self.db[stock.RESOURCES].update_one({"id": "pack"}, {"$set": {"unit_cost": 200}})
        await self.reserve(version=2, options=[{"option_id": "count", "value_id": "2"}])
        self.assertEqual(Decimal((await self.view())["expected_cost"]), 120)
        await self.consume()
        await self.assertCode("bundle_consumed_amendment_requires_reconciliation",
            self.reserve(version=3, options=[{"option_id": "count", "value_id": "3"}]))

    async def test_current_product_supplier_slots_preserve_sizes_and_text_edits(self):
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"cost_price": 50,
            "variants": [{"id": "M", "cost_price": 50}, {"id": "XL", "cost_price": 50}],
            "options": [{"id": "text", "type": "text", "required": True}]}})
        await self.define([{"kind": "current_product", "quantity": 2, "sourcing_mode": "supplier", "supplier_id": "supplier-a",
            "slots": [{"slot_id": "first", "variant_id": "M", "customization_option_ids": ["text"]},
                      {"slot_id": "second", "variant_id": "XL", "customization_option_ids": ["text"]}]}])
        await self.reserve(options=[{"option_id": "text", "value": "FIRST"}])
        first = (await self.view())["leaves"]
        self.assertEqual({r["variant_id"] for r in first}, {"M", "XL"})
        self.assertTrue(all("FIRST" in str(r.get("customization")) for r in first))
        await self.reserve(version=2, options=[{"option_id": "text", "value": "CHANGED"}])
        leaves = (await self.view())["leaves"]
        self.assertTrue(all("CHANGED" in str(r.get("customization")) for r in leaves))
        for leaf in leaves:
            if leaf == leaves[0]:
                wrong = await self.lot("root", supplier="supplier-a", variant=leaf["variant_id"], specifications={"text": "WRONG"})
                await self.assertCode("bundle_purchase_receipt_customization_mismatch",
                    bundle.refresh_requirements(self.db, "owner", "order", actor_id="owner", receipt={
                        "order_line_id": "line", "leaf_id": leaf["leaf_id"], "receipt_id": wrong}))
            rid = await self.lot("root", supplier="supplier-a", variant=leaf["variant_id"], specifications=leaf["customization"])
            await bundle.refresh_requirements(self.db, "owner", "order", actor_id="owner", receipt={
                "order_line_id": "line", "leaf_id": leaf["leaf_id"], "receipt_id": rid})
        await self.consume()
        await self.assertCode("bundle_consumed_amendment_requires_reconciliation",
            self.reserve(version=3, options=[{"option_id": "text", "value": "TOO LATE"}]))

    async def test_automatic_customization_amendment_retains_history_not_receipts(self):
        from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {
            "options": [{"id": "text", "type": "text", "required": True}]}})
        await self.db[PRODUCT_OPERATION_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "root",
            "bundle_sourcing": {"revision": 1, "rules": [
                {"rule_id": "first", "customization": {"text": "FIRST"}, "supplier_id": "supplier-a"},
                {"rule_id": "changed", "customization": {"text": "CHANGED"}, "supplier_id": "supplier-b"}]}})
        await self.define([{"kind": "current_product", "quantity": 1, "sourcing_mode": "auto",
            "slots": [{"slot_id": "first", "customization_option_ids": ["text"]}]}])
        await self.reserve(options=[{"option_id": "text", "value": "FIRST"}])
        receipt = await self.lot("root", supplier="supplier-a", specifications={"text": "FIRST"})
        await self.attach("root", receipt)
        await self.reserve(version=2, options=[{"option_id": "text", "value": "CHANGED"}])
        leaf = (await self.view())["leaves"][0]
        self.assertEqual(leaf["supplier_id"], "supplier-b")
        self.assertEqual(leaf["receipt_ids"], [])
        self.assertEqual(leaf["reserved_quantity"], "0")
        await self.assertCode("bundle_purchase_receipt_customization_mismatch", self.attach("root", receipt))
        plan = await self.db[stock.PLANS].find_one({"order_id": "order"})
        self.assertEqual(plan["bundle_amendments"][-1]["units"][0]["bundle_leaves"][0]["receipt_ids"], [receipt])
        await stock.release_component_stock(self.db, merchant_id="owner", order_id="order", source_version=3)
        self.assertEqual((await bundle.inventory_lots(self.db, "owner", []))[0]["available"], 1)

    async def test_current_product_readiness_does_not_reserve_parent_twice(self):
        from fulfillment_v2_routes import build_order_fulfillment_decision, INVENTORY_RESERVATIONS
        await self.current_fixture()
        await self.lot("root", qty=2)
        options = [{"option_id": "count", "value_id": "2"}]
        await self.reserve(options=options)
        order = self.order_dto("paid")
        from order_engine.models import ShippingDTO, AddressDTO
        order.shipping = ShippingDTO(address=AddressDTO(city="Synthetic city", street="Synthetic street"))
        order.items[0].options_raw = options
        decision = await build_order_fulfillment_decision(self.db, user_id="owner", order=order)
        self.assertTrue(decision["ready_to_ship"], decision)
        self.assertEqual(await self.db[INVENTORY_RESERVATIONS].count_documents({"status": "active"}), 0)
        units = await self.db[stock.UNITS].find({"order_id": "order"}).to_list(10)
        self.assertEqual(sum(Decimal(a["quantity"]) for row in units for a in row["allocations"]), 2)

    async def test_bundle_parent_excluded_from_legacy_supplier_catalog_and_stale_pieces(self):
        import reviewed_products_catalog as reviewed
        import preparation_piece_operations as pieces
        from reviewed_product_sorting import install_reviewed_product_sorting
        install_reviewed_product_sorting()
        await self.current_fixture(mode="supplier")
        await self.reserve(options=[{"option_id": "count", "value_id": "2"}])
        workflow = {"user_id": "owner", "order_number": "order", "stage": "reviewed", "items": [
            {"order_item_id": "line", "supplier_export": True, "product_id": "root", "quantity": 1}]}
        await self.db[reviewed.WORKFLOWS].insert_one(workflow)
        # Only the presentation DTO adapter is substituted. All snapshot reads,
        # queue filtering, batch lookup and forbidden-write checks use real Mongo.
        with patch.object(reviewed, "get_orders", AsyncMock(return_value={"order": self.order_dto("paid")})):
            context = await reviewed.load_reviewed_product_context(self.db, user_id="owner", order_number="order")
        self.assertEqual(context["catalog"]["products"], [])
        self.assertTrue((await self.db[reviewed.WORKFLOWS].find_one({"order_number": "order"}))["items"][0]["supplier_export"])
        await self.db[pieces.BATCHES].insert_one({"id": "stale", "user_id": "owner", "status": "ready", "lines": [
            {"order_number": "order", "order_item_id": "line", "product_id": "root", "quantity": 1}]})
        await self.assertCode("bundle_parent_preparation_export_forbidden",
            pieces.materialize_preparation_pieces(self.db, user_id="owner", registry={"batch_id": "stale"}))
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 0)

    async def test_current_product_mixed_supplier_and_conditional_stock_addon(self):
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"cost_price": 50,
            "options": [{"id": "gift", "type": "select", "values": [
                {"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]}]}})
        await self.define([{"kind": "current_product", "quantity": 2, "sourcing_mode": "supplier", "supplier_id": "supplier-a"},
            self.item("c", "supplier", "supplier-b"),
            {**self.item("a"), "condition": {"option_id": "gift", "value_id": "yes"}}])
        await self.lot("a")
        await self.reserve(options=[{"option_id": "gift", "value_id": "no"}])
        self.assertNotIn("a", {r.get("product_id") for r in (await self.view())["leaves"]})
        await self.reserve(version=2, options=[{"option_id": "gift", "value_id": "yes"}])
        view = await self.view()
        self.assertEqual(Decimal(view["expected_cost"]), 140)
        self.assertEqual(Decimal(view["current_product_quantity"]), 2)
        groups = bundle.report_totals([{**view, "order_number": "order"}])["supplier_requirements"]
        self.assertEqual({r["supplier_id"] for r in groups}, {"supplier-a", "supplier-b"})
        self.assertEqual(sum(Decimal(r["reserved_quantity"]) for r in view["leaves"] if r.get("product_id") == "a"), 1)
        await stock.release_component_stock(self.db, merchant_id="owner", order_id="order", source_version=3)
        self.assertEqual(await self.db[stock.UNITS].count_documents({"state": "reserved"}), 0)
