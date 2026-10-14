"""Real G47 journal adoption of pending physical stock; no provider/network calls."""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from tests import test_g47_purchase_approval_integration as g47
import purchase_receiving_service as receiving
from operational_physical_receipts import receive


class PhysicalAdoptionTests(unittest.IsolatedAsyncioTestCase):
    async def inventory_and_financial_snapshot(self):
        names = {"warehouse_locations", "warehouse_location_events", "mezan_inventory_receipts_v2",
                 "mz2_inventory_cost_states", "purchase_invoices", "liabilities"}
        names.update(n for n in await self.db.list_collection_names()
                     if n.startswith(("accounting_", "mz2_opening")))
        return {n: await self.db[n].find({}).sort("_id", 1).to_list(1000) for n in sorted(names)}

    async def test_partial_single_invoice_adoption_is_rejected_without_effects(self):
        receipt = await self.pending()
        invoice, request = await self.invoice(receipt_id=receipt["id"])
        request["receipts"][0]["quantity"] = 4
        before = await self.inventory_and_financial_snapshot()
        response = await self.fixture.approve(invoice, request)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "purchase_full_receive_required")
        self.assertEqual(await self.inventory_and_financial_snapshot(), before)

    async def test_valued_returns_reject_and_pending_residual_return_preserves_valuation(self):
        from fastapi import HTTPException
        from operational_physical_receipts import return_pending
        receipt = await self.pending()
        invoice, request = await self.invoice(4, 20, receipt["id"])
        response = await self.fixture.approve(invoice, request)
        self.assertEqual(response.status_code, 200, response.text)
        valued = await self.db.mezan_inventory_receipts_v2.find_one({"physical_receipt_id": receipt["id"]})
        for target, quantity in ((valued["id"], 1), (receipt["id"], 7)):
            before = await self.inventory_and_financial_snapshot()
            with self.assertRaises(HTTPException) as rejected:
                await return_pending(self.db, user=self.fixture.actor, receipt_id=target,
                                     source_return_id="unsupported-valued-return", quantity=quantity)
            self.assertEqual(rejected.exception.status_code, 409)
            self.assertEqual(await self.inventory_and_financial_snapshot(), before)
        before = await self.inventory_and_financial_snapshot()
        await return_pending(self.db, user=self.fixture.actor, receipt_id=receipt["id"],
                             source_return_id="pending-residual-return", quantity=6)
        self.assertEqual(await self.physical(), 4)
        after = await self.inventory_and_financial_snapshot()
        for name in before:
            if name not in {"warehouse_locations", "warehouse_location_events", "mezan_inventory_receipts_v2"}:
                self.assertEqual(after[name], before[name], name)
        before = after
        with self.assertRaises(HTTPException) as rejected:
            await return_pending(self.db, user=self.fixture.actor, receipt_id=receipt["id"],
                                 source_return_id="residual-exhausted", quantity=1)
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(await self.inventory_and_financial_snapshot(), before)

    async def asyncSetUp(self):
        self.fixture = g47.PurchaseApprovalMongoIntegration()
        self.fixture.addAsyncCleanup = self.addAsyncCleanup
        await self.fixture.asyncSetUp()
        self.db = self.fixture.db
        await self.db.warehouse_locations_warehouses.insert_one({"id":"warehouse","user_id":"owner","status":"active"})

    async def pending(self, quantity=10):
        return await receive(self.db, user=self.fixture.actor, payload={
            "source_document_id": "SYNTHETIC-001",
            "source_line_id": "physical-line", "supplier_id": "supplier",
            "line": {"item_type": "PRODUCT", "product_id": "product-A", "variant_id": "variant-A", "quantity": quantity},
            "unit": "piece", "operational_unit_cost": 15, "location_id": "location-product",
            "scanned_location_barcode": "LOC-P", "preparation_state": "ready_complete",
            "specifications": {},
        })

    async def invoice(self, quantity=10, unit_cost=15, receipt_id=None):
        payload = self.fixture.payload(lines=[{
            "id": "line-product", "item_type": "PRODUCT", "product_id": "product-A",
            "variant_id": "variant-A", "quantity": quantity, "unit_cost": unit_cost}])
        invoice = await self.fixture.draft(payload)
        request = self.fixture.request_for(invoice)
        request["receipts"][0].update(physical_receipt_id=receipt_id, physical_source_line_id="physical-line")
        return invoice, request

    async def physical(self):
        location = await self.db.warehouse_locations.find_one({"id": "location-product"})
        return sum(row["quantity"] for row in location["occupancy"]["items"])

    async def test_exact_adoption_and_retry_do_not_double_quantity_cost_or_journal(self):
        receipt = await self.pending()
        self.assertEqual(await self.physical(), 10)
        self.assertEqual(await self.db.mz2_inventory_cost_states.count_documents({}), 0)
        invoice, request = await self.invoice(receipt_id=receipt["id"])
        first = await self.fixture.approve(invoice, request)
        await self.fixture.assert_success(first, total_minor=15000)
        self.assertEqual(await self.physical(), 10)
        again = await self.fixture.approve(invoice, request)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(await self.physical(), 10)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 1)
        cost = await self.db.mz2_inventory_cost_states.find_one({})
        self.assertEqual(cost["average_cost"], "15.000000")

    async def test_partial_physical_adoption_preserves_full_invoice_and_weights_only_valued(self):
        receipt = await self.pending()
        invoice, request = await self.invoice(4, 20, receipt["id"])
        first = await self.fixture.approve(invoice, request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(await self.physical(), 10)
        cost = await self.db.mz2_inventory_cost_states.find_one({})
        self.assertEqual(cost["average_cost"], "20.000000")
        from operational_inventory_projection import projection
        rows=(await projection(self.db,"owner"))["items"]
        self.assertEqual(sum(r["physical"] for r in rows),10)
        self.assertEqual(sum(r["available"] for r in rows),4)
        self.assertEqual(sum(r["financially_pending"] for r in rows),6)
        invoice, request = await self.invoice(6, 10, receipt["id"])
        second = await self.fixture.approve(invoice, request)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(await self.physical(), 10)
        cost = await self.db.mz2_inventory_cost_states.find_one({})
        self.assertEqual(cost["average_cost"], "14.000000")
        rows=(await projection(self.db,"owner"))["items"]
        self.assertEqual(sum(r["physical"] for r in rows),10)
        self.assertEqual(sum(r["available"] for r in rows),10)
        self.assertEqual(sum(r["financially_pending"] for r in rows),0)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 2)

    async def test_failure_after_adoption_rolls_back_stock_cost_and_journal(self):
        receipt = await self.pending()
        before = await self.db.warehouse_locations.find_one({"id": "location-product"})
        invoice, request = await self.invoice(receipt_id=receipt["id"])
        with patch.object(receiving, "post_journal_v2", AsyncMock(side_effect=RuntimeError("synthetic journal failure"))):
            with self.assertRaises(RuntimeError):
                await self.fixture.approve(invoice, request)
        self.assertEqual(await self.db.warehouse_locations.find_one({"id": "location-product"}), before)
        self.assertEqual(await self.db.mz2_inventory_cost_states.count_documents({}), 0)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 0)
        retry = await self.fixture.approve(invoice, request)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(await self.physical(), 10)

    async def test_concurrent_distinct_invoices_cannot_over_adopt(self):
        receipt = await self.pending()
        first, a = await self.invoice(6, 15, receipt["id"])
        second, b = await self.invoice(6, 15, receipt["id"])
        results = await asyncio.gather(self.fixture.approve(first, a), self.fixture.approve(second, b))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(await self.physical(), 10)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 1)

    async def test_wrong_source_line_rejects_without_quantity_cost_or_journal(self):
        receipt = await self.pending()
        invoice, request = await self.invoice(receipt_id=receipt["id"])
        request["receipts"][0]["physical_source_line_id"] = "wrong-source"
        response = await self.fixture.approve(invoice, request)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.physical(), 10)
        self.assertEqual(await self.db.mz2_inventory_cost_states.count_documents({}), 0)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 0)

    async def test_component_adoption_has_no_sku_and_no_double_stock(self):
        receipt = await receive(self.db, user=self.fixture.actor, payload={
            "source_document_id": "SYNTHETIC-001", "source_line_id": "physical-line", "supplier_id": "supplier",
            "line": {"item_type": "STOCK_COMPONENT", "resource_id": "component-A", "category_id": "metal", "quantity": 10},
            "unit": "piece", "operational_unit_cost": 15, "location_id": "location-component",
            "scanned_location_barcode": "LOC-C", "preparation_state": "ready_complete", "specifications": {}})
        invoice = await self.fixture.draft(self.fixture.payload(lines=[{
            "id": "line-component", "item_type": "STOCK_COMPONENT", "resource_id": "component-A",
            "category_id": "metal", "quantity": 10, "unit_cost": 15}]))
        request = self.fixture.request_for(invoice)
        request["receipts"][0].update(physical_receipt_id=receipt["id"], physical_source_line_id="physical-line")
        response = await self.fixture.approve(invoice, request)
        await self.fixture.assert_success(response, total_minor=15000)
        location = await self.db.warehouse_locations.find_one({"id": "location-component"})
        self.assertEqual(sum(row["quantity"] for row in location["occupancy"]["items"]), 10)
        self.assertTrue(all(not row.get("sku") for row in location["occupancy"]["items"]))

    async def test_damaged_pending_stock_cannot_become_available_by_financial_adoption(self):
        receipt = await self.pending()
        await self.db.warehouse_locations.update_one({"id": "location-product"}, {"$set": {"occupancy.items.0.condition": "damaged"}})
        invoice, request = await self.invoice(receipt_id=receipt["id"])
        response = await self.fixture.approve(invoice, request)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.physical(), 10)
        self.assertEqual(await self.db.mz2_inventory_cost_states.count_documents({}), 0)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 0)

    async def test_corrupted_pending_proof_cannot_enter_existing_average(self):
        receipt = await self.pending()
        identity = receipt["inventory_identity"]
        await self.db.mz2_inventory_cost_states.insert_one({"_id": receiving.identity_key("owner", identity),
            "user_id": "owner", "inventory_identity": identity, "average_cost": "99.000000",
            "authoritative": True, "cost_policy_version": receiving.COST_POLICY})
        await self.db.mezan_inventory_receipts_v2.update_one({"id": receipt["id"]}, {"$set": {"status": "pending"}})
        invoice, request = await self.invoice(receipt_id=receipt["id"])
        response = await self.fixture.approve(invoice, request)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "physical_pending_reconciliation_required")
        self.assertEqual(await self.physical(), 10)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 0)


    async def test_proof_quality_flags_cannot_be_erased_by_adoption(self):
        receipt=await self.pending()
        invoice,request=await self.invoice(receipt_id=receipt["id"])
        for field,value in [("quality_status","quarantine"),("inspection_status","pending_inspection"),("receipt_confirmed",False)]:
            with self.subTest(field=field):
                await self.db.mezan_inventory_receipts_v2.update_one({"id":receipt["id"]},{"$set":{field:value}})
                response=await self.fixture.approve(invoice,request)
                self.assertEqual(response.status_code,409,response.text)
                self.assertEqual(await self.physical(),10)
                self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}),0)
                await self.db.mezan_inventory_receipts_v2.update_one({"id":receipt["id"]},{"$unset":{field:""}})
