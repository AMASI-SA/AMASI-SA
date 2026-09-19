"""Real replica-set pause barrier, durable ingress and production router tests."""
import asyncio
import unittest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from accounting_atomic import atomic_owner
from accounting_write_control import set_write_state, write_state
from accounting_ingress import replay_pending
from accounting_receivable_service import execute, managed_owner
from accounting_refund_drafts import observe_refund
from bnpl.ledger_bridge import post_bnpl_sale_to_ledger
from financial_provider_apps import make_financial_provider_apps_router
import test_mz2_receivable_workflow as fixtures


class WriteControlTests(unittest.IsolatedAsyncioTestCase):
    configure = fixtures.WorkflowTests.configure
    source = fixtures.WorkflowTests.source
    payload = fixtures.WorkflowTests.payload
    count_writes = fixtures.WorkflowTests.count_writes
    asyncTearDown = fixtures.WorkflowTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.WorkflowTests.asyncSetUp(self)
        await self.client.aclose()
        self.app = FastAPI()
        async def authenticated_actor():
            return {"id": self.actor}
        self.app.include_router(make_financial_provider_apps_router(self.db, authenticated_actor))
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://local")
        self.base = "/financial-provider-apps/accounting-module"

    async def control(self, paused):
        row = await write_state(self.db, "owner")
        return await set_write_state(self.db, owner="owner", actor_id="owner",
            paused=paused, revision=row["revision"], reason="isolated synthetic test")

    async def post_sale(self):
        return await execute(self.db, owner="owner", actor_id="owner",
            actor_name="test", **self.payload())

    async def assert_balanced(self, expected_groups):
        rows = await self.db.general_ledger.find({}).to_list(100)
        groups = {r["txn_group_id"] for r in rows}
        self.assertEqual(len(groups), expected_groups)
        for group in groups:
            legs = [r for r in rows if r["txn_group_id"] == group]
            self.assertEqual(len(legs), 3)
            self.assertEqual(sum(r["amount"] for r in legs if r["side"] == "debit"), 115)
            self.assertEqual(sum(r["amount"] for r in legs if r["side"] == "credit"), 115)

    async def test_ui_api_paused_reads_work_and_writes_fail(self):
        policy = await self.db.mz2_sales_tax_policies.find_one({"_id":"owner"})
        settings = await self.db.settings.find_one({"user_id":"owner"})
        response = await self.client.put(self.base + "/write-control", json={
            "paused": True, "revision": 0, "reason": "maintenance"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["paused"])
        self.assertEqual((await self.client.get(self.base + "/sales-tax")).status_code, 200)
        preview = await self.client.post(self.base + "/receivables/preview", json=self.payload())
        self.assertEqual(preview.status_code, 200, preview.text)
        before = await self.count_writes()
        calls = [
            self.client.post(self.base + "/receivables/execute", json={**self.payload(),
                "preview_hash": preview.json()["preview_hash"]}),
            self.client.put(self.base + "/sales-tax", json={"rate":"20","revision":1,
                "effective_at":"2020-01-01T00:00:00Z","reason":"blocked"}),
            self.client.patch(self.base + "/settlements/drafts/not-present", json={"notes":"blocked"}),
        ]
        for result in await asyncio.gather(*calls):
            self.assertEqual(result.status_code, 423, result.text)
        self.assertEqual(await self.count_writes(), before)
        self.assertEqual(await self.db.mz2_sales_tax_policies.find_one({"_id":"owner"}), policy)
        self.assertEqual(await self.db.settings.find_one({"user_id":"owner"}), settings)
        self.assertEqual((await self.client.post(self.base + "/write-control/replay")).status_code,423)

    async def test_authority_revision_tenant_and_sticky_routing(self):
        self.actor = "viewer"
        self.assertEqual((await self.client.get(self.base + "/write-control")).status_code,200)
        self.assertEqual((await self.client.put(self.base + "/write-control", json={
            "paused":True,"revision":0,"reason":"forbidden"})).status_code,403)
        self.assertEqual((await self.client.post(self.base + "/write-control/replay")).status_code,403)
        await self.control(True)
        with self.assertRaises(HTTPException) as error:
            await set_write_state(self.db,owner="owner",actor_id="owner",paused=False,revision=0,reason="stale")
        self.assertEqual(error.exception.status_code,409)
        self.assertFalse((await write_state(self.db,"other"))["paused"])
        # Synthetic destructive fixture only: even if both old routing markers
        # disappear, the durable control marker prevents legacy fallback.
        await self.db.mz2_sales_tax_policies.delete_one({"_id":"owner"})
        await self.db.settings.delete_one({"user_id":"owner"})
        self.assertTrue(await managed_owner(self.db,"owner"))
        payment = await self.db.payment_transactions.find_one({"user_id":"owner"})
        result = await post_bnpl_sale_to_ledger(self.db,user_id="owner",txn=payment)
        self.assertEqual(result["state"],"deferred")
        await self.assert_balanced(0)

    async def test_ingress_persists_and_resume_replay_is_idempotent(self):
        await self.control(True)
        payment = await self.db.payment_transactions.find_one({"user_id":"owner"})
        results = await asyncio.gather(*[
            post_bnpl_sale_to_ledger(self.db,user_id="owner",txn={**payment,"source":source})
            for source in ("webhook","sync","background")])
        self.assertEqual({r["state"] for r in results},{"deferred"})
        self.assertEqual(await self.db.mz2_ingress_events.count_documents({"state":"pending"}),1)
        await self.assert_balanced(0)
        await self.control(False)
        await asyncio.gather(replay_pending(self.db,"owner"),replay_pending(self.db,"owner"))
        await self.assert_balanced(1)
        self.assertEqual(await self.db.mz2_ingress_events.count_documents({"state":"pending"}),0)
        again = await post_bnpl_sale_to_ledger(self.db,user_id="owner",txn=payment)
        self.assertEqual(again["state"],"already_posted")
        await self.assert_balanced(1)
        await self.control(True)
        result = await observe_refund(self.db,owner="owner",order_number=payment["order_reference_id"],
            source={"kind":"verified_webhook"},payload={"status":{"slug":"refunded"}})
        self.assertEqual(result["state"],"deferred")
        self.assertEqual(await self.db.mz2_customer_refunds.count_documents({}),0)
        await self.control(False)
        await replay_pending(self.db,"owner")
        self.assertEqual(await self.db.mz2_customer_refunds.count_documents({}),1)
        await self.assert_balanced(1)  # a refund observation never posts

    async def test_pause_waits_for_inflight_commit_without_partial_journal(self):
        import ledger_core
        original = ledger_core.post_ledger_entry
        first = asyncio.Event(); release = asyncio.Event()
        async def hold(*args, **kwargs):
            result = await original(*args, **kwargs)
            if not first.is_set():
                first.set(); await release.wait()
            return result
        with patch.object(ledger_core,"post_ledger_entry",side_effect=hold):
            writer = asyncio.create_task(self.post_sale())
            await asyncio.wait_for(first.wait(),10)
            pause = asyncio.create_task(self.control(True))
            try:
                await asyncio.sleep(0.1)
                self.assertFalse(pause.done())
                await self.assert_balanced(0)
            finally:
                release.set()
            await asyncio.wait_for(writer,15)
            self.assertTrue((await asyncio.wait_for(pause,15))["paused"])
        await self.assert_balanced(1)
        # A repeated completed operation is a read-only idempotent response.
        # Use a different economic event to prove rejection of NEW writes.
        await self.source("tabby")
        with self.assertRaises(HTTPException) as error:
            await execute(self.db, owner="owner", actor_id="owner",
                actor_name="test", **self.payload("tabby"))
        self.assertEqual(error.exception.status_code,423)

    async def test_pending_event_survives_client_restart_and_failed_replay(self):
        import os
        import ledger_core
        from motor.motor_asyncio import AsyncIOMotorClient
        await self.control(True)
        payment = await self.db.payment_transactions.find_one({"user_id":"owner"})
        await post_bnpl_sale_to_ledger(self.db,user_id="owner",txn=payment)
        # New client/process state reads the durable pause and queue, without
        # copying any in-memory controller state from the first instance.
        restarted = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
        try:
            other = restarted[self.db.name]
            self.assertTrue((await write_state(other,"owner"))["paused"])
            await self.control(False)
            original = ledger_core.post_ledger_entry
            async def abort(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("synthetic replay interruption")
            with patch.object(ledger_core,"post_ledger_entry",side_effect=abort):
                result = await replay_pending(other,"owner")
            self.assertEqual(result["pending_events"],1)
            self.assertEqual(result["processed"],0)
            await self.assert_balanced(0)
            await replay_pending(other,"owner")
            await replay_pending(self.db,"owner")
            await self.assert_balanced(1)
            self.assertEqual(await other.mz2_ingress_events.count_documents({"state":"pending"}),0)
        finally:
            restarted.close()

    async def test_failed_inflight_write_aborts_then_pause_and_retry(self):
        import ledger_core
        original = ledger_core.post_ledger_entry
        async def abort(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("synthetic first-leg failure")
        with patch.object(ledger_core,"post_ledger_entry",side_effect=abort):
            with self.assertRaises(RuntimeError):
                await self.post_sale()
        await self.control(True)
        await self.assert_balanced(0)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}),0)
        await self.control(False)
        await self.post_sale()
        await self.assert_balanced(1)


if __name__ == "__main__":
    unittest.main()
