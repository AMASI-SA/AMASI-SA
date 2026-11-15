"""Linked-account regressions against real notification/conversation functions.

Only the async Mongo boundary and push enqueue boundary are synthetic. Merchant
resolution, recipient policy, task reconciliation and projection execute normally.
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import operational_instruction_notifications as notices
from operational_instruction_conversations import CONVERSATIONS, EVENTS, reconcile_instruction
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from test_operational_instruction_notifications import DB


class InstructionAccountScope(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DB()
        self.db.raw.users.insert_many([
            {"id": "owner", "role": "owner", "name": "Owner"},
            *[{"id": actor, "created_by": "owner", "role": "employee", "name": actor.upper()}
              for actor in ("cs", "a", "b", "unrelated")],
        ])
        self.instruction = {"id": "linked-note", "user_id": "owner", "created_by": "cs",
            "order_number": "100", "scope": "order", "action_type": "urgent_preparation",
            "thread_protocol": 1, "target_stages": ["preparation"], "status": "active",
            "note": "Prepare the assigned products", "created_at": datetime.now(timezone.utc)}
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(dict(self.instruction))
        self.db.raw.mezan_preparation_pieces_v1.insert_many([
            {"user_id": "owner", "order_number": "100", "piece_id": pid, "order_item_id": pid + "-item",
             "product_name": "Product " + pid, "responsible_employee_id": actor,
             "status": "in_progress", "execution_status": "in_progress"}
            for pid, actor in (("p1", "a"), ("p2", "b"))])
        self.db.raw.order_review_workflows.insert_one({"user_id": "owner", "order_number": "100", "stage": "in_progress"})
        enqueue = patch.object(notices, "_enqueue", AsyncMock())
        enqueue.start()
        self.addCleanup(enqueue.stop)
        await notices.ensure_instruction_tasks(self.db, "owner", self.instruction)

    def registry_link(self, actor, merchant="owner"):
        self.db.raw.users.update_one({"id": actor}, {"$unset": {"created_by": ""}})
        self.db.raw.mezan_employees_v2.update_one({"account_user_id": actor},
            {"$set": {"user_id": merchant}}, upsert=True)

    async def test_linked_creator_and_assignee_receive_default_events(self):
        for actor in ("cs", "a", "unrelated"):
            self.registry_link(actor)
        self.assertEqual(await notices.recipients(self.db, "owner", self.instruction), {"cs", "a", "b"})
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="created")
        self.assertEqual({row["recipient_id"] for row in self.db.raw[notices.NOTIFICATIONS].find()}, {"cs", "a", "b"})
        source = self.db.raw[notices.NOTIFICATIONS].find_one({"recipient_id": "a"})
        self.assertEqual((source["sender_id"], source["sender_name"]), ("cs", "CS"))

    async def test_linked_participants_are_retained_by_conversation_projection(self):
        self.registry_link("cs")
        self.registry_link("a")
        rows = await reconcile_instruction(self.db, self.instruction)
        self.assertEqual({row["assignee_id"] for row in rows}, {"a", "b"})
        own = next(row for row in rows if row["assignee_id"] == "a")
        self.assertEqual((own["creator_name"], own["assignee_name"]), ("CS", "A"))
        self.assertEqual({piece["piece_id"] for piece in own["pieces"]}, {"p1"})

    async def test_linked_background_assignment_catchup_remains_idempotent(self):
        self.registry_link("a")
        for _ in range(2):
            await notices.refresh_actor_instructions(self.db, merchant_id="owner", actor_id="a")
        rows = list(self.db.raw[notices.NOTIFICATIONS].find())
        self.assertEqual([(row["recipient_id"], row["event_type"]) for row in rows], [("a", "assigned")])
        self.assertEqual(self.db.raw[CONVERSATIONS].count_documents({"merchant_id": "owner", "assignee_id": "a"}), 1)

    async def test_linked_submitter_receives_result_after_assignment_moves(self):
        self.registry_link("a")
        self.db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p1"}, {"$set": {"responsible_employee_id": "b"}})
        await notices.emit_instruction_notification(self.db, merchant_id="owner",
            instruction={**self.instruction, "submitted_by": "a", "rejection_note": "Review the adjustment"},
            event_type="rejected")
        self.assertEqual(self.db.raw[notices.NOTIFICATIONS].count_documents({"recipient_id": "a", "event_type": "rejected"}), 1)

    async def test_current_merchant_precedence_cannot_be_overridden_by_registry_or_tasks(self):
        for actor in ("a", "b"):
            self.registry_link(actor)
        self.db.raw.users.update_one({"id": "a"}, {"$set": {"created_by": "other"}})
        self.db.raw.users.update_one({"id": "b"}, {"$set": {"created_by": "owner"}})
        self.db.raw.mezan_employees_v2.update_one({"account_user_id": "b"}, {"$set": {"user_id": "other"}})
        self.assertEqual(await notices.recipients(self.db, "owner", self.instruction), {"cs", "b"})
        rows = await reconcile_instruction(self.db, self.instruction)
        self.assertEqual({row["assignee_id"] for row in rows}, {"b"})
        await notices.refresh_actor_instructions(self.db, merchant_id="owner", actor_id="a")
        self.assertEqual(self.db.raw[notices.NOTIFICATIONS].count_documents({"recipient_id": "a"}), 0)

    async def test_invalid_or_inactive_link_never_creates_a_recipient_or_catchup(self):
        cases = (
            ({}, None), ({}, "other"), ({"role": "owner", "created_by": "owner"}, "owner"),
            ({"is_owner": True, "created_by": "owner"}, "owner"), ({"disabled": True}, "owner"),
            ({"is_active": False}, "owner"), ({"deleted_at": "2026-11-14"}, "owner"),
        )
        for account, link in cases:
            with self.subTest(account=account, registry_merchant=link):
                self.db.raw.users.replace_one({"id": "a"}, {"id": "a", "role": "employee", "name": "A", **account})
                self.db.raw.mezan_employees_v2.delete_many({"account_user_id": "a"})
                if link:
                    self.db.raw.mezan_employees_v2.insert_one({"account_user_id": "a", "user_id": link})
                self.assertNotIn("a", await notices.recipients(self.db, "owner", self.instruction))
                await notices.refresh_actor_instructions(self.db, merchant_id="owner", actor_id="a")
                self.assertEqual(self.db.raw[notices.NOTIFICATIONS].count_documents({"recipient_id": "a"}), 0)

    async def test_historical_participant_name_is_not_an_active_delivery_grant(self):
        self.registry_link("a")
        self.db.raw.users.update_one({"id": "a"}, {"$set": {"is_active": False}})
        rows = await reconcile_instruction(self.db, self.instruction)
        own = next(row for row in rows if row["assignee_id"] == "a")
        self.assertEqual(own["assignee_name"], "A")
        self.assertNotIn("a", await notices.recipients(self.db, "owner", self.instruction))
        await notices.refresh_actor_instructions(self.db, merchant_id="owner", actor_id="a")
        self.assertEqual(self.db.raw[notices.NOTIFICATIONS].count_documents({"recipient_id": "a"}), 0)

    async def test_push_enqueue_observes_the_durable_conversation_projection(self):
        observed = []

        async def enqueue(db, row):
            saved = await db[notices.NOTIFICATIONS].find_one({"_id": row["id"]})
            projected = await db[EVENTS].find_one({"merchant_id": "owner", "recipient_id": "a", "source_notice_id": row["id"]})
            self.assertIsNotNone(projected)
            self.assertIs(saved["conversation_pending"], False)
            observed.append(row["id"])

        with patch.object(notices, "_enqueue", enqueue):
            result = await notices.emit_instruction_notification(self.db, merchant_id="owner",
                instruction=self.instruction, event_type="created", recipient_ids={"a"})
        self.assertEqual(result, 1)
        self.assertEqual(len(observed), 1)

    async def test_projection_failure_keeps_inbox_and_push_without_failing_business_command(self):
        before = self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": self.instruction["id"]})
        delivered = []

        async def enqueue(db, row):
            saved = await db[notices.NOTIFICATIONS].find_one({"_id": row["id"]})
            self.assertIs(saved["conversation_pending"], True)
            delivered.append(saved["id"])

        with patch("operational_instruction_conversations.capture_notifications", side_effect=RuntimeError("synthetic projection failure")), \
                patch.object(notices, "_enqueue", enqueue):
            result = await notices.emit_instruction_notification(self.db, merchant_id="owner",
                instruction=self.instruction, event_type="created", recipient_ids={"a"})
        self.assertEqual(result, 1)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(self.db.raw[notices.NOTIFICATIONS].count_documents({"recipient_id": "a"}), 1)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": self.instruction["id"]}), before)

    async def test_repeated_emission_preserves_original_delivery_age(self):
        await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
            event_type="created", recipient_ids={"a"})
        original_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.db.raw[notices.NOTIFICATIONS].update_one({"recipient_id": "a"}, {"$set": {"created_at": original_at}})
        queued_at = []

        async def enqueue(db, row):
            queued_at.append(row["created_at"])

        with patch.object(notices, "_enqueue", enqueue):
            await notices.emit_instruction_notification(self.db, merchant_id="owner", instruction=self.instruction,
                event_type="created", recipient_ids={"a"})
        self.assertEqual(queued_at, [original_at])
        self.assertEqual(self.db.raw[notices.NOTIFICATIONS].count_documents({"recipient_id": "a"}), 1)
