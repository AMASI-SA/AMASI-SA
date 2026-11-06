"""Recipient-only HTTP metadata and paging on disposable loopback Mongo."""
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from bson.codec_options import CodecOptions
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

import operational_instruction_notifications as notices
import order_tracking_notes_routes as routes
from mobile_app_permissions import MOBILE_APP_ACCESS
from mobile_app_request_context import mobile_app_request_user
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS


class QueryTrace(CommandListener):
    def __init__(self): self.pipelines = []
    def started(self, event):
        if event.command_name == "aggregate" and event.command.get("aggregate") == notices.NOTIFICATIONS:
            self.pipelines.append(deepcopy(event.command["pipeline"]))
    def succeeded(self, event): pass
    def failed(self, event): pass


class NotificationSenderDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.trace = QueryTrace()
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[self.trace])
        self.db = self.mongo["notification_directory_" + uuid4().hex]
        self.addCleanup(self.mongo.close)
        self.actor = {"id": "recipient", "created_by": "owner", "role": "operations", "_session_client": "amasi_mobile"}
        await self.db.users.insert_many([
            self.actor, {"id": "owner", "role": "owner", "name": "Owner"},
            {"id": "a", "created_by": "owner", "name": "Employee A"},
            {"id": "b", "created_by": "owner", "name": "Employee B"},
            {"id": "foreign", "created_by": "other-merchant", "name": "Private foreign name"},
        ])
        await self.db[MOBILE_APP_ACCESS].insert_one({"owner_user_id": "owner", "user_id": "recipient",
            "enabled": True, "permissions": ["app.page.my_products"]})
        self.when = datetime(2026, 11, 6, tzinfo=timezone.utc)
        await self.db[ORDER_TRACKING_INSTRUCTIONS].insert_one({"id": "instruction", "user_id": "owner", "created_by": "a",
            "created_by_name": "Employee A", "status": "active", "order_number": "synthetic", "note": "private full instruction"})
        await self.db[notices.NOTIFICATIONS].insert_many([
            {"id": f"a-{index:02}", "merchant_id": "owner", "recipient_id": "recipient", "sender_id": "a",
             "instruction_id": "instruction", "title": "Notice A", "message": f"Selected sender body {index}",
             "order_number": "synthetic", "created_at": self.when + timedelta(seconds=index),
             "read_at": self.when if index % 2 else None, "metadata": {"secret_payload": "must not serialize"}}
            for index in range(11)
        ] + [
            {"id": "b-1", "merchant_id": "owner", "recipient_id": "recipient", "sender_id": "b",
             "instruction_id": "instruction", "created_at": self.when, "message": "B private body", "read_at": None},
            {"id": "foreign-tenant", "merchant_id": "other-merchant", "recipient_id": "recipient", "sender_id": "foreign",
             "instruction_id": "instruction", "created_at": self.when, "message": "foreign body", "read_at": None},
            {"id": "other-recipient", "merchant_id": "owner", "recipient_id": "another", "sender_id": "b",
             "instruction_id": "instruction", "created_at": self.when, "message": "another recipient body", "read_at": None},
        ])
        async def current(request: Request):
            return await mobile_app_request_user(self.db, self.actor, path=request.url.path, method=request.method)
        app = FastAPI(); app.include_router(routes.make_order_tracking_notes_router(self.db, current), prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.eager = patch.object(routes, "notification_feed", AsyncMock(side_effect=AssertionError("No eager inbox hydration")))
        self.eager.start(); self.addCleanup(self.eager.stop)
        self.enqueue = patch.object(notices, "_enqueue", AsyncMock())
        self.enqueue.start(); self.addCleanup(self.enqueue.stop)

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("notification_directory_"))
            await self.mongo.drop_database(self.db.name)

    async def test_directory_names_only_and_sender_paging_ten(self):
        response = await self.client.get("/api/order-tracking-notes/notifications/me?directory_only=true")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json(); self.assertTrue(data["directory_only"])
        self.assertEqual({row["id"]: row["total"] for row in data["items"]}, {"a": 11, "b": 1})
        self.assertEqual(data["unread_count"], 7)
        self.assertNotIn("body", response.text); self.assertNotIn("private full", response.text)
        self.assertTrue(all(set(row) == {"id", "name", "total", "unread_count"} for row in data["items"]))
        page = await self.client.get("/api/order-tracking-notes/notifications/me?sender_id=a")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(len(page.json()["items"]), 10)
        self.assertNotIn("B private body", page.text); self.assertNotIn("secret_payload", page.text)
        next_page = await self.client.get("/api/order-tracking-notes/notifications/me", params={"sender_id": "a", "cursor": page.json()["next_cursor"]})
        self.assertEqual(len(next_page.json()["items"]), 1)
        self.assertEqual(len({row["id"] for row in page.json()["items"] + next_page.json()["items"]}), 11)

    async def test_recipient_only_read_and_fresh_read_unread_separation(self):
        unread_url = "/api/order-tracking-notes/notifications/me?sender_id=a&read_state=unread"
        before = (await self.client.get(unread_url)).json()["items"]
        target = before[0]["id"]
        response = await self.client.post(f"/api/order-tracking-notes/notifications/{target}/read", json={})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(target, [row["id"] for row in (await self.client.get(unread_url)).json()["items"]])
        read = await self.client.get("/api/order-tracking-notes/notifications/me?sender_id=a&read_state=read")
        self.assertIn(target, [row["id"] for row in read.json()["items"]])
        self.assertEqual((await self.db[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": "instruction"}))["status"], "active")
        self.assertEqual(await self.db[notices.TASKS].count_documents({}), 0)
        for identity in ["foreign-tenant", "other-recipient"]:
            response = await self.client.post(f"/api/order-tracking-notes/notifications/{identity}/read", json={})
            self.assertEqual(response.status_code, 404)
            self.assertIsNone((await self.db[notices.NOTIFICATIONS].find_one({"id": identity}))["read_at"])

    async def test_foreign_sender_permission_and_bad_cursor_rejected(self):
        self.assertEqual((await self.client.get("/api/order-tracking-notes/notifications/me?sender_id=foreign")).status_code, 404)
        self.assertEqual((await self.client.get("/api/order-tracking-notes/notifications/me?sender_id=a&cursor=bad")).status_code, 422)
        await self.db[MOBILE_APP_ACCESS].delete_many({})
        response = await self.client.get("/api/order-tracking-notes/notifications/me?directory_only=true")
        self.assertEqual(response.status_code, 403)

    async def test_event_sender_is_actual_author_not_creator_or_recipient(self):
        instruction = await self.db[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": "instruction"})
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=instruction,
            event_type="submitted", recipient_ids={"recipient"}, sender_id="b")
        saved = await self.db[notices.NOTIFICATIONS].find_one({"event_type": "submitted"})
        self.assertEqual(saved["sender_id"], "b")
        self.assertNotEqual(saved["sender_id"], saved["recipient_id"])
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction={**instruction, "notification_revision": "foreign-attempt"},
            event_type="submitted", recipient_ids={"recipient"}, sender_id="foreign")
        unknown = await self.db[notices.NOTIFICATIONS].find_one({"event_type": "submitted", "sender_id": "__system__"})
        self.assertTrue(unknown)
        directory = (await self.client.get("/api/order-tracking-notes/notifications/me?directory_only=true")).json()
        self.assertIn("__system__", [row["id"] for row in directory["items"]])
        self.assertNotIn("Private foreign name", str(directory))

    async def test_count_mode_contains_no_notification_payload(self):
        response = await self.client.get("/api/order-tracking-notes/notifications/me?count_only=true")
        self.assertEqual(response.json(), {"count_only": True, "unread_count": 7})

    async def test_legacy_creator_is_resolved_without_inventing_comment_sender(self):
        await self.db[notices.NOTIFICATIONS].insert_many([
            {"id": "legacy-created", "merchant_id": "owner", "recipient_id": "recipient", "instruction_id": "instruction",
             "event_type": "created", "message": "legacy authored note", "created_at": self.when, "read_at": None},
            {"id": "legacy-comment", "merchant_id": "owner", "recipient_id": "recipient", "instruction_id": "instruction",
             "event_type": "comment", "message": "legacy author was not stored", "created_at": self.when, "read_at": None},
        ])
        directory = (await self.client.get("/api/order-tracking-notes/notifications/me?directory_only=true")).json()
        self.assertEqual({item["id"]: item["total"] for item in directory["items"]}, {"a": 12, "b": 1, "__system__": 1})
        system = await self.client.get("/api/order-tracking-notes/notifications/me?sender_id=__system__")
        self.assertEqual([row["id"] for row in system.json()["items"]], ["legacy-comment"])
        self.actor = {"id": "owner", "role": "owner", "_session_client": "amasi_mobile"}
        response = await self.client.post("/api/order-tracking-notes/notifications/legacy-comment/read", json={})
        self.assertEqual(response.status_code, 404, "Merchant ownership cannot mark an employee's notice as read")

    async def test_selected_sender_query_does_not_scan_other_known_sender_bodies(self):
        await self.db[notices.NOTIFICATIONS].create_index([
            ("merchant_id", 1), ("recipient_id", 1), ("sender_id", 1), ("created_at", -1), ("id", -1)])
        await self.db[notices.NOTIFICATIONS].insert_many([
            {"id": f"other-{index}", "merchant_id": "owner", "recipient_id": "recipient", "sender_id": "b",
             "instruction_id": "instruction", "created_at": self.when, "read_at": None, "message": "Unselected body " * 200}
            for index in range(1000)
        ])
        response = await self.client.get("/api/order-tracking-notes/notifications/me?sender_id=a")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["items"]), 10)
        self.assertNotIn("Unselected body", response.text)
        captured = self.trace.pipelines[-1]
        # This Windows Mongo build emits invalid UTF-8 in EXPLAIN diagnostic
        # labels. Only that diagnostic document tolerates replacement; HTTP data
        # reads above retain strict BSON decoding and budget metrics stay numeric.
        proof = await self.db.command({"explain": {"aggregate": notices.NOTIFICATIONS, "pipeline": captured, "cursor": {}},
                                       "verbosity": "executionStats"},
                                      codec_options=CodecOptions(unicode_decode_error_handler="replace"))
        stats = proof.get("executionStats") or next((stage["$cursor"]["executionStats"]
            for stage in proof.get("stages", []) if "$cursor" in stage), None)
        self.assertIsNotNone(stats)
        self.assertIs(type(stats["totalDocsExamined"]), int)
        self.assertIs(type(stats["nReturned"]), int)
        self.assertGreater(stats["nReturned"], 0)
        self.assertLessEqual(stats["totalDocsExamined"], 20, "Selected sender must use tenant/recipient/sender index before legacy joins")
