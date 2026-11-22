"""Financial push transport contracts; provider requests stay in a mock transport."""
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from operational_push import (
    ANDROID_RTL_PRESENTATION, DEVICES, OUTBOX, enqueue_operational_push,
    make_operational_push_router, now, send_to_provider,
)
from test_operational_push import DB


NOTICE_ID = "fn_" + "a" * 64


class EmployeeFinancialPushTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DB()
        enabled = patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "1"})
        enabled.start()
        self.addCleanup(enabled.stop)
        self.device = {
            "_id": "financial-test-device", "kind": "expo", "actor_id": "recipient",
            "merchant_id": "owner", "session_id": "session", "generation": "generation",
            "installation_id": "financial_test_installation", "active": True,
            "token": "ExpoPushToken[synthetic]", "notification_presentation": ANDROID_RTL_PRESENTATION,
            "financial_notifications_version": 1,
        }
        self.db.raw[DEVICES].insert_one(dict(self.device))
        self.payload = {
            "event_id": NOTICE_ID, "financial_notice_id": NOTICE_ID,
            "notification_type": "employee_finance", "recipient_id": "recipient",
            "title": "أماسي — حركة مالية", "body": "تم تسجيل دفعة بمبلغ 500.00 ريال بواسطة موظف تجريبي",
            "priority": "normal", "unread_count": 1,
        }

    async def enqueue(self, **changes):
        await enqueue_operational_push(self.db, **{
            "recipient_id": "recipient", "event_id": NOTICE_ID, "merchant_id": "owner",
            "notification_type": "employee_finance", "financial_notice_id": NOTICE_ID,
            "created_at": now(), **changes,
        })

    async def test_enqueue_is_typed_merchant_scoped_mobile_only_and_idempotent(self):
        for identity, merchant, kind in (("foreign", "other-owner", "expo"), ("web", "owner", "web")):
            self.db.raw[DEVICES].insert_one({**self.device, "_id": identity,
                "installation_id": identity + "_installation", "merchant_id": merchant, "kind": kind})
        self.db.raw[DEVICES].insert_one({**self.device, "_id": "older-app",
            "installation_id": "older_app_installation", "financial_notifications_version": None})
        await self.enqueue()
        await self.enqueue()
        jobs = list(self.db.raw[OUTBOX].find())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["device_id"], self.device["_id"])
        self.assertEqual(jobs[0]["merchant_id"], "owner")
        self.assertEqual(jobs[0]["notification_type"], "employee_finance")
        self.assertEqual(jobs[0]["financial_notice_id"], NOTICE_ID)
        self.assertEqual(jobs[0].get("instruction_id", ""), "")

    async def test_financial_enqueue_rejects_incomplete_or_mixed_identity(self):
        for changes in (
            {"merchant_id": None}, {"financial_notice_id": ""},
            {"financial_notice_id": "fn_" + "b" * 64}, {"instruction_id": "instruction"},
            {"notification_type": "unknown"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await self.enqueue(**changes)
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 0)

    async def provider_request(self, device):
        requests = []

        def provider(request):
            requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"data": {"status": "ok", "id": "synthetic-finance-ticket"}})

        client_class = httpx.AsyncClient

        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(provider), **kwargs)

        with patch("operational_push.httpx.AsyncClient", side_effect=client):
            result = await send_to_provider(device, self.payload)
        self.assertEqual(result, {"status": "receipt_pending", "ticket": "synthetic-finance-ticket"})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], "/--/api/v2/push/send")
        return requests[0][1]

    async def test_native_transport_keeps_financial_tap_and_official_text(self):
        message = await self.provider_request(self.device)
        data = message["data"]
        self.assertEqual(data["notification_type"], "employee_finance")
        self.assertEqual(data["financial_notice_id"], NOTICE_ID)
        self.assertEqual(data["recipient_id"], "recipient")
        self.assertEqual(data["event_id"], NOTICE_ID)
        self.assertNotIn("instruction_id", data)
        self.assertEqual(data["amasi_notification"]["body"], self.payload["body"])
        self.assertEqual(data["amasi_notification"]["unread_count"], 1)
        self.assertNotIn("title", message)
        self.assertNotIn("channelId", message)
        self.assertLess(len(json.dumps(message, ensure_ascii=False).encode()), 4000)

    async def test_legacy_transport_retains_financial_tap_and_text(self):
        message = await self.provider_request({**self.device, "notification_presentation": None})
        self.assertEqual(message["title"], self.payload["title"])
        self.assertEqual(message["body"], self.payload["body"])
        self.assertEqual(message["data"]["financial_notice_id"], NOTICE_ID)
        self.assertEqual(message["data"]["notification_type"], "employee_finance")

    async def test_accepted_financial_event_is_not_replayed_after_relogin(self):
        await self.enqueue()
        self.db.raw[OUTBOX].update_one({}, {"$set": {"status": "provider_accepted", "ticket": "accepted"}})
        self.db.raw[DEVICES].update_one({"_id": self.device["_id"]}, {"$set": {"generation": "next-generation"}})
        await self.enqueue()
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 1)

    async def test_stale_financial_event_does_not_create_phone_job(self):
        await self.enqueue(created_at=now() - timedelta(days=3))
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 0)

    async def test_device_capability_requires_new_client_and_clears_on_downgrade(self):
        actor = {"id": "recipient", "created_by": "owner", "role": "employee",
            "_review_session": {"origin_session_ref": "session"}}
        app = FastAPI()
        app.include_router(make_operational_push_router(self.db, lambda: actor))
        base = {"installation_id": "financial_test_installation", "kind": "expo",
            "token": "ExpoPushToken[capability-synthetic]"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            for capability, expected in ((1, 1), (None, None), (1, 1), (2, None)):
                body = {**base, **({"financial_notifications_version": capability} if capability is not None else {})}
                response = await client.post("/operational-notifications/devices/register", json=body)
                self.assertEqual(response.status_code, 200, response.text)
                device = self.db.raw[DEVICES].find_one({"token": base["token"]})
                self.assertEqual(device.get("financial_notifications_version"), expected)
            for invalid in (True, "1", 1.5):
                response = await client.post("/operational-notifications/devices/register",
                    json={**base, "financial_notifications_version": invalid})
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
