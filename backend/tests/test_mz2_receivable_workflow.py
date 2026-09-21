"""ASGI + actual ledger/core with a dedicated real Mongo replica set."""
import asyncio
import copy
import os
from uuid import uuid4
import unittest
from unittest.mock import patch

from fastapi import FastAPI, APIRouter, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_receivable_routes import install_accounting_receivable_routes
from accounting_receivable_service import OPERATION, prepare, execute
from accounting_sales_tax_service import save_policy
from bnpl.ledger_bridge import post_bnpl_sale_to_ledger, post_bnpl_refund_to_ledger
from accounting_recognition_evidence import EvidenceError

BASE = "/accounting-module"
WHEN = "2020-01-02T12:00:00Z"


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ["MZ2_TEST_MONGO_URI"]
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["mz2_atomic_test_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        # No production connection fallback; a unique fixture DB per test.

        self.actor = "owner"
        await self.db.users.insert_many([
            {"id": "owner", "role": "owner", "name": "Test accountant"},
            {"id": "viewer", "role": "viewer", "created_by": "owner",
             "accounting_permissions": ["accounting.settlements.view"]},
            {"id": "other", "role": "owner"},
        ])
        await self.db.settings.insert_one({"user_id": "owner", "mezan2_financial_cutover": {
            "operation_id": OPERATION, "cutover_at": "2020-01-01T00:00:00Z",
        }})
        await self.configure()
        self.app = FastAPI()
        router = APIRouter()
        async def authenticated_actor():
            return {"id": self.actor}
        install_accounting_receivable_routes(router, self.db, authenticated_actor)
        self.app.include_router(router)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://local")
        await self.source()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.mongo.close()

    async def configure(self, rate="15", revision=0, effective_at="2020-01-01T00:00:00Z"):
        return await save_policy(self.db, owner="owner", actor_id="owner", rate=rate,
                                 effective_at=effective_at, revision=revision, reason="Synthetic local test")

    async def source(self, provider="tamara"):
        number = "SYN-MANUAL-TAX-" + provider
        payment = {"id": "local-" + provider, "user_id": "owner", "provider": provider,
                   "provider_id": "SYN-CAPTURE-" + provider, "order_reference_id": number,
                   "amount": "115.00", "captured_amount": "115.00", "currency": "SAR",
                   "status": "captured", "source": "synthetic_import",
                   "captured_at": "2020-01-02T11:00:00Z"}
        await self.db.payment_transactions.insert_one(payment)
        await self.db.orders_db.insert_one({
            "id": number, "user_id": "owner", "order_number": number,
            "total_amount": "115.00", "currency": "SAR", "payment_method": provider,
            "order_status": "completed", "delivered_at": WHEN,
            "order_created_at": "2020-01-02T10:00:00Z", "tax_percent": "8", "tax_amount": "8.52",
        })
        return payment

    def payload(self, provider="tamara", refund_id=None):
        return {"provider": provider, "payment_id": "SYN-CAPTURE-" + provider, "refund_id": refund_id}

    async def preview_and_post(self, provider="tamara", refund_id=None):
        body = self.payload(provider, refund_id)
        preview = await self.client.post(BASE + "/receivables/preview", json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["state"], "eligible", preview.text)
        result = await self.client.post(BASE + "/receivables/execute",
            json={**body, "preview_hash": preview.json()["preview_hash"]})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def count_writes(self):
        return {name: await self.db[name].count_documents({}) for name in (
            "general_ledger", "accounting_audit_log", "mz2_recognition_events",
            "mz2_recognition_locks", "mz2_sales_tax_policies",
        )}

    async def test_three_providers_route_to_ledger_and_duplicate_ingress(self):
        for provider in ("tamara", "tabby", "emkan"):
            if provider != "tamara":
                await self.source(provider)
            result = await self.preview_and_post(provider)
            legs = await self.db.general_ledger.find({"txn_group_id": result["txn_group_id"]}).to_list(10)
            self.assertEqual(len(legs), 3)
            self.assertEqual({r["entity_type"]: r["amount"] for r in legs},
                             {"payment_gateway": 115, "revenue": 100, "tax": 15})
            self.assertEqual(sum(r["amount"] for r in legs if r["side"] == "debit"),
                             sum(r["amount"] for r in legs if r["side"] == "credit"))
            payment = await self.db.payment_transactions.find_one({"provider": provider})
            payment.update(source="webhook", id="different-local-id")
            before = await self.count_writes()
            again = await post_bnpl_sale_to_ledger(self.db, user_id="owner", txn=payment)
            self.assertEqual(again["txn_group_id"], result["txn_group_id"])
            self.assertEqual(again["state"], "already_posted")
            self.assertEqual(await self.count_writes(), before)

    async def test_tax_change_stale_preview_and_frozen_posting(self):
        proposal = await prepare(self.db, owner="owner", **self.payload())
        await self.configure("20", 1)
        before = await self.count_writes()
        with self.assertRaisesRegex(EvidenceError, "preview_changed"):
            await execute(self.db, owner="owner", actor_id="owner", actor_name="owner",
                          preview_hash=proposal["preview_hash"], **self.payload())
        self.assertEqual(before, await self.count_writes())
        result = await self.preview_and_post()
        self.assertEqual(result["tax"]["tax"], "19.17")
        await self.configure("0", 2)
        frozen = await prepare(self.db, owner="owner", **self.payload())
        self.assertEqual(frozen["tax"]["tax"], "19.17")
        self.assertEqual(frozen["txn_group_id"], result["txn_group_id"])

    async def test_partial_full_refund_uses_original_rate(self):
        await self.preview_and_post()
        await self.configure("20", 1)
        for index in (1, 2):
            rid = "SYN-REFUND-" + str(index)
            await self.db.payment_refunds.insert_one({
                "id": rid, "user_id": "owner", "provider": "tamara", "provider_refund_id": rid,
                "provider_payment_id": "SYN-CAPTURE-tamara", "currency": "SAR",
                "amount": "57.50", "status": "completed", "refunded_at": "2020-01-03T12:00:00Z",
                "source": "synthetic_refund",
            })
            result = await self.preview_and_post(refund_id=rid)
            self.assertEqual((result["tax"]["net"], result["tax"]["tax"]), ("50.00", "7.50"))
            before = await self.count_writes()
            row = await self.db.payment_refunds.find_one({"provider_refund_id": rid})
            again = await post_bnpl_refund_to_ledger(self.db, user_id="owner", refund=row)
            self.assertEqual(again["state"], "already_posted")
            self.assertEqual(await self.count_writes(), before)
        legs = await self.db.general_ledger.find({}).to_list(20)
        for entity in ("payment_gateway", "revenue", "tax"):
            self.assertEqual(sum(r["amount"] * (1 if r["side"] == "debit" else -1)
                                 for r in legs if r["entity_type"] == entity), 0)

    async def test_missing_zero_and_conflicting_order_no_writes(self):
        await self.db.mz2_sales_tax_policies.delete_many({})
        before = await self.count_writes()
        result = await self.client.post(BASE + "/receivables/preview", json=self.payload())
        self.assertEqual(result.json()["state"], "rejected")
        self.assertEqual(before, await self.count_writes())
        await self.configure("0")
        result = await self.preview_and_post()
        self.assertEqual(result["tax"]["tax"], "0.00")
        self.assertEqual(await self.db.general_ledger.count_documents({}), 2)
        await self.source("tabby")
        await self.db.orders_db.update_one({"payment_method": "tabby"}, {"$set": {"total_amount": "116"}})
        before = await self.count_writes()
        result = await self.client.post(BASE + "/receivables/preview", json=self.payload("tabby"))
        self.assertIn("order_principal_conflict", result.json()["reasons"])
        self.assertEqual(before, await self.count_writes())

    async def test_viewer_and_other_owner_rejected_without_write(self):
        proposal = await prepare(self.db, owner="owner", **self.payload())
        self.actor = "viewer"
        before = await self.count_writes()
        self.assertEqual((await self.client.get(BASE + "/sales-tax")).status_code, 200)
        self.assertEqual((await self.client.post(BASE + "/receivables/preview", json=self.payload())).status_code, 200)
        denied = await self.client.post(BASE + "/receivables/execute",
            json={**self.payload(), "preview_hash": proposal["preview_hash"]})
        self.assertEqual(denied.status_code, 403)
        denied = await self.client.put(BASE + "/sales-tax", json={
            "rate": "20", "effective_at": WHEN, "revision": 1, "reason": "denied",
        })
        self.assertEqual(denied.status_code, 403)
        self.actor = "other"
        other = await self.client.post(BASE + "/receivables/preview", json=self.payload())
        self.assertIn("payment_evidence_missing", other.json()["reasons"])
        self.assertEqual(await self.count_writes(), before)

    async def test_conflicting_ingress_and_prior_other_journal(self):
        await self.preview_and_post()
        payment = await self.db.payment_transactions.find_one({"provider": "tamara"})
        payment["amount"] = "114.00"
        before = await self.count_writes()
        with self.assertRaises(EvidenceError):
            await post_bnpl_sale_to_ledger(self.db, user_id="owner", txn=payment)
        self.assertEqual(await self.count_writes(), before)
        await self.source("tabby")
        await self.db.general_ledger.insert_one({
            "user_id": "owner", "status": "posted", "entry_type": "sale",
            "metadata": {"order_reference_id": "SYN-MANUAL-TAX-tabby"},
        })
        before = await self.count_writes()
        with self.assertRaisesRegex(EvidenceError, "existing_journal"):
            await prepare(self.db, owner="owner", **self.payload("tabby"))
        self.assertEqual(await self.count_writes(), before)

    async def test_interrupted_post_aborts_and_retry_completes(self):
        import ledger_core
        original = ledger_core.post_ledger_entry
        async def after_first_leg(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("injected write interruption")
        with patch.object(ledger_core, "post_ledger_entry", side_effect=after_first_leg):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                await self.preview_and_post()
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertEqual(await self.db.accounting_audit_log.count_documents({}), 0)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}), 0)
        await self.preview_and_post()
        self.assertEqual(await self.db.general_ledger.count_documents({}), 3)

    async def test_two_ingress_requests_one_group(self):
        results = await asyncio.gather(*[
            execute(self.db, owner="owner", actor_id="owner", actor_name="test", **self.payload())
            for _ in range(2)
        ], return_exceptions=True)
        self.assertTrue(any(isinstance(r, dict) and r["state"] == "posted" for r in results))
        self.assertEqual(await self.db.general_ledger.count_documents({}), 3)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}), 1)

    async def test_owner_outside_mezan2_retains_existing_bridge_behavior(self):
        result = await post_bnpl_sale_to_ledger(self.db, user_id="legacy-test-owner", txn={
            "provider": "tabby", "provider_id": "SYN-LEGACY", "amount": 115,
            "status": "closed", "created_at_provider": "2020-01-02T10:00:00Z",
        })
        self.assertTrue(result["ok"])
        legs = await self.db.general_ledger.find({"user_id": "legacy-test-owner"}).to_list(10)
        self.assertEqual(len(legs), 2)
        self.assertEqual({row["entity_type"] for row in legs}, {"revenue", "payment_gateway"})

    async def test_missing_dates_identity_currency_and_status_reject_no_write(self):
        for field, value in (("captured_at", None), ("provider_id", ""),
                             ("currency", "USD"), ("status", "authorized")):
            payment = await self.db.payment_transactions.find_one({"provider": "tamara"})
            original = copy.deepcopy(payment)
            await self.db.payment_transactions.update_one({"_id": payment["_id"]}, {"$set": {field: value}})
            before = await self.count_writes()
            payload = self.payload()
            if field == "provider_id":
                payload["payment_id"] = ""
            with self.assertRaises((EvidenceError, ValueError)):
                await prepare(self.db, owner="owner", **payload)
            self.assertEqual(await self.count_writes(), before)
            await self.db.payment_transactions.replace_one({"_id": original["_id"]}, original)

    async def test_actual_tabby_normalizer_shape_without_fixture_only_fields(self):
        from bnpl.sync_service import _normalise_payment
        payment = _normalise_payment({
            "id": "SYN-CAPTURE-tabby", "amount": "115.00", "currency": "SAR", "status": "CLOSED",
            "order": {"reference_id": "SYN-MANUAL-TAX-tabby"},
            "captures": [{"id": "SYN-C1", "amount": "50.00", "created_at": "2020-01-02T11:00:00Z"},
                         {"id": "SYN-C2", "amount": "65.00", "created_at": "2020-01-02T13:00:00Z"}],
        }, "owner")
        self.assertNotIn("captured_at", payment)
        self.assertNotIn("source", payment)
        await self.db.payment_transactions.insert_one(payment)
        await self.db.orders_db.insert_one({
            "id": "SYN-MANUAL-TAX-tabby", "user_id": "owner", "order_number": "SYN-MANUAL-TAX-tabby",
            "total_amount": "115.00", "currency": "SAR", "payment_method": "tabby",
            "order_status": "completed", "completed_at": WHEN, "tax_percent": "8",
        })
        result = await self.preview_and_post("tabby")
        self.assertEqual(result["event"]["recognized_at"], "2020-01-02T13:00:00+00:00")
        self.assertEqual(result["tax"]["tax"], "15.00")
        self.assertEqual(result["event"]["source"]["payment_source"], "payment_transactions.raw_payload")

    async def test_new_sale_to_existing_settlement_service_and_balance_guard(self):
        from accounting_settlement_service import post_reviewed_settlement
        from ledger_core import compute_balance
        await self.db.accounts.insert_one({"id": "SYN-BANK", "user_id": "owner",
                                           "account_type": "bank", "name": "Synthetic bank"})
        draft = {"id": "SYN-NEW-DRAFT", "status": "reviewed", "provider": "tamara",
                 "bank_account_id": "SYN-BANK", "statement_reference": "SYN-NEW-SETTLEMENT",
                 "source_file_id": "SYN-STATEMENT", "source_file_hash": "synthetic-local-test",
                 "idempotency_key": "SYN-SETTLEMENT-KEY", "review_reasons": [],
                 "amounts": {"gross_sales": 115, "commission": 3, "commission_vat": 0.45,
                             "settlement_fee": 1, "settlement_fee_vat": 0.15, "reported_net": 110.40}}
        before = await self.count_writes()
        with self.assertRaises(HTTPException):
            await post_reviewed_settlement(self.db, owner_id="owner", actor={"id": "owner"}, draft=draft)
        self.assertEqual(await self.count_writes(), before)
        await self.preview_and_post()
        balance = await compute_balance(self.db, user_id="owner", entity_type="payment_gateway",
                                        entity_id="tamara", sub_account="receivable")
        self.assertEqual(balance["net_balance"], 115)
        await self.configure("20", 1)
        result = await post_reviewed_settlement(self.db, owner_id="owner", actor={"id": "owner"}, draft=draft)
        self.assertEqual(result["debit_total"], result["credit_total"])
        balance = await compute_balance(self.db, user_id="owner", entity_type="payment_gateway",
                                        entity_id="tamara", sub_account="receivable")
        self.assertEqual(balance["net_balance"], 0)
        bank = await compute_balance(self.db, user_id="owner", entity_type="bank", entity_id="SYN-BANK")
        self.assertEqual(bank["net_balance"], 110.40)
        self.assertEqual(result["preview"]["amounts"]["commission_vat"], 0.45)
        before = await self.count_writes()
        with self.assertRaises(HTTPException):
            await post_reviewed_settlement(self.db, owner_id="owner", actor={"id": "owner"}, draft=draft)
        self.assertEqual(await self.count_writes(), before)


if __name__ == "__main__":
    unittest.main()

