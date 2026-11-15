"""Android presentation capability/transport contract; synthetic HTTP only."""
import base64
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from operational_push import DEVICES, make_operational_push_router, send_to_provider
from test_operational_push import DB


CAPABILITY = "amasi_android_rtl_v1"


class NativePresentationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DB()
        self.actor = {"id": "staff", "created_by": "owner", "role": "employee",
            "_review_session": {"origin_session_ref": "synthetic-session"}}
        self.registration = {"installation_id": "synthetic_installation_1234",
            "kind": "expo", "token": "ExpoPushToken[synthetic]"}
        app = FastAPI()
        app.include_router(make_operational_push_router(self.db, lambda: self.actor))
        self.client = AsyncClient(transport=ASGITransport(app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        enabled = patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "1"})
        enabled.start()
        self.addCleanup(enabled.stop)
        self.payload = {"title": "موظف تجريبي — تعليمات المهمة", "body": "نفذ التعديل المطلوب\nالطلب TEST-501",
            "event_id": "event-1", "notice_id": "event-1", "instruction_id": "instruction-1",
            "conversation_id": "conversation-1", "priority": "normal", "unread_count": 27}
        self.device = {"kind": "expo", "token": "ExpoPushToken[synthetic]",
            "notification_presentation": CAPABILITY}

    async def request_provider(self, device=None, payload=None, ticket=None):
        requests = []
        def provider(request):
            requests.append((request.url.path, json.loads(request.content)))
            data = {"receipt-synthetic": {"status": "ok"}} if ticket else {"status": "ok", "id": "receipt-synthetic"}
            return httpx.Response(200, json={"data": data})
        client_class = httpx.AsyncClient
        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(provider), **kwargs)
        with patch("operational_push.httpx.AsyncClient", side_effect=client):
            result = await send_to_provider(device or self.device, payload or self.payload, ticket=ticket)
        return result, requests

    async def register(self, **changes):
        response = await self.client.post("/operational-notifications/devices/register", json={**self.registration, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        return self.db.raw[DEVICES].find_one({"active": True})

    async def test_native_capability_is_stored_on_own_session_registration(self):
        row = await self.register(notification_presentation=CAPABILITY)
        self.assertEqual(row["notification_presentation"], CAPABILITY)
        self.assertEqual((row["actor_id"], row["merchant_id"], row["session_id"]),
            ("staff", "owner", "synthetic-session"))

    async def test_old_client_reregistration_explicitly_clears_stale_capability(self):
        before = await self.register(notification_presentation=CAPABILITY)
        after = await self.register()
        self.assertIsNone(after.get("notification_presentation"))
        self.assertEqual(before["generation"], after["generation"])
        self.assertEqual(self.db.raw[DEVICES].count_documents({"active": True}), 1)

    async def test_unknown_capability_is_cleared_instead_of_enabling_new_transport(self):
        await self.register(notification_presentation=CAPABILITY)
        for unsupported in (None, "", "amasi_android_rtl_v2", "ios"):
            with self.subTest(capability=unsupported):
                row = await self.register(notification_presentation=unsupported)
                self.assertIsNone(row.get("notification_presentation"))

    async def test_web_registration_cannot_opt_into_android_transport(self):
        subscription = {"endpoint": "https://fcm.googleapis.com/synthetic",
            "keys": {name: base64.urlsafe_b64encode(bytes(length)).decode()
                for name, length in (("p256dh", 65), ("auth", 16))}}
        with patch("operational_push.web_ready", return_value=True):
            row = await self.register(kind="web", subscription=subscription, notification_presentation=CAPABILITY)
        self.assertIsNone(row.get("notification_presentation"))

    async def test_capable_android_uses_data_only_high_priority_with_scoped_tap_and_content(self):
        result, requests = await self.request_provider()
        self.assertEqual(result, {"status": "receipt_pending", "ticket": "receipt-synthetic"})
        self.assertEqual(len(requests), 1)
        path, message = requests[0]
        self.assertEqual(path, "/--/api/v2/push/send")
        # Expo also treats a top-level channelId as a notification message.
        # The installed native handler sets the existing channel and sound.
        self.assertEqual(set(message), {"to", "data", "priority", "ttl"})
        self.assertEqual((message["priority"], message["ttl"]), ("high", 3600))
        self.assertEqual(message["data"], {
            "event_id": "event-1", "notice_id": "event-1", "instruction_id": "instruction-1",
            "conversation_id": "conversation-1", "priority": "normal",
            "amasi_notification": {"version": 1, "presentation": CAPABILITY,
                "title": self.payload["title"], "body": self.payload["body"], "unread_count": 27}})
        self.assertLessEqual(len(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()), 4000)

    async def test_legacy_or_unknown_clients_keep_standard_notification_transport(self):
        for capability in (None, "", "unknown"):
            with self.subTest(capability=capability):
                device = {**self.device, "notification_presentation": capability}
                _, requests = await self.request_provider(device=device)
                message = requests[0][1]
                self.assertEqual(message["title"], self.payload["title"])
                self.assertEqual(message["body"], self.payload["body"])
                self.assertEqual(message["sound"], "default")
                self.assertEqual(message["priority"], "default")
                self.assertNotIn("amasi_notification", message["data"])

    async def test_only_nonnegative_bounded_integer_count_is_sent(self):
        for count in (None, -1, 1.2, True, "27", 2147483648):
            with self.subTest(count=count):
                _, requests = await self.request_provider(payload={**self.payload, "unread_count": count})
                self.assertNotIn("unread_count", requests[0][1]["data"]["amasi_notification"])
        _, requests = await self.request_provider(payload={**self.payload, "unread_count": 0})
        self.assertEqual(requests[0][1]["data"]["amasi_notification"]["unread_count"], 0)

    async def test_existing_receipt_polls_without_a_new_notification_on_capable_devices(self):
        result, requests = await self.request_provider(ticket="receipt-synthetic")
        self.assertEqual(result["status"], "provider_accepted")
        self.assertEqual(requests, [("/--/api/v2/push/getReceipts", {"ids": ["receipt-synthetic"]})])

    async def test_capable_payload_size_guard_runs_before_provider_submission(self):
        result, requests = await self.request_provider(payload={**self.payload, "instruction_id": "x" * 4500})
        self.assertEqual(result, {"status": "failed", "error": "payload_too_large"})
        self.assertEqual(requests, [])
