"""Real replica-set pause barrier, durable ingress and production router tests."""
import asyncio
import unittest
from decimal import Decimal
from mz2_native_fixture import provision_native_opening
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from accounting_atomic import atomic_owner
from stock_component_consumption_service import UNITS as COMPONENT_UNITS
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
        await provision_native_opening(self.db)
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
        rows = await self.db.accounting_general_ledger_v2.find({"entry_type": {"$ne": "opening_balance"}}).to_list(100)
        groups = {r["txn_group_id"] for r in rows}
        self.assertEqual(len(groups), expected_groups)
        for group in groups:
            legs = [r for r in rows if r["txn_group_id"] == group]
            self.assertEqual(len(legs), 3)
            self.assertEqual(sum(Decimal(r["amount"]) for r in legs if r["side"] == "debit"), 115)
            self.assertEqual(sum(Decimal(r["amount"]) for r in legs if r["side"] == "credit"), 115)

    async def test_ui_api_paused_reads_work_and_writes_fail(self):
        policy = await self.db.mz2_sales_tax_policies.find_one({"_id":"owner"})
        settings = await self.db.settings.find_one({"user_id":"owner"})
        response = await self.client.put(self.base + "/write-control", json={
            "paused": True, "revision": 0, "reason": "maintenance"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["paused"])
        self.assertEqual((await self.client.get(self.base + "/sales-tax")).status_code, 200)
        self.assertEqual((await self.client.get("/financial-provider-apps")).status_code,200)
        preview = await self.client.post(self.base + "/receivables/preview", json=self.payload())
        self.assertEqual(preview.status_code, 200, preview.text)
        before = await self.count_writes()
        calls = [
            self.client.post(self.base + "/receivables/execute", json={**self.payload(),
                "preview_hash": preview.json()["preview_hash"]}),
            self.client.put(self.base + "/sales-tax", json={"rate":"20","revision":1,
                "effective_at":"2020-01-01T00:00:00Z","reason":"blocked"}),
            self.client.patch(self.base + "/settlements/drafts/not-present", json={"notes":"blocked"}),
            self.client.post("/financial-provider-apps/tamara/tax-invoices", json={
                "invoice_number":"PAUSED-SYN", "issue_date":"2020-01-02",
                "net_amount":100,"vat_amount":15,"total_amount":115}),
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
        self.assertTrue((await write_state(self.db,"other"))["paused"])
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
        import accounting_recognition_native as native
        original = native.post_journal_v2
        first = asyncio.Event(); release = asyncio.Event()
        async def hold(*args, **kwargs):
            result = await original(*args, **kwargs)
            if not first.is_set():
                first.set(); await release.wait()
            return result
        with patch.object(native,"post_journal_v2",side_effect=hold):
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
        import accounting_recognition_native as native
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
            original = native.post_journal_v2
            async def abort(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("synthetic replay interruption")
            with patch.object(native,"post_journal_v2",side_effect=abort):
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
        import accounting_recognition_native as native
        original = native.post_journal_v2
        async def abort(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("synthetic first-leg failure")
        with patch.object(native,"post_journal_v2",side_effect=abort):
            with self.assertRaises(RuntimeError):
                await self.post_sale()
        await self.control(True)
        await self.assert_balanced(0)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}),0)
        await self.control(False)
        await self.post_sale()
        await self.assert_balanced(1)



class FailClosedWriteControlTests(unittest.IsolatedAsyncioTestCase):
    """Unconfigured owners remain paused until the existing owner control API resumes."""
    configure = fixtures.WorkflowTests.configure
    source = fixtures.WorkflowTests.source
    payload = fixtures.WorkflowTests.payload
    count_writes = fixtures.WorkflowTests.count_writes
    asyncTearDown = fixtures.WorkflowTests.asyncTearDown
    assert_balanced = WriteControlTests.assert_balanced

    async def asyncSetUp(self):
        await fixtures.WorkflowTests.asyncSetUp(self)
        await provision_native_opening(self.db)
        await self.client.aclose()
        # Remove only this unique synthetic DB's control fixture. No runtime
        # initializer is involved in simulating a first deployment.
        self.native_backend = {k: v for k, v in (await self.db.mz2_atomic_owners.find_one({"_id": "owner"})).items() if k.startswith("ledger_backend_")}
        await self.db.mz2_atomic_owners.delete_one({"_id": "owner"})
        self.app = FastAPI()

        async def authenticated_actor():
            return {"id": self.actor}

        self.app.include_router(
            make_financial_provider_apps_router(self.db, authenticated_actor),
            prefix="/api",
        )
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://local")
        self.control_url = "/api/financial-provider-apps/accounting-module/write-control"

    async def resume(self):
        response = await self.client.put(self.control_url, json={
            "paused": False, "revision": 0, "reason": "explicit synthetic owner resume",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["paused"])
        self.assertEqual(response.json()["revision"], 1)
        return response

    async def test_missing_row_get_is_paused_revision_zero_without_bootstrap(self):
        self.assertEqual((await write_state(self.db, "owner"))["revision"], 0)
        self.assertTrue((await write_state(self.db, "owner"))["paused"])
        response = await self.client.get(self.control_url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            {key: response.json()[key] for key in ("paused", "revision", "can_manage")},
            {"paused": True, "revision": 0, "can_manage": True},
        )
        self.assertIsNone(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}))
        self.assertEqual(await self.db.mz2_write_control_audit.count_documents({}), 0)

    async def test_missing_field_is_paused_and_read_leaves_existing_row_unchanged(self):
        before = {"_id": "owner", "revision": 7, "unrelated": "preserve"}
        await self.db.mz2_atomic_owners.insert_one(before)
        response = await self.client.get(self.control_url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["paused"])
        self.assertEqual(response.json()["revision"], 0)
        self.assertEqual(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}), before)

    async def test_ordinary_atomic_rejects_unconfigured_or_paused_before_callback(self):
        before_writes = await self.count_writes()
        location = {
            "_id": "synthetic-location", "user_id": "owner",
            "occupancy": {"items": [
                {"item_type": "product", "product_id": "synthetic-product", "quantity": 3},
                {"item_type": "stock_component", "resource_id": "synthetic-box", "quantity": 6},
            ]},
        }
        await self.db.warehouse_locations.insert_one(location)
        for pause_fields in (None, {}, {"writes_paused": True},
                             {"writes_paused": None}, {"writes_paused": 0}):
            with self.subTest(pause_fields=pause_fields):
                await self.db.mz2_atomic_owners.delete_one({"_id": "owner"})
                before = None
                if pause_fields is not None:
                    before = {"_id": "owner", "revision": 7, **pause_fields}
                    await self.db.mz2_atomic_owners.insert_one(before)
                called = False

                async def forbidden_work(scoped):
                    nonlocal called
                    called = True
                    await scoped.general_ledger.insert_one({
                        "user_id": "owner", "status": "draft", "amount": 1,
                    })
                    await scoped.warehouse_locations.update_one(
                        {"_id": location["_id"]}, {"$set": {"occupancy.items": []}})
                    await scoped[COMPONENT_UNITS].insert_one({
                        "user_id": "owner", "resource_id": "synthetic-box",
                    })

                with self.assertRaises(HTTPException) as error:
                    await atomic_owner(self.db, "owner", forbidden_work)
                self.assertEqual(error.exception.status_code, 423)
                self.assertEqual(error.exception.detail["code"], "mz2_writes_paused")
                self.assertFalse(called)
                self.assertEqual(await self.count_writes(), before_writes)
                self.assertEqual(await self.db.warehouse_locations.find_one(
                    {"_id": location["_id"]}), location)
                self.assertEqual(await self.db[COMPONENT_UNITS].count_documents({}), 0)
                self.assertEqual(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}), before)
                self.assertEqual(await self.db.mz2_write_control_audit.count_documents({}), 0)

    async def test_owner_explicit_resume_bootstraps_once_and_allows_ordinary_atomic(self):
        await self.resume()
        row = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        self.assertFalse(row["writes_paused"])
        self.assertEqual(row["control_revision"], 1)
        self.assertTrue(row["mezan2_managed"])
        self.assertEqual(row["control_changed_by"], "owner")
        for field in ("ledger_backend_state", "ledger_backend_revision",
                      "ledger_backend_contract_revision", "ledger_backend_activation_ref"):
            self.assertNotIn(field, row)
        audits = await self.db.mz2_write_control_audit.find({}).to_list(10)
        self.assertEqual(len(audits), 1)
        self.assertEqual({key: audits[0][key] for key in (
            "user_id", "actor_id", "paused", "previous_paused", "revision")}, {
                "user_id": "owner", "actor_id": "owner", "paused": False,
                "previous_paused": True, "revision": 1,
            })
        self.assertEqual(audits[0]["at"], row["control_changed_at"])
        self.assertEqual(audits[0]["reason"], row["control_reason"])

        async def allowed_work(scoped):
            await scoped.synthetic_control_probe.insert_one({"_id": "committed", "owner": "owner"})
            return "committed"

        self.assertEqual(await atomic_owner(self.db, "owner", allowed_work), "committed")
        self.assertEqual(await self.db.synthetic_control_probe.count_documents({}), 1)
        repeated = await self.client.put(self.control_url, json={
            "paused": False, "revision": 0, "reason": "stale resume",
        })
        self.assertEqual(repeated.status_code, 409, repeated.text)
        self.assertEqual(await self.db.mz2_write_control_audit.count_documents({}), 1)
        self.assertEqual((await write_state(self.db, "owner"))["revision"], 1)
        self.assertTrue((await write_state(self.db, "other"))["paused"])

    async def test_viewer_and_accountant_cannot_bootstrap_or_resume(self):
        await self.db.users.insert_one({
            "id": "accountant", "role": "accountant", "created_by": "owner",
            "accounting_permissions": ["accounting.settlements.view"],
        })
        for actor in ("viewer", "accountant"):
            with self.subTest(actor=actor):
                self.actor = actor
                state = await self.client.get(self.control_url)
                self.assertEqual(state.status_code, 200, state.text)
                self.assertTrue(state.json()["paused"])
                self.assertFalse(state.json()["can_manage"])
                response = await self.client.put(self.control_url, json={
                    "paused": False, "revision": 0, "reason": "unauthorized synthetic resume",
                })
                self.assertEqual(response.status_code, 403, response.text)
                self.assertIsNone(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}))
        self.actor = "owner"
        empty_reason = await self.client.put(self.control_url, json={
            "paused": False, "revision": 0, "reason": "",
        })
        self.assertEqual(empty_reason.status_code, 422, empty_reason.text)
        self.assertIsNone(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}))
        self.assertEqual(await self.db.mz2_write_control_audit.count_documents({}), 0)

    async def assert_unconfigured_ingress_is_durable(self, before):
        if before is not None:
            await self.db.mz2_atomic_owners.insert_one(before)
        payment = await self.db.payment_transactions.find_one({"user_id": "owner"})
        results = await asyncio.gather(*[
            post_bnpl_sale_to_ledger(self.db, user_id="owner", txn={**payment, "source": source})
            for source in ("webhook", "sync", "background")
        ])
        self.assertEqual({result["state"] for result in results}, {"deferred"})
        self.assertTrue(all(result["financial_write"] is False for result in results))
        self.assertEqual(await self.db.mz2_ingress_events.count_documents({"state": "pending"}), 1)
        await self.assert_balanced(0)
        self.assertEqual(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}), before)
        observed = await observe_refund(
            self.db, owner="owner", order_number=payment["order_reference_id"],
            source={"kind": "verified_webhook"}, payload={"status": {"slug": "refunded"}},
        )
        self.assertEqual(observed["state"], "deferred")
        self.assertEqual(await self.db.mz2_customer_refunds.count_documents({}), 0)
        self.assertEqual(await self.db.mz2_ingress_events.count_documents({"state": "pending"}), 2)
        self.assertEqual(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}), before)
        with self.assertRaises(HTTPException) as error:
            await replay_pending(self.db, "owner")
        self.assertEqual(error.exception.status_code, 423)
        await self.resume()
        # Resuming writes does not restore an erased V2 backend contract.
        await replay_pending(self.db, "owner")
        await self.assert_balanced(0)
        self.assertEqual(await self.db.mz2_ingress_events.count_documents({"kind": "sale", "state": "pending"}), 1)
        # Restore only the separately established synthetic backend evidence.
        await self.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": self.native_backend})
        await asyncio.gather(replay_pending(self.db, "owner"), replay_pending(self.db, "owner"))
        await self.assert_balanced(1)
        self.assertEqual(await self.db.mz2_customer_refunds.count_documents({}), 1)
        self.assertEqual(await self.db.mz2_ingress_events.count_documents({"state": "pending"}), 0)
        await replay_pending(self.db, "owner")
        await self.assert_balanced(1)

    async def test_missing_row_ingress_remains_pending_until_owner_resume(self):
        await self.assert_unconfigured_ingress_is_durable(None)

    async def test_missing_field_ingress_remains_pending_until_owner_resume(self):
        await self.assert_unconfigured_ingress_is_durable({"_id": "owner", "revision": 7})


if __name__ == "__main__":
    unittest.main()
