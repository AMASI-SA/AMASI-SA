"""Existing sale/refund/settlement chain, real sealed Mongo and no Legacy IO."""
import os
import unittest
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException

import test_mz2_receivable_workflow as fixtures
from mz2_native_fixture import provision_native_opening
from customer_native_fixture import CustomerLegacyAccess
from accounting_receivable_service import execute
from accounting_customer_refunds import create_case, create_bank_payment, post_bank_payment
from accounting_refund_entitlements import recognize_entitlement
from accounting_settlement_service import post_reviewed_settlement


class NativeRecognitionCutoverTests(unittest.IsolatedAsyncioTestCase):
    configure = fixtures.WorkflowTests.configure
    source = fixtures.WorkflowTests.source
    payload = fixtures.WorkflowTests.payload
    asyncTearDown = fixtures.WorkflowTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.WorkflowTests.asyncSetUp(self)
        await provision_native_opening(self.db, bank_balances={"bank": "1000"})

    async def test_sale_refund_settlement_and_replay_never_touch_legacy(self):
        from accounting_recognition_native import native_rows
        await self.db.general_ledger.insert_one(dict(user_id="owner", status="posted",
            metadata={"order_reference_id": "SYN-MANUAL-TAX-tamara"}, amount=999999))
        await self.db.account_transactions.insert_one(dict(user_id="owner", account_id="bank",
            reference="native-proof", amount=999999))
        before = {name: await self.db[name].find({}).to_list(None) for name in
                  ("general_ledger", "account_transactions", "accounts", "accounting_audit_log")}
        await self.db.payment_refunds.insert_one(dict(user_id="owner", provider="tamara",
            provider_refund_id="native-refund", provider_payment_id="SYN-CAPTURE-tamara",
            amount="57.50", currency="SAR", status="completed", source="synthetic_import",
            refunded_at="2020-01-03T12:00:00Z"))
        listener = CustomerLegacyAccess()
        observed = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], event_listeners=[listener])
        db = observed[self.db.name]
        actor = {"id": "owner"}
        try:
            with self.assertRaises(HTTPException) as denied:
                await execute(db, owner="owner", actor_id="other", actor_name="other", **self.payload())
            self.assertEqual(denied.exception.status_code, 403)
            self.assertEqual(await self.db.mz2_recognition_events.count_documents({}), 0)
            sale = await execute(db, owner="owner", actor_id="owner", actor_name="owner", **self.payload())
            case = await create_case(db, owner="owner", actor=actor, original_key=sale["event_key"],
                case_reference="native-case", amount="57.50", recognized_at="2020-01-03T12:00:00Z", reason="Synthetic return")
            facts = dict(owner="owner", actor=actor, case_id=case["id"], amount="57.50",
                recognized_at="2020-01-03T12:00:00Z", reason="Synthetic confirmed right", evidence_ref="native-credit-note")
            due = await recognize_entitlement(db, **facts)
            payment = await create_bank_payment(db, owner="owner", actor=actor, original_key=sale["event_key"],
                case_reference="native-case", bank_account_id="", amount="57.50", paid_at="2020-01-03T12:00:00Z",
                bank_reference="native-refund", proof_name="", proof_base64="", execution_channel="tamara",
                provider_refund_id="native-refund")
            paid = await post_bank_payment(db, owner="owner", actor=actor, payment_id=payment["id"])
            self.assertEqual((await recognize_entitlement(db, **facts))["due_txn_group_id"], due["due_txn_group_id"])
            self.assertEqual((await post_bank_payment(db, owner="owner", actor=actor,
                payment_id=payment["id"]))["txn_group_id"], paid["txn_group_id"])
            draft = dict(id="native-settlement", user_id="owner", status="reviewed", provider="tamara",
                bank_account_id="bank", statement_reference="native-statement", statement_date="2020-01-04",
                idempotency_key="native-settlement", review_reasons=[], amounts=dict(
                    gross_sales=57.5, commission=2, commission_vat=0.3, reported_net=55.2))
            settled = await post_reviewed_settlement(db, owner_id="owner", actor=actor, draft=draft)
            self.assertTrue(settled["txn_group_id"])
            with self.assertRaises(HTTPException):
                await post_reviewed_settlement(db, owner_id="owner", actor=actor, draft=draft)
            rows = await native_rows(db, "owner")
            from decimal import Decimal
            net = lambda kind, identifier: sum((Decimal(r["amount"]) * (1 if r["side"] == "debit" else -1)
                for r in rows if r["entity_type"] == kind and r["entity_id"] == identifier), Decimal(0))
            self.assertEqual(net("payment_gateway", "tamara"), 0)
            self.assertEqual(net("bank", "bank"), Decimal("1055.20"))
            self.assertEqual(net("expense", "provider_commission"), Decimal("2"))
            self.assertEqual(net("expense", "provider_commission_vat"), Decimal("0.30"))
            self.assertEqual(net("revenue", "bnpl_sales"), -50)
            self.assertEqual(net("tax", "sales_vat_payable"), Decimal("-7.50"))
            from accounting_mz2_reports import mz2_financial_position, mz2_trial_balance
            for reader in (mz2_financial_position, mz2_trial_balance):
                report = await reader(db, owner="owner")
                self.assertEqual(report["status"], "available", report)
            # A valid but unrelated journal is not an idempotent success.
            await db.mz2_customer_refund_payments.update_one({"id": payment["id"]},
                {"$set": {"txn_group_id": sale["txn_group_id"]}})
            with self.assertRaisesRegex(HTTPException, "native_event_journal_conflict"):
                await post_bank_payment(db, owner="owner", actor=actor, payment_id=payment["id"])
            await db.mz2_customer_refunds.update_one({"id": case["id"]},
                {"$set": {"due_txn_group_id": sale["txn_group_id"]}})
            with self.assertRaisesRegex(HTTPException, "native_event_journal_conflict"):
                await recognize_entitlement(db, **facts)
            self.assertEqual(listener.accesses, [])
        finally:
            observed.close()
        for name, original in before.items():
            self.assertEqual(await self.db[name].find({}).to_list(None), original)
