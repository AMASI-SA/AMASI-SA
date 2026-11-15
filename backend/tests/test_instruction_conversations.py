"""Conversation lifecycle and isolation through HTTP on disposable Mongo only."""
import os
import asyncio
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

import operational_instruction_notifications as notices
import order_tracking_notes_routes as routes
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from mobile_app_permissions import MOBILE_APP_ACCESS
from mobile_app_request_context import mobile_app_request_user
from supplier_receiving_routes import SUPPLIER_INVOICES

PREFIX = "/api/order-tracking-notes"
PIECES = "mezan_preparation_pieces_v1"


class Trace(CommandListener):
    def __init__(self):
        self.commands = []

    def started(self, event):
        self.commands.append((event.command_name, deepcopy(event.command)))

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class InstructionConversations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.trace = Trace()
        self.mongo = AsyncIOMotorClient(uri, event_listeners=[self.trace])
        self.db = self.mongo["instruction_conversations_" + uuid4().hex]
        await self.db.users.insert_many([
            {"id": "owner", "role": "owner", "name": "Owner"},
            *[{"id": actor, "created_by": "owner", "role": "employee", "name": actor.upper()}
              for actor in ("cs", "a", "b", "unrelated")],
            {"id": "foreign", "created_by": "other", "role": "employee"},
        ])
        await self.db[MOBILE_APP_ACCESS].insert_many([
            {"owner_user_id": "other" if actor == "foreign" else "owner", "user_id": actor,
             "enabled": True, "permissions": ["app.page.my_products"]}
            for actor in ("cs", "a", "b", "unrelated", "foreign")])
        self.instruction = {"id": "urgent", "user_id": "owner", "created_by": "cs", "created_by_name": "CS",
            "order_number": "100", "scope": "order", "action_type": "urgent_preparation", "priority": "urgent",
            "thread_protocol": 1, "target_stages": ["preparation", "supplier_receiving"], "status": "active",
            "note": "Please prepare these products", "created_at": datetime.now(timezone.utc)}
        await self.db[ORDER_TRACKING_INSTRUCTIONS].insert_one(dict(self.instruction))
        await self.db[PIECES].insert_many([
            {"user_id": "owner", "order_number": "100", "piece_id": pid, "order_item_id": pid + "-item",
             "product_name": "Product " + pid, "file_number": "FILE-" + pid, "image_url": pid + ".png",
             "responsible_employee_id": actor, "status": "in_progress", "execution_status": "in_progress"}
            for pid, actor in (("p1", "a"), ("p2", "a"), ("p3", "b"))])
        await self.db.order_review_workflows.insert_one({"user_id": "owner", "order_number": "100", "stage": "in_progress"})
        self.actor = await self.db.users.find_one({"id": "a"})

        async def current(request: Request):
            return await mobile_app_request_user(self.db, {**self.actor, "_session_client": "amasi_mobile"},
                path=request.url.path, method=request.method)

        async def context(db, user):
            return {"actor_id": user["id"], "merchant_id": user.get("created_by", user["id"]),
                "is_owner": user["id"] == "owner", "permissions": set(), "responsibilities": set(), "warehouse_ids": None}

        self.patches = [patch.object(routes, "_actor_context", context),
            patch.object(routes, "get_order", AsyncMock(side_effect=routes.OrderNotFoundError("missing"))),
            patch.object(notices, "_enqueue", AsyncMock())]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(routes.make_order_tracking_notes_router(self.db, current), prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app), base_url="http://test")
        await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="created", recipient_ids={"a", "b", "cs"})

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()
            for item in reversed(self.patches):
                item.stop()
            self.assertTrue(self.db.name.startswith("instruction_conversations_"))
            await self.mongo.drop_database(self.db.name)
            self.mongo.close()

    async def select(self, actor):
        self.actor = await self.db.users.find_one({"id": actor})

    async def listing(self, **params):
        response = await self.client.get(PREFIX + "/notifications/me", params={"conversation_only": "true", **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def detail(self, conversation_id="", **params):
        response = await self.client.get(PREFIX + "/instructions/urgent", params={
            "conversation_only": "true", "conversation_id": conversation_id, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def read(self, detail):
        conversation = detail["conversation"]
        response = await self.client.post(PREFIX + "/instructions/urgent/conversation/read", json={
            "conversation_id": conversation["id"], "last_event_id": conversation["last_event_id"],
            "event_ids": [event["id"] for event in detail["timeline"]]})
        self.assertEqual(response.status_code, 200, response.text)

    async def finish(self, ids):
        await self.db[PIECES].update_many({"piece_id": {"$in": ids}}, {"$set": {
            "status": "received", "execution_status": "received_from_supplier", "supplier_dispatch_status": "received",
            "received_at": datetime.now(timezone.utc), "remaining_service_count": 0}})
        from operational_instruction_progress import refresh_instruction_progress
        await refresh_instruction_progress(self.db, self.instruction)

    async def test_order_conversation_cards_read_receipt_and_private_reply(self):
        rows = (await self.listing())["items"]
        self.assertEqual(len(rows), 1)
        cid = rows[0]["conversation_id"]
        detail = await self.detail(cid)
        self.assertEqual({card["piece_id"] for card in detail["product_cards"]}, {"p1", "p2"})
        self.assertEqual(detail["conversation"]["peer"]["id"], "cs")
        await self.read(detail)
        await self.read(detail)
        after = await self.detail(cid)
        self.assertEqual(sum(event["kind"] == "read" for event in after["timeline"]), 1)
        self.assertEqual(after["conversation"]["state"], "active")
        self.assertEqual(after["conversation"]["unread_count"], 0)
        self.assertEqual(await self.db[notices.TASKS].count_documents({"acknowledged_at": {"$exists": True}}), 0)
        self.assertEqual(await self.db[notices.TASKS].count_documents({"status": {"$ne": "pending"}}), 0)
        payload = {"conversation_id": cid, "body": "Private employee reply", "client_request_id": "message-request-000001"}
        for _ in range(2):
            response = await self.client.post(PREFIX + "/instructions/urgent/conversation/messages", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post(PREFIX + "/instructions/urgent/conversation/messages", json={**payload, "body": "changed"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(sum(event["body"] == payload["body"] for event in (await self.detail(cid))["timeline"]), 1)
        await self.select("b")
        denied = await self.client.get(PREFIX + "/instructions/urgent", params={"conversation_only": "true", "conversation_id": cid})
        self.assertEqual(denied.status_code, 404)
        own = await self.detail()
        self.assertEqual([card["piece_id"] for card in own["product_cards"]], ["p3"])
        self.assertNotIn("Private employee reply", str(own))

    async def test_receipt_completes_unread_employee_and_sender_result_until_read(self):
        cid = (await self.listing())["items"][0]["conversation_id"]
        await self.finish(["p1"])
        self.assertEqual((await self.listing())["items"][0]["state"], "active")
        await self.finish(["p2"])
        self.assertEqual((await self.listing())["items"], [])
        history = await self.detail(cid)
        self.assertEqual(history["conversation"]["state"], "completed")
        await self.select("b")
        self.assertEqual(len((await self.listing())["items"]), 1)
        await self.select("cs")
        completed = next(row for row in (await self.listing())["items"] if row["peer"]["id"] == "a")
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["outcome"], "completed")
        self.assertGreater(completed["unread_count"], 0)
        creator_detail = await self.detail(cid)
        self.assertEqual({card["piece_id"] for card in creator_detail["product_cards"]}, {"p1", "p2"})
        await self.read(creator_detail)
        self.assertNotIn(cid, [row["id"] for row in (await self.listing())["items"]])
        self.assertIn(cid, [row["id"] for row in (await self.listing(view="history"))["items"]])
        self.assertEqual((await self.db[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": "urgent"}))["status"], "active")
        self.assertEqual(await self.db[notices.TASKS].count_documents({"status": {"$ne": "pending"}}), 0)
        self.assertEqual(await self.db[SUPPLIER_INVOICES].count_documents({}), 0)

    async def test_visible_read_does_not_consume_concurrent_completion(self):
        await self.select("cs")
        cid = next(row["id"] for row in (await self.listing())["items"] if row["peer"]["id"] == "a")
        before = await self.detail(cid)
        await self.finish(["p1", "p2"])
        await self.read(before)
        completed = next(row for row in (await self.listing())["items"] if row["id"] == cid)
        self.assertEqual(completed["state"], "completed")
        self.assertGreater(completed["unread_count"], 0)

    async def test_draft_partial_and_business_blocking_never_auto_complete(self):
        cid = (await self.listing())["items"][0]["id"]
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {
            "supplier_receiving_session_id": "draft", "status": "received", "execution_status": "awaiting_remaining_services",
            "supplier_dispatch_status": "partial_received", "remaining_service_count": 1}})
        from operational_instruction_progress import refresh_instruction_progress
        await refresh_instruction_progress(self.db, self.instruction)
        self.assertEqual((await self.detail(cid))["conversation"]["state"], "active")
        self.instruction.update(action_type="edit_product", task_protocol=1, operational_hold=True)
        await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one({"id": "urgent"}, {"$set": {
            "action_type": "edit_product", "task_protocol": 1, "operational_hold": True}})
        await self.finish(["p1", "p2"])
        self.assertEqual((await self.detail(cid))["conversation"]["state"], "active")
        self.assertTrue((await self.db[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": "urgent"}))["operational_hold"])

    async def test_completed_history_snapshot_and_legacy_push_resolve_without_guess_access(self):
        legacy = await self.db[notices.NOTIFICATIONS].find_one({"recipient_id": "a", "event_type": "created", "conversation_id": {"$exists": False}})
        cid = (await self.listing())["items"][0]["id"]
        await self.finish(["p1", "p2"])
        await self.db[PIECES].delete_many({"responsible_employee_id": "a"})
        detail = await self.detail(notice_id=legacy["id"])
        self.assertEqual(detail["conversation"]["id"], cid)
        self.assertEqual({card["piece_id"] for card in detail["product_cards"]}, {"p1", "p2"})
        for actor in ("unrelated", "owner", "foreign"):
            await self.select(actor)
            response = await self.client.get(PREFIX + "/instructions/urgent", params={
                "conversation_only": "true", "conversation_id": cid, "notice_id": legacy["id"]})
            self.assertEqual(response.status_code, 404)

    async def test_metadata_poll_is_bounded_and_does_not_hydrate_or_write_when_caught_up(self):
        await self.listing(directory_only="true")
        await self.listing(directory_only="true")
        self.trace.commands.clear()
        for params in ({"directory_only": "true"}, {"count_only": "true"}, {"sender_id": "cs", "q": "FILE-p1"}):
            response = await self.listing(**params)
            if "q" in params:
                self.assertEqual(len(response["items"]), 1)
        hydrated = [cmd for name, cmd in self.trace.commands if name == "find" and cmd.get("find") in {
            PIECES, ORDER_TRACKING_INSTRUCTIONS, "unified_orders", "order_review_workflows"}]
        self.assertEqual(hydrated, [])
        self.assertEqual([name for name, _ in self.trace.commands if name in {"insert", "update", "delete", "findAndModify"}], [])

    async def test_creator_private_thread_omits_other_employee_events_and_evidence(self):
        await self.select("b")
        b = await self.detail()
        response = await self.client.post(PREFIX + "/instructions/urgent/conversation/messages", json={
            "conversation_id": b["conversation"]["id"], "body": "B confidential response", "client_request_id": "employee-b-message-0001"})
        self.assertEqual(response.status_code, 200)
        await self.db[routes.ORDER_TRACKING_EVIDENCE].insert_one({"user_id": "owner", "instruction_id": "urgent",
            "id": "b-evidence", "created_by": "b", "result_note": "B confidential evidence"})
        await self.select("cs")
        a_id = next(row["id"] for row in (await self.listing())["items"] if row["peer"]["id"] == "a")
        a = await self.detail(a_id)
        self.assertNotIn("B confidential", str(a))
        self.assertNotIn("events", a)
        self.assertIsNone(a.get("edit_preview"))
        self.assertEqual({card["piece_id"] for card in a["product_cards"]}, {"p1", "p2"})

    async def test_ambiguous_creator_notice_requires_explicit_employee_choice(self):
        await self.select("cs")
        source = await self.db[notices.NOTIFICATIONS].find_one({"recipient_id": "cs", "event_type": "created"})
        detail = await self.detail(notice_id=source["id"])
        self.assertIsNone(detail["conversation"])
        self.assertEqual({row["peer"]["id"] for row in detail["conversation_choices"]}, {"a", "b"})
        self.assertEqual(detail["product_cards"], [])
        self.assertEqual(detail["timeline"], [])

    async def test_pending_reassignment_revokes_former_employee_but_keeps_creator_history(self):
        a_id = (await self.listing())["items"][0]["id"]
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {"responsible_employee_id": "b"}})
        response = await self.client.get(PREFIX + "/instructions/urgent", params={"conversation_only": "true", "conversation_id": a_id})
        self.assertEqual(response.status_code, 404)
        await self.select("cs")
        creator = await self.detail(a_id)
        self.assertFalse(creator["conversation"]["can_reply"])
        await self.select("b")
        b = await self.detail()
        self.assertEqual({card["piece_id"] for card in b["product_cards"]}, {"p1", "p2", "p3"})

    async def test_future_stage_does_not_disclose_thread_until_assignment_is_active(self):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS
        for collection in (CONVERSATIONS, EVENTS, notices.NOTIFICATIONS, notices.TASKS):
            await self.db[collection].delete_many({})
        self.instruction.update(action_type="edit_product", task_protocol=1, target_stages=["assembly_labeling"])
        await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one({"id": "urgent"}, {"$set": self.instruction})
        await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="created", recipient_ids={"cs"})
        response = await self.client.get(PREFIX + "/instructions/urgent", params={"conversation_only": "true"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual((await self.listing())["items"], [])
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {"status": "ready_for_assembly"}})
        await self.db.order_review_workflows.update_one({"order_number": "100"}, {"$set": {"claimed_by": "a"}})
        await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="assigned", recipient_ids={"a"})
        self.assertEqual({card["piece_id"] for card in (await self.detail())["product_cards"]}, {"p1", "p2"})

    async def test_native_permission_required_for_both_exact_conversation_writes(self):
        detail = await self.detail()
        cid = detail["conversation"]["id"]
        await self.db[MOBILE_APP_ACCESS].delete_one({"user_id": "a"})
        for path, payload in (("read", {"conversation_id": cid, "last_event_id": detail["conversation"]["last_event_id"]}),
            ("messages", {"conversation_id": cid, "body": "denied", "client_request_id": "denied-request-00001"})):
            response = await self.client.post(PREFIX + "/instructions/urgent/conversation/" + path, json=payload)
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(await self.db["operational_instruction_comments_v1"].count_documents({"body": "denied"}), 0)

    async def test_cancelled_instruction_is_terminal_without_false_ready_result(self):
        cid = (await self.listing())["items"][0]["id"]
        await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one({"id": "urgent"}, {"$set": {"status": "cancelled"}})
        detail = await self.detail(cid)
        self.assertEqual(detail["conversation"]["state"], "completed")
        self.assertEqual(detail["conversation"]["outcome"], "cancelled")
        self.assertEqual(detail["conversation"]["completion_label"], "أُلغيت التعليمات")
        self.assertNotIn("جاهز", str(detail["timeline"]))

    async def test_completed_old_push_suppressed_but_later_private_message_is_delivered(self):
        import operational_push as push
        cid = (await self.listing())["items"][0]["id"]
        old_notice = await self.db[notices.NOTIFICATIONS].find_one({"recipient_id": "a", "event_type": "created"})
        await self.finish(["p1", "p2"])
        await self.db[push.DEVICES].insert_one({"_id": "device", "generation": "g1", "merchant_id": "owner", "actor_id": "a",
            "kind": "expo", "active": True, "session_id": "synthetic-session", "installation_id": "synthetic_installation_1234",
            "token": "ExpoPushToken[synthetic]"})
        await self.db.order_review_auth_sessions_v2.insert_one({"_id": "synthetic-session", "actor_id": "a",
            "user_id": "owner", "state": "active", "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})

        async def job(event_id):
            await push.enqueue_operational_push(self.db, recipient_id="a", event_id=event_id, instruction_id="urgent")

        sender = AsyncMock(return_value={"status": "provider_accepted"})
        # Exercise the real current device/session/native-access gates. The old
        # fixture omitted kind/session and mocked activity before previews existed.
        with patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "1"}):
            await job(old_notice["id"])
            self.assertTrue(await push.deliver_one(self.db, sender=sender))
            sender.assert_not_awaited()
            await self.select("cs")
            response = await self.client.post(PREFIX + "/instructions/urgent/conversation/messages", json={
                "conversation_id": cid, "body": "A new question after receipt", "client_request_id": "post-completion-message-1"})
            self.assertEqual(response.status_code, 200, response.text)
            fresh = await self.db[notices.NOTIFICATIONS].find_one({"recipient_id": "a", "event_type": "comment"})
            await job(fresh["id"])
            self.assertTrue(await push.deliver_one(self.db, sender=sender))
            sender.assert_awaited_once()
            payload = sender.await_args.args[1]
            self.assertEqual(payload["conversation_id"], cid)
            self.assertEqual(payload["notice_id"], fresh["id"])
            self.assertIn("A new question after receipt", payload["body"])
        await self.select("a")
        row = next(row for row in (await self.listing())["items"] if row["id"] == cid)
        self.assertTrue(row["alert_active"])
        await self.read(await self.detail(cid))
        self.assertNotIn(cid, [row["id"] for row in (await self.listing())["items"]])

    async def test_supplier_receipt_custody_preserves_proven_history_without_later_product_updates(self):
        cid = (await self.listing())["items"][0]["id"]
        at = datetime.now(timezone.utc)
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {
            "responsible_employee_id": "b", "previous_responsible_employee_id": "a", "reassignment_reason": "supplier_receipt",
            "reassignment_invoice_id": "synthetic-receipt", "reassignment_session_id": "synthetic-session", "reassigned_at": at,
            "status": "received", "execution_status": "received_from_supplier", "supplier_dispatch_status": "received",
            "received_at": at, "remaining_service_count": 0}})
        from operational_instruction_progress import refresh_instruction_progress
        await refresh_instruction_progress(self.db, self.instruction)
        self.assertEqual((await self.listing())["items"], [])
        completed = await self.detail(cid)
        self.assertEqual(completed["conversation"]["outcome"], "completed")
        self.assertEqual({card["piece_id"] for card in completed["product_cards"]}, {"p1", "p2"})
        await self.db[PIECES].update_many({"piece_id": {"$in": ["p1", "p2"]}}, {"$set": {
            "responsible_employee_id": "unrelated", "reassignment_reason": "manual", "product_name": "Later private product changes"}})
        history = await self.detail(cid)
        self.assertNotIn("Later private product changes", str(history))
        self.assertEqual(history["conversation"]["state"], "completed")

    async def test_completed_status_alone_does_not_grant_access_after_routine_reassignment(self):
        cid = (await self.listing())["items"][0]["id"]
        await self.finish(["p1", "p2"])
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {
            "responsible_employee_id": "b", "reassignment_reason": "manual", "product_name": "Private later revision"}})
        response = await self.client.get(PREFIX + "/instructions/urgent", params={"conversation_only": "true", "conversation_id": cid})
        self.assertEqual(response.status_code, 404)
        await self.select("cs")
        creator_history = await self.detail(cid)
        self.assertFalse(creator_history["conversation"]["can_reply"])
        self.assertNotIn("Private later revision", str(creator_history))

    async def test_new_assigned_product_reopens_only_its_employee_thread_with_new_result(self):
        cid = (await self.listing())["items"][0]["id"]
        await self.read(await self.detail(cid))
        await self.finish(["p1", "p2"])
        await self.select("cs")
        await self.read(await self.detail(cid))
        await self.db[PIECES].insert_one({"user_id": "owner", "order_number": "100", "piece_id": "p4",
            "order_item_id": "p4-item", "product_name": "New assignment", "responsible_employee_id": "a", "status": "in_progress"})
        await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="assigned", recipient_ids={"a"})
        await self.select("a")
        row = next(row for row in (await self.listing())["items"] if row["id"] == cid)
        self.assertEqual(row["state"], "active")
        self.assertGreater(row["unread_count"], 0)
        await self.finish(["p4"])
        await self.select("cs")
        row = next(row for row in (await self.listing())["items"] if row["id"] == cid)
        self.assertEqual(row["outcome"], "completed")
        self.assertGreater(row["unread_count"], 0)

    async def test_work_assignment_date_uses_employee_history_and_survives_supplier_transfer(self):
        original = datetime(2026, 10, 1, tzinfo=timezone.utc)
        reassigned = datetime(2026, 11, 8, tzinfo=timezone.utc)
        received = datetime(2026, 11, 11, tzinfo=timezone.utc)
        await self.db[PIECES].update_one({"piece_id": "p1"}, {"$set": {"assigned_at": original,
            "file_created_at": original - timedelta(days=4), "reassigned_at": received,
            "assignment_history": [
                {"assignment_id": "assign_original", "responsible_employee_id": "b", "assigned_at": original},
                {"assignment_id": "assign_current", "responsible_employee_id": "a", "assigned_at": reassigned},
                {"assignment_id": "assign_receipt", "responsible_employee_id": "a", "assigned_at": received,
                    "reason": "supplier_receipt", "source": "supplier_receiving_invoice_approval"}]}})
        detail = await self.detail()
        p1 = next(card for card in detail["product_cards"] if card["piece_id"] == "p1")
        self.assertEqual(datetime.fromisoformat(p1["assigned_at"]), reassigned)
        await self.finish(["p1", "p2"])
        await self.db[PIECES].update_one({"piece_id": "p1"}, {"$set": {"employee_assigned_at": received}})
        history = await self.detail(detail["conversation"]["id"])
        self.assertEqual(next(card for card in history["product_cards"] if card["piece_id"] == "p1")["assigned_at"], p1["assigned_at"])

    async def test_delayed_active_observer_cannot_resurrect_completed_thread(self):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS, reconcile_instruction
        cid = (await self.listing())["items"][0]["id"]
        old_pieces = await self.db[PIECES].find({}).to_list(10)
        old_pieces[0]["product_name"] = "Older observed snapshot"
        old_tasks = await self.db[notices.TASKS].find({}).to_list(10)
        reached, release = asyncio.Event(), asyncio.Event()
        collection_type = type(self.db[CONVERSATIONS])
        original_update = collection_type.update_one

        async def delayed(collection, query, update, *args, **kwargs):
            if (collection.name == CONVERSATIONS and query.get("_id") == cid
                    and update.get("$set", {}).get("state") == "active"):
                reached.set()
                await release.wait()
            return await original_update(collection, query, update, *args, **kwargs)

        with patch.object(collection_type, "update_one", delayed):
            stale = asyncio.create_task(reconcile_instruction(self.db, self.instruction, pieces=old_pieces, tasks=old_tasks))
            await asyncio.wait_for(reached.wait(), 5)
            try:
                await self.finish(["p1", "p2"])
            finally:
                release.set()
            await stale
        row = await self.db[CONVERSATIONS].find_one({"id": cid})
        self.assertEqual(row["state"], "completed")
        result = await self.db[EVENTS].find_one({"conversation_id": cid, "recipient_id": "cs", "kind": "completed"})
        self.assertEqual(result["created_at"], row["completed_at"])
        self.assertEqual(await self.db[EVENTS].count_documents({"conversation_id": cid, "recipient_id": "cs", "kind": "completed"}), 1)
        self.assertEqual((await self.listing())["items"], [])

    async def reset_as_blocking(self, *, edit_flow=False):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS
        from instruction_edit_flow import snapshot
        for collection in (CONVERSATIONS, EVENTS, notices.TASKS, notices.NOTIFICATIONS):
            await self.db[collection].delete_many({})
        await self.db.users.update_one({"id": "cs"}, {"$set": {"role": "customer_service"}})
        items = [{"order_item_id": pid + "-item", "name": "Product " + pid,
            "quantity": 1, "options_normalized": {"size": "M"}} for pid in ("p1", "p2", "p3")]
        self.instruction.update(action_type="edit_order", task_protocol=1, required_action="write_result",
            approval_required=not edit_flow, target_stages=["preparation"])
        if edit_flow:
            self.instruction.update(edit_flow=1, edit_phase="awaiting_stop",
                edit_baseline=snapshot(items, [row["order_item_id"] for row in items]))
        await self.db[ORDER_TRACKING_INSTRUCTIONS].replace_one({"id": "urgent"}, deepcopy(self.instruction))
        await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="created", recipient_ids={"a", "b", "cs"})
        return items

    async def test_opt_in_edit_stop_revision_and_submission_keep_live_scoped_business_fields(self):
        items = await self.reset_as_blocking(edit_flow=True)
        with patch.object(routes, "get_order", AsyncMock(side_effect=lambda *args, **kwargs: {"items": deepcopy(items)})):
            selected = {}
            for actor in ("a", "b"):
                await self.select(actor)
                detail = await self.detail()
                selected[actor] = detail["conversation"]["id"]
                tasks = detail["instruction"]["tasks"]
                self.assertTrue(detail["capabilities"]["can_acknowledge_edit_stop"])
                await self.read(detail)
                still_pending = await self.detail(selected[actor])
                self.assertTrue(all(not task.get("stop_acknowledged_at") for task in still_pending["instruction"]["tasks"]))
                response = await self.client.post(PREFIX + "/instructions/urgent/stop-acknowledgment", json={
                    "task_ids": [task["id"] for task in tasks], "note": actor + " stopped"})
                self.assertEqual(response.status_code, 200, response.text)
                fresh = await self.detail(selected[actor])
                self.assertTrue(all(task.get("stop_acknowledged_at") for task in fresh["instruction"]["tasks"]))
                self.assertEqual({task["stop_note"] for task in fresh["instruction"]["tasks"]}, {actor + " stopped"})
            items[0]["options_normalized"]["size"] = "L"
            await self.select("cs")
            author = await self.detail(selected["a"])
            self.assertEqual(author["instruction"]["edit_phase"], "awaiting_salla")
            self.assertTrue(author["capabilities"]["can_send_edit_revision"])
            self.assertTrue(author["edit_preview"]["changed"])
            self.assertEqual(set(author["edit_preview"]), {"changed", "revision"})
            self.assertEqual({task["assignee_id"] for task in author["instruction"]["tasks"]}, {"a"})
            digest = author["edit_preview"]["revision"]
            response = await self.client.post(PREFIX + "/instructions/urgent/send-edit", json={"revision": digest})
            self.assertEqual(response.status_code, 200, response.text)
            for actor in ("a", "b"):
                await self.select(actor)
                detail = await self.detail(selected[actor])
                self.assertEqual(detail["instruction"]["edit_phase"], "revision_sent")
                self.assertEqual(detail["instruction"]["edit_revision"], digest)
                self.assertTrue(detail["edit_revision_current"])
                self.assertTrue(detail["capabilities"]["can_submit_tasks"])
                if actor == "a":
                    self.assertEqual(next(card for card in detail["product_cards"] if card["piece_id"] == "p1")["requested_options"]["size"], "L")
                response = await self.client.post(PREFIX + "/instructions/urgent/tasks/submit", json={
                    "task_ids": [task["id"] for task in detail["instruction"]["tasks"]], "confirmation": "modified",
                    "note": actor + " completed work", "revision": digest})
                self.assertEqual(response.status_code, 200, response.text)
                fresh = await self.detail(selected[actor])
                self.assertEqual({task["status"] for task in fresh["instruction"]["tasks"]}, {"submitted"})
                self.assertEqual({task["result_note"] for task in fresh["instruction"]["tasks"]}, {actor + " completed work"})
                self.assertTrue(all(task.get("submitted_at") for task in fresh["instruction"]["tasks"]))
            final = await self.db[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": "urgent"})
            self.assertEqual(final["status"], "completed")  # edit_flow=1 has no second approval.

    async def test_opt_in_existing_approval_flow_keeps_submission_rejection_and_approval(self):
        await self.reset_as_blocking()
        selected = {}

        async def submit(actor):
            await self.select(actor)
            detail = await self.detail(selected.get(actor, ""))
            selected[actor] = detail["conversation"]["id"]
            response = await self.client.post(PREFIX + "/instructions/urgent/tasks/submit", json={
                "task_ids": [task["id"] for task in detail["instruction"]["tasks"]],
                "confirmation": "modified", "note": actor + " submitted"})
            self.assertEqual(response.status_code, 200, response.text)

        await submit("a")
        await submit("b")
        await self.select("cs")
        author = await self.detail(selected["a"])
        self.assertEqual(author["instruction"]["status"], "waiting_customer_service_approval")
        self.assertTrue(author["capabilities"]["can_approve_resume"])
        self.assertEqual({task["assignee_id"] for task in author["instruction"]["tasks"]}, {"a"})
        response = await self.client.post(PREFIX + "/instructions/urgent/reject", json={
            "task_ids": [task["id"] for task in author["instruction"]["tasks"]], "note": "A should correct the size"})
        self.assertEqual(response.status_code, 200, response.text)
        await self.select("a")
        rejected = await self.detail(selected["a"])
        self.assertEqual({task["status"] for task in rejected["instruction"]["tasks"]}, {"pending"})
        self.assertEqual({task["rejection_note"] for task in rejected["instruction"]["tasks"]}, {"A should correct the size"})
        await self.select("b")
        other = await self.detail(selected["b"])
        self.assertNotIn("A should correct the size", str(other))
        await submit("a")
        await self.select("cs")
        author = await self.detail(selected["a"])
        self.assertTrue(author["capabilities"]["can_approve_resume"])
        response = await self.client.post(PREFIX + "/instructions/urgent/approve", json={"note": "Approved after actual execution"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.detail(selected["a"]))["instruction"]["status"], "completed")

    async def test_preupgrade_notice_reconstructs_only_proven_supplier_receipt_history(self):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS
        source = await self.db[notices.NOTIFICATIONS].find_one({"recipient_id": "a", "event_type": "created"})
        for collection in (CONVERSATIONS, EVENTS):
            await self.db[collection].delete_many({})
        await self.db[notices.NOTIFICATIONS].update_many({}, {"$unset": {"conversation_pending": ""}})
        at = datetime.now(timezone.utc)
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {
            "responsible_employee_id": "b", "previous_responsible_employee_id": "a", "reassignment_reason": "supplier_receipt",
            "reassignment_invoice_id": "legacy-receipt", "reassignment_session_id": "legacy-session", "reassigned_at": at,
            "status": "received", "execution_status": "received_from_supplier", "supplier_dispatch_status": "received",
            "received_at": at, "remaining_service_count": 0}})
        await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)
        self.assertEqual(await self.db[notices.TASKS].count_documents({"assignee_id": "a"}), 0)
        from operational_instruction_progress import refresh_instruction_progress
        await refresh_instruction_progress(self.db, self.instruction)
        self.assertIsNone((await self.db[notices.NOTIFICATIONS].find_one({"id": source["id"]}))["read_at"])
        self.assertEqual((await self.listing())["items"], [])
        await self.select("cs")
        result = next(row for row in (await self.listing())["items"] if row["peer"]["id"] == "a")
        self.assertEqual(result["outcome"], "completed")
        self.assertGreater(result["unread_count"], 0)
        await self.select("a")
        detail = await self.detail(notice_id=source["id"])
        self.assertEqual(detail["conversation"]["outcome"], "completed")
        self.assertEqual({card["piece_id"] for card in detail["product_cards"]}, {"p1", "p2"})
        self.assertEqual(await self.db[notices.TASKS].count_documents({"assignee_id": "a"}), 0)
        for collection in (CONVERSATIONS, EVENTS):
            await self.db[collection].delete_many({})
        await self.db[PIECES].update_many({"piece_id": {"$in": ["p1", "p2"]}}, {"$set": {"reassignment_reason": "manual"}})
        denied = await self.client.get(PREFIX + "/instructions/urgent", params={"conversation_only": "true", "notice_id": source["id"]})
        self.assertEqual(denied.status_code, 404)
        await self.select("unrelated")
        response = await self.client.get(PREFIX + "/instructions/urgent", params={"conversation_only": "true", "notice_id": source["id"]})
        self.assertEqual(response.status_code, 404)

    async def test_preupgrade_closed_history_uses_owned_task_snapshot_when_pieces_are_archived(self):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS
        source = await self.db[notices.NOTIFICATIONS].find_one({"recipient_id": "a", "event_type": "created"})
        for collection in (CONVERSATIONS, EVENTS):
            await self.db[collection].delete_many({})
        await self.db[notices.NOTIFICATIONS].update_many({}, {"$unset": {"conversation_pending": ""}})
        await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one({"id": "urgent"}, {"$set": {"status": "completed"}})
        await self.db[PIECES].delete_many({"responsible_employee_id": "a"})
        detail = await self.detail(notice_id=source["id"])
        self.assertEqual({card["piece_id"] for card in detail["product_cards"]}, {"p1", "p2"})
        self.assertEqual({card["name"] for card in detail["product_cards"]}, {"Product p1", "Product p2"})
        self.assertTrue(all(card["assigned_at"] is None for card in detail["product_cards"]))
        self.assertNotIn("p3", str(detail["product_cards"]))

    async def test_preupgrade_submitted_history_survives_second_get_and_read_without_new_owner_fields(self):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS
        source = await self.db[notices.NOTIFICATIONS].find_one({"recipient_id": "a", "event_type": "created"})
        for collection in (CONVERSATIONS, EVENTS):
            await self.db[collection].delete_many({})
        await self.db[notices.NOTIFICATIONS].update_many({}, {"$unset": {"conversation_pending": ""}})
        await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one({"id": "urgent"}, {"$set": {"status": "completed"}})
        await self.db[notices.TASKS].update_many({"assignee_id": "a"}, {"$set": {"status": "submitted", "result_note": "A historical work"}})
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {
            "responsible_employee_id": "b", "product_name": "B new private change", "reassignment_reason": "manual"}})
        first = await self.detail(notice_id=source["id"])
        second = await self.detail(first["conversation"]["id"])
        await self.read(second)
        third = await self.detail(first["conversation"]["id"])
        self.assertNotIn("B new private change", str(third))
        self.assertEqual({card["name"] for card in third["product_cards"]}, {"Product p1", "Product p2"})

    async def test_manual_nonblocking_close_is_closed_history_not_a_ready_result(self):
        cid = (await self.listing())["items"][0]["id"]
        await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one({"id": "urgent"}, {"$set": {
            "status": "completed", "completed_by": "cs", "completed_at": datetime.now(timezone.utc)}})
        await self.select("cs")
        detail = await self.detail(cid)
        self.assertEqual(detail["conversation"]["outcome"], "closed")
        self.assertFalse(detail["conversation"]["alert_active"])
        self.assertNotIn("جاهز", detail["conversation"]["completion_label"])

    async def test_direct_preparation_requires_committed_readiness_timestamp(self):
        cid = (await self.listing())["items"][0]["id"]
        from operational_instruction_progress import refresh_instruction_progress
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {"assembly_status": "ready"}})
        await refresh_instruction_progress(self.db, self.instruction)
        self.assertEqual((await self.detail(cid))["conversation"]["state"], "active")
        await self.db[PIECES].update_many({"responsible_employee_id": "a"}, {"$set": {"assembly_ready_at": datetime.now(timezone.utc)}})
        await refresh_instruction_progress(self.db, self.instruction)
        self.assertEqual((await self.listing())["items"], [])
        await self.select("cs")
        detail = await self.detail(cid)
        self.assertEqual(detail["conversation"]["outcome"], "completed")
        self.assertEqual(detail["conversation"]["completion_label"], "المنتج جاهز")

    async def test_native_unread_count_matches_inbox_scope_without_writes(self):
        from operational_instruction_conversations import conversation_unread_count, EVENTS
        expected = (await self.listing(count_only="true"))["unread_count"]
        self.assertGreater(expected, 0)
        # A foreign tenant and another employee cannot inflate this count.
        await self.db[EVENTS].insert_many([
            {"id": "foreign", "event_id": "foreign", "merchant_id": "other", "recipient_id": "a",
             "conversation_id": "foreign", "kind": "message", "body": "foreign", "read_at": None,
             "silent": False, "author_id": "foreign", "created_at": datetime.now(timezone.utc)},
            {"id": "unrelated", "event_id": "unrelated", "merchant_id": "owner", "recipient_id": "unrelated",
             "conversation_id": "unrelated", "kind": "message", "body": "unrelated", "read_at": None,
             "silent": False, "author_id": "cs", "created_at": datetime.now(timezone.utc)}])
        self.trace.commands.clear()
        self.assertEqual(await conversation_unread_count(self.db, merchant_id="owner", actor_id="a"), expected)
        self.assertFalse(any(command in {"insert", "update", "delete", "findAndModify"} for command, _ in self.trace.commands))
        detail = await self.detail()
        await self.read(detail)
        self.assertEqual(await conversation_unread_count(self.db, merchant_id="owner", actor_id="a"), 0)

    async def test_native_count_retires_completed_assignee_alert_like_inbox(self):
        from operational_instruction_conversations import conversation_unread_count
        self.assertGreater(await conversation_unread_count(self.db, merchant_id="owner", actor_id="a"), 0)
        await self.finish(["p1", "p2"])
        self.assertEqual(await conversation_unread_count(self.db, merchant_id="owner", actor_id="a"), 0)
        self.assertEqual((await self.listing(count_only="true"))["unread_count"], 0)
        await self.select("cs")
        expected = (await self.listing(count_only="true"))["unread_count"]
        self.assertGreater(expected, 0)
        self.assertEqual(await conversation_unread_count(self.db, merchant_id="owner", actor_id="cs"), expected)

    async def test_item_and_piece_scopes_preserve_target_semantics_and_employee_cards(self):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS
        # Multiple physical units of the same item belong to different employees.
        await self.db[PIECES].update_many({}, {"$set": {"order_item_id": "shared-item"}})
        for scope, target, own_ids in (("item", "shared-item", {"p1", "p2"}), ("piece", "p1", {"p1"})):
            with self.subTest(scope=scope):
                for collection in (CONVERSATIONS, EVENTS, notices.NOTIFICATIONS, notices.TASKS):
                    await self.db[collection].delete_many({})
                self.instruction.update(scope=scope, target_id=target, target_ids=[target])
                await self.db[ORDER_TRACKING_INSTRUCTIONS].replace_one({"id": "urgent"}, deepcopy(self.instruction))
                await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)
                await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
                    event_type="created", recipient_ids={"a", "b", "cs"} if scope == "item" else {"a", "cs"})
                await self.select("a")
                detail = await self.detail()
                cid = detail["conversation"]["id"]
                self.assertEqual({card["piece_id"] for card in detail["product_cards"]}, own_ids)
                self.assertEqual(detail["instruction"]["target_ids"], [target])
                self.assertEqual(set(detail["instruction"]["target_piece_ids"]), own_ids)
                await self.select("cs")
                author = await self.detail(cid)
                self.assertEqual({card["piece_id"] for card in author["product_cards"]}, own_ids)
                await self.select("b")
                forbidden = await self.client.get(PREFIX + "/instructions/urgent", params={
                    "conversation_only": "true", "conversation_id": cid})
                self.assertEqual(forbidden.status_code, 404)
                if scope == "item":
                    self.assertEqual({card["piece_id"] for card in (await self.detail())["product_cards"]}, {"p3"})
                else:
                    forbidden = await self.client.get(PREFIX + "/instructions/urgent", params={"conversation_only": "true"})
                    self.assertEqual(forbidden.status_code, 404)

    async def clear_conversation_projection(self):
        from operational_instruction_conversations import CONVERSATIONS, EVENTS, BOOKMARKS, COMMENTS
        for collection in (CONVERSATIONS, EVENTS, BOOKMARKS, COMMENTS, notices.NOTIFICATIONS):
            await self.db[collection].delete_many({})

    async def test_registry_linked_creator_and_assignee_use_private_native_routes(self):
        from fulfillment_v2_routes import _actor_context as real_actor_context
        await self.clear_conversation_projection()
        await self.db.users.update_many({"id": {"$in": ["cs", "a"]}}, {"$unset": {"created_by": ""}})
        await self.db.mezan_employees_v2.insert_many([
            {"account_user_id": actor, "user_id": "owner"} for actor in ("cs", "a")])
        # Default recipient resolution is part of this regression; explicit
        # recipient_ids would hide the missing registry-linked delivery bug.
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="created")
        self.assertEqual(set(await self.db[notices.NOTIFICATIONS].distinct("recipient_id")), {"cs", "a", "b"})
        with patch.object(routes, "_actor_context", real_actor_context):
            await self.select("a")
            directory = await self.listing(directory_only="true")
            self.assertEqual([row["id"] for row in directory["items"]], ["cs"])
            detail = await self.detail()
            cid = detail["conversation"]["id"]
            self.assertEqual(detail["conversation"]["peer"], {"id": "cs", "name": "CS"})
            self.assertEqual({card["piece_id"] for card in detail["product_cards"]}, {"p1", "p2"})
            await self.read(detail)
            self.assertEqual((await self.listing(count_only="true"))["unread_count"], 0)
            response = await self.client.post(PREFIX + "/instructions/urgent/conversation/messages", json={
                "conversation_id": cid, "body": "Linked employee private response", "client_request_id": "linked-private-message-01"})
            self.assertEqual(response.status_code, 200, response.text)
            await self.select("cs")
            creator = await self.detail(cid)
            self.assertTrue(any(event["body"] == "Linked employee private response" for event in creator["timeline"]))
            self.assertEqual({card["piece_id"] for card in creator["product_cards"]}, {"p1", "p2"})
            await self.select("b")
            forbidden = await self.client.get(PREFIX + "/instructions/urgent", params={
                "conversation_only": "true", "conversation_id": cid})
            self.assertEqual(forbidden.status_code, 404)
            self.assertNotIn("Linked employee private response", forbidden.text)

    async def test_registry_link_cannot_override_foreign_unlinked_or_owner_identity(self):
        from fulfillment_v2_routes import _actor_context as real_actor_context
        from operational_instruction_conversations import CONVERSATIONS, COMMENTS, conversation_id
        cid = conversation_id("owner", "urgent", "a")
        cases = (
            ({}, None, 403), ({}, "other", 403), ({"created_by": "other"}, "owner", 404),
            ({"role": "owner", "created_by": "owner"}, "owner", 404),
            ({"is_owner": True, "created_by": "owner"}, "owner", 404),
        )
        with patch.object(routes, "_actor_context", real_actor_context):
            for account, registry_merchant, status in cases:
                with self.subTest(account=account, registry_merchant=registry_merchant):
                    await self.clear_conversation_projection()
                    await self.db.users.replace_one({"id": "a"}, {"id": "a", "role": "employee", "name": "A", **account})
                    await self.db.mezan_employees_v2.delete_many({"account_user_id": "a"})
                    if registry_merchant:
                        await self.db.mezan_employees_v2.insert_one({"account_user_id": "a", "user_id": registry_merchant})
                    await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
                        event_type="created")
                    self.assertEqual(await self.db[notices.NOTIFICATIONS].count_documents({"recipient_id": "a"}), 0)
                    self.assertEqual(await self.db[CONVERSATIONS].count_documents({"merchant_id": "owner", "assignee_id": "a"}), 0)
                    await self.select("a")
                    response = await self.client.get(PREFIX + "/instructions/urgent", params={
                        "conversation_only": "true", "conversation_id": cid})
                    self.assertEqual(response.status_code, status, response.text)
                    for route, payload in (("read", {"conversation_id": cid}), ("messages", {
                            "conversation_id": cid, "body": "Denied linked identity", "client_request_id": "denied-linked-message-01"})):
                        denied = await self.client.post(PREFIX + "/instructions/urgent/conversation/" + route, json=payload)
                        self.assertEqual(denied.status_code, status, denied.text)
                    self.assertEqual(await self.db[COMMENTS].count_documents({"author_id": "a"}), 0)

    async def test_trusted_legacy_merchant_wins_over_conflicting_registry_in_native_routes(self):
        from fulfillment_v2_routes import _actor_context as real_actor_context
        await self.clear_conversation_projection()
        await self.db.mezan_employees_v2.insert_one({"account_user_id": "a", "user_id": "other"})
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="created")
        self.assertEqual(await self.db[notices.NOTIFICATIONS].count_documents({"recipient_id": "a"}), 1)
        with patch.object(routes, "_actor_context", real_actor_context):
            await self.select("a")
            detail = await self.detail()
            self.assertEqual({card["piece_id"] for card in detail["product_cards"]}, {"p1", "p2"})
            self.assertEqual(detail["conversation"]["peer"]["id"], "cs")

