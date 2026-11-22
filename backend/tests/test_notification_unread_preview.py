"""Two unread human previews through real mobile HTTP and disposable Mongo."""
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo.errors import ExecutionTimeout
from pymongo.monitoring import CommandListener

import operational_instruction_conversations as conversations
import order_tracking_notes_routes as routes
from mobile_app_permissions import MOBILE_APP_ACCESS
from mobile_app_request_context import mobile_app_request_user
from operational_instruction_notifications import NOTIFICATIONS


URL = "/api/order-tracking-notes/notifications/me/unread-preview"
EVENTS = conversations.EVENTS
CONVERSATIONS = conversations.CONVERSATIONS


class Trace(CommandListener):
    def __init__(self):
        self.commands = []

    def started(self, event):
        self.commands.append((event.command_name, deepcopy(event.command)))

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class NotificationUnreadPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.trace = Trace()
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[self.trace])
        self.db = self.mongo["notification_unread_preview_" + uuid4().hex]
        self.actor_id = "recipient"
        self.at = datetime(2026, 11, 22, 9, tzinfo=timezone.utc)
        await self.db.users.insert_many([
            {"id": "owner", "role": "owner", "name": "Owner"},
            *[{"id": actor, "created_by": "owner", "role": "employee", "name": actor.upper()}
              for actor in ("recipient", "a", "b", "c", "unrelated")],
            {"id": "foreign", "created_by": "another-owner", "role": "employee", "name": "Foreign"},
            {"id": "driver", "created_by": "owner", "role": "store_driver", "name": "Driver"},
        ])
        await self.db[MOBILE_APP_ACCESS].insert_many([
            {"owner_user_id": "another-owner" if actor == "foreign" else "owner", "user_id": actor,
             "enabled": True, "permissions": ["app.page.my_products"]}
            for actor in ("recipient", "a", "b", "c", "unrelated", "foreign")])

        async def current(request: Request):
            user = await self.db.users.find_one({"id": self.actor_id}, {"_id": 0})
            return await mobile_app_request_user(self.db, {**user, "_session_client": "amasi_mobile"},
                path=request.url.path, method=request.method)

        app = FastAPI()
        app.include_router(routes.make_order_tracking_notes_router(self.db, current), prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app), base_url="http://test")
        self.patches = [
            patch.object(conversations, name, AsyncMock(side_effect=AssertionError("Preview must not hydrate or backfill")))
            for name in ("backfill_actor", "reconcile_instruction", "conversation_detail", "conversation_listing")
        ] + [patch.object(routes, "notification_feed", AsyncMock(side_effect=AssertionError("No full inbox")))]
        for item in self.patches:
            item.start()
        await conversations.ensure_indexes(self.db)

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()
            for item in reversed(self.patches):
                item.stop()
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("notification_unread_preview_"))
            await self.mongo.drop_database(self.db.name)
            self.mongo.close()

    async def thread(self, key, sender="a", recipient="recipient", **extra):
        row = {"_id": key, "id": key, "merchant_id": "owner", "instruction_id": "instruction-" + key,
            "creator_id": sender, "creator_name": "اسم " + sender,
            "assignee_id": recipient, "assignee_name": "اسم " + recipient, "assignee_active": True,
            "state": "active", "order_number": "299845123", "tasks": [{"private": "must not hydrate"}],
            "pieces": [{"private": "must not hydrate"}], **extra}
        await self.db[CONVERSATIONS].insert_one(row)
        return row

    async def event(self, key, thread, seconds, **extra):
        row = {"_id": key, "id": key, "event_id": key, "merchant_id": thread["merchant_id"],
            "recipient_id": "recipient", "instruction_id": thread["instruction_id"],
            "conversation_id": thread["id"], "author_id": thread["creator_id"],
            "author_name": "Author snapshot", "read_at": None, "silent": False, "kind": "message",
            "preview": "معاينة " + key, "body": "PRIVATE FULL BODY " * 2000,
            "metadata": {"secret": "must not serialize"}, "created_at": self.at + timedelta(seconds=seconds), **extra}
        await self.db[EVENTS].insert_one(row)
        return row

    async def get_preview(self, suffix=""):
        response = await self.client.get(URL + suffix)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIs(result["unread_preview"], True)
        self.assertEqual(result["schema_version"], 1)
        self.assertLessEqual(len(result["items"]), 2)
        return result["items"]

    async def test_two_latest_events_total_and_same_employee_allowed(self):
        a = await self.thread("a")
        b = await self.thread("b", sender="b")
        await self.event("a-old", a, 1)
        await self.event("b-old", b, 2)
        await self.event("a-new", a, 3)
        await self.event("a-newest", a, 4)
        items = await self.get_preview("?limit=999&recipient_id=owner&merchant_id=another-owner")
        self.assertEqual([row["event_id"] for row in items], ["a-newest", "a-new"])
        self.assertEqual([row["sender"] for row in items], [{"id": "a", "name": "اسم a"}] * 2)
        self.assertEqual([row["instruction_id"] for row in items], [a["instruction_id"]] * 2)
        self.assertEqual([row["conversation_id"] for row in items], ["a"] * 2)
        self.assertEqual(items[0]["order_number"], "299845123")
        self.assertNotIn("PRIVATE FULL BODY", str(items))
        self.assertNotIn("secret", str(items))

    async def test_filter_before_limit_and_stable_event_id_tie_break(self):
        a = await self.thread("a")
        revoked = await self.thread("revoked", assignee_active=False)
        outsider = await self.thread("outsider", recipient="unrelated")
        foreign = await self.thread("foreign", merchant_id="another-owner")
        await self.event("eligible-a", a, 1)
        await self.event("eligible-z", a, 1)
        bad = [
            ("read", a, {"read_at": self.at}), ("silent", a, {"silent": True}),
            ("own", a, {"author_id": "recipient"}), ("system", a, {"author_id": "__system__"}),
            ("wrong-author", a, {"author_id": "unrelated"}), ("read-event", a, {"kind": "read"}),
            ("revoked", revoked, {}), ("outsider", outsider, {}), ("foreign", foreign, {}),
            ("other-recipient", a, {"recipient_id": "unrelated"}),
            ("wrong-instruction", a, {"instruction_id": "another-instruction"}),
            ("missing-thread", a, {"conversation_id": "missing"}),
            ("blank-preview", a, {"preview": "  "}),
            ("padded-event", a, {"event_id": " padded-event"}),
        ]
        for index, (key, conv, extra) in enumerate(bad, 10):
            await self.event(key, conv, index, **extra)
        items = await self.get_preview()
        self.assertEqual([row["event_id"] for row in items], ["eligible-z", "eligible-a"])

    async def test_legacy_notice_read_excluded_without_changing_event(self):
        a = await self.thread("a")
        await self.event("older", a, 1)
        await self.event("notice-event", a, 2, source_notice_id="notice-source")
        await self.db[NOTIFICATIONS].insert_one({"id": "notice-source", "merchant_id": "owner",
            "recipient_id": "recipient", "instruction_id": a["instruction_id"], "read_at": None})
        self.assertEqual([row["event_id"] for row in await self.get_preview()], ["notice-event", "older"])
        response = await self.client.post("/api/order-tracking-notes/notifications/notice-source/read")
        self.assertEqual(response.status_code, 200, response.text)
        event = await self.db[EVENTS].find_one({"event_id": "notice-event"})
        self.assertIsNone(event["read_at"])
        self.assertEqual([row["event_id"] for row in await self.get_preview()], ["older"])

    async def test_source_notice_join_is_tenant_recipient_and_instruction_scoped(self):
        a = await self.thread("a")
        await self.event("valid", a, 1, source_notice_id="shared-source")
        await self.db[NOTIFICATIONS].insert_many([
            {"id": "shared-source", "merchant_id": "another-owner", "recipient_id": "recipient", "instruction_id": a["instruction_id"], "read_at": self.at},
            {"id": "shared-source", "merchant_id": "owner", "recipient_id": "unrelated", "instruction_id": a["instruction_id"], "read_at": self.at},
            {"id": "shared-source", "merchant_id": "owner", "recipient_id": "recipient", "instruction_id": "other", "read_at": self.at},
            {"id": "shared-source", "merchant_id": "owner", "recipient_id": "recipient", "instruction_id": a["instruction_id"], "read_at": None},
        ])
        items = await self.get_preview()
        self.assertEqual([row["event_id"] for row in items], ["valid"])
        self.assertEqual(items[0]["notice_id"], "shared-source")

    async def test_creator_can_read_private_reply_but_no_sibling_conversation(self):
        own = await self.thread("own", sender="recipient", recipient="a", state="completed", assignee_active=False)
        sibling = await self.thread("sibling", sender="b", recipient="c")
        await self.event("private-reply", own, 1, author_id="a")
        await self.event("sibling-reply", sibling, 2, author_id="c")
        items = await self.get_preview()
        self.assertEqual([row["event_id"] for row in items], ["private-reply"])
        self.assertEqual(items[0]["sender"], {"id": "a", "name": "اسم a"})

    async def test_linked_employee_scope_and_disabled_native_access(self):
        a = await self.thread("a")
        await self.event("own", a, 1)
        await self.db.users.update_one({"id": "recipient"}, {"$unset": {"created_by": ""}})
        await self.db.mezan_employees_v2.insert_one({"account_user_id": "recipient", "user_id": "owner"})
        self.assertEqual([row["event_id"] for row in await self.get_preview()], ["own"])
        await self.db[MOBILE_APP_ACCESS].update_one({"user_id": "recipient"}, {"$set": {"enabled": False}})
        response = await self.client.get(URL)
        self.assertEqual(response.status_code, 403, response.text)
        self.actor_id = "driver"
        response = await self.client.get(URL)
        self.assertEqual(response.status_code, 403, response.text)

    async def test_empty_foreign_session_and_read_updates_do_not_return_stale_rows(self):
        self.assertEqual(await self.get_preview(), [])
        a = await self.thread("a")
        await self.event("event", a, 1)
        self.assertEqual(len(await self.get_preview()), 1)
        self.actor_id = "foreign"
        self.assertEqual(await self.get_preview(), [])
        self.actor_id = "recipient"
        await self.db[EVENTS].update_one({"event_id": "event"}, {"$set": {"read_at": self.at}})
        self.assertEqual(await self.get_preview(), [])

    async def test_get_is_read_only_one_bounded_aggregate_and_no_body_hydration(self):
        a = await self.thread("a")
        await self.event("event", a, 1, preview="س" * 400)
        await self.db[NOTIFICATIONS].insert_one({"id": "legacy-pending", "merchant_id": "owner",
            "recipient_id": "recipient", "conversation_pending": True, "read_at": None})
        self.trace.commands.clear()
        items = await self.get_preview()
        commands = list(self.trace.commands)
        self.assertEqual(len(items[0]["preview"]), 240)
        aggregates = [command for name, command in commands if name == "aggregate"]
        self.assertEqual(len(aggregates), 1)
        aggregate = aggregates[0]
        self.assertEqual(aggregate["aggregate"], EVENTS)
        self.assertEqual(aggregate["cursor"]["batchSize"], 2)
        self.assertLessEqual(aggregate["maxTimeMS"], 2000)
        self.assertFalse({name for name, _ in commands} & {"insert", "update", "delete", "findAndModify", "createIndexes"})
        pipeline = aggregate["pipeline"]
        self.assertNotIn("$push", repr(pipeline))
        self.assertNotIn("'body'", repr(pipeline))
        self.assertNotIn("mezan_preparation_pieces", repr(pipeline))
        self.assertNotIn("order_review_workflows", repr(pipeline))
        self.assertEqual(await self.db[conversations.BOOKMARKS].count_documents({}), 0)
        self.assertTrue((await self.db[NOTIFICATIONS].find_one({"id": "legacy-pending"}))["conversation_pending"])

    async def test_unread_index_and_explain_do_not_scan_read_history(self):
        a = await self.thread("a")
        await self.event("visible", a, 1)
        await self.db[EVENTS].insert_many([
            {"_id": "old-" + str(i), "event_id": "old-" + str(i), "merchant_id": "owner", "recipient_id": "recipient",
             "conversation_id": "a", "instruction_id": a["instruction_id"], "author_id": "a", "read_at": self.at,
             "silent": False, "kind": "message", "preview": "read history", "created_at": self.at}
            for i in range(1000)])
        self.trace.commands.clear()
        self.assertEqual([row["event_id"] for row in await self.get_preview()], ["visible"])
        command = next(command for name, command in self.trace.commands if name == "aggregate")
        explanation = await self.db.command({"explain": {"aggregate": EVENTS, "pipeline": command["pipeline"], "cursor": {}}, "verbosity": "executionStats"})
        indexes = await self.db[EVENTS].index_information()
        self.assertIn("recipient_unread_preview", indexes)
        self.assertIn("recipient_notice_reference", await self.db[NOTIFICATIONS].index_information())
        text = repr(explanation)
        self.assertIn("recipient_unread_preview", text)
        cursor = explanation["stages"][0]["$cursor"]
        self.assertLess(cursor["executionStats"]["totalDocsExamined"], 10)

    async def test_query_timeout_is_unavailable_not_an_empty_inbox(self):
        original = AsyncIOMotorCollection.aggregate

        def timeout(collection, *args, **kwargs):
            if collection.name == EVENTS:
                raise ExecutionTimeout("Synthetic bounded read timeout")
            return original(collection, *args, **kwargs)

        with patch.object(AsyncIOMotorCollection, "aggregate", timeout):
            response = await self.client.get(URL)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "notification_preview_temporarily_unavailable")
        self.assertNotIn("items", response.json())


if __name__ == "__main__":
    unittest.main()
