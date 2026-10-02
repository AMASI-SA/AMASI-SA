"""Real Mongo transactions/storage, including controlled commit-fault injection."""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorClientSession
from pymongo.errors import OperationFailure
from accounting_receivable_service import execute
from accounting_atomic import atomic_owner
from bnpl.ledger_bridge import post_bnpl_sale_to_ledger
from decimal import Decimal
from accounting_recognition_native import native_rows
from mz2_native_fixture import provision_native_opening

async def compute_balance(db, *, user_id, entity_type, entity_id, sub_account=None):
    rows = await native_rows(db, user_id)
    return {"net_balance": sum((Decimal(str(r["amount"])) * (1 if r["side"] == "debit" else -1) for r in rows if r["entity_type"] == entity_type and r["entity_id"] == entity_id and (sub_account is None or r.get("sub_account") == sub_account)), Decimal(0))}
import test_mz2_receivable_workflow as fixtures

WORKER = r'''
import asyncio, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
import accounting_ledger_v2 as native_ledger
from accounting_receivable_service import execute
async def main():
    client = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
    db = client[sys.argv[1]]
    if sys.argv[2] == "native_journal":
        original = native_ledger._insert_prepared_journal
        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            os._exit(77)
        native_ledger._insert_prepared_journal = crash
    result = await execute(db, owner="owner", actor_id="owner", actor_name="synthetic",
        provider="tamara", payment_id="SYN-CAPTURE-tamara")
    os._exit(78)  # committed, response deliberately lost
asyncio.run(main())
'''


class AtomicRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WorkflowTests.asyncSetUp(self)
        await provision_native_opening(self.db, bank_balances={"SYN-BANK": 0})
    asyncTearDown = fixtures.WorkflowTests.asyncTearDown
    configure = fixtures.WorkflowTests.configure
    source = fixtures.WorkflowTests.source
    payload = fixtures.WorkflowTests.payload
    preview_and_post = fixtures.WorkflowTests.preview_and_post
    count_writes = fixtures.WorkflowTests.count_writes

    async def crash_worker(self, point):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", WORKER, self.db.name, point)
        return await asyncio.wait_for(process.wait(), 30)

    async def assert_one_balanced_sale(self):
        rows = [r for r in await native_rows(self.db, "owner") if r["entry_type"] != "opening_balance"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["txn_group_id"] for r in rows}), 1)
        self.assertEqual(sum(Decimal(str(r["amount"])) for r in rows if r["side"] == "debit"), 115)
        self.assertEqual(sum(Decimal(str(r["amount"])) for r in rows if r["side"] == "credit"), 115)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({"status":"posted"}), 1)
        balance = await compute_balance(self.db, user_id="owner",
            entity_type="payment_gateway", entity_id="tamara", sub_account="receivable")
        self.assertEqual(balance["net_balance"], 115)
        print("Synthetic journal: one balanced group; expected receivable delta verified")

    async def test_aborted_transient_commit_retries_one_native_sale(self):
        original = AsyncIOMotorClientSession.commit_transaction
        attempts = 0

        async def commit(session):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await session.abort_transaction()
                raise OperationFailure("synthetic aborted commit conflict", 112,
                    {"errorLabels": ["TransientTransactionError"]})
            return await original(session)

        with patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            await self.preview_and_post()
        self.assertEqual(attempts, 2)
        await self.assert_one_balanced_sale()
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def _assert_unknown_commit_does_not_replay(self, labels):
        original = AsyncIOMotorClientSession.commit_transaction
        attempts = 0

        async def commit(session):
            nonlocal attempts
            attempts += 1
            await original(session)
            raise OperationFailure("synthetic lost commit reply", 91,
                {"errorLabels": labels})

        with patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            with self.assertRaisesRegex(OperationFailure, "lost commit reply"):
                await self.preview_and_post()
        self.assertEqual(attempts, 1)
        await self.assert_one_balanced_sale()
        before = await self.count_writes()
        result = await execute(self.db, owner="owner", actor_id="owner",
            actor_name="synthetic", **self.payload())
        self.assertEqual(result["state"], "already_posted")
        self.assertEqual(await self.count_writes(), before)

    async def test_unknown_commit_result_is_not_blindly_replayed(self):
        await self._assert_unknown_commit_does_not_replay(["UnknownTransactionCommitResult"])

    async def test_ambiguous_commit_labels_preserve_unknown_result_boundary(self):
        await self._assert_unknown_commit_does_not_replay([
            "TransientTransactionError", "UnknownTransactionCommitResult"])

    async def test_nontransient_commit_failure_aborts_without_retry(self):
        attempts = 0
        before = await self.count_writes()

        async def commit(session):
            nonlocal attempts
            attempts += 1
            await session.abort_transaction()
            raise OperationFailure("synthetic nontransient commit denial", 121)

        with patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            with self.assertRaisesRegex(OperationFailure, "nontransient"):
                await self.preview_and_post()
        self.assertEqual(attempts, 1)
        self.assertEqual(await self.count_writes(), before)

    async def test_transient_commit_retry_rechecks_owner_write_pause(self):
        attempts = 0
        before = await self.count_writes()
        preview = await self.client.post(fixtures.BASE + "/receivables/preview",
            json=self.payload())
        self.assertEqual(preview.status_code, 200, preview.text)

        async def commit(session):
            nonlocal attempts
            attempts += 1
            await session.abort_transaction()
            await self.db.mz2_atomic_owners.update_one({"_id": "owner"},
                {"$set": {"writes_paused": True}})
            raise OperationFailure("synthetic aborted commit conflict", 112,
                {"errorLabels": ["TransientTransactionError"]})

        with patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            response = await self.client.post(fixtures.BASE + "/receivables/execute",
                json={**self.payload(), "preview_hash": preview.json()["preview_hash"]})
        self.assertEqual(response.status_code, 423, response.text)
        self.assertEqual(response.json()["detail"]["code"], "mz2_writes_paused")
        self.assertEqual(attempts, 1)
        self.assertEqual(await self.count_writes(), before)

    async def test_transient_commit_retry_has_finite_attempt_budget(self):
        attempts = 0
        before = await self.count_writes()

        async def commit(session):
            nonlocal attempts
            attempts += 1
            await session.abort_transaction()
            raise OperationFailure("synthetic repeated aborted commit", 112,
                {"errorLabels": ["TransientTransactionError"]})

        with patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            with self.assertRaisesRegex(OperationFailure, "repeated aborted"):
                await self.preview_and_post()
        self.assertEqual(attempts, 3)
        self.assertEqual(await self.count_writes(), before)

    async def test_expired_retry_admission_does_not_start_another_transaction(self):
        attempts = 0
        before = await self.count_writes()

        async def commit(session):
            nonlocal attempts
            attempts += 1
            await session.abort_transaction()
            raise OperationFailure("synthetic expired aborted commit", 112,
                {"errorLabels": ["TransientTransactionError"]})

        with patch("accounting_atomic.monotonic", side_effect=[0, 121]), \
                patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            with self.assertRaisesRegex(OperationFailure, "expired aborted"):
                await self.preview_and_post()
        self.assertEqual(attempts, 1)
        self.assertEqual(await self.count_writes(), before)

    async def test_unlabelled_commit_conflict_does_not_authorize_retry(self):
        attempts = 0
        before = await self.count_writes()

        async def commit(session):
            nonlocal attempts
            attempts += 1
            await session.abort_transaction()
            raise OperationFailure("synthetic unlabelled conflict", 112)

        with patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            with self.assertRaisesRegex(OperationFailure, "unlabelled conflict"):
                await self.preview_and_post()
        self.assertEqual(attempts, 1)
        self.assertEqual(await self.count_writes(), before)

    async def test_cancelled_commit_is_not_replayed(self):
        attempts = 0
        before = await self.count_writes()

        async def commit(session):
            nonlocal attempts
            attempts += 1
            await session.abort_transaction()
            raise asyncio.CancelledError()

        with patch.object(AsyncIOMotorClientSession, "commit_transaction", commit):
            with self.assertRaises(asyncio.CancelledError):
                await self.preview_and_post()
        self.assertEqual(attempts, 1)
        self.assertEqual(await self.count_writes(), before)

    async def test_process_death_after_native_journal_insert_then_restart_retry(self):
        self.assertEqual(await self.crash_worker("native_journal"), 77)
        # A separate client sees no partial leg, audit or posting marker.
        self.assertEqual([r for r in await native_rows(self.db, "owner") if r["entry_type"] != "opening_balance"], [])
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}), 0)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        balance = await compute_balance(self.db, user_id="owner",
            entity_type="payment_gateway", entity_id="tamara", sub_account="receivable")
        self.assertEqual(balance["net_balance"], 0)
        # Mongo expires the dead session's uncommitted transaction; no lock
        # deletion or manual ledger repair is needed.
        # Default Mongo transaction lifetime is 60s; expiry cleanup can add
        # another interval. Do not require the shortened CI server setting.
        await asyncio.wait_for(self.preview_and_post(), 150)
        await self.assert_one_balanced_sale()

    async def test_committed_response_lost_then_restart_returns_same_group(self):
        self.assertEqual(await self.crash_worker("after_commit"), 78)
        before = await self.count_writes()
        prior = await self.db.mz2_recognition_events.find_one({})
        result = await execute(self.db, owner="owner", actor_id="owner",
                               actor_name="synthetic", **self.payload())
        self.assertEqual(result["state"], "already_posted")
        self.assertEqual(result["txn_group_id"], prior["txn_group_id"])
        self.assertEqual(await self.count_writes(), before)
        await self.assert_one_balanced_sale()

    async def test_simultaneous_import_sync_webhook_share_identity(self):
        payment = await self.db.payment_transactions.find_one({"provider":"tamara"})
        results = await asyncio.gather(
            execute(self.db, owner="owner", actor_id="owner", actor_name="import", **self.payload()),
            post_bnpl_sale_to_ledger(self.db, user_id="owner", txn={**payment,"source":"sync"}),
            post_bnpl_sale_to_ledger(self.db, user_id="owner", txn={**payment,"source":"webhook"}),
        )
        self.assertEqual(len({r["txn_group_id"] for r in results}), 1)
        await self.assert_one_balanced_sale()

    async def test_standalone_refuses_before_financial_write(self):
        client = AsyncIOMotorClient(os.environ["MZ2_TEST_STANDALONE_URI"])
        standalone = client[self.db.name]
        try:
            called = False
            async def callback(scoped):
                nonlocal called
                called = True
                await scoped.general_ledger.insert_one({"status":"posted"})
            with self.assertRaises(HTTPException) as error:
                await atomic_owner(standalone, "owner", callback)
            self.assertEqual(error.exception.status_code, 503)
            self.assertFalse(called)
            self.assertEqual(await standalone.general_ledger.count_documents({}), 0)
        finally:
            client.close()

    async def test_settlement_failure_after_ledger_before_status_aborts_all(self):
        import accounting_settlement_service as service
        await self.preview_and_post()
        await self.db.accounts.insert_one({
            "user_id":"owner","id":"SYN-BANK","name":"Synthetic bank","account_type":"bank"})
        # Deliberate same-ID legacy fixture remains for old writer/report contracts.


        draft = {"id":"SYN-DRAFT","user_id":"owner","status":"reviewed","provider":"tamara",
            "bank_account_id":"SYN-BANK","statement_reference":"SYN-ATOMIC",
            "idempotency_key":"SYN-ATOMIC","review_reasons":[],
            "amounts":{"gross_sales":115,"reported_net":115}}
        await self.db.accounting_settlements_v2.insert_one(draft)
        before = await self.count_writes()
        async def whole_operation(scoped):
            result = await service.post_reviewed_settlement(
                scoped, owner_id="owner", actor={"id":"owner"}, draft=draft)
            await scoped.accounting_settlements_v2.update_one(
                {"id":draft["id"],"user_id":"owner"},
                {"$set":{"status":"posted","ledger_txn_group_id":result["txn_group_id"]}})
            raise RuntimeError("after journal and draft")
        with self.assertRaisesRegex(RuntimeError,"after journal"):
            await atomic_owner(self.db,"owner",whole_operation)
        self.assertEqual(await self.count_writes(),before)
        self.assertEqual((await self.db.accounting_settlements_v2.find_one({"id":draft["id"]}))["status"],"reviewed")
        balance = await compute_balance(self.db,user_id="owner",entity_type="bank",entity_id="SYN-BANK")
        self.assertEqual(balance["net_balance"],0)
        result = await service.post_reviewed_settlement(
            self.db,owner_id="owner",actor={"id":"owner"},draft=draft)
        balance = await compute_balance(self.db,user_id="owner",entity_type="bank",entity_id="SYN-BANK")
        self.assertEqual(balance["net_balance"],115)
        print("Synthetic settlement: rollback and expected bank delta verified")



    async def test_post_endpoint_aborts_then_retries_once(self):
        from fastapi import APIRouter
        import accounting_settlement_routes as routes
        router = APIRouter()
        async def actor():
            return {"id": self.actor}
        routes.install_accounting_settlement_routes(router, self.db, actor)
        self.app.include_router(router)
        await self.preview_and_post()
        await self.db.accounts.insert_one({
            "user_id":"owner","id":"SYN-BANK","name":"Synthetic bank","account_type":"bank"})
        # Deliberate same-ID legacy fixture remains for old writer/report contracts.


        await self.db.accounting_settlements_v2.insert_one({
            "id":"SYN-ROUTE","user_id":"owner","status":"reviewed","provider":"tamara",
            "bank_account_id":"SYN-BANK","statement_reference":"SYN-ROUTE",
            "idempotency_key":"SYN-ROUTE","review_reasons":[],
            "amounts":{"gross_sales":115,"reported_net":115}})
        url = "/accounting-module/settlements/drafts/SYN-ROUTE/post"
        original = routes.post_reviewed_settlement
        async def fail_after_journal(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("route interrupted after journal")
        before = await self.count_writes()
        with patch.object(routes, "post_reviewed_settlement", side_effect=fail_after_journal):
            with self.assertRaisesRegex(RuntimeError, "route interrupted"):
                await self.client.post(url, json={})
        self.assertEqual(await self.count_writes(), before)
        self.assertEqual((await self.db.accounting_settlements_v2.find_one({"id":"SYN-ROUTE"}))["status"],"reviewed")
        response = await self.client.post(url, json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["status"],"posted")
        before = await self.count_writes()
        again = await self.client.post(url,json={})
        self.assertEqual(again.status_code,409,again.text)
        self.assertEqual(await self.count_writes(),before)

    async def test_one_cent_imbalance_cannot_commit(self):
        from accounting_ledger_v2 import post_journal_v2, AccountingLedgerV2Error
        before = await self.count_writes()
        async def callback(scoped):
            return await post_journal_v2(scoped._db,user_id="owner",actor_id="owner",actor_name="synthetic",
                idempotency_key="unbalanced-test", source="synthetic", effective_at="2020-01-02T12:00:00Z",
                txn_type="bnpl_sale", entries=[
                    {"entity_type":"payment_gateway","entity_id":"tamara","side":"debit","amount":"1.00",
                     "entry_type":"bnpl_sale", "leg_key":"receivable"},
                    {"entity_type":"revenue","entity_id":"bnpl_sales","side":"credit","amount":"0.99",
                     "entry_type":"bnpl_sale", "leg_key":"revenue"}], mongo_session=scoped._session)
        with self.assertRaises(AccountingLedgerV2Error) as error:
            await atomic_owner(self.db,"owner",callback)
        self.assertEqual(error.exception.code, "journal_unbalanced")
        self.assertEqual(await self.count_writes(), before)


if __name__ == "__main__":
    unittest.main()

