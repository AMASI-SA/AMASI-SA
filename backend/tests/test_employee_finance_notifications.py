"""Real Mongo/HTTP employee financial notification contract; no provider requests."""
import asyncio
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI, Request, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo.monitoring import CommandListener

from operational_balance_service import create_movement, save_opening
from operational_balance_store import STATES, read, mutate, digest
from mobile_app_permissions import MOBILE_APP_ACCESS
from mobile_app_request_context import mobile_app_request_user


class Trace(CommandListener):
    def __init__(self): self.commands = []
    def started(self, event): self.commands.append((event.command_name, deepcopy(event.command)))
    def succeeded(self, event): pass
    def failed(self, event): pass


class EmployeeFinanceNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri: self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.trace = Trace()
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[self.trace])
        self.db = self.mongo["employee_finance_notice_" + uuid4().hex]
        self.owner = "merchant"
        self.recipient = "recipient"
        self.employee = "employee-v2"
        self.clock = datetime.now(timezone.utc).isoformat()
        self.actor_id = self.recipient
        await self.db.users.insert_many([
            {"id": self.owner, "role": "owner", "name": "المسجل", "is_active": True},
            {"id": self.recipient, "role": "employee", "name": "الموظف", "created_by": self.owner, "is_active": True},
            {"id": "other", "role": "employee", "name": "آخر", "created_by": self.owner, "is_active": True},
            {"id": "foreign", "role": "employee", "name": "آخر", "created_by": "foreign-owner", "is_active": True},
            {"id": "driver", "role": "store_driver", "created_by": self.owner, "is_active": True},
        ])
        await self.db.mezan_employees_v2.insert_many([
            {"id": self.employee, "user_id": self.owner, "account_user_id": self.recipient, "display_name": "موظف الاختبار", "status": "active"},
            {"id": "other-employee", "user_id": self.owner, "account_user_id": "other", "display_name": "موظف آخر", "status": "active"},
            {"id": "foreign-employee", "user_id": "foreign-owner", "account_user_id": "foreign", "display_name": "آخر", "status": "active"},
        ])
        await self.db[MOBILE_APP_ACCESS].insert_many([
            {"owner_user_id": "foreign-owner" if actor == "foreign" else self.owner, "user_id": actor,
             "enabled": True, "permissions": ["app.page.my_products"]}
            for actor in (self.recipient, "other", "foreign")])
        await self.db.mz2_financial_accounts.insert_one({"id": "bank", "user_id": self.owner,
            "name": "بنك الاختبار", "account_type": "bank", "currency": "SAR", "status": "active"})
        await self.db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": self.owner, "name": "مورد الاختبار", "status": "active"})
        for kind, identity, value, side in [("bank", "bank", "10000", "for_us"),
                ("employee", self.employee, "2000", "for_party"), ("employee", "other-employee", "1000", "for_party"),
                ("employee_custody", self.employee, "500", "for_us"), ("supplier", "supplier", "1000", "for_party")]:
            await save_opening(self.db, self.owner, self.owner, {"request_id": "opening-"+kind+identity,
                "party_type": kind, "party_id": identity, "amount": value, "currency": "SAR", "direction": side}, clock=self.clock)
        await save_opening(self.db, self.owner, self.owner, {"request_id": "start-test"}, finish=True, clock=self.clock)

    async def asyncTearDown(self):
        if hasattr(self, "client"): await self.client.aclose()
        if hasattr(self, "db"):
            await self.mongo.drop_database(self.db.name)
            self.mongo.close()

    def movement(self, **changes):
        return {"request_id": "payment-1", "party_type": "employee", "party_id": self.employee,
            "bank_id": "bank", "amount": "100.00", "currency": "SAR", "direction": "outgoing",
            "kind": "payment", "note": "دفعة اختبار", "receipt_id": None, "order_number": None,
            "reference": "", "allocations": [], **changes}

    async def pay(self, **changes):
        return await create_movement(self.db, self.owner, self.owner, self.movement(**changes), clock=self.clock)

    async def api(self):
        from employee_finance_notification_routes import make_employee_finance_notification_router
        from employee_finance_notifications import ensure_financial_notification_indexes
        await ensure_financial_notification_indexes(self.db)
        async def current(request: Request):
            actor = await self.db.users.find_one({"id": self.actor_id}, {"_id": 0})
            return await mobile_app_request_user(self.db, {**actor, "_session_client": "amasi_mobile"},
                path=request.url.path, method=request.method)
        app = FastAPI()
        app.include_router(make_employee_finance_notification_router(self.db, current), prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app), base_url="http://test")
        return self.client

    async def drain(self, enabled=False):
        from employee_finance_notifications import ensure_financial_notification_indexes, retry_employee_financial_notifications
        await ensure_financial_notification_indexes(self.db)
        with patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "1" if enabled else "0"}):
            return await retry_employee_financial_notifications(self.db)

    async def notices(self):
        from employee_finance_notifications import NOTICES
        return await self.db[NOTICES].find({}).sort("id", 1).to_list(100)

    async def test_committed_payment_has_durable_intent_once(self):
        replies = await asyncio.gather(*[self.pay() for _ in range(5)])
        movement = replies[0]
        self.assertTrue(all(row == movement for row in replies))
        again = await self.pay()
        self.assertEqual(movement, again)
        state = await read(self.db, self.owner)
        self.assertEqual(len(state["movements"]), 1)
        pending = state.get("employee_finance_notification_pending", [])
        self.assertEqual(len(pending), 1, "Successful employee movement must atomically retain its notification intent")
        self.assertEqual(pending[0]["amount"], "100.00")
        self.assertEqual(pending[0]["recipient_id"], self.recipient)
        self.assertEqual(pending[0]["movement_id"], movement["id"])
        self.assertEqual(pending[0]["recorded_by"], {"id": self.owner, "name": "المسجل"})

    async def test_failed_movement_cas_cannot_materialize_notice(self):
        before = await read(self.db, self.owner)
        original = AsyncIOMotorCollection.replace_one
        class Rejected:
            matched_count = 0
        async def reject(collection, *args, **kwargs):
            if collection.name == STATES: return Rejected()
            return await original(collection, *args, **kwargs)
        with patch.object(AsyncIOMotorCollection, "replace_one", reject):
            with self.assertRaises(HTTPException) as error:
                await self.pay()
        self.assertEqual(error.exception.detail["code"], "operational_concurrent_change")
        self.assertEqual(await read(self.db, self.owner), before)
        await self.drain()
        self.assertEqual(await self.notices(), [])

    async def test_crash_before_materialization_retries_without_read_reset(self):
        import employee_finance_notifications as notices
        from pymongo.errors import AutoReconnect
        await self.pay()
        state = await read(self.db, self.owner)
        intent = deepcopy(state[notices.PENDING][0])
        original = AsyncIOMotorCollection.update_one
        async def fail(collection, *args, **kwargs):
            if collection.name == notices.NOTICES: raise AutoReconnect("synthetic retry")
            return await original(collection, *args, **kwargs)
        with patch.object(AsyncIOMotorCollection, "update_one", fail):
            with self.assertRaises(AutoReconnect): await self.drain()
        self.assertEqual(len((await read(self.db, self.owner))["movements"]), 1)
        self.assertTrue((await read(self.db, self.owner))[notices.PENDING_ACTIVE])
        await self.drain()
        await self.api()
        response = await self.client.post("/api/employee-finance/notifications/"+intent["id"]+"/read")
        self.assertEqual(response.status_code, 200, response.text)
        original_notice = (await self.notices())[0]
        async def restore_queue(state):
            state[notices.PENDING] = [intent]
            state[notices.PENDING_ACTIVE] = True
        await mutate(self.db, self.owner, restore_queue)
        await self.drain()
        self.assertEqual(await self.notices(), [original_notice])
        self.assertEqual((await read(self.db, self.owner))[notices.PENDING], [])

    async def test_drain_cas_does_not_erase_a_concurrent_movement_intent(self):
        import employee_finance_notifications as notices
        await self.pay()
        original = AsyncIOMotorCollection.update_one
        appended = False
        async def interleave(collection, *args, **kwargs):
            nonlocal appended
            result = await original(collection, *args, **kwargs)
            if collection.name == notices.NOTICES and not appended:
                appended = True
                await self.pay(request_id="concurrent-next", amount="50")
            return result
        with patch.object(AsyncIOMotorCollection, "update_one", interleave): await self.drain()
        state = await read(self.db, self.owner)
        self.assertEqual(len(state["movements"]), 2)
        self.assertEqual(len(state[notices.PENDING]), 2)
        await self.drain()
        self.assertEqual(len(await self.notices()), 2)
        self.assertEqual((await read(self.db, self.owner))[notices.PENDING], [])

    async def test_financial_writer_retries_after_worker_revision_change(self):
        import employee_finance_notifications as notices
        await self.pay()
        calls = 0
        async def edit(state):
            nonlocal calls
            calls += 1
            if calls == 1: await self.drain()
            state["test_marker"] = "saved"
        await mutate(self.db, self.owner, edit)
        state = await read(self.db, self.owner)
        self.assertEqual(calls, 2)
        self.assertEqual(state[notices.PENDING], [])
        self.assertFalse(state[notices.PENDING_ACTIVE])
        self.assertEqual(state["test_marker"], "saved")
        self.assertEqual(len(await self.notices()), 1)

    async def test_disabled_push_drains_bounded_intents_without_balance_reads(self):
        import employee_finance_notifications as notices
        for n in range(12): await self.pay(request_id=f"bounded-{n}", amount="1")
        await notices.ensure_financial_notification_indexes(self.db)
        self.trace.commands.clear()
        with patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "0"}):
            result = await notices.retry_employee_financial_notifications(self.db)
        self.assertEqual(result, {"materialized": 10, "dispatched": 0})
        reads = [command for name, command in self.trace.commands if name == "find"]
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0]["find"], STATES)
        self.assertEqual(reads[0]["projection"], {"_id": 1, "owner_id": 1, "revision": 1, notices.PENDING: {"$slice": 11}})
        self.assertEqual(reads[0]["maxTimeMS"], 1500)
        self.assertFalse(any(name in {"aggregate", "createIndexes"} for name, _ in self.trace.commands))
        self.assertEqual(len((await read(self.db, self.owner))[notices.PENDING]), 2)
        await self.drain()
        self.assertEqual(len(await self.notices()), 12)
        self.assertFalse((await read(self.db, self.owner))[notices.PENDING_ACTIVE])

    async def test_pending_index_ignores_owners_without_notifications(self):
        import employee_finance_notifications as notices
        await self.pay()
        await notices.ensure_financial_notification_indexes(self.db)
        await self.db[STATES].insert_many([{"_id": f"unused-{n}", "owner_id": f"unused-{n}", "revision": 0} for n in range(1000)])
        explain = await self.db.command("explain", {"find": STATES, "filter": {notices.PENDING_ACTIVE: True},
            "projection": {"_id": 1, "owner_id": 1, "revision": 1, notices.PENDING: {"$slice": 11}}, "limit": 1,
            "sort": {"_id": 1}}, verbosity="executionStats")
        self.assertIn("employee_finance_notice_pending", str(explain["queryPlanner"]))
        self.assertLessEqual(explain["executionStats"]["totalDocsExamined"], 2)

    async def test_new_backdated_entry_is_fresh_but_legacy_replay_is_not_backfilled(self):
        import employee_finance_notifications as notices
        await self.pay(business_date="2026-10-05")
        with patch.object(notices, "stage_employee_financial_notifications", AsyncMock()):
            await self.pay(request_id="historic-before-feature", amount="10")
        await self.pay(request_id="historic-before-feature", amount="10")
        state = await read(self.db, self.owner)
        self.assertEqual(len(state["movements"]), 2)
        self.assertEqual(len(state[notices.PENDING]), 1)
        self.assertEqual(state[notices.PENDING][0]["created_at"], self.clock)
        self.assertEqual(state[notices.PENDING][0]["business_date"], "2026-10-05")
        self.assertNotEqual(state[notices.PENDING][0]["created_at"][:10], "2026-10-05")

    async def test_custody_source_notifies_its_employee_and_preserves_money_gates(self):
        import employee_finance_notifications as notices
        await self.pay(party_type="supplier", party_id="supplier", bank_id=self.employee, source_account_type="employee_custody")
        await self.drain()
        rows = await self.notices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recipient_id"], self.recipient)
        self.assertEqual(rows[0]["kind"], "custody_spend")
        self.assertEqual(rows[0]["amount"], "100.00")
        self.assertNotIn("مورد الاختبار", rows[0]["body"])
        self.assertNotIn("balance_after", rows[0])
        with self.assertRaises(HTTPException) as error:
            await self.pay(request_id="forbidden-custody-employee", party_id="other-employee",
                bank_id=self.employee, source_account_type="employee_custody")
        self.assertEqual(error.exception.detail["code"], "operational_custody_source_invalid")
        await self.drain()
        self.assertEqual(await self.notices(), rows)

    async def test_entry_types_and_self_recorded_movement_are_distinct(self):
        import employee_finance_notifications as notices
        commands = [
            {"request_id": "collection", "direction": "incoming", "kind": "collection"},
            {"request_id": "accrual", "kind": "correction", "bank_id": None, "workspace_reason": "accrual"},
            {"request_id": "adjustment", "kind": "correction", "direction": "incoming", "bank_id": None},
            {"request_id": "custody-fund", "party_type": "employee_custody"},
            {"request_id": "custody-return", "party_type": "employee_custody", "direction": "incoming", "kind": "collection"},
            {"request_id": "custody-adjustment", "party_type": "employee_custody", "direction": "incoming", "kind": "correction", "bank_id": None},
        ]
        for command in commands: await self.pay(**command)
        await create_movement(self.db, self.owner, self.recipient, self.movement(request_id="self-recorded"), clock=self.clock)
        pending = (await read(self.db, self.owner))[notices.PENDING]
        self.assertEqual({r["kind"] for r in pending}, {"collection", "accrual", "debit_adjustment", "custody_grant", "custody_return", "custody_credit_adjustment", "payment"})
        self.assertTrue(any(r["recorded_by"]["id"] == self.recipient for r in pending))
        self.assertTrue(all("تم تحويل" not in r["body"] for r in pending))

    async def test_external_salary_settlement_uses_safe_exact_counterpart_reference(self):
        from operational_balance_workspace import WorkspaceEntry, save_entry
        await self.db.mz2_external_parties_v2.insert_one({"id": "external", "user_id": self.owner, "name": "جهة اختبار", "status": "active", "currency": "SAR"})
        # The canonical external directory uses its existing collection.
        from operational_balance_sources import ENTITY_COLLECTIONS
        if ENTITY_COLLECTIONS["external_person"] != "mz2_external_parties_v2":
            await self.db[ENTITY_COLLECTIONS["external_person"]].insert_one({"id": "external", "user_id": self.owner, "name": "جهة اختبار", "status": "active", "currency": "SAR"})
        payload = WorkspaceEntry(request_id="external-salary", expected_session_scope=digest([self.owner,self.owner]),
            party_type="external_person", party_id="external", side="for_party", amount="80", business_date=self.clock[:10],
            reason="salary_settlement", employee_id=self.employee).command()
        actor = await self.db.users.find_one({"id": self.owner}, {"_id": 0})
        result = await save_entry(self.db, self.owner, actor, payload, "mezan2")
        self.assertEqual(await save_entry(self.db, self.owner, actor, payload, "mezan2"), result)
        await self.drain()
        rows = await self.notices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["movement_id"], result["movement_id"]+":counterpart")
        self.assertRegex(rows[0]["id"], r"^fn_[a-f0-9]{64}$")
        self.assertEqual(rows[0]["amount"], "80.00")

    async def test_self_only_http_detail_read_count_and_snapshot(self):
        import employee_finance_notifications as notices
        await self.pay()
        await self.drain()
        await self.api()
        identity = (await self.notices())[0]["id"]
        listing = await self.client.get("/api/employee-finance/notifications/me")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.headers["Cache-Control"], "no-store")
        self.assertEqual(listing.json()["items"][0]["id"], identity)
        detail = await self.client.get("/api/employee-finance/notifications/"+identity)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["recorded_by"], {"id": self.owner, "name": "المسجل"})
        self.assertEqual(detail.json()["read_at"], None)
        self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 1)
        await self.db.users.update_one({"id": self.owner}, {"$set": {"name": "اسم جديد"}})
        self.assertEqual((await self.client.get("/api/employee-finance/notifications/"+identity)).json()["recorded_by"]["name"], "المسجل")
        self.assertEqual((await self.client.post("/api/employee-finance/notifications/"+identity+"/read")).json(), {"ok": True})
        read_at = (await self.notices())[0]["read_at"]
        self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 0)
        self.assertEqual((await self.client.post("/api/employee-finance/notifications/"+identity+"/read")).status_code, 200)
        self.assertEqual((await self.notices())[0]["read_at"], read_at)

    async def test_unrelated_foreign_owner_driver_and_relinked_accounts_are_denied(self):
        import employee_finance_notifications as notices
        await self.pay()
        await self.drain()
        await self.api()
        identity = (await self.notices())[0]["id"]
        for actor in ("other", "foreign", self.owner, "driver"):
            self.actor_id = actor
            self.assertIn((await self.client.get("/api/employee-finance/notifications/"+identity)).status_code, (403,404))
            self.assertIn((await self.client.post("/api/employee-finance/notifications/"+identity+"/read")).status_code, (403,404))
        self.actor_id = self.recipient
        await self.db.mezan_employees_v2.update_one({"id": self.employee}, {"$set": {"account_user_id": "other"}})
        self.assertEqual((await self.client.get("/api/employee-finance/notifications/"+identity)).status_code, 403)
        self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 0)
        self.assertIsNone((await self.notices())[0]["read_at"])

    async def test_workspace_only_access_is_current_and_self_scoped(self):
        import employee_finance_notifications as notices
        from mobile_app_permissions import mobile_app_access_for_user, OPERATIONAL_APP_READ
        from operational_balance_access import POLICY_FIELD
        from test_operational_balance_workspace import resource
        # Canonical scoped-workspace policy; no separate native page grant.
        policy = {"owner_id": self.owner, "version": 1, "enabled": True,
            "operations": ["supplier.payment"],
            "resources": [resource("supplier", "supplier"), resource("bank", "bank")]}
        await self.db[MOBILE_APP_ACCESS].delete_one({"user_id": self.recipient})
        await self.db.users.update_one({"id": self.recipient}, {"$set": {POLICY_FIELD: policy}})
        actor = await self.db.users.find_one({"id": self.recipient}, {"_id": 0})
        access = await mobile_app_access_for_user(self.db, actor)
        self.assertTrue(access["enabled"])
        self.assertIn(OPERATIONAL_APP_READ, access["permissions"])
        await self.pay()
        await self.drain()
        await self.api()
        identity = (await self.notices())[0]["id"]
        base = "/api/employee-finance/notifications/"
        response = await self.client.get(base + "me")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["id"] for row in response.json()["items"]], [identity])
        self.assertEqual((await self.client.get(base + identity)).status_code, 200)
        self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 1)
        device = {"kind": "expo", "financial_notifications_version": 1,
            "actor_id": self.recipient, "merchant_id": self.owner}
        job = {"notification_type": "employee_finance", "financial_notice_id": identity,
            "event_id": identity, "actor_id": self.recipient, "merchant_id": self.owner}
        self.assertEqual((await notices.financial_push_payload(self.db, device, job))["recipient_id"], self.recipient)
        # Current policy revocation/owner mismatch immediately removes access.
        for changes in ({"enabled": False}, {"owner_id": "foreign-owner"}):
            await self.db.users.update_one({"id": self.recipient}, {"$set": {POLICY_FIELD: {**policy, **changes}}})
            self.assertEqual((await self.client.get(base + "me")).status_code, 403)
            self.assertEqual((await self.client.get(base + identity)).status_code, 403)
            self.assertEqual((await self.client.post(base + identity + "/read")).status_code, 403)
            self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 0)
            self.assertIsNone(await notices.financial_push_payload(self.db, device, job))
            self.assertIsNone((await self.notices())[0]["read_at"])
        await self.db.users.update_one({"id": self.recipient}, {"$set": {POLICY_FIELD: policy}})
        self.actor_id = "other"
        self.assertEqual((await self.client.get(base + identity)).status_code, 404)
        self.actor_id = self.recipient
        self.assertEqual((await self.client.post(base + identity + "/read")).json(), {"ok": True})

    async def test_employee_lifecycle_and_mobile_access_are_rechecked(self):
        import employee_finance_notifications as notices
        await self.pay()
        await self.drain()
        await self.api()
        identity = (await self.notices())[0]["id"]
        for changes in ({"active": False}, {"disabled": True}, {"status": "inactive"}, {"archived": True}):
            await self.db.mezan_employees_v2.update_one({"id": self.employee}, {"$set": changes})
            self.assertEqual((await self.client.get("/api/employee-finance/notifications/"+identity)).status_code, 403)
            self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 0)
            await self.db.mezan_employees_v2.update_one({"id": self.employee}, {"$unset": {k: "" for k in changes}, "$set": {"status": "active"}} if "status" not in changes else {"$set": {"status": "active"}})
        await self.db[MOBILE_APP_ACCESS].update_one({"user_id": self.recipient}, {"$set": {"enabled": False}})
        self.assertEqual((await self.client.get("/api/employee-finance/notifications/me")).status_code, 403)
        self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 0)

    async def test_list_cursor_is_bounded_private_and_does_not_mark_read(self):
        import employee_finance_notifications as notices
        for n in range(12): await self.pay(request_id=f"page-{n}", amount="1")
        await self.drain(); await self.drain(); await self.api()
        first = await self.client.get("/api/employee-finance/notifications/me")
        self.assertEqual(len(first.json()["items"]), 10)
        second = await self.client.get("/api/employee-finance/notifications/me", params={"cursor": first.json()["next_cursor"]})
        self.assertEqual(len(second.json()["items"]), 2)
        self.assertEqual(len({r["id"] for r in first.json()["items"]+second.json()["items"]}), 12)
        self.assertIsNone(second.json()["next_cursor"])
        self.assertEqual(await notices.financial_unread_count(self.db, self.owner, self.recipient), 12)
        self.assertEqual((await self.client.get("/api/employee-finance/notifications/me?limit=11")).status_code, 422)
        self.assertEqual((await self.client.get("/api/employee-finance/notifications/me?cursor=bad")).status_code, 422)
        self.actor_id = "other"
        self.assertEqual((await self.client.get("/api/employee-finance/notifications/me", params={"cursor":first.json()["next_cursor"]})).json()["items"], [])


if __name__ == "__main__": unittest.main()
