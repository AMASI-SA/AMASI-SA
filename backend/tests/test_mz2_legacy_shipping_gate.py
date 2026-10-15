"""Isolated replica tests for the legacy shipping/P02 financial boundary."""
import asyncio
import copy
import os
from urllib.parse import urlsplit
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

import shipping_accounts
from ledger_double_write import mirror_account_txn_to_ledger


class LegacyShippingGateUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_posted_guard_rejects_before_any_mutator_is_called(self):
        db = SimpleNamespace(
            account_transactions=SimpleNamespace(delete_one=AsyncMock()),
            general_ledger=SimpleNamespace(
                find_one=AsyncMock(return_value={"_id": "synthetic-mirror"}),
                delete_many=AsyncMock(), update_one=AsyncMock(),
            ),
        )
        with patch.object(shipping_accounts, "_recompute_shipping_account_balance", AsyncMock()) as balance:
            for _ in range(2):
                with self.assertRaises(HTTPException) as caught:
                    await shipping_accounts._delete_shipping_payment_tx(
                        db, "synthetic-owner", transaction_id="synthetic-tx", account_id="synthetic-bank")
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail["code"], "shipping_payment_posted_financially_immutable")
        db.account_transactions.delete_one.assert_not_awaited()
        db.general_ledger.delete_many.assert_not_awaited()
        db.general_ledger.update_one.assert_not_awaited()
        balance.assert_not_awaited()

    async def test_unmirrored_delete_never_calls_a_gl_mutator(self):
        db = SimpleNamespace(
            account_transactions=SimpleNamespace(delete_one=AsyncMock()),
            general_ledger=SimpleNamespace(
                find_one=AsyncMock(return_value=None), delete_many=AsyncMock(), update_one=AsyncMock()),
        )
        with patch.object(shipping_accounts, "_recompute_shipping_account_balance", AsyncMock()) as balance:
            await shipping_accounts._delete_shipping_payment_tx(
                db, "synthetic-owner", transaction_id="synthetic-tx", account_id="synthetic-bank")
        db.account_transactions.delete_one.assert_awaited_once()
        balance.assert_awaited_once()
        db.general_ledger.delete_many.assert_not_awaited()
        db.general_ledger.update_one.assert_not_awaited()

    async def test_locked_mirror_returns_before_any_atomic_or_gl_work(self):
        db = SimpleNamespace(settings=SimpleNamespace(find_one=AsyncMock(return_value=None)))
        with patch("accounting_atomic.atomic_owner", AsyncMock()) as atomic:
            result = await mirror_account_txn_to_ledger(
                db, user_id="synthetic-owner", account_id="synthetic-bank",
                account_transaction_id="synthetic-tx", amount=25, direction="out",
                transaction_type="shipping_debt_payment")
        self.assertEqual(result, {"skipped": True, "reason": "p02_shipping_cod_locked"})
        atomic.assert_not_awaited()


class LegacyShippingGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ["MZ2_TEST_MONGO_URI"]
        if urlsplit(uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise AssertionError("This suite requires an explicitly isolated local replica")
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["mz2_legacy_shipping_gate_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.owner = "synthetic-owner"
        await self.db.mz2_atomic_owners.insert_one({
            "_id": self.owner, "revision": 0, "writes_paused": False,
        })
        await self.db.accounts.insert_one({
            "id": "synthetic-bank", "user_id": self.owner,
            "expected_orders_balance": 1000, "current_balance": 1000,
        })
        await self.db.settings.insert_one({
            "user_id": self.owner,
            "mezan2_financial_cutover": {
                "operation_id": "MZ2-FIN-CUTOVER-001", "status": "active",
                "cutover_at": "2026-01-01T00:00:00+00:00",
                "p02_shipping_cod_enabled": False,
                "p02_shipping_cod_activation_ref": "SYNTHETIC-P02-EVIDENCE",
            },
        })

        async def actor(request, db):
            return {"id": request.headers.get("X-Test-Owner", self.owner), "role": "owner"}

        self.auth = patch.object(shipping_accounts, "get_current_user_from_db", actor)
        self.auth.start()
        app = FastAPI()
        app.include_router(shipping_accounts._build_router(self.db))
        self.client = AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://isolated.test")
        self.payload = {
            "amount": 125, "payment_date": "2026-09-29", "invoice_number": "SYNTHETIC-1",
            "paid_from_account_id": "synthetic-bank", "note": "synthetic fixture",
        }

    async def asyncTearDown(self):
        await self.client.aclose()
        self.auth.stop()
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def active(self):
        await self.db.settings.update_one({"user_id": self.owner}, {"$set": {
            "mezan2_financial_cutover.p02_shipping_cod_enabled": True}})

    async def post(self, *, key=None, **changes):
        payload = dict(self.payload, **changes)
        if key is not None:
            payload["idempotency_key"] = key
        return await self.client.post("/shipping-accounts/SyntheticCourier/payments", json=payload)

    async def snapshot(self):
        names = ("shipping_payments", "account_transactions", "accounts", "general_ledger",
                 "mz2_atomic_owners", "accounting_journal_groups_v2", "accounting_general_ledger_v2")
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(100) for name in names}

    async def assert_effects(self, *, payments, movements, legs, balance):
        self.assertEqual(await self.db.shipping_payments.count_documents({}), payments)
        self.assertEqual(await self.db.account_transactions.count_documents({}), movements)
        self.assertEqual(await self.db.general_ledger.count_documents({}), legs)
        account = await self.db.accounts.find_one({"user_id": self.owner})
        self.assertEqual(account["current_balance"], balance)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 0)
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}), 0)

    async def test_locked_add_is_operational_without_financial_mirror(self):
        await self.db.mz2_atomic_owners.delete_one({"_id": self.owner})
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["financial_posting"], "not_posted_p02_locked")
        await self.assert_effects(payments=1, movements=1, legs=0, balance=875)
        self.assertEqual(await self.db.mz2_atomic_owners.count_documents({}), 0)

    async def test_locked_direct_mirror_does_not_create_gl_or_owner_control(self):
        await self.db.mz2_atomic_owners.delete_one({"_id": self.owner})
        result = await mirror_account_txn_to_ledger(
            self.db, user_id=self.owner, account_id="synthetic-bank",
            account_transaction_id="synthetic-tx", amount=125, direction="out",
            transaction_type="shipping_debt_payment", counter_entity_type="shipping_company",
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "p02_shipping_cod_locked")
        await self.assert_effects(payments=0, movements=0, legs=0, balance=1000)
        self.assertEqual(await self.db.mz2_atomic_owners.count_documents({}), 0)

    async def test_locked_delete_without_mirror_restores_operational_balance(self):
        payment = (await self.post()).json()
        result = await self.client.delete("/shipping-accounts/payments/" + payment["id"])
        self.assertEqual(result.status_code, 200, result.text)
        await self.assert_effects(payments=0, movements=0, legs=0, balance=1000)

    async def test_active_delete_without_mirror_is_operational_only(self):
        payment = (await self.post()).json()
        await self.active()
        result = await self.client.delete("/shipping-accounts/payments/" + payment["id"])
        self.assertEqual(result.status_code, 200, result.text)
        await self.assert_effects(payments=0, movements=0, legs=0, balance=1000)

    async def test_posted_mirror_blocks_delete_and_retry_locked_and_active(self):
        await self.active()
        payment = (await self.post(key="immutable-payment")).json()
        for enabled in (True, False):
            await self.db.settings.update_one({"user_id": self.owner}, {"$set": {
                "mezan2_financial_cutover.p02_shipping_cod_enabled": enabled}})
            before = await self.snapshot()
            for _ in range(2):
                response = await self.client.delete("/shipping-accounts/payments/" + payment["id"])
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], {
                    "code": "shipping_payment_posted_financially_immutable",
                    "message": "لا يمكن حذف دفعة شحن تم ترحيلها محاسبيًا. استخدم إجراء تصحيح/عكس محاسبي.",
                })
                self.assertEqual(await self.snapshot(), before)

    async def test_payment_metadata_blocks_delete_even_when_link_is_missing(self):
        await self.active()
        payment = (await self.post(key="missing-link")).json()
        await self.db.shipping_payments.update_one({"id": payment["id"]}, {
            "$unset": {"linked_transaction_id": "", "paid_from_account_id": ""}})
        before = await self.snapshot()
        response = await self.client.delete("/shipping-accounts/payments/" + payment["id"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_posted_link_is_immutable_without_assuming_mirror_source_label(self):
        await self.active()
        payment = (await self.post(key="source-label")).json()
        await self.db.general_ledger.update_many({}, {"$unset": {"metadata.source": ""}})
        before = await self.snapshot()
        response = await self.client.delete("/shipping-accounts/payments/" + payment["id"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_active_post_is_balanced_and_retry_is_idempotent(self):
        await self.active()
        first = await self.post(key="payment-1")
        self.assertEqual(first.status_code, 200, first.text)
        second = await self.post(key="payment-1")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["financial_posting"], "posted")
        await self.assert_effects(payments=1, movements=1, legs=2, balance=875)
        rows = await self.db.general_ledger.find({}).to_list(2)
        self.assertEqual({row["side"] for row in rows}, {"debit", "credit"})
        self.assertEqual(len({row["txn_group_id"] for row in rows}), 1)
        self.assertEqual([row["amount"] for row in rows], [125, 125])

    async def test_concurrent_retry_commits_once(self):
        await self.active()
        responses = await asyncio.gather(*(self.post(key="concurrent") for _ in range(4)))
        self.assertEqual([r.status_code for r in responses], [200] * 4)
        self.assertEqual(len({r.json()["id"] for r in responses}), 1)
        await self.assert_effects(payments=1, movements=1, legs=2, balance=875)

    async def test_active_missing_idempotency_key_fails_before_mutation(self):
        await self.active()
        before = await self.snapshot()
        response = await self.post()
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_reused_key_with_different_payload_fails_without_changes(self):
        await self.active()
        self.assertEqual((await self.post(key="stable-key")).status_code, 200)
        before = await self.snapshot()
        response = await self.post(key="stable-key", amount=126)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_locked_retry_after_activation_does_not_backfill(self):
        first = await self.post(key="locked-payment")
        self.assertEqual(first.status_code, 200, first.text)
        await self.active()
        second = await self.post(key="locked-payment")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        await self.assert_effects(payments=1, movements=1, legs=0, balance=875)

    async def test_orphan_operational_movement_cannot_look_financially_posted(self):
        payment = (await self.post(key="orphan-operational")).json()
        await self.db.shipping_payments.delete_one({"id": payment["id"]})
        await self.active()
        before = await self.snapshot()
        response = await self.post(key="orphan-operational")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_active_pause_denies_without_operational_or_financial_effects(self):
        await self.active()
        await self.db.mz2_atomic_owners.update_one({"_id": self.owner}, {"$set": {"writes_paused": True}})
        before = await self.snapshot()
        response = await self.post(key="paused")
        self.assertEqual(response.status_code, 423, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_active_legacy_path_preserves_writer_transition_fence(self):
        await self.active()
        for state in ("transition_blocked", "v2_active"):
            await self.db.mz2_atomic_owners.update_one({"_id": self.owner}, {"$set": {
                "ledger_backend_state": state, "ledger_backend_revision": 1,
                "ledger_backend_contract_revision": 1,
                "ledger_backend_activation_ref": "SYNTHETIC-ACTIVATION",
            }})
            before = await self.snapshot()
            response = await self.post(key="writer-fence")
            self.assertEqual(response.status_code, 423, response.text)
            self.assertEqual(await self.snapshot(), before)

    async def test_active_closed_period_rolls_back_operational_and_gl_rows(self):
        await self.active()
        await self.db.mz2_accounting_periods.insert_one({
            "user_id": self.owner, "month": "2026-09", "closed": True,
        })
        before = await self.snapshot()
        response = await self.post(key="closed-period")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_unrelated_mirror_still_works_while_p02_locked(self):
        result = await mirror_account_txn_to_ledger(
            self.db, user_id=self.owner, account_id="synthetic-bank",
            account_transaction_id="synthetic-expense", amount=25, direction="out",
            transaction_type="expense", transaction_date="2026-09-29",
        )
        self.assertFalse(result["skipped"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), 2)

    async def test_gate_loss_before_transaction_never_downgrades_to_operational_success(self):
        await self.active()
        before = await self.snapshot()
        original = shipping_accounts.atomic_owner

        async def lose_gate(db, owner, callback):
            await self.db.settings.update_one({"user_id": self.owner}, {"$set": {
                "mezan2_financial_cutover.p02_shipping_cod_enabled": False}})
            return await original(db, owner, callback)

        with patch.object(shipping_accounts, "atomic_owner", lose_gate):
            response = await self.post(key="lost-gate")
        self.assertEqual(response.status_code, 423, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_operational_attempt_does_not_escalate_when_gate_opens(self):
        original = shipping_accounts._post_shipping_payment_tx

        async def open_gate(*args, **kwargs):
            await self.active()
            return await original(*args, **kwargs)

        with patch.object(shipping_accounts, "_post_shipping_payment_tx", open_gate):
            response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["financial_posting"], "not_posted_p02_locked")
        await self.assert_effects(payments=1, movements=1, legs=0, balance=875)

    async def test_second_gl_leg_failure_rolls_back_every_effect(self):
        await self.active()
        before = await self.snapshot()
        original = AsyncIOMotorCollection.insert_many

        async def fail_after_first(collection, documents, *args, **kwargs):
            if collection.name == "general_ledger" and collection.database.name == self.db.name:
                await collection.insert_one(copy.deepcopy(documents[0]), **kwargs)
                raise RuntimeError("synthetic second leg failure")
            return await original(collection, documents, *args, **kwargs)

        with patch.object(AsyncIOMotorCollection, "insert_many", fail_after_first):
            response = await self.post(key="fail-second-leg")
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_payment_failure_after_mirror_rolls_back_every_effect(self):
        await self.active()
        before = await self.snapshot()
        original = AsyncIOMotorCollection.insert_one

        async def fail_payment(collection, document, *args, **kwargs):
            if collection.name == "shipping_payments" and collection.database.name == self.db.name:
                raise RuntimeError("synthetic payment failure")
            return await original(collection, document, *args, **kwargs)

        with patch.object(AsyncIOMotorCollection, "insert_one", fail_payment):
            response = await self.post(key="fail-payment")
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_other_tenant_cannot_delete_or_use_bank(self):
        await self.active()
        payment = (await self.post(key="owner-payment")).json()
        before = await self.snapshot()
        headers = {"X-Test-Owner": "other-synthetic-owner"}
        response = await self.client.delete("/shipping-accounts/payments/" + payment["id"], headers=headers)
        self.assertEqual(response.status_code, 404, response.text)
        response = await self.client.post("/shipping-accounts/SyntheticCourier/payments",
                                          json=self.payload, headers=headers)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(await self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
