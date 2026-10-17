"""Composition V2 acceptance on isolated real warehouse/receipt transactions."""
import os
import unittest
from decimal import Decimal

import test_bundle_fulfillment_v2 as fixtures
import bundle_catalog_v2 as catalog
import bundle_fulfillment_v2 as bundle
import stock_component_consumption_service as stock
from purchase_receiving_service import COST_POLICY, COST_STATES, identity_key


@unittest.skipUnless(os.environ.get("MZ2_TEST_MONGO_URI"), "requires isolated loopback replica set")
class CompositionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    # Reuse fixture helpers, without inheriting/collecting the existing suite.
    asyncSetUp = fixtures.BundleMongoTests.asyncSetUp
    asyncTearDown = fixtures.BundleMongoTests.asyncTearDown
    lot = fixtures.BundleMongoTests.lot
    reserve = fixtures.BundleMongoTests.reserve
    view = fixtures.BundleMongoTests.view
    consume = fixtures.BundleMongoTests.consume
    assertCode = fixtures.BundleMongoTests.assertCode

    async def resource_lot(self, rid, cost):
        item = {"item_type": "stock_component", "resource_id": rid, "receipt_id": "lot-" + rid,
                "quantity": 5, "configuration_key": "component-" + rid}
        await self.db[stock.RECEIPTS].insert_one({**item, "id": "lot-" + rid, "user_id": "owner",
            "location_id": "loc", "warehouse_id": "warehouse", "status": "posted",
            "source_type": "purchase_invoice", "source_id": "fixture-" + rid, "source_line_id": rid,
            "unit_purchase_cost": str(cost)})
        await self.db[stock.LOCATIONS].update_one({"id": "loc"}, {
            "$push": {"occupancy.items": item}, "$inc": {"occupancy.total_quantity": 5}})
        await self.db[COST_STATES].insert_one({"_id": identity_key("owner", item), "user_id": "owner",
            "authoritative": True, "cost_policy_version": COST_POLICY, "average_cost": str(cost)})

    async def composition(self):
        independent = {"cost_treatment": "independent", "assembly_origin": "inhouse", "evidence_ref": "synthetic-owner"}
        for pid, cost in (("a", 50), ("b", 30)):
            await self.db[catalog.COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": pid, "base_cost": cost})
            await self.lot(pid, qty=5)
            await self.db[COST_STATES].update_one({"_id": identity_key("owner", {"item_type": "product", "product_id": pid})}, {"$set": {"average_cost": str(cost)}})
        for pid, rid, cost in (("a", "hoodie-bag", 2), ("b", "necklace-box", 3), ("b", "necklace-bag", 1), (None, "wrap", 5)):
            await self.db[catalog.RESOURCES].insert_one({"user_id": "owner", "id": rid, "name": rid,
                "kind": "stock_component", "unit_cost": cost, "track_inventory": True, "unit": "piece"})
            await self.resource_lot(rid, cost)
            if pid:
                await self.db[catalog.PRODUCT_RESOURCE_BINDINGS].insert_one({"id": "bind-" + rid,
                    "user_id": "owner", "salla_product_id": pid, "resource_id": rid, "quantity": 1})
                await self.db[catalog.COST_PROFILES].update_one({"salla_product_id": pid}, {"$set": {
                    "composition_components.product_resource:bind-" + rid: independent}})
        self.originals = await self.db[catalog.PRODUCT_RESOURCE_BINDINGS].find({}, {"_id": 0}).to_list(20)
        result = await catalog.save_bundle(self.db, "owner", "root", {"product_type": "composite", "schema_version": 2,
            "expected_revision": 0, "items": [
                {"kind": "product", "product_id": pid, "quantity": 1, "component_overrides": [
                    {"binding_kind": "product_resource", "binding_id": "bind-" + rid, "use": False}]}
                for pid, rid in (("a", "hoodie-bag"), ("b", "necklace-bag"))] + [
                    {"kind": "resource", "resource_id": "wrap", "quantity": 1}]}, "owner")
        self.assertEqual(Decimal(result["expansion"]["estimated_cost"]), 88)

    async def quantities(self):
        row = await self.db[stock.LOCATIONS].find_one({"id": "loc"})
        return {r.get("resource_id") or r.get("product_id"): Decimal(str(r["quantity"])) for r in row["occupancy"]["items"]}

    async def test_88_composition_consumption_retry_and_frozen_policy(self):
        await self.composition()
        await self.reserve()
        await self.db[catalog.COST_PROFILES].update_many({}, {"$set": {"base_cost": 999, "composition_components": {}}})
        await self.reserve(qty=2, version=2)
        self.assertEqual(Decimal((await self.view())["expected_cost"]), 176)
        await self.consume()
        self.assertTrue((await self.consume())["duplicate"])
        self.assertEqual(Decimal((await self.view())["actual_cost"]), 176)
        remaining = await self.quantities()
        self.assertEqual({k: remaining[k] for k in ("a", "b", "necklace-box", "wrap")},
                         {k: Decimal(3) for k in ("a", "b", "necklace-box", "wrap")})
        self.assertEqual(remaining["hoodie-bag"], 5)
        self.assertEqual(remaining["necklace-bag"], 5)
        self.assertEqual(await self.db[catalog.PRODUCT_RESOURCE_BINDINGS].find({}, {"_id": 0}).to_list(20), self.originals)

    async def test_cancel_before_consumption_releases_without_touching_exclusions(self):
        await self.composition()
        before = await self.quantities()
        await self.reserve()
        await stock.release_component_stock(self.db, merchant_id="owner", order_id="order", source_version=2)
        await stock.release_component_stock(self.db, merchant_id="owner", order_id="order", source_version=2)
        self.assertEqual(await self.quantities(), before)
        self.assertEqual(await self.db[stock.UNITS].count_documents({"order_id": "order", "state": "reserved"}), 0)
        await self.assertCode("component_order_cancelled", self.consume())

    async def test_silver_abeer_only_matches_exact_variant_and_name(self):
        from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {
            "variants": [{"id": "silver"}, {"id": "gold"}],
            "options": [{"id": "color", "values": [{"id": "silver"}, {"id": "gold"}]},
                        {"id": "name", "type": "text", "required": True}]}})
        await self.db[PRODUCT_OPERATION_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "root",
            "bundle_sourcing": {"revision": 1, "supplier_id": "supplier-a", "allow_split": True}})
        await catalog.save_bundle(self.db, "owner", "root", {"product_type": "composite", "schema_version": 2,
            "expected_revision": 0, "items": [{"kind": "current_product", "quantity": 1,
                "slots": [{"slot_id": "necklace", "customization_option_ids": ["name"], "variant_rules": [
                    {"variant_id": color, "conditions": [{"option_id": "color", "value_id": color}]}
                    for color in ("silver", "gold")]}]}]}, "owner")
        await self.lot("root", variant="gold", specifications={"name": "عبير"})
        await self.lot("root", variant="silver", specifications={"name": "سارة"})
        options = [{"option_id": "color", "value_id": "silver"}, {"option_id": "name", "value": "عبير"}]
        await self.reserve(order="missing-exact", options=options)
        missing = (await self.view("missing-exact"))["leaves"][0]
        self.assertEqual(missing["reserved_quantity"], "0")
        self.assertEqual(missing["supplier_quantity"], "1")
        exact = await self.lot("root", variant="silver", specifications={"name": "عبير"})
        await self.reserve(order="exact-stock", options=options)
        self.assertTrue((await self.view("exact-stock"))["ready"])
        unit = await self.db[stock.UNITS].find_one({"order_id": "exact-stock"})
        self.assertEqual([a["lot_id"] for a in unit["allocations"]], [exact])
        await self.consume("exact-stock")
        remaining = (await self.db[stock.LOCATIONS].find_one({"id": "loc"}))["occupancy"]["items"]
        self.assertEqual(sum(Decimal(str(r["quantity"])) for r in remaining), 2)
