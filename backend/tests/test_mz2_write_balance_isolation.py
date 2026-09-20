"""Real Mongo writer acceptance: only approved MZ2 balances authorize postings."""
import unittest
from uuid import uuid4
from decimal import Decimal
from fastapi import APIRouter, HTTPException
import test_mz2_daily_refunds as daily
import test_mz2_customer_advances as advances
from mz2_report_fixtures import provision_report_opening
from accounting_module_contract import OPERATION_ID
from accounting_settlement_service import post_reviewed_settlement

class WriteBalanceIsolationTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = daily.DailyRefundTests.asyncTearDown
    configure = daily.DailyRefundTests.configure
    source = daily.DailyRefundTests.source
    payload = daily.DailyRefundTests.payload
    preview_and_post = daily.DailyRefundTests.preview_and_post
    post = daily.DailyRefundTests.post
    setup_sale = daily.DailyRefundTests.setup_sale
    case = daily.DailyRefundTests.case
    confirm = daily.DailyRefundTests.confirm
    movement = daily.DailyRefundTests.movement

    async def asyncSetUp(self):
        await daily.DailyRefundTests.asyncSetUp(self)
        await provision_report_opening(self.db, amount=10)
        await self.db.accounts.update_one({"user_id": "owner", "id": "bank"}, {"$set": {"name": "SYN isolated bank"}})
        # The durable coordination row is initialized outside Mongo's business
        # transaction. Provision it before rollback baselines so comparisons
        # measure financial state, audits, counters and row revision changes.
        await self.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$setOnInsert": {"revision": 0}}, upsert=True)

    async def snapshot(self):
        result = {}
        for name in sorted(await self.db.list_collection_names()):
            rows = await self.db[name].find({}).sort("_id", 1).to_list(None)
            if rows:
                result[name] = rows
        return result

    async def sentinel(self, entity, identifier, sub, side, *, value=999999, owner="owner", tagged=False, at="2020-01-03T12:00:00Z"):
        group = "SYN-SENTINEL-" + uuid4().hex
        metadata = {"accounting_at": at}
        if tagged:
            metadata.update(operation_id=OPERATION_ID, recognition_event_key=group)
        await self.db.general_ledger.insert_many([
            {"id": uuid4().hex, "user_id": owner, "status": "posted", "txn_group_id": group,
             "entry_type": "bnpl_sale" if tagged else "bank_transfer", "entity_type": et, "entity_id": eid,
             "sub_account": account, "side": direction, "amount": value, "metadata": metadata,
             "posted_at": "2026-09-20T12:00:00Z", "created_at": "2026-09-20T12:00:00Z"}
            for et, eid, account, direction in ((entity, identifier, sub, side),
                ("equity", "SYN", "capital", "credit" if side == "debit" else "debit"))])
        return group

    async def settlement(self, amount, reference=None):
        reference = reference or uuid4().hex
        draft = dict(id="SYN-DRAFT-"+reference, user_id="owner", status="reviewed", provider="tamara",
            bank_account_id="bank", statement_reference="SYN-"+reference, statement_date="2020-01-03",
            idempotency_key="SYN-"+reference, review_reasons=[], amounts=dict(gross_sales=amount, reported_net=amount))
        return await post_reviewed_settlement(self.db, owner_id="owner", actor={"id": "owner"}, draft=draft)

    async def refund(self, amount="40", channel="bank"):
        key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-WRITE-REFUND", "115")
        await self.confirm(row)
        payment = await self.movement(key, row["case_reference"], amount, channel=channel)
        return row, payment

    async def approve(self, payment):
        return await self.client.post(daily.BASE+"/bank-payments/"+payment["id"]+"/approve")

    async def assert_denied_without_writes(self, payment, expected="insufficient_refund_execution_balance"):
        before = await self.snapshot()
        response = await self.approve(payment)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn(expected, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_settlement_legacy_receivable_cannot_fund_then_qualified_sale_can(self):
        await self.sentinel("payment_gateway", "tamara", "receivable", "debit")
        before = await self.snapshot()
        with self.assertRaises(HTTPException) as denied:
            await self.settlement(115, "INSUFFICIENT")
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(await self.snapshot(), before)
        await self.setup_sale(gross="115")
        result = await self.settlement(115, "QUALIFIED")
        self.assertTrue(result["txn_group_id"])
        legs = await self.db.general_ledger.find({"txn_group_id": result["txn_group_id"]}).to_list(None)
        self.assertEqual(len(legs), 2)
        self.assertEqual({(r["entity_type"], r["side"], r["amount"]) for r in legs},
            {("bank", "debit", 115), ("payment_gateway", "credit", 115)})
        self.assertEqual(sum(Decimal(str(r["amount"]))* (1 if r["side"]=="debit" else -1) for r in legs), 0)

    async def test_bank_refund_legacy_cash_cannot_fund_then_qualified_settlement_can(self):
        row, payment = await self.refund()
        await self.sentinel("bank", "bank", "main", "debit")
        await self.db.accounts.update_one({"id": "bank"}, {"$set": {"current_balance": 999999}})
        await self.assert_denied_without_writes(payment)
        await self.settlement(115, "BANK-FUNDING")
        count_before = await self.db.general_ledger.count_documents({})
        result = await self.approve(payment)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)
        paid_group = result.json()["txn_group_id"]
        legs = await self.db.general_ledger.find({"txn_group_id": paid_group}).to_list(None)
        self.assertEqual(len(legs), 2)
        self.assertEqual(sum(r["amount"] if r["side"] == "debit" else -r["amount"] for r in legs), 0)
        self.assertFalse(any(r["entity_type"] in {"tax", "revenue"} for r in legs))
        duplicate = await self.approve(payment)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["txn_group_id"], paid_group)
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)
        self.assertEqual((await self.db.mz2_customer_refunds.find_one({"id": row["id"]}))["remaining"], "75.00")

    async def test_provider_refund_legacy_receivable_cannot_fund_then_qualified_activity_can(self):
        _, payment = await self.refund(channel="tamara")
        await self.settlement(115, "DRAIN-PROVIDER")
        await self.sentinel("payment_gateway", "tamara", "receivable", "debit")
        await self.assert_denied_without_writes(payment)
        await self.sentinel("payment_gateway", "tamara", "receivable", "debit", value=115, tagged=True)
        count_before = await self.db.general_ledger.count_documents({})
        result = await self.approve(payment)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)
        paid_group = result.json()["txn_group_id"]
        legs = await self.db.general_ledger.find({"txn_group_id": paid_group}).to_list(None)
        self.assertEqual(len(legs), 2)
        self.assertEqual(sum(r["amount"] if r["side"] == "debit" else -r["amount"] for r in legs), 0)
        self.assertFalse(any(r["entity_type"] in {"tax", "revenue"} for r in legs))
        duplicate = await self.approve(payment)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["txn_group_id"], paid_group)
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)

    async def test_negative_legacy_execution_and_same_case_liability_do_not_reject(self):
        row, payment = await self.refund(channel="tamara")
        await self.sentinel("payment_gateway", "tamara", "receivable", "credit")
        await self.sentinel("liability", row["id"], "customer_refund_payable", "debit")
        count_before = await self.db.general_ledger.count_documents({})
        result = await self.approve(payment)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)
        paid_group = result.json()["txn_group_id"]
        legs = await self.db.general_ledger.find({"txn_group_id": paid_group}).to_list(None)
        self.assertEqual(len(legs), 2)
        self.assertEqual(sum(r["amount"] if r["side"] == "debit" else -r["amount"] for r in legs), 0)
        self.assertFalse(any(r["entity_type"] in {"tax", "revenue"} for r in legs))
        duplicate = await self.approve(payment)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["txn_group_id"], paid_group)
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)
        self.assertEqual((await self.db.mz2_customer_refunds.find_one({"id": row["id"]}))["remaining"], "75.00")

    async def test_precutover_tagged_and_foreign_owner_cannot_fund_bank_refund(self):
        _, payment = await self.refund()
        await self.sentinel("bank", "bank", "main", "debit", tagged=True, at="2019-12-31T12:00:00Z")
        await self.sentinel("bank", "bank", "main", "debit", owner="other", tagged=True)
        await self.assert_denied_without_writes(payment)

    async def test_unknown_or_malformed_tagged_source_blocks_without_partial_writes(self):
        _, payment = await self.refund(channel="tamara")
        for field, value in (("entry_type", "unsupported_mz2_kind"), ("metadata.accounting_at", "not-a-date")):
            group = await self.sentinel("bank", "bank", "main", "debit", tagged=True, value=10)
            await self.db.general_ledger.update_many({"txn_group_id": group}, {"$set": {field: value}})
            await self.assert_denied_without_writes(payment, "mz2_balance_not_ready")
            await self.db.general_ledger.delete_many({"txn_group_id": group})

    async def test_missing_approved_opening_blocks_writer_without_partial_effect(self):
        _, payment = await self.refund(channel="tamara")
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"mezan2_financial_cutover.opening_balance_approved_by": ""}})
        await self.assert_denied_without_writes(payment, "mz2_balance_not_ready")

    async def test_advance_liability_and_execution_ignore_legacy(self):
        from accounting_customer_advances import install_customer_advance_routes
        await self.db.orders_db.update_many({}, {"$set": {"order_status": "pending"}, "$unset": {"delivered_at": ""}})
        router = APIRouter()
        async def actor(): return {"id": self.actor}
        install_customer_advance_routes(router, self.db, actor)
        self.app.include_router(router)
        body = advances.AdvanceTests.capture_body(self)
        response = await self.client.post(advances.BASE, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        await self.sentinel("liability", row["id"], "customer_advance", "debit")
        await self.db.orders_db.update_many({}, {"$set": {"order_status": "cancelled"}})
        response = await self.client.post(advances.BASE+"/"+row["id"]+"/cancel", json=dict(
            accounting_at="2020-01-31T23:00:00+03:00", evidence_ref="SYN-CANCELLATION"))
        self.assertEqual(response.status_code, 200, response.text)
        await self.sentinel("liability", row["id"], "customer_refund_payable", "debit")
        await self.sentinel("payment_gateway", "tamara", "receivable", "credit")
        payload = await advances.AdvanceTests.refund_evidence(self, "SYN-ADVANCE-40", "40", "2020-02-02T10:00:00+03:00")
        response = await self.client.post(advances.BASE+"/"+row["id"]+"/payments", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.db.mz2_customer_advances.find_one({"id": row["id"]}))["remaining"], "75.00")

    async def test_balance_helper_rejects_root_wrong_owner_and_ended_session(self):
        from accounting_atomic import atomic_owner
        from accounting_mz2_balances import read_mz2_write_balances
        before = await self.snapshot()
        with self.assertRaises(HTTPException) as raw:
            await read_mz2_write_balances(self.db, owner="owner")
        self.assertEqual(raw.exception.detail, "mz2_balance_requires_owner_transaction")
        escaped = []
        async def wrong_owner(scoped):
            escaped.append(scoped)
            await read_mz2_write_balances(scoped, owner="other")
        with self.assertRaises(HTTPException) as other:
            await atomic_owner(self.db, "owner", wrong_owner)
        self.assertEqual(other.exception.detail, "mz2_balance_requires_owner_transaction")
        self.assertEqual(await self.snapshot(), before)
        with self.assertRaises(HTTPException) as ended:
            await read_mz2_write_balances(escaped[0], owner="owner")
        self.assertEqual(ended.exception.detail, "mz2_balance_requires_owner_transaction")
        self.assertEqual(await self.snapshot(), before)

    async def test_balance_helper_reads_own_uncommitted_journal_and_abort_leaves_no_delta(self):
        from accounting_atomic import atomic_owner
        from accounting_mz2_balances import read_mz2_write_balances
        from ledger_core import post_txn_group
        before = await self.snapshot()
        async def transaction(scoped):
            initial = await read_mz2_write_balances(scoped, owner="owner")
            self.assertEqual(initial.net_balance(entity_type="payment_gateway", entity_id="tamara", sub_account="receivable"), Decimal("0"))
            posted = await post_txn_group(scoped, user_id="owner", actor_id="owner", actor_name="SYN",
                txn_type="bnpl_sale", metadata={"operation_id": OPERATION_ID, "recognition_event_key": "SYN-UNCOMMITTED",
                    "recognized_at": "2020-01-03T12:00:00Z"}, entries=[
                    dict(entity_type="payment_gateway", entity_id="tamara", sub_account="receivable", side="debit", amount=115, entry_type="bnpl_sale"),
                    dict(entity_type="revenue", entity_id="bnpl_sales", side="credit", amount=100, entry_type="bnpl_sale"),
                    dict(entity_type="tax", entity_id="sales_vat_payable", side="credit", amount=15, entry_type="bnpl_sale")])
            balances = await read_mz2_write_balances(scoped, owner="owner")
            self.assertEqual(balances.net_balance(entity_type="payment_gateway", entity_id="tamara", sub_account="receivable"), Decimal("115"))
            self.assertEqual(await scoped.general_ledger.count_documents({"txn_group_id": posted["txn_group_id"]}), 3)
            self.assertEqual(await self.db.general_ledger.count_documents({"txn_group_id": posted["txn_group_id"]}), 0)
            raise RuntimeError("SYN deliberate abort after balance read")
        with self.assertRaisesRegex(RuntimeError, "deliberate abort"):
            await atomic_owner(self.db, "owner", transaction)
        self.assertEqual(await self.snapshot(), before)

    async def test_documented_different_provider_uses_only_qualified_target_balance(self):
        import base64
        key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-TAMARA-TABBY", "115")
        await self.confirm(row)
        settings = await self.db.settings.find_one({"user_id": "owner"})
        cutover = settings["mezan2_financial_cutover"]
        await self.db.settings.update_one({"user_id": "owner"}, {"$push": {
            "mezan2_financial_cutover.opening_balance_zero_accounts": {
                "entity_type": "payment_gateway", "entity_id": "tabby", "sub_account": "receivable",
                "evidence_ref": "SYN-ZERO-TABBY", "accounting_at": cutover["cutover_at"],
                "opening_balance_txn_group_id": cutover["opening_balance_txn_group_id"]}}})
        common = dict(original_key=key, case_reference=row["case_reference"], amount="40",
            paid_at="2020-01-04T12:00:00Z", execution_channel="tabby")
        no_proof = await self.post("/bank-payments", {**common, "bank_reference": "SYN-NO-DOC",
            "provider_refund_id": "SYN-TABBY-NO-DOC"})
        await self.assert_denied_without_writes(no_proof, "documented_execution_provider_proof_required")
        documented = await self.post("/bank-payments", {**common, "bank_reference": "SYN-TABBY-DOC-40",
            "provider_refund_id": "SYN-TABBY-REFUND-40", "proof_name": "SYN-tabby-execution.txt",
            "proof_base64": base64.b64encode(b"SYN provider Tabby execution40 for original Tamara case SYN-TAMARA-TABBY").decode()})
        canonical = dict(user_id="owner", provider="tabby", provider_refund_id="SYN-TABBY-REFUND-40",
            status="completed", source="synthetic_provider_document", currency="SAR", amount="40",
            refunded_at="2020-01-04T12:00:00Z")
        for change in ({"status": "pending"}, {"status": "failed"}, {"status": "cancelled"},
                       {"status": "unknown"}, {"amount": "41"}, {"amount": "not-money"},
                       {"refunded_at": "2020-01-05T12:00:00Z"}, {"refunded_at": "bad-date"},
                       {"currency": "USD"}, {"synthesised": True}, {"source": ""},
                       {"raw": {"_fetch_error": "SYN unavailable"}}):
            inserted = await self.db.payment_refunds.insert_one({**canonical, **change})
            await self.assert_denied_without_writes(documented, "documented_provider_execution_evidence_conflict")
            await self.db.payment_refunds.delete_one({"_id": inserted.inserted_id})
        duplicate_evidence = await self.db.payment_refunds.insert_many([dict(canonical), dict(canonical)])
        await self.assert_denied_without_writes(documented, "documented_provider_execution_evidence_conflict")
        await self.db.payment_refunds.delete_many({"_id": {"$in": duplicate_evidence.inserted_ids}})
        original_execution = await self.db.payment_refunds.insert_one({**canonical, "provider": "tamara",
            "provider_payment_id": "SYN-CAPTURE-tamara", "provider_refund_id": "SYN-ORIGINAL-EXECUTED"})
        await self.assert_denied_without_writes(documented, "original_provider_execution_conflicts_with_documented_payment")
        await self.db.payment_refunds.delete_one({"_id": original_execution.inserted_id})
        await self.sentinel("payment_gateway", "tabby", "receivable", "debit")
        await self.assert_denied_without_writes(documented)
        await self.setup_sale(provider="tabby", gross="115")
        count_before = await self.db.general_ledger.count_documents({})
        posted = await self.approve(documented)
        self.assertEqual(posted.status_code, 200, posted.text)
        group = posted.json()["txn_group_id"]
        legs = await self.db.general_ledger.find({"txn_group_id": group}).to_list(None)
        self.assertEqual(len(legs), 2)
        self.assertEqual({(r["entity_type"], r["entity_id"], r["side"], r["amount"]) for r in legs},
            {("liability", row["id"], "debit", 40), ("payment_gateway", "tabby", "credit", 40)})
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)
        duplicate = await self.approve(documented)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["txn_group_id"], group)
        self.assertEqual(await self.db.general_ledger.count_documents({}), count_before + 2)
        current = await self.db.mz2_customer_refunds.find_one({"id": row["id"]})
        self.assertEqual(current["remaining"], "75.00")

    async def test_provider_identity_whitespace_cannot_create_second_execution(self):
        key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-CANONICAL-REFUND", "115")
        await self.confirm(row)
        common = dict(original_key=key, case_reference=row["case_reference"], amount="40",
            paid_at="2020-01-04T12:00:00Z", execution_channel="tamara")
        first = await self.post("/bank-payments", {**common, "bank_reference": "SYN-DOC-A",
            "provider_refund_id": "SYN-CANONICAL-PROVIDER-40"})
        second = await self.post("/bank-payments", {**common, "bank_reference": "SYN-DOC-B",
            "provider_refund_id": "  SYN-CANONICAL-PROVIDER-40  "})
        self.assertEqual(second["provider_refund_id"], "SYN-CANONICAL-PROVIDER-40")
        self.assertNotEqual(first["id"], second["id"])
        before = await self.db.general_ledger.count_documents({})
        result = await self.approve(first)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), before+2)
        await self.assert_denied_without_writes(second, "provider_refund_already_accounted")
        self.assertEqual((await self.db.mz2_customer_refunds.find_one({"id": row["id"]}))["remaining"], "75.00")

    async def test_existing_noncanonical_pending_provider_identity_requires_review(self):
        _, payment = await self.refund(channel="tamara")
        await self.db.mz2_customer_refund_payments.update_one({"id": payment["id"]},
            {"$set": {"provider_refund_id": "  SYN-HISTORICAL-NONCANONICAL  "}})
        await self.assert_denied_without_writes(payment, "provider_refund_identity_requires_canonical_draft")
