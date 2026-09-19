"""Real Mongo integration: no mocked transaction/session or ledger storage."""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_receivable_service import execute
from accounting_atomic import atomic_owner
from bnpl.ledger_bridge import post_bnpl_sale_to_ledger
from ledger_core import compute_balance
import test_mz2_receivable_workflow as fixtures

WORKER = r'''
import asyncio, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
import ledger_core
from accounting_receivable_service import execute
async def main():
    client = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
    db = client[sys.argv[1]]
    if sys.argv[2] == "first_leg":
        original = ledger_core.post_ledger_entry
        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            os._exit(77)
        ledger_core.post_ledger_entry = crash
    result = await execute(db, owner="owner", actor_id="owner", actor_name="synthetic",
        provider="tamara", payment_id="SYN-CAPTURE-tamara")
    os._exit(78)  # committed, response deliberately lost
asyncio.run(main())
'''


class AtomicRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkflowTests.asyncSetUp
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
        rows = await self.db.general_ledger.find({}).to_list(20)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["txn_group_id"] for r in rows}), 1)
        self.assertEqual(sum(r["amount"] for r in rows if r["side"] == "debit"), 115)
        self.assertEqual(sum(r["amount"] for r in rows if r["side"] == "credit"), 115)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({"status":"posted"}), 1)
        balance = await compute_balance(self.db, user_id="owner",
            entity_type="payment_gateway", entity_id="tamara", sub_account="receivable")
        self.assertEqual(balance["net_balance"], 115)
        print("Synthetic journal: one balanced group; expected receivable delta verified")

    async def test_process_death_after_first_leg_then_restart_retry(self):
        self.assertEqual(await self.crash_worker("first_leg"), 77)
        # A separate client sees no partial leg, audit or posting marker.
        for name in ("general_ledger", "accounting_audit_log", "mz2_recognition_events"):
            self.assertEqual(await self.db[name].count_documents({}), 0, name)
        balance = await compute_balance(self.db, user_id="owner",
            entity_type="payment_gateway", entity_id="tamara", sub_account="receivable")
        self.assertEqual(balance["net_balance"], 0)
        # Mongo expires the dead session's uncommitted transaction; no lock
        # deletion or manual ledger repair is needed.
        await asyncio.wait_for(self.preview_and_post(), 45)
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
        from ledger_core import post_txn_group
        async def callback(scoped):
            return await post_txn_group(scoped,user_id="owner",actor_id="owner",actor_name="synthetic",
                txn_type="bnpl_sale", entries=[
                    {"entity_type":"payment_gateway","entity_id":"tamara","side":"debit","amount":1,
                     "entry_type":"bnpl_sale"},
                    {"entity_type":"revenue","entity_id":"bnpl_sales","side":"credit","amount":0.99,
                     "entry_type":"bnpl_sale"}])
        with self.assertRaises(HTTPException):
            await atomic_owner(self.db,"owner",callback)
        self.assertEqual(await self.db.general_ledger.count_documents({}),0)
        self.assertEqual(await self.db.accounting_audit_log.count_documents({}),0)


if __name__ == "__main__":
    unittest.main()

