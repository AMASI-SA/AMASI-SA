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
        # This successful-write fixture has explicit owner write-control state.
        await self.db.mz2_atomic_owners.insert_one({
            "_id": "owner", "revision": 0, "writes_paused": False, "control_revision": 0,
        })

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

    async def test_creation_fence_rejects_api_ingress_and_explicit_replay_without_financial_effects(self):
        from accounting_ingress import replay_pending
        for provider in ("tamara", "tabby", "emkan"):
            if provider != "tamara":
                await self.source(provider)
            payment = await self.db.payment_transactions.find_one({"provider": provider})
            for created, code in (
                (None, "order_creation_timestamp_required"),
                ("", "order_creation_timestamp_required"),
                ("not-a-date", "order_creation_timestamp_invalid"),
                ("2019-12-31T23:59:59Z", "pre_cutover_order"),
            ):
                with self.subTest(provider=provider, created=created):
                    await self.db.orders_db.update_one({"payment_method": provider},
                        {"$set": {"order_created_at": created}})
                    before = await self.count_writes()
                    preview = await self.client.post(BASE + "/receivables/preview", json=self.payload(provider))
                    self.assertEqual(preview.json(), {"state": "rejected", "reasons": [code]})
                    posted = await self.client.post(BASE + "/receivables/execute", json={
                        **self.payload(provider), "preview_hash": "0" * 64})
                    self.assertEqual(posted.status_code, 409, posted.text)
                    self.assertEqual(posted.json()["detail"]["code"], code)
                    with self.assertRaisesRegex(EvidenceError, code):
                        await post_bnpl_sale_to_ledger(self.db, user_id="owner", txn=payment)
                    replay = await replay_pending(self.db, "owner")
                    self.assertEqual(replay["processed"], 0)
                    self.assertGreater(replay["pending_events"], 0)
                    self.assertEqual(await self.count_writes(), before)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def test_creation_at_cutoff_posts_and_changed_creation_invalidates_prior_preview(self):
        proposal = await prepare(self.db, owner="owner", **self.payload())
        await self.db.orders_db.update_one({"payment_method": "tamara"},
            {"$set": {"order_created_at": "2019-12-31T23:59:59Z"}})
        before = await self.count_writes()
        with self.assertRaisesRegex(EvidenceError, "pre_cutover_order"):
            await execute(self.db, owner="owner", actor_id="owner", actor_name="owner",
                preview_hash=proposal["preview_hash"], **self.payload())
        self.assertEqual(await self.count_writes(), before)
        await self.db.orders_db.update_one({"payment_method": "tamara"},
            {"$set": {"order_created_at": "2020-01-01T03:00:00+03:00"}})
        result = await self.preview_and_post()
        self.assertTrue(result["txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), 3)

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
        from mz2_report_fixtures import provision_write_opening
        await provision_write_opening(self.db)
        sale = await self.preview_and_post()
        await self.configure("20", 1)
        for index in (1, 2):
            rid = "SYN-REFUND-" + str(index)
            await self.db.payment_refunds.insert_one({
                "id": rid, "user_id": "owner", "provider": "tamara", "provider_refund_id": rid,
                "provider_payment_id": "SYN-CAPTURE-tamara", "currency": "SAR",
                "amount": "57.50", "status": "completed", "refunded_at": "2020-01-03T12:00:00Z",
                "source": "synthetic_refund",
            })
            rejected = await self.client.post(BASE + '/receivables/preview', json=self.payload(refund_id=rid))
            self.assertEqual(rejected.json()['state'], 'rejected')
            root=BASE+'/customer-refunds'
            case=await self.client.post(root,json=dict(original_key=sale['event_key'],case_reference=rid,
                amount='57.50',recognized_at='2020-01-03T12:00:00Z',reason='Synthetic daily refund'))
            self.assertEqual(case.status_code,200,case.text)
            confirmed=await self.client.post(root+'/'+case.json()['id']+'/recognize',json=dict(amount='57.50',
                recognized_at='2020-01-03T12:00:00Z',reason='Synthetic confirmed right',evidence_ref=rid))
            self.assertEqual(confirmed.status_code,200,confirmed.text)
            payment=await self.client.post(root+'/bank-payments',json=dict(original_key=sale['event_key'],case_reference=rid,
                amount='57.50',paid_at='2020-01-03T12:00:00Z',execution_channel='tamara',bank_reference=rid,provider_refund_id=rid))
            self.assertEqual(payment.status_code,200,payment.text)
            approved=await self.client.post(root+'/bank-payments/'+payment.json()['id']+'/approve')
            self.assertEqual(approved.status_code,200,approved.text)
            result=approved.json()
            self.assertNotIn('tax',result)
            self.assertEqual((confirmed.json()["tax"]["net"], confirmed.json()["tax"]["tax"]), ("50.00", "7.50"))
            before = await self.count_writes()
            row = await self.db.payment_refunds.find_one({"provider_refund_id": rid})
            again = await post_bnpl_refund_to_ledger(self.db, user_id="owner", refund=row)
            self.assertEqual(again["reason"], "daily_movement_approval_required")
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
        # Permit this synthetic writer without enrolling it in Mezan 2 routing.
        await self.db.mz2_atomic_owners.insert_one({
            "_id": "legacy-test-owner", "revision": 0,
            "writes_paused": False, "control_revision": 0,
        })
        from accounting_receivable_service import managed_owner
        self.assertFalse(await managed_owner(self.db, "legacy-test-owner"))
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

    async def test_actual_tabby_normalizer_with_documented_order_creation(self):
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
            "order_created_at": "2020-01-02T10:00:00Z",
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
        from mz2_report_fixtures import provision_write_opening
        await provision_write_opening(self.db, bank_zero_ids=('SYN-BANK',))
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


class PublicAccountingErrorBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Exercise serialized API responses without any database or provider access."""

    async def asyncSetUp(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from accounting_shipping_p02 import install_shipping_p02_routes, ShippingAccountingError
        from accounting_shipping_settlements import install_shipping_settlement_routes
        from accounting_sales_tax import TaxError

        self.owner = {"id": "synthetic-security-owner", "role": "owner"}
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=self.owner)))
        app, router = FastAPI(), APIRouter()

        async def authenticated_owner():
            return self.owner

        install_accounting_receivable_routes(router, db, authenticated_owner)
        install_shipping_p02_routes(router, db, authenticated_owner)
        install_shipping_settlement_routes(router, db, authenticated_owner)
        app.include_router(router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://local")
        recognition = {"provider": "tamara", "payment_id": "synthetic-payment"}
        settlement = {
            "movement_id": "synthetic-movement", "counterparty_type": "courier",
            "counterparty_id": "synthetic-courier", "settlement_type": "fee_payment",
            "reason": "Synthetic security test",
        }
        self.cases = [
            ("PUT", "/sales-tax", "accounting_receivable_routes.save_policy",
             {"rate": "15", "effective_at": WHEN, "revision": 0, "reason": "Synthetic test"},
             (TaxError,), "invalid_decimal", 409),
            ("POST", "/receivables/preview", "accounting_receivable_routes.prepare",
             recognition, (EvidenceError, TaxError), "payment_evidence_missing", 200),
            ("POST", "/receivables/execute", "accounting_receivable_routes.execute",
             {**recognition, "preview_hash": "a" * 64},
             (EvidenceError, TaxError), "preview_changed_review_again", 409),
            ("GET", "/shipping-p02/courier-fee/synthetic-evidence/preview",
             "accounting_shipping_p02.prepare_courier_fee", None,
             (ShippingAccountingError, TaxError), "order_evidence_missing", 200),
            ("POST", "/shipping-p02/courier-fee", "accounting_shipping_p02.post_courier_fee",
             {"evidence_id": "synthetic-evidence"}, (ShippingAccountingError,),
             "shipping_event_source_conflict", 409),
            ("GET", "/shipping-p02/store-driver-cod/synthetic-assignment/preview",
             "accounting_shipping_p02.prepare_store_driver_cod", None,
             (ShippingAccountingError, TaxError), "store_driver_collection_missing", 200),
            ("POST", "/shipping-p02/store-driver-cod",
             "accounting_shipping_p02.post_store_driver_cod",
             {"assignment_id": "synthetic-assignment"}, (ShippingAccountingError, TaxError),
             "cod_collection_order_amount_conflict", 409),
            ("POST", "/shipping-p02/settlements/preview",
             "accounting_shipping_settlements.prepare_shipping_settlement", settlement,
             (ShippingAccountingError,), "daily_movement_not_found", 200),
            ("POST", "/shipping-p02/settlements/post",
             "accounting_shipping_settlements.post_shipping_settlement", settlement,
             (ShippingAccountingError,), "daily_movement_concurrent_consumption", 409),
        ]

    async def asyncTearDown(self):
        await self.client.aclose()

    async def request_case(self, case, *, error=None, result=None):
        from unittest.mock import AsyncMock
        method, path, target, payload, _, _, _ = case
        service = AsyncMock(side_effect=error) if error is not None else AsyncMock(return_value=result)
        with patch(target, service):
            response = await self.client.request(method, BASE + path, json=payload)
        service.assert_awaited_once()
        return response

    def assert_error_response(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        expected = ({"state": "rejected", "reasons": [code]} if status == 200
                    else {"detail": {"code": code, "message": code}})
        self.assertEqual(response.json(), expected)

    async def test_unknown_exception_details_never_reach_public_responses(self):
        for case in self.cases:
            for error_type in case[4]:
                for args in (
                    (),
                    ("unreviewed_internal_failure_code",),
                    ("Traceback: internal-driver at /private/accounting.py; database=internal-db",),
                    ("provider response: internal request metadata",),
                    (case[5] + "\ninternal database details",),
                    (" " + case[5],),
                    (case[5] + "\u0000",),
                    (case[5], "internal provider details"),
                    ({"internal": "database details"},),
                ):
                    with self.subTest(path=case[1], error=error_type.__name__, args=args):
                        response = await self.request_case(case, error=error_type(*args))
                        self.assert_error_response(response, case[6], "accounting_request_rejected")

    async def test_known_business_codes_and_success_responses_are_preserved(self):
        for case in self.cases:
            with self.subTest(path=case[1]):
                response = await self.request_case(case, error=case[4][0](case[5]))
                self.assert_error_response(response, case[6], case[5])
                result = {"state": "eligible", "synthetic": True}
                response = await self.request_case(case, result=result)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), result)

    async def test_exception_stringification_and_string_subclasses_are_not_trusted(self):
        class UntrustedString(str):
            def __str__(self):
                raise AssertionError("must not stringify an exception argument")

        for case in self.cases:
            error_type = case[4][0]

            class InternalError(error_type):
                def __str__(self):
                    raise AssertionError("must not stringify internal exception details")

            with self.subTest(path=case[1]):
                response = await self.request_case(case, error=InternalError(case[5]))
                self.assert_error_response(response, case[6], case[5])
                response = await self.request_case(case, error=InternalError(UntrustedString(case[5])))
                self.assert_error_response(response, case[6], "accounting_request_rejected")

    async def test_existing_http_write_barrier_is_not_reclassified(self):
        for case in self.cases:
            with self.subTest(path=case[1]):
                response = await self.request_case(
                    case, error=HTTPException(423, detail={"code": "mz2_writes_paused"}))
                self.assertEqual(response.status_code, 423)
                self.assertEqual(response.json(), {"detail": {"code": "mz2_writes_paused"}})
