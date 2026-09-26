"""G47 synthetic integration against real, loopback-only MongoDB transactions.

Run with MZ2_TEST_MONGO_URI pointing at a disposable replica set.  Authentication
is the only dependency override: actor revalidation, permissions, atomic_owner,
inventory placement, accounting readiness and the sealed V2 writer remain real.
No production/Preview URL or database is accepted by this fixture.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_ledger_v2 import (
    ensure_accounting_ledger_v2_indexes, verify_active_opening_v2, verify_journal_v2,
)
from accounting_module_contract import ACCOUNTING_PERMISSION_KEYS, EVIDENCE_SECTIONS, OPERATION_ID
from accounting_module_readiness import build_accounting_module_status
from purchase_invoices_routes import attach_purchase_invoice_routes, ensure_purchase_invoices_indexes
import purchase_invoices_routes as invoice_routes
import purchase_receiving_service as receiving


def local_uri(name: str, default: str | None = None) -> str:
    uri = os.environ.get(name, default)
    if not uri:
        raise unittest.SkipTest(f"Set {name} to a disposable loopback MongoDB")
    parsed = urlsplit(uri)
    if parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise RuntimeError("G47 integration tests accept unauthenticated loopback MongoDB only")
    if parsed.path not in {"", "/"}:
        raise RuntimeError("Provide a server URI only; the test chooses its disposable database")
    return uri


def app_for(db, actor):
    app = FastAPI()
    router = APIRouter(prefix="/api")
    attach_purchase_invoice_routes(router, db)
    app.include_router(router)

    async def synthetic_identity():
        return dict(actor)

    pending = list(app.routes)
    overrides = 0
    while pending:
        route = pending.pop()
        included = getattr(route, "original_router", None)
        if included is not None:
            pending.extend(included.routes)
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", []):
            if getattr(dependency.call, "__name__", None) == "current_user":
                app.dependency_overrides[dependency.call] = synthetic_identity
                overrides += 1
    if not overrides:
        raise AssertionError("No authentication dependency was overridden")
    return app


class LoseCommittedResponse(httpx.AsyncBaseTransport):
    """Lose one HTTP response only after the real ASGI application has finished."""
    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.lost = False

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if request.url.path.endswith("/approve-receive") and not self.lost:
            self.lost = True
            await response.aread()
            await response.aclose()
            raise httpx.ReadError("Synthetic loss after committed application response", request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


class PurchaseApprovalMongoIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(local_uri("MZ2_TEST_MONGO_URI"), serverSelectionTimeoutMS=3000)
        hello = await self.mongo.admin.command("hello")
        self.assertTrue(hello.get("setName"), "Positive cases require a real replica set")
        self.db_name = "g47_purchase_" + uuid.uuid4().hex
        self.db = self.mongo[self.db_name]
        self.addAsyncCleanup(self.drop_disposable_database)
        self.actor = {"id": "owner", "role": "owner", "name": "Synthetic owner", "is_active": True}
        await self.seed(self.db)
        self.app = app_for(self.db, self.actor)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://synthetic.local")
        self.addAsyncCleanup(self.client.aclose)

    async def drop_disposable_database(self):
        self.assertTrue(self.db_name.startswith("g47_purchase_"))
        await self.mongo.drop_database(self.db_name)
        self.mongo.close()

    async def seed(self, db):
        await db.users.insert_one(dict(self.actor))
        await db.counterparties.insert_one({"id": "supplier", "user_id": "owner", "kind": "supplier", "name": "Synthetic supplier"})
        await db.mezan_component_categories_v2.insert_one({"id": "metal", "user_id": "owner", "name": "Synthetic metal"})
        await db.mezan_cost_resources_v2.insert_many([
            {"id": "component-A", "user_id": "owner", "name": "Synthetic component", "code": "COMP-A", "category_ids": ["metal"], "track_inventory": True, "kind": "stock_component", "status": "active", "initial_unit_cost": 99, "unit_cost": 99, "cost_authoritative": False},
            {"id": "service-A", "user_id": "owner", "name": "Synthetic service", "code": "SERVICE", "category_ids": ["metal"], "track_inventory": False, "kind": "service", "status": "active"},
        ])
        await db.mezan_products_v2.insert_many([
            {"id": "catalog-A", "user_id": "owner", "mezan_product_id": "product-A", "salla_product_id": "1001", "name": "Synthetic selected product", "sku": "PARENT", "variants_count": 2,
             "variants": [{"id": "variant-A", "sku": "SHARED-SKU", "barcode": "VAR-A"}, {"id": "variant-B", "sku": "SHARED-SKU", "barcode": "VAR-B"}]},
            {"id": "catalog-B", "user_id": "owner", "mezan_product_id": "product-B", "salla_product_id": "1002", "name": "Synthetic other product", "sku": "SHARED-SKU", "variants": []},
        ])
        await db.warehouse_locations.insert_many([
            {"id": f"location-{suffix}", "user_id": "owner", "warehouse_id": "warehouse", "purpose": "permanent_storage", "code": suffix.upper(), "barcode_value": barcode, "state": "empty", "max_items": 10000, "occupancy": {"items": [], "total_quantity": 0}}
            for suffix, barcode in [("component", "LOC-C"), ("product", "LOC-P")]
        ])
        # Explicit synthetic, approved ZERO opening. Its real verifier and real
        # readiness model run here and again within the posting transaction.
        cutover_at = "2026-09-01T00:00:00.000000Z"
        pointer = "zero:synthetic-opening"
        cutover = {
            "operation_id": OPERATION_ID, "status": "active", "cutover_at": cutover_at,
            "ledger_source": "accounting_v2_operation_scoped", "evidence_sheet_ref": "SYNTHETIC-APPROVED-ZERO",
            "evidence_sections": {row["id"]: {"ref": "SYNTHETIC-" + row["id"]} for row in EVIDENCE_SECTIONS},
            "opening_balance_preview_id": "synthetic-opening", "opening_balance_preview_balanced": True,
            "opening_balance_approved_at": cutover_at, "opening_balance_approved_by": "owner",
            "opening_balance_txn_group_id": pointer, "opening_active_txn_group_id": pointer, "opening_root_txn_group_id": pointer,
        }
        mappings = [("inventory_asset", "asset", "inventory", "inventory"), ("supplier_payable", "supplier", "supplier", "payable"), ("input_vat", "tax", "input-vat", "input_vat")]
        await db.mz2_opening_balance_drafts.insert_one({
            "id": "synthetic-opening", "user_id": "owner", "status": "posted", "zero_only": True,
            "txn_group_id": pointer, "opening_root_txn_group_id": pointer, "cutover_at": cutover_at,
            "preview_hash": hashlib.sha256(b"synthetic-approved-zero-preview").hexdigest(),
            "approval_hash": hashlib.sha256(b"synthetic-approved-zero-approval").hexdigest(),
            "evidence_snapshot": cutover["evidence_sections"],
            "lines": [{"category": category, "entity_type": entity_type, "entity_id": entity_id, "sub_account": subaccount, "label": "Synthetic " + entity_id, "amount": "0.00"} for category, entity_type, entity_id, subaccount in mappings],
        })
        await db.settings.insert_one({"user_id": "owner", "mezan2_financial_cutover": cutover})
        await db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0, "writes_paused": False,
            "ledger_backend_state": "v2_active", "ledger_backend_revision": 2, "ledger_backend_contract_revision": 1,
            "ledger_backend_activation_ref": "SYNTHETIC-G47-APPROVED-ZERO"})
        verified = await verify_active_opening_v2(db, user_id="owner", cutover=cutover)
        self.assertTrue(verified)
        self.assertTrue(build_accounting_module_status(cutover, opening_posted_verified=verified)["cutover"]["safe_active"])
        await ensure_accounting_ledger_v2_indexes(db)
        await ensure_purchase_invoices_indexes(db)

    def payload(self, **changes):
        payload = {"supplier_counterparty_id": "supplier", "invoice_number": "SYNTHETIC-001", "invoice_date": "2026-09-26",
            "lines": [
                {"id": "line-component", "item_type": "STOCK_COMPONENT", "resource_id": "component-A", "category_id": "metal", "quantity": 4, "unit_cost": 2.5},
                {"id": "line-product", "item_type": "PRODUCT", "product_id": "product-A", "variant_id": "variant-A", "quantity": 2, "unit_cost": 10, "sku": "UNTRUSTED-SKU"},
            ], "tax_amount": 0, "tax_treatment": "none", "inventory_account_id": "inventory", "supplier_account_id": "supplier", "input_vat_account_id": "input-vat"}
        payload.update(changes)
        return payload

    async def draft(self, payload=None):
        response = await self.client.post("/api/purchase-invoices", json=payload or self.payload())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def request_for(self, invoice):
        return {"expected_revision": invoice["revision"], "operation_id": invoice["approval_operation_id"], "receipts": [
            {"line_id": line["id"], "quantity": line["quantity"], "location_id": "location-component" if line["item_type"] == "STOCK_COMPONENT" else "location-product",
             "scanned_location_barcode": "LOC-C" if line["item_type"] == "STOCK_COMPONENT" else "LOC-P", "preparation_state": "ready_complete", "specifications": {}}
            for line in invoice["lines"]]}

    async def approve(self, invoice, payload=None, client=None):
        return await (client or self.client).post(f"/api/purchase-invoices/{invoice['id']}/approve-receive", json=payload or self.request_for(invoice))

    async def assert_no_effects(self):
        for collection in [receiving.RECEIPTS, receiving.COST_STATES, "liabilities", "accounting_journal_groups_v2", "accounting_general_ledger_v2", "accounting_audit_log_v2", "general_ledger"]:
            self.assertEqual(await self.db[collection].count_documents({}), 0, collection)

    async def assert_success(self, response, *, total_minor=3000, debit_legs=None):
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["state"], "approved")
        self.assertEqual(result["operation"]["status"], "succeeded")
        group_id = result["txn_group_id"]
        verification = await verify_journal_v2(self.db, user_id="owner", txn_group_id=group_id)
        self.assertTrue(verification["verified"], verification)
        groups = await self.db.accounting_journal_groups_v2.find({}).to_list(20)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["debit_total_minor"], total_minor)
        self.assertEqual(groups[0]["credit_total_minor"], total_minor)
        legs = await self.db.accounting_general_ledger_v2.find({}).to_list(20)
        if debit_legs:
            self.assertEqual({leg["sub_account"]: leg["amount_minor"] for leg in legs if leg["side"] == "debit"}, debit_legs)
        self.assertEqual(await self.db.liabilities.count_documents({}), 1)
        liability = await self.db.liabilities.find_one({})
        self.assertEqual(liability["accounting_txn_group_id"], group_id)
        self.assertEqual(liability["expected_amount"], total_minor / 100)
        self.assertEqual(liability["paid_amount"], 0)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        return result

    async def upload_evidence(self):
        response = await self.client.post("/api/purchase-invoices/tax-evidence", data={"supplier_counterparty_id": "supplier", "invoice_number": "SYNTHETIC-001"}, files={"file": ("synthetic.pdf", b"%PDF-1.4\nSynthetic invoice evidence only\n", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["file_id"]

    async def test_draft_has_zero_financial_stock_and_supplier_statement_effects(self):
        invoice = await self.draft()
        self.assertEqual((invoice["schema_version"], invoice["state"], invoice["revision"]), ("g47-v1", "draft", 1))
        self.assertTrue(invoice["approval_operation_id"])
        self.assertEqual(invoice["lines"][1]["sku"], "SHARED-SKU")
        await self.assert_no_effects()
        self.assertEqual(await self.db[receiving.OPERATIONS].count_documents({}), 0)
        for location in await self.db.warehouse_locations.find({}).to_list(10):
            self.assertEqual(location["occupancy"], {"items": [], "total_quantity": 0})
        statement = await self.client.get("/api/purchase-invoices/supplier/supplier/statement")
        self.assertEqual(statement.status_code, 200, statement.text)
        self.assertNotIn(invoice["id"], str(statement.json()))

    async def test_full_approval_exact_variant_ssot_sealed_journal_liability_and_cost(self):
        invoice = await self.draft()
        result = await self.assert_success(await self.approve(invoice), debit_legs={"inventory": 3000})
        receipts = await self.db[receiving.RECEIPTS].find({}).to_list(10)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(set(result["receipt_ids"]), {row["id"] for row in receipts})
        product = next(row for row in receipts if row["item_type"] == "product")
        self.assertEqual((product["product_id"], product["variant_id"], product["sku"]), ("product-A", "variant-A", "SHARED-SKU"))
        component = await self.db.mezan_cost_resources_v2.find_one({"id": "component-A"})
        self.assertEqual(component["unit_cost"], 2.5)
        self.assertEqual(component["initial_unit_cost"], 99)
        self.assertTrue(component["cost_authoritative"])
        self.assertEqual(component["cost_source"], "purchase_invoice")
        self.assertEqual(component["cost_policy_version"], receiving.COST_POLICY)
        for suffix, quantity in [("component", 4), ("product", 2)]:
            location = await self.db.warehouse_locations.find_one({"id": "location-" + suffix})
            self.assertEqual(location["occupancy"]["total_quantity"], quantity)
            self.assertEqual(len(location["occupancy"]["items"]), 1)
            self.assertFalse(location["occupancy"]["items"][0].get("components_consumed"))
        states = await self.db[receiving.COST_STATES].find({}).to_list(10)
        self.assertEqual(len(states), 2)
        self.assertTrue(all("quantity" not in state and "on_hand" not in state for state in states))

    async def test_duplicate_retry_returns_same_operation_receipts_and_journal(self):
        invoice = await self.draft()
        first = await self.assert_success(await self.approve(invoice))
        second = await self.assert_success(await self.approve(invoice))
        self.assertEqual(first["txn_group_id"], second["txn_group_id"])
        self.assertEqual(first["receipt_ids"], second["receipt_ids"])
        self.assertEqual(await self.db[receiving.RECEIPTS].count_documents({}), 2)

    async def test_concurrent_identical_approval_commits_once(self):
        invoice = await self.draft()
        responses = await asyncio.gather(self.approve(invoice), self.approve(invoice))
        results = [await self.assert_success(response) for response in responses]
        self.assertEqual(results[0]["txn_group_id"], results[1]["txn_group_id"])
        self.assertEqual(results[0]["receipt_ids"], results[1]["receipt_ids"])
        self.assertEqual(await self.db[receiving.RECEIPTS].count_documents({}), 2)

    async def test_lost_response_after_real_commit_retries_without_duplicate_effects(self):
        invoice = await self.draft()
        async with httpx.AsyncClient(transport=LoseCommittedResponse(self.app), base_url="http://synthetic.local") as lossy:
            with self.assertRaises(httpx.ReadError):
                await self.approve(invoice, client=lossy)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 1)
        await self.assert_success(await self.approve(invoice))
        self.assertEqual(await self.db[receiving.RECEIPTS].count_documents({}), 2)

    async def test_fault_after_actual_placement_rolls_back_all_effects_then_same_operation_retries(self):
        invoice = await self.draft()
        original = receiving.place_inventory_receipt
        placements = []

        async def fail_after_real_placement(*args, **kwargs):
            result = await original(*args, **kwargs)
            placements.append(kwargs["receipt_id"])
            raise HTTPException(503, detail={"code": "synthetic_after_placement"})

        with patch.object(receiving, "place_inventory_receipt", fail_after_real_placement):
            response = await self.approve(invoice)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(len(placements), 1)
        await self.assert_no_effects()
        location = await self.db.warehouse_locations.find_one({"id": "location-component"})
        self.assertEqual(location["occupancy"], {"items": [], "total_quantity": 0})
        operation = await self.db[receiving.OPERATIONS].find_one({})
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["id"], invoice["approval_operation_id"])
        await self.assert_success(await self.approve(invoice))
        self.assertEqual(await self.db[receiving.OPERATIONS].count_documents({}), 1)

    async def test_nondeductible_tax_is_capitalized_and_not_input_vat(self):
        invoice = await self.draft(self.payload(tax_amount=4.5, tax_treatment="non_deductible"))
        await self.assert_success(await self.approve(invoice), total_minor=3450, debit_legs={"inventory": 3450})
        receipts = await self.db[receiving.RECEIPTS].find({}).to_list(10)
        byline = {row["line_id"]: row for row in receipts}
        self.assertEqual(byline["line-component"]["total_purchase_cost"], "11.50")
        self.assertEqual(byline["line-component"]["average_cost_after"], "2.875000")
        self.assertEqual(byline["line-product"]["total_purchase_cost"], "23.00")

    async def test_deductible_tax_uses_preserved_bound_evidence_and_separate_vat(self):
        evidence = await self.upload_evidence()
        invoice = await self.draft(self.payload(tax_amount=4.5, tax_treatment="deductible", tax_evidence_ref=evidence, tax_evidence_verified=True))
        await self.assert_success(await self.approve(invoice), total_minor=3450, debit_legs={"inventory": 3000, "input_vat": 450})
        receipt = await self.db[receiving.RECEIPTS].find_one({"line_id": "line-component"})
        self.assertEqual(receipt["average_cost_after"], "2.500000")
        leg = await self.db.accounting_general_ledger_v2.find_one({"sub_account": "input_vat"})
        self.assertEqual(leg["metadata"]["evidence_ref"], evidence)

    async def test_tampered_preserved_tax_source_fails_closed(self):
        evidence = await self.upload_evidence()
        invoice = await self.draft(self.payload(tax_amount=4.5, tax_treatment="deductible", tax_evidence_ref=evidence, tax_evidence_verified=True))
        await self.db.accounting_source_files.update_one({"file_id": evidence}, {"$set": {"content": b"tampered synthetic content"}})
        response = await self.approve(invoice)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "purchase_tax_invoice_evidence_required")
        await self.assert_no_effects()

    async def test_positive_prior_stock_without_approved_cost_fails_closed(self):
        item = {"item_type": "stock_component", "resource_id": "component-A", "quantity": 5,
                "configuration_key": receiving.stable_id("component-configuration", "component-A"), "receipt_id": "synthetic-prior-stock"}
        await self.db.warehouse_locations.update_one({"id": "location-component"}, {"$set": {"occupancy": {"items": [item], "total_quantity": 5}}})
        invoice = await self.draft()
        response = await self.approve(invoice)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "inventory_cost_reconciliation_required")
        await self.assert_no_effects()
        location = await self.db.warehouse_locations.find_one({"id": "location-component"})
        self.assertEqual(location["occupancy"]["items"], [item])
        self.assertEqual(location["occupancy"]["total_quantity"], 5)

    async def test_full_quantity_and_stable_identity_are_required_before_reservation(self):
        invoice = await self.draft()
        for mutation in ["partial", "omitted", "wrong_operation", "stale_revision"]:
            with self.subTest(mutation=mutation):
                request = self.request_for(invoice)
                if mutation == "partial":
                    request["receipts"][0]["quantity"] -= 1
                elif mutation == "omitted":
                    request["receipts"].pop()
                elif mutation == "wrong_operation":
                    request["operation_id"] = "client-invented-retry-identity"
                else:
                    request["expected_revision"] += 1
                response = await self.approve(invoice, request)
                self.assertIn(response.status_code, {409, 422}, response.text)
                await self.assert_no_effects()
                self.assertEqual(await self.db[receiving.OPERATIONS].count_documents({}), 0)

    async def test_missing_variant_service_and_foreign_mapping_are_rejected(self):
        for mutation in ["missing_variant", "service"]:
            with self.subTest(mutation=mutation):
                payload = self.payload()
                if mutation == "missing_variant":
                    payload["lines"][1].pop("variant_id")
                else:
                    payload["lines"][0]["resource_id"] = "service-A"
                response = await self.client.post("/api/purchase-invoices", json=payload)
                self.assertIn(response.status_code, {409, 422}, response.text)
                await self.assert_no_effects()
        invoice = await self.draft(self.payload(inventory_account_id="unapproved-account"))
        response = await self.approve(invoice)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "purchase_account_mapping_required")
        await self.assert_no_effects()

    async def test_actor_owner_reassignment_between_route_checks_aborts_each_write(self):
        invoice = await self.draft()
        original = invoice_routes.actor_scope
        for action in ["create", "update", "delete", "tax_evidence"]:
            with self.subTest(action=action):
                await self.db.users.replace_one({"id": "owner"}, dict(self.actor))
                before = await self.db.purchase_invoices.find_one({"id": invoice["id"]})
                checks = []

                async def move_actor_after_outer_check(db, user, permission="accounting.inventory.view"):
                    result = await original(db, user, permission)
                    checks.append(result[1])
                    if len(checks) == 1:
                        # A real concurrent administrative change, before the
                        # write transaction starts; no mocked permission result.
                        await self.db.users.update_one({"id": "owner"}, {"$set": {
                            "role": "employee", "created_by": "other-owner",
                            "accounting_permissions": sorted(ACCOUNTING_PERMISSION_KEYS)}})
                    return result

                with patch.object(invoice_routes, "actor_scope", move_actor_after_outer_check):
                    if action == "create":
                        response = await self.client.post("/api/purchase-invoices", json=self.payload())
                    elif action == "update":
                        response = await self.client.put(f"/api/purchase-invoices/{invoice['id']}", json=self.payload(expected_revision=1, notes="Must not save"))
                    elif action == "delete":
                        response = await self.client.delete(f"/api/purchase-invoices/{invoice['id']}?expected_revision=1")
                    else:
                        response = await self.client.post("/api/purchase-invoices/tax-evidence", data={"supplier_counterparty_id": "supplier", "invoice_number": "SYNTHETIC-001"}, files={"file": ("synthetic.pdf", b"%PDF-1.4\nSynthetic\n", "application/pdf")})
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["detail"]["code"], "purchase_actor_scope_changed")
                self.assertEqual(checks, ["owner", "other-owner"])
                self.assertEqual(await self.db.purchase_invoices.find_one({"id": invoice["id"]}), before)
                self.assertEqual(await self.db.purchase_invoices.count_documents({}), 1)
                self.assertEqual(await self.db.accounting_source_files.count_documents({}), 0)
                self.assertEqual(await self.db.mz2_purchase_tax_evidence.count_documents({}), 0)
                await self.assert_no_effects()

    async def test_weighted_average_uses_actual_remaining_quantity_and_authoritative_prior_cost(self):
        identity = {"item_type": "stock_component", "resource_id": "component-A"}
        item = {**identity, "quantity": 2, "configuration_key": receiving.stable_id("component-configuration", "component-A"), "receipt_id": "synthetic-approved-prior-stock"}
        await self.db.warehouse_locations.update_one({"id": "location-component"}, {"$set": {"occupancy": {"items": [item], "total_quantity": 2}}})
        await self.db[receiving.COST_STATES].insert_one({"_id": receiving.identity_key("owner", identity), "user_id": "owner", "average_cost": "5.000000", "authoritative": True, "cost_policy_version": receiving.COST_POLICY, "inventory_identity": identity})
        invoice = await self.draft()
        await self.assert_success(await self.approve(invoice))
        receipt = await self.db[receiving.RECEIPTS].find_one({"line_id": "line-component"})
        self.assertEqual(receipt["on_hand_before"], 2)
        self.assertEqual(receipt["average_cost_before"], "5.000000")
        self.assertEqual(receipt["average_cost_after"], "3.333333")
        location = await self.db.warehouse_locations.find_one({"id": "location-component"})
        self.assertEqual(location["occupancy"]["total_quantity"], 6)

    async def test_nondeductible_fractional_cent_allocation_preserves_total(self):
        payload = self.payload(tax_amount=0.01, tax_treatment="non_deductible")
        for line in payload["lines"]:
            line.update(quantity=1, unit_cost=1)
        invoice = await self.draft(payload)
        await self.assert_success(await self.approve(invoice), total_minor=201, debit_legs={"inventory": 201})
        receipts = await self.db[receiving.RECEIPTS].find({}).sort("line_id", 1).to_list(10)
        self.assertEqual([row["total_purchase_cost"] for row in receipts], ["1.01", "1.00"])

    async def test_real_standalone_rejects_before_any_draft_or_posting_write(self):
        uri = local_uri("MZ2_TEST_STANDALONE_URI", "mongodb://127.0.0.1:28148/?directConnection=true")
        mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        name = "g47_purchase_" + uuid.uuid4().hex
        try:
            self.assertFalse((await mongo.admin.command("hello")).get("setName"))
            db = mongo[name]
            await db.users.insert_one(dict(self.actor))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db, self.actor)), base_url="http://synthetic.local") as client:
                response = await client.post("/api/purchase-invoices", json=self.payload())
            self.assertEqual(response.status_code, 503, response.text)
            self.assertIn("accounting_requires_transactional_replica_set", response.text)
            for collection in ["purchase_invoices", receiving.RECEIPTS, "liabilities", "accounting_journal_groups_v2", "mz2_atomic_owners"]:
                self.assertEqual(await db[collection].count_documents({}), 0, collection)
        finally:
            self.assertTrue(name.startswith("g47_purchase_"))
            await mongo.drop_database(name)
            mongo.close()


if __name__ == "__main__":
    unittest.main()
