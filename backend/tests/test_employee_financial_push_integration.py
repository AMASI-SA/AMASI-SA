"""Actual committed movement, own-notice HTTP and push worker with disposable Mongo."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

import employee_finance_notifications as finance
import operational_push as push
import test_employee_finance_notifications as fixtures
from employee_finance_notification_routes import make_employee_finance_notification_router
from mobile_app_permissions import MOBILE_APP_ACCESS
from mobile_app_request_context import mobile_app_request_user
from order_tracking_notes_routes import make_order_tracking_notes_router


class EmployeeFinancialPushIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.EmployeeFinanceNotificationTests()
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.db = self.fixture.db
        self.actor_id = self.fixture.recipient
        enabled = patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "1"})
        enabled.start()
        self.addCleanup(enabled.stop)
        await finance.ensure_financial_notification_indexes(self.db)
        await self.db.order_review_auth_sessions_v2.insert_one({
            "_id": "finance-session", "actor_id": self.actor_id, "user_id": self.fixture.owner,
            "state": "active", "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        })
        self.device = {
            "_id": "finance-device", "actor_id": self.actor_id, "merchant_id": self.fixture.owner,
            "generation": "finance-generation", "session_id": "finance-session", "kind": "expo",
            "installation_id": "financial_test_installation", "token": "ExpoPushToken[synthetic]",
            "active": True, "financial_notifications_version": 1,
            "notification_presentation": push.ANDROID_RTL_PRESENTATION,
        }
        await self.db[push.DEVICES].insert_one(dict(self.device))

        async def current(request: Request):
            user = await self.db.users.find_one({"id": self.actor_id}, {"_id": 0})
            return await mobile_app_request_user(self.db, {**user, "_session_client": "amasi_mobile"},
                path=request.url.path, method=request.method)

        app = FastAPI()
        app.include_router(make_order_tracking_notes_router(self.db, current), prefix="/api")
        app.include_router(make_employee_finance_notification_router(self.db, current), prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.sent = []

    async def sender(self, device, payload, **kwargs):
        self.sent.append({"device": device, "payload": payload, **kwargs})
        return {"status": "provider_accepted"}

    async def notice(self):
        movement = await self.fixture.pay()
        await finance.retry_employee_financial_notifications(self.db)
        notice = await self.db[finance.NOTICES].find_one({"movement_id": movement["id"]})
        self.assertIsNotNone(notice)
        self.assertEqual(await self.db[push.OUTBOX].count_documents({}), 1)
        return notice

    async def test_committed_payment_reaches_only_own_device_with_exact_financial_copy(self):
        notice = await self.notice()
        self.assertTrue(await push.deliver_one(self.db, self.sender))
        self.assertEqual(len(self.sent), 1)
        payload = self.sent[0]["payload"]
        self.assertEqual(payload["notification_type"], "employee_finance")
        self.assertEqual(payload["financial_notice_id"], notice["id"])
        self.assertEqual(payload["recipient_id"], self.fixture.recipient)
        self.assertIn("100.00", payload["body"])
        self.assertIn("دفعة لك", payload["body"])
        self.assertIn("المسجل", payload["body"])
        self.assertNotIn("تم تحويل", payload["body"])
        self.assertNotIn("instruction_id", payload)
        self.assertEqual(payload["unread_count"], 1)
        self.assertFalse(await push.deliver_one(self.db, self.sender))

    async def test_combined_http_count_preserves_finance_only_unread_and_legacy_contract(self):
        notice = await self.notice()
        url = "/api/order-tracking-notes/notifications/me?count_only=true&conversation_only=true"
        old = await self.client.get(url)
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(old.json()["unread_count"], 0)
        self.assertNotIn("financial_included", old.json())
        combined = await self.client.get(url + "&include_financial=true")
        self.assertEqual(combined.status_code, 200, combined.text)
        self.assertIs(combined.json()["financial_included"], True)
        self.assertEqual(combined.json()["financial_unread_count"], 1)
        self.assertEqual(combined.json()["unread_count"], 1)
        self.assertEqual(combined.headers["cache-control"], "no-store")
        self.actor_id = "other"
        other = await self.client.get(url + "&include_financial=true")
        self.assertEqual(other.json()["unread_count"], 0)
        self.actor_id = self.fixture.recipient
        read = await self.client.post("/api/employee-finance/notifications/" + notice["id"] + "/read")
        self.assertEqual(read.status_code, 200, read.text)
        after = await self.client.get(url + "&include_financial=true")
        self.assertEqual(after.json()["unread_count"], 0)

    async def test_notice_read_while_count_awaits_suppresses_new_send(self):
        notice = await self.notice()
        original = push._native_unread_count

        async def count_then_read(db, device):
            count = await original(db, device)
            await db[finance.NOTICES].update_one({"_id": notice["id"]},
                {"$set": {"read_at": datetime.now(timezone.utc).isoformat()}})
            return count

        with patch.object(push, "_native_unread_count", side_effect=count_then_read):
            await push.deliver_one(self.db, self.sender)
        self.assertEqual(self.sent, [])
        self.assertEqual((await self.db[push.OUTBOX].find_one())["status"], "discarded")

    async def test_financial_and_instruction_unread_counts_coexist(self):
        await self.notice()
        from operational_instruction_conversations import CONVERSATIONS, EVENTS
        at = datetime.now(timezone.utc)
        await self.db[CONVERSATIONS].insert_one({
            "_id": "thread-finance-coexist", "id": "thread-finance-coexist", "merchant_id": self.fixture.owner,
            "instruction_id": "instruction-coexist", "creator_id": self.fixture.owner,
            "creator_name": "المسجل", "assignee_id": self.fixture.recipient,
            "assignee_name": "الموظف", "assignee_active": True, "state": "active", "order_number": "TEST-100",
        })
        await self.db[EVENTS].insert_many([{
            "_id": "instruction-event-" + str(index), "id": "instruction-event-" + str(index),
            "event_id": "instruction-event-" + str(index), "merchant_id": self.fixture.owner,
            "recipient_id": self.fixture.recipient, "instruction_id": "instruction-coexist",
            "conversation_id": "thread-finance-coexist", "author_id": self.fixture.owner,
            "author_name": "المسجل", "read_at": None, "silent": False, "kind": "message",
            "preview": "تعليمات تجريبية", "body": "تعليمات تجريبية", "created_at": at,
        } for index in range(2)])
        url = "/api/order-tracking-notes/notifications/me?count_only=true&conversation_only=true"
        old = await self.client.get(url)
        self.assertEqual(old.json()["unread_count"], 2)
        combined = await self.client.get(url + "&include_financial=true")
        self.assertEqual(combined.json()["unread_count"], 3)
        self.assertEqual(combined.json()["financial_unread_count"], 1)
        await push.deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["payload"]["unread_count"], 3)

    async def test_employee_account_relink_during_count_suppresses_financial_text(self):
        await self.notice()
        original = push._native_unread_count

        async def count_then_relink(db, device):
            count = await original(db, device)
            await db.mezan_employees_v2.update_one({"id": self.fixture.employee}, {"$set": {"account_user_id": "other"}})
            return count

        with patch.object(push, "_native_unread_count", side_effect=count_then_relink):
            await push.deliver_one(self.db, self.sender)
        self.assertEqual(self.sent, [])

    async def test_app_access_revoked_during_count_suppresses_financial_text(self):
        await self.notice()
        original = push._native_unread_count

        async def count_then_revoke(db, device):
            count = await original(db, device)
            await db[MOBILE_APP_ACCESS].update_one({"user_id": self.fixture.recipient}, {"$set": {"enabled": False}})
            return count

        with patch.object(push, "_native_unread_count", side_effect=count_then_revoke):
            await push.deliver_one(self.db, self.sender)
        self.assertEqual(self.sent, [])

    async def test_device_switch_or_capability_downgrade_during_count_suppresses_send(self):
        for change in ({"actor_id": "other", "generation": "new"}, {"financial_notifications_version": None}):
            with self.subTest(change=change):
                await self.db[push.DEVICES].replace_one({"_id": self.device["_id"]}, dict(self.device))
                await self.db[push.OUTBOX].delete_many({})
                await self.db[finance.NOTICES].delete_many({})
                await self.fixture.pay(request_id="race-" + str(len(change)), reference="race-" + str(len(change)))
                await finance.retry_employee_financial_notifications(self.db)
                self.assertEqual(await self.db[push.OUTBOX].count_documents({}), 1)
                original = push._native_unread_count

                async def count_then_switch(db, device):
                    count = await original(db, device)
                    await db[push.DEVICES].update_one({"_id": device["_id"]}, {"$set": change})
                    return count

                with patch.object(push, "_native_unread_count", side_effect=count_then_switch):
                    await push.deliver_one(self.db, self.sender)
                self.assertEqual(self.sent, [])
                self.assertEqual((await self.db[push.OUTBOX].find_one())["status"], "discarded")

    async def test_old_client_downgrade_before_delivery_preserves_unread_notice(self):
        notice = await self.notice()
        await self.db[push.DEVICES].update_one({"_id": self.device["_id"]}, {"$unset": {"financial_notifications_version": ""}})
        await push.deliver_one(self.db, self.sender)
        self.assertEqual(self.sent, [])
        self.assertEqual((await self.db[push.OUTBOX].find_one())["status"], "discarded")
        self.assertIsNone((await self.db[finance.NOTICES].find_one({"_id": notice["id"]}))["read_at"])

    async def test_provider_failure_retries_same_event_without_reposting_money(self):
        notice = await self.notice()

        async def fail(*args, **kwargs):
            raise TimeoutError("synthetic provider failure")

        await push.deliver_one(self.db, fail)
        self.assertEqual((await self.db[push.OUTBOX].find_one())["status"], "retry")
        await self.fixture.pay()
        await finance.retry_employee_financial_notifications(self.db)
        self.assertEqual(await self.db[finance.NOTICES].count_documents({}), 1)
        self.assertEqual(await self.db[push.OUTBOX].count_documents({}), 1)
        await self.db[push.OUTBOX].update_one({}, {"$set": {"next_at": push.now()}})
        await push.deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["payload"]["event_id"], notice["id"])

    async def test_receipt_poll_keeps_provider_ticket_without_new_financial_hydration(self):
        notice = await self.notice()

        async def accepted_ticket(device, payload, **kwargs):
            return {"status": "receipt_pending", "ticket": "synthetic-accepted-ticket"}

        await push.deliver_one(self.db, accepted_ticket)
        await self.db[push.OUTBOX].update_one({}, {"$set": {"next_at": push.now()}})
        await self.db[finance.NOTICES].update_one({"_id": notice["id"]}, {"$set": {"read_at": push.now().isoformat()}})
        with patch.object(finance, "financial_push_payload", side_effect=AssertionError("Receipt polling must not prepare a new send")):
            await push.deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["ticket"], "synthetic-accepted-ticket")
        self.assertEqual((await self.db[push.OUTBOX].find_one())["status"], "provider_accepted")


if __name__ == "__main__":
    unittest.main()
