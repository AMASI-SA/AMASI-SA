"""Configured native drafts and real atomic receiving, loopback replica only.

Reuse the established G47 synthetic fixture and its real writer/ledger gates.
Every test creates and drops its own uuid database; no runtime environment URL
or production database is read.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_g47_purchase_approval_integration as g47
from test_purchase_configuration_contract import product
from product_inventory_rules import canonical_specifications
import purchase_receiving_service as receiving
from purchase_invoices_routes import PurchaseApproval


class NativePurchaseConfigurationMongo(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = g47.PurchaseApprovalMongoIntegration.asyncSetUp
    drop_disposable_database = g47.PurchaseApprovalMongoIntegration.drop_disposable_database
    draft = g47.PurchaseApprovalMongoIntegration.draft
    approve = g47.PurchaseApprovalMongoIntegration.approve
    assert_no_effects = g47.PurchaseApprovalMongoIntegration.assert_no_effects
    assert_success = g47.PurchaseApprovalMongoIntegration.assert_success

    async def seed(self, db):
        await g47.PurchaseApprovalMongoIntegration.seed(self, db)
        source = product()
        variants = source["variants"]
        variants[0].update(id="variant-A", sku="SHARED-SKU", barcode="VAR-A")
        variants[1].update(id="variant-B", sku="SHARED-SKU", barcode="VAR-B")
        await db.mezan_products_v2.update_one({"mezan_product_id": "product-A"}, {"$set": {
            "main_image": source["main_image"], "options": source["options"], "variants": variants}})

    def payload(self, **changes):
        value = g47.PurchaseApprovalMongoIntegration.payload(self, **changes)
        value["lines"][1]["purchase_configuration"] = {"state": "ready", "selections": [
            {"option_id": "color", "value_id": "green"}, {"option_id": "size", "value_id": "m"}],
            "inputs": [{"option_id": "letters", "value": "A B"}]}
        return value

    def request_for(self, invoice):
        request = g47.PurchaseApprovalMongoIntegration.request_for(self, invoice)
        by_id = {line["id"]: line for line in invoice["lines"]}
        for receipt in request["receipts"]:
            line = by_id[receipt["line_id"]]
            if line.get("purchase_configuration"):
                receipt["preparation_state"] = "ready_complete" if line["purchase_configuration"]["state"] == "ready" else "requires_preparation"
                receipt["specifications"] = {v["name"]: v["value"] for v in line["purchase_option_labels"]}
        return request

    async def test_catalog_draft_reload_and_real_receipt_keep_exact_options_and_identity(self):
        catalog = await self.client.get("/api/purchase-invoices/catalog")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        product_row = next(p for p in catalog.json()["products"] if p["product_id"] == "product-A")
        self.assertEqual(product_row["image_url"], "https://example.test/pullover.webp")
        self.assertEqual(product_row["variants"][0]["selections"][0], {"option_id": "color", "value_id": "green"})
        invoice = await self.draft()
        await self.assert_no_effects()
        response = await self.client.get("/api/purchase-invoices/" + invoice["id"])
        restored = response.json()
        self.assertEqual(restored["lines"], invoice["lines"])
        line = restored["lines"][1]
        self.assertEqual(line["variant_id"], "variant-A")
        self.assertEqual(line["purchase_configuration"], self.payload()["lines"][1]["purchase_configuration"])
        request = self.request_for(restored)
        forged = deepcopy(request)
        forged["receipts"][1]["specifications"] = {}
        response = await self.approve(restored, forged)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "purchase_receipt_configuration_mismatch")
        self.assertEqual(await self.db[receiving.OPERATIONS].count_documents({}), 0)
        await self.assert_no_effects()
        result = await self.assert_success(await self.approve(restored, request))
        retry = await self.assert_success(await self.approve(restored, request))
        self.assertEqual(result["receipt_ids"], retry["receipt_ids"])
        receipt = await self.db[receiving.RECEIPTS].find_one({"item_type": "product"})
        self.assertEqual((receipt["product_id"], receipt["variant_id"], receipt["sku"]), ("product-A", "variant-A", "SHARED-SKU"))
        self.assertEqual(receipt["specifications"], canonical_specifications(request["receipts"][1]["specifications"]))
        self.assertEqual(receipt["preparation_state"], "ready_complete")
        self.assertEqual(await self.db[receiving.RECEIPTS].count_documents({}), 2)

    async def test_raw_customer_text_is_deferred_and_lost_response_retries_identical_receipts(self):
        payload = self.payload()
        payload["lines"][1]["purchase_configuration"].update(state="raw", inputs=[])
        invoice = await self.draft(payload)
        request = self.request_for(invoice)
        self.assertNotIn("الأحرف", request["receipts"][1]["specifications"])
        async with httpx.AsyncClient(transport=g47.LoseCommittedResponse(self.app), base_url="http://synthetic.local") as lossy:
            with self.assertRaises(httpx.ReadError):
                await self.approve(invoice, request, client=lossy)
        await self.assert_success(await self.approve(invoice, request))
        receipt = await self.db[receiving.RECEIPTS].find_one({"item_type": "product"})
        self.assertEqual(receipt["preparation_state"], "requires_preparation")
        self.assertEqual(receipt["specifications"], canonical_specifications({"اللون": "أخضر", "المقاس": "M"}))
        self.assertEqual(await self.db[receiving.RECEIPTS].count_documents({}), 2)

    async def test_definition_change_before_freeze_keeps_draft_editable_without_effects(self):
        invoice = await self.draft()
        await self.db.mezan_products_v2.update_one({"mezan_product_id": "product-A"}, {"$set": {"options.2.name": "الاسم المطبوع"}})
        response = await self.approve(invoice)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "purchase_configuration_definition_changed")
        stored = await self.db.purchase_invoices.find_one({"id": invoice["id"]})
        self.assertEqual(stored["state"], "draft")
        self.assertEqual(await self.db[receiving.OPERATIONS].count_documents({}), 0)
        await self.assert_no_effects()

    async def test_definition_change_between_prepare_and_commit_rolls_back_and_freezes_retry(self):
        invoice = await self.draft()
        original = receiving.atomic_owner
        calls = 0

        async def change_after_prepare(db, owner, callback, **kwargs):
            nonlocal calls
            result = await original(db, owner, callback, **kwargs)
            calls += 1
            if calls == 1:
                await self.db.mezan_products_v2.update_one({"mezan_product_id": "product-A"}, {"$set": {"options.2.name": "الاسم المطبوع"}})
            return result

        with patch.object(receiving, "atomic_owner", change_after_prepare):
            response = await self.approve(invoice)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "purchase_configuration_definition_changed")
        await self.assert_no_effects()
        operation = await self.db[receiving.OPERATIONS].find_one({})
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["request"], PurchaseApproval.model_validate(self.request_for(invoice)).model_dump(mode="json"))
        self.assertEqual((await self.db.purchase_invoices.find_one({"id": invoice["id"]}))["state"], "approving")
