"""Exact composition acceptance gaps against an isolated real replica set."""
import os
import unittest
from decimal import Decimal

import test_bundle_fulfillment_v2 as fixtures
import test_bundle_composition_lifecycle as composition
import bundle_catalog_v2 as catalog
import bundle_fulfillment_v2 as bundle
import stock_component_consumption_service as stock
from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES
from purchase_receiving_service import COST_STATES, identity_key


@unittest.skipUnless(os.environ.get("MZ2_TEST_MONGO_URI"), "requires isolated loopback replica set")
class ExactCompositionAcceptance(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BundleMongoTests.asyncSetUp
    asyncTearDown = fixtures.BundleMongoTests.asyncTearDown
    lot = fixtures.BundleMongoTests.lot
    reserve = fixtures.BundleMongoTests.reserve
    view = fixtures.BundleMongoTests.view
    consume = fixtures.BundleMongoTests.consume
    attach = fixtures.BundleMongoTests.attach
    resource_lot = composition.CompositionLifecycleTests.resource_lot
    quantities = composition.CompositionLifecycleTests.quantities
    composition = composition.CompositionLifecycleTests.composition

    async def save(self, key, items, revision=0):
        return await catalog.save_bundle(self.db, "owner", key, {"product_type": "composite", "schema_version": 2,
            "expected_revision": revision, "items": items}, "owner")

    async def test_distinct_parent_bundles_keep_opposite_exclusions(self):
        await self.composition()
        original = await catalog.expand_bundle(self.db, "owner", "root")
        included = await self.save("nested", [{"kind": "product", "product_id": pid} for pid in ("a", "b")] + [{"kind": "resource", "resource_id": "wrap"}])
        self.assertEqual(Decimal(original["estimated_cost"]), 88)
        self.assertEqual(Decimal(included["expansion"]["estimated_cost"]), 91)
        self.assertEqual({d["resource_id"] for d in original["component_decisions"] if not d["use"]}, {"hoodie-bag", "necklace-bag"})
        self.assertTrue(all(d["use"] for d in included["expansion"]["component_decisions"]))
        await self.reserve()
        await stock.reserve_component_stock(self.db, merchant_id="owner", order_id="included-order", lines=[{
            "order_line_id": "line", "product_id": "nested", "quantity": 1}], source_version=1, source_created_at="2026-10-01T00:00:00+00:00")
        await self.consume()
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="included-order")
        remaining = await self.quantities()
        self.assertEqual(remaining["hoodie-bag"], 4)
        self.assertEqual(remaining["necklace-bag"], 4)
        self.assertEqual(await catalog.expand_bundle(self.db, "owner", "root"), original)
        self.assertEqual(await self.db[catalog.PRODUCT_RESOURCE_BINDINGS].find({}, {"_id": 0}).to_list(20), self.originals)

    async def test_customer_options_have_opposite_exclusions_in_same_bundle(self):
        await self.composition()
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"options": [{"id": "pack", "required": True, "values": [{"id": "shared"}, {"id": "individual"}]}]}})
        rows = []
        for choice in ("shared", "individual"):
            for pid, rid in (("a", "hoodie-bag"), ("b", "necklace-bag")):
                rows.append({"kind": "product", "product_id": pid, "condition": {"option_id": "pack", "value_id": choice},
                    "component_overrides": [{"binding_kind": "product_resource", "binding_id": "bind-" + rid, "use": choice == "individual"}]})
        await self.save("root", rows + [{"kind": "resource", "resource_id": "wrap"}], revision=1)
        for choice, expected in (("shared", 88), ("individual", 91)):
            await self.reserve(order=choice, options=[{"option_id": "pack", "value_id": choice}])
            self.assertEqual(Decimal((await self.view(choice))["expected_cost"]), expected)
            await self.consume(choice)
            self.assertEqual(Decimal((await self.view(choice))["actual_cost"]), expected)
        remaining = await self.quantities()
        self.assertEqual(remaining["hoodie-bag"], 4)
        self.assertEqual(remaining["necklace-bag"], 4)
        self.assertEqual(remaining["wrap"], 3)

    async def test_three_product_composition_full_lifecycle_cost_113(self):
        await self.composition()
        await self.db[catalog.COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "c", "base_cost": 25})
        await self.lot("c", qty=5)
        await self.db[COST_STATES].update_one({"_id": identity_key("owner", {"item_type": "product", "product_id": "c"})}, {"$set": {"average_cost": "25"}})
        current = await self.db[catalog.BUNDLES].find_one({"product_id": "root"})
        await self.save("root", current["items"] + [{"kind": "product", "product_id": "c"}], revision=1)
        await self.reserve()
        view = await self.view()
        self.assertEqual(len([leaf for leaf in view["leaves"] if leaf["kind"] == "product"]), 3)
        self.assertEqual(Decimal(view["expected_cost"]), 113)
        await self.consume()
        self.assertTrue((await self.consume())["duplicate"])
        self.assertEqual(Decimal((await self.view())["actual_cost"]), 113)
        remaining = await self.quantities()
        self.assertEqual(remaining["c"], 4)
        self.assertEqual(remaining["wrap"], 4)
        self.assertEqual(remaining["hoodie-bag"], 5)

    async def mapped_necklace(self):
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"options": [
            {"id": "color", "required": True, "values": [{"id": "silver"}, {"id": "gold"}]}, {"id": "name", "type": "text", "required": True}]}})
        await self.db[stock.PRODUCTS].update_one({"id": "a"}, {"$set": {"variants": [{"id": "silver"}, {"id": "gold"}],
            "options": [{"id": "engraving", "type": "text", "required": True}]}})
        await self.db[PRODUCT_OPERATION_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "a", "bundle_sourcing": {
            "revision": 1, "supplier_id": "supplier-a", "stock_first": True, "allow_split": True}})
        await self.save("root", [{"kind": "product", "product_id": "a", "variant_rules": [
            {"conditions": [{"option_id": "color", "value_id": color}], "variant_id": color} for color in ("silver", "gold")],
            "customization_mappings": [{"option_id": "name", "target_option_id": "engraving"}]}])
        return await self.lot("a", qty=1, variant="silver", specifications={"engraving": "عبير"})

    async def supplier_scenario(self, color, name):
        stock_receipt = await self.mapped_necklace()
        await self.reserve(options=[{"option_id": "color", "value_id": color}, {"option_id": "name", "value": name}])
        leaf = (await self.view())["leaves"][0]
        self.assertEqual(leaf["variant_id"], color)
        self.assertEqual(leaf["customization"], {"engraving": name})
        self.assertEqual(leaf["reserved_quantity"], "0")
        self.assertEqual(leaf["supplier_quantity"], "1")
        self.assertEqual(leaf["supplier_id"], "supplier-a")
        receipt = await self.lot("a", variant=color, specifications={"engraving": name}, supplier="supplier-a")
        await self.attach("a", receipt)
        await self.attach("a", receipt)
        self.assertTrue((await self.view())["ready"])
        await self.consume()
        self.assertTrue((await self.consume())["duplicate"])
        self.assertEqual(Decimal((await self.view())["actual_cost"]), 12)
        items = (await self.db[stock.LOCATIONS].find_one({"id": "loc"}))["occupancy"]["items"]
        self.assertEqual(sum(Decimal(str(r["quantity"])) for r in items if r["receipt_id"] == stock_receipt), 1)

    async def test_gold_ordinary_linked_product_routes_supplier_to_receipt_and_consume(self):
        await self.supplier_scenario("gold", "عبير")

    async def test_other_name_ordinary_linked_product_preserves_abeer_stock(self):
        await self.supplier_scenario("silver", "سارة")
