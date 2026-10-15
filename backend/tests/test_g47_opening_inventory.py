"""Opening inventory API + real sealed opening ledger + real Mongo transactions."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import post_journal_v2, post_opening_journal_v2, verify_journal_v2
from opening_inventory_routes import make_opening_inventory_router
import opening_inventory_service as opening
import purchase_receiving_service as purchase
import test_g47_purchase_approval_integration as purchase_fixture


class OpeningInventoryIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(purchase_fixture.local_uri("MZ2_TEST_MONGO_URI"), serverSelectionTimeoutMS=3000)
        self.assertTrue((await self.mongo.admin.command("hello")).get("setName"))
        self.db_name = "g47_opening_" + uuid.uuid4().hex
        self.db = self.mongo[self.db_name]
        self.addAsyncCleanup(self.cleanup_db)
        self.actor = {"id": "owner", "role": "owner", "name": "Synthetic owner", "is_active": True}
        await purchase_fixture.PurchaseApprovalMongoIntegration.seed(self, self.db)
        for suffix in ["base", "variant-b", "component-2"]:
            await self.db.warehouse_locations.insert_one({"id": "location-" + suffix, "user_id": "owner", "warehouse_id": "warehouse",
                "purpose": "permanent_storage", "code": suffix.upper(), "barcode_value": suffix.upper(), "state": "empty",
                "max_items": 100, "occupancy": {"items": [], "total_quantity": 0}})
        entries = [
            {"leg_key": "inventory", "entity_type": "asset", "entity_id": "inventory", "sub_account": "inventory", "side": "debit", "amount": "60.00", "entry_type": "opening_balance"},
            {"leg_key": "equity", "entity_type": "equity", "entity_id": "opening-equity", "sub_account": "main", "side": "credit", "amount": "60.00", "entry_type": "opening_balance"},
        ]
        async def post(scoped):
            return await post_opening_journal_v2(scoped._db, user_id="owner", actor_id="owner", actor_name="Synthetic owner",
                opening_operation_id="synthetic-approved-inventory-opening", approved_preview_hash=hashlib.sha256(b"approved inventory 60").hexdigest(),
                effective_at="2026-09-01T00:00:00.000000Z", entries=entries, mongo_session=scoped._session)
        posted = await atomic_owner(self.db, "owner", post)
        self.opening_id = posted["group"]["txn_group_id"]
        self.assertTrue((await verify_journal_v2(self.db, user_id="owner", txn_group_id=self.opening_id))["verified"])
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {
            "mezan2_financial_cutover.opening_active_txn_group_id": self.opening_id,
            "mezan2_financial_cutover.opening_root_txn_group_id": self.opening_id,
            "mezan2_financial_cutover.opening_balance_txn_group_id": self.opening_id,
            "mezan2_financial_cutover.evidence_sections.inventory": {"ref": "SYNTHETIC-INVENTORY-SHEET"}}})
        await self.db.mz2_opening_balance_drafts.update_one({"id": "synthetic-opening"}, {"$set": {"txn_group_id": self.opening_id, "zero_only": False}})
        async def actor():
            return dict(self.actor)
        self.app = purchase_fixture.app_for(self.db, self.actor)
        self.app.include_router(make_opening_inventory_router(self.db, actor), prefix="/api")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://synthetic.local")
        self.addAsyncCleanup(self.client.aclose)

    async def cleanup_db(self):
        self.assertTrue(self.db_name.startswith("g47_opening_"))
        await self.mongo.drop_database(self.db_name)
        self.mongo.close()

    def document(self):
        def row(identity, qty, cost, locations):
            return {**identity, "inventory_account_id": "inventory", "opening_quantity": str(qty), "opening_unit_cost": str(cost),
                "opening_total_cost": str(qty * cost), "allocations": [{"location_id": loc, "quantity": str(amount), "scanned_location_barcode": barcode,
                    "preparation_state": "ready_complete", "specifications": {}} for loc, amount, barcode in locations]}
        return {"schema_version": opening.SCHEMA, "cost_policy_version": purchase.COST_POLICY,
            "cutover_at": "2026-09-01T00:00:00+00:00", "opening_txn_group_id": self.opening_id, "evidence_ref": "SYNTHETIC-INVENTORY-SHEET",
            "rows": [
                row({"item_type": "PRODUCT", "product_id": "product-B"}, 2, 10, [("location-base", 2, "BASE")]),
                row({"item_type": "PRODUCT", "product_id": "product-A", "variant_id": "variant-A"}, 3, 4, [("location-product", 3, "LOC-P")]),
                row({"item_type": "PRODUCT", "product_id": "product-A", "variant_id": "variant-B"}, 1, 8, [("location-variant-b", 1, "VARIANT-B")]),
                row({"item_type": "STOCK_COMPONENT", "resource_id": "component-A", "category_id": "metal"}, 4, 5, [("location-component", 2, "LOC-C"), ("location-component-2", 2, "COMPONENT-2")]),
            ]}

    async def upload(self, document=None):
        data = json.dumps(document or self.document(), sort_keys=True).encode()
        return await self.client.post("/api/opening-inventory/imports", files={"file": ("synthetic-opening.json", data, "application/json")})

    async def imported(self, document=None):
        response = await self.upload(document)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def approve(self, imported, **changes):
        body = {"approve": True, "evidence_sha256": imported["evidence_sha256"], "baseline_sha256": imported["baseline_sha256"], **changes}
        return await self.client.post(f"/api/opening-inventory/imports/{imported['id']}/approve", json=body)

    async def assert_uninitialized(self):
        for name in [opening.INITIALIZATIONS, purchase.RECEIPTS, purchase.COST_STATES, "liabilities", "general_ledger"]:
            self.assertEqual(await self.db[name].count_documents({}), 0, name)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 1)
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}), 2)

    async def test_import_has_no_stock_effect_and_preserves_exact_evidence_bytes(self):
        imported = await self.imported()
        await self.assert_uninitialized()
        original = await self.db.accounting_source_files.find_one({"file_id": imported["source_file_id"]})
        self.assertEqual(bytes(original["content"]), json.dumps(self.document(), sort_keys=True).encode())
        self.assertEqual(original["sha256"], imported["evidence_sha256"])
        self.assertEqual(len(imported["baseline_locations"]), 5)
        self.assertEqual(imported["state"], "imported")
        for location in await self.db.warehouse_locations.find({}).to_list(10):
            self.assertEqual(location["occupancy"]["total_quantity"], 0)

    async def test_approve_base_product_two_variants_component_and_two_locations(self):
        imported = await self.imported()
        before = await self.db.accounting_general_ledger_v2.find({}).to_list(10)
        response = await self.approve(imported)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "approved")
        self.assertEqual(await self.db[purchase.COST_STATES].count_documents({}), 4)
        self.assertEqual(await self.db[purchase.RECEIPTS].count_documents({}), 5)
        states = await self.db[purchase.COST_STATES].find({}).to_list(10)
        variants = {state["inventory_identity"].get("variant_id"): state["average_cost"] for state in states if state["inventory_identity"].get("product_id") == "product-A"}
        self.assertEqual(variants, {"variant-A": "4.000000", "variant-B": "8.000000"})
        self.assertEqual(sum(float(state["opening_total_cost"]) for state in states), 60)
        self.assertEqual(sum(loc["occupancy"]["total_quantity"] for loc in await self.db.warehouse_locations.find({}).to_list(10)), 10)
        self.assertTrue(all(state["authoritative"] and state["evidence_sha256"] == imported["evidence_sha256"] for state in states))
        self.assertEqual(await self.db.accounting_general_ledger_v2.find({}).to_list(10), before)
        self.assertTrue((await verify_journal_v2(self.db, user_id="owner", txn_group_id=self.opening_id))["verified"])

    async def test_concurrent_retry_initializes_once_and_new_evidence_cannot_backfill(self):
        imported = await self.imported()
        responses = await asyncio.gather(self.approve(imported), self.approve(imported))
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(responses[0].json()["receipt_ids"], responses[1].json()["receipt_ids"])
        self.assertEqual(await self.db[purchase.RECEIPTS].count_documents({}), 5)
        self.assertEqual(await self.db[opening.INITIALIZATIONS].count_documents({}), 1)
        self.assertEqual((await self.imported())["id"], imported["id"])
        changed = self.document()
        changed["cutover_at"] = "2026-09-01T00:00:00.000000Z"
        response = await self.upload(changed)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_already_initialized")

    async def test_missing_variant_cost_zero_and_allocation_mismatch_fail_closed(self):
        for kind in ["missing_variant", "missing_cost", "zero_cost", "allocation", "total"]:
            with self.subTest(kind=kind):
                doc = self.document()
                if kind == "missing_variant":
                    del doc["rows"][1]["variant_id"]
                elif kind == "missing_cost":
                    del doc["rows"][0]["opening_unit_cost"]
                elif kind == "zero_cost":
                    doc["rows"][0]["opening_unit_cost"] = "0"
                elif kind == "allocation":
                    doc["rows"][3]["allocations"][0]["quantity"] = "1"
                else:
                    doc["rows"][0]["opening_total_cost"] = "19"
                response = await self.upload(doc)
                self.assertIn(response.status_code, {409, 422}, response.text)
                await self.assert_uninitialized()

    async def test_ledger_total_and_account_identity_mismatch_fail(self):
        for kind in ["value", "account"]:
            with self.subTest(kind=kind):
                doc = self.document()
                if kind == "value":
                    doc["rows"][0].update(opening_unit_cost="11", opening_total_cost="22")
                else:
                    doc["rows"][0]["inventory_account_id"] = "unapproved-inventory"
                response = await self.upload(doc)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "opening_inventory_ledger_valuation_mismatch")
                await self.assert_uninitialized()

    async def test_existing_positive_matching_stock_is_adopted_without_doubling(self):
        item = {"item_type": "stock_component", "resource_id": "component-A", "quantity": 2, "receipt_id": "synthetic-legacy-stock",
                "configuration_key": purchase.stable_id("component-configuration", "component-A"), "preparation_state": "ready_complete",
                "specifications": {}, "source_type": "purchase_invoice", "source_id": "synthetic-legacy-invoice",
                "lot_id": "synthetic-original-lot", "original_evidence_ref": "synthetic-original-evidence"}
        await self.db.warehouse_locations.update_one({"id": "location-component"}, {"$set": {"occupancy": {"items": [item], "total_quantity": 2}}})
        imported = await self.imported()
        response = await self.approve(imported)
        self.assertEqual(response.status_code, 200, response.text)
        loc = await self.db.warehouse_locations.find_one({"id": "location-component"})
        self.assertEqual(loc["occupancy"]["total_quantity"], 2)
        adopted = loc["occupancy"]["items"][0]
        for key, value in item.items():
            self.assertEqual(adopted[key], value, key)
        opening_receipt = await self.db[purchase.RECEIPTS].find_one({"location_id": "location-component"})
        self.assertEqual(opening_receipt["adopted_receipt_ids"], ["synthetic-legacy-stock"])

    async def test_existing_preparation_or_specification_cannot_be_rewritten_by_opening(self):
        item = {"item_type": "stock_component", "resource_id": "component-A", "quantity": 2, "receipt_id": "synthetic-existing",
                "configuration_key": purchase.stable_id("component-configuration", "component-A"),
                "preparation_state": "requires_preparation", "specifications": {"finish": "raw"}}
        await self.db.warehouse_locations.update_one({"id": "location-component"}, {"$set": {"occupancy": {"items": [item], "total_quantity": 2}}})
        response = await self.upload()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_existing_configuration_requires_reconciliation")
        self.assertEqual((await self.db.warehouse_locations.find_one({"id": "location-component"}))["occupancy"]["items"], [item])
        item.update(preparation_state="ready_complete", specifications={}, source_type="stock_preparation_order",
                    component_provenance={"authority": "synthetic-historical-manufacturing"})
        await self.db.warehouse_locations.update_one({"id": "location-component"}, {"$set": {"occupancy": {"items": [item], "total_quantity": 2}}})
        response = await self.upload()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_existing_provenance_requires_reconciliation")
        self.assertEqual((await self.db.warehouse_locations.find_one({"id": "location-component"}))["occupancy"]["items"], [item])
        await self.assert_uninitialized()

    async def test_any_omitted_positive_location_or_quantity_conflict_fails(self):
        extra = {"id": "unlisted", "user_id": "owner", "state": "occupied", "occupancy": {"items": [{"item_type": "stock_component", "resource_id": "component-A", "quantity": 1}], "total_quantity": 1}}
        await self.db.warehouse_locations.insert_one(extra)
        response = await self.upload()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_positive_stock_omitted")
        await self.db.warehouse_locations.delete_one({"id": "unlisted"})
        await self.db.warehouse_locations.update_one({"id": "location-component"}, {"$set": {"occupancy": extra["occupancy"]}})
        response = await self.upload()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_existing_quantity_requires_reconciliation")
        await self.assert_uninitialized()

    async def test_changed_stock_evidence_or_cutover_cannot_be_approved(self):
        imported = await self.imported()
        original = await self.db.accounting_source_files.find_one({"file_id": imported["source_file_id"]})
        await self.db.accounting_source_files.update_one({"_id": original["_id"]}, {"$set": {"content": b"tampered"}})
        response = await self.approve(imported)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_evidence_changed")
        await self.db.accounting_source_files.replace_one({"_id": original["_id"]}, original)
        await self.db.warehouse_locations.update_one({"id": "location-base"}, {"$set": {"max_items": 101}})
        response = await self.approve(imported)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_inventory_changed_since_import")
        await self.db.warehouse_locations.update_one({"id": "location-base"}, {"$set": {"max_items": 100}})
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"mezan2_financial_cutover.opening_active_txn_group_id": "changed"}})
        response = await self.approve(imported)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_opening_changed")
        await self.assert_uninitialized()

    async def test_write_pause_closed_period_and_nonowner_block(self):
        imported = await self.imported()
        await self.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"writes_paused": True}})
        self.assertEqual((await self.approve(imported)).status_code, 423)
        await self.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"writes_paused": False}})
        await self.db.mz2_accounting_periods.insert_one({"user_id": "owner", "month": "2026-09", "closed": True})
        response = await self.approve(imported)
        self.assertEqual(response.json()["detail"]["code"], "accounting_period_closed")
        await self.db.mz2_accounting_periods.delete_many({})
        await self.db.users.update_one({"id": "owner"}, {"$set": {"role": "employee", "created_by": "other-owner"}})
        self.assertEqual((await self.approve(imported)).status_code, 403)
        await self.assert_uninitialized()

    async def test_lifecycle_activity_without_journal_blocks_initialization(self):
        imported = await self.imported()
        for collection in ["mezan_component_consumption_plans_v1", "mezan_component_consumption_units_v1", "mezan_component_order_lifecycle_v1"]:
            with self.subTest(collection=collection):
                await self.db[collection].insert_one({"user_id": "owner", "state": "reserved"})
                response = await self.approve(imported)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "opening_inventory_operational_activity_present")
                await self.db[collection].delete_many({})
        await self.assert_uninitialized()

    async def test_transaction_failure_after_projections_rolls_back_and_retry_succeeds(self):
        imported = await self.imported()
        original = opening.bump_product_cost_revision
        async def fail_after_real_write(*args, **kwargs):
            await original(*args, **kwargs)
            raise HTTPException(503, detail={"code": "synthetic-opening-failure"})
        with patch.object(opening, "bump_product_cost_revision", fail_after_real_write):
            response = await self.approve(imported)
        self.assertEqual(response.status_code, 503, response.text)
        await self.assert_uninitialized()
        for location in await self.db.warehouse_locations.find({}).to_list(10):
            self.assertEqual(location["occupancy"]["total_quantity"], 0)
        response = await self.approve(imported)
        self.assertEqual(response.status_code, 200, response.text)

    async def test_subsequent_real_purchase_uses_opening_moving_average(self):
        payload = purchase_fixture.PurchaseApprovalMongoIntegration.payload(self)
        response = await self.client.post("/api/purchase-invoices", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        invoice = response.json()
        approval = purchase_fixture.PurchaseApprovalMongoIntegration.request_for(self, invoice)
        blocked = await self.client.post(f"/api/purchase-invoices/{invoice['id']}/approve-receive", json=approval)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "opening_inventory_initialization_required")
        await self.assert_uninitialized()
        imported = await self.imported()
        self.assertEqual((await self.approve(imported)).status_code, 200)
        response = await self.client.post(f"/api/purchase-invoices/{invoice['id']}/approve-receive", json=approval)
        self.assertEqual(response.status_code, 200, response.text)
        component = await self.db[purchase.COST_STATES].find_one({"inventory_identity.resource_id": "component-A"})
        variant = await self.db[purchase.COST_STATES].find_one({"inventory_identity.product_id": "product-A", "inventory_identity.variant_id": "variant-A"})
        self.assertEqual(component["average_cost"], "3.750000")
        self.assertEqual(variant["average_cost"], "6.400000")
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 2)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertTrue((await verify_journal_v2(self.db, user_id="owner", txn_group_id=response.json()["txn_group_id"]))["verified"])

    async def test_actual_ready_opening_receipt_does_not_fabricate_manufacturing_exemption(self):
        from datetime import datetime
        from fulfillment_v2_routes import reconcile_component_order_lifecycle
        from order_engine.models import OrderDTO, OrderItemDTO, OrderSourceDTO, PaymentDTO, ShippingDTO, AddressDTO
        import stock_component_consumption_service as consumption
        imported = await self.imported()
        self.assertEqual((await self.approve(imported)).status_code, 200)
        receipt = await self.db[purchase.RECEIPTS].find_one({"location_id": "location-product"})
        self.assertEqual(receipt["source_type"], "opening_inventory")
        self.assertNotIn("component_provenance", receipt)
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"g47_inventory.component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
        await self.db[consumption.PRODUCT_BINDINGS].insert_one({"id": "synthetic-recipe", "user_id": "owner", "salla_product_id": "1001", "resource_id": "component-A", "quantity": 1})
        stamp = "2026-09-26T12:00:00+00:00"
        # Canonical intake precedes lifecycle reconciliation in Production.
        await self.db.unified_orders.insert_one({
            "user_id": "owner", "order_number": "new-order",
            "raw_by_source": {"salla_direct": {"date": stamp}},
        })
        order = OrderDTO(order_id="new-order", order_number="new-order", created_at=datetime.fromisoformat(stamp), source=OrderSourceDTO(source_order_id="new-order"), status="under_review",
            payment=PaymentDTO(method="cod"), shipping=ShippingDTO(address=AddressDTO(city="Synthetic city", street="Synthetic street")),
            items=[OrderItemDTO(order_item_id="line", product_id="1001", variant_id="variant-A", name="Synthetic product", sku="SHARED-SKU", quantity=1)])
        decision = {"warehouse_ids": ["warehouse"], "lines": [{"order_item_id": "line", "preparation_satisfied_by_ready_stock": True,
            "inventory_allocations": [{"receipt_id": receipt["id"], "quantity": 1, "source_type": "opening_inventory"}]}]}
        result = await reconcile_component_order_lifecycle(self.db, user_id="owner", order=order, source_updated_at=stamp, actor_id="owner", decision=decision, strict=True)
        self.assertEqual(result["state"], "reserved")
        unit = await self.db[consumption.UNITS].find_one({"order_id": "new-order"})
        self.assertIsNone(unit["prebuilt"])
        self.assertEqual(unit["resource_demands"][0]["resource_id"], "component-A")
        self.assertEqual(unit["allocations"][0]["quantity"], "1")
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 1)

    async def test_real_operational_journal_prevents_retroactive_initialization(self):
        async def post(scoped):
            return await post_journal_v2(scoped._db, user_id="owner", actor_id="owner", actor_name="Synthetic owner", idempotency_key="synthetic-prior-operation",
                txn_type="synthetic_operation", source="synthetic_test", effective_at="2026-09-26T12:00:00+00:00", mongo_session=scoped._session,
                entries=[{"leg_key": "bank", "entity_type": "bank", "entity_id": "synthetic-bank", "sub_account": "main", "entry_type": "synthetic_operation", "side": "debit", "amount": "1.00"},
                         {"leg_key": "offset", "entity_type": "equity", "entity_id": "synthetic-equity", "sub_account": "main", "entry_type": "synthetic_operation", "side": "credit", "amount": "1.00"}])
        await atomic_owner(self.db, "owner", post)
        response = await self.upload()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "opening_inventory_operational_activity_present")
        self.assertEqual(await self.db[opening.IMPORTS].count_documents({}), 0)
        self.assertEqual(await self.db[purchase.COST_STATES].count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
