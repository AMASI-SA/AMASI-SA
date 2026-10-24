import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_operational_instruction_notifications import DB
from operational_instruction_comments import make_instruction_comments_router, COMMENTS
from operational_instruction_notifications import TASKS, NOTIFICATIONS, ensure_instruction_tasks
from operational_instruction_progress import refresh_instruction_progress
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS, enforce_instruction_rows, instruction_applies_to, instruction_snapshot


class CommentProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DB()
        for uid in ("owner", "creator", "a", "b"):
            self.db.raw.users.insert_one({"id": uid, "name": uid, "role": "owner" if uid == "owner" else "employee", "created_by": "owner"})
        self.row = {"id": "n", "user_id": "owner", "created_by": "creator", "order_number": "1", "scope": "order",
            "action_type": "edit_order", "status": "active", "task_protocol": 1, "thread_protocol": 1,
            "target_stages": ["preparation"], "note": "change", "created_at": datetime.now(timezone.utc)}
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(dict(self.row))
        for pid, uid in (("p1", "a"), ("p2", "b")):
            self.db.raw.mezan_preparation_pieces_v1.insert_one({"piece_id": pid, "order_item_id": pid, "responsible_employee_id": uid,
                "product_name": pid, "status": "in_progress", "user_id": "owner", "order_number": "1"})
        self.db.raw.order_review_workflows.insert_one({"user_id": "owner", "order_number": "1", "stage": "in_progress"})
        self.actor = self.db.raw.users.find_one({"id": "a"})
        async def current(): return self.actor
        self.app = FastAPI(); self.app.include_router(make_instruction_comments_router(self.db, current))
        self.client = TestClient(self.app)

    def tearDown(self): self.client.close()

    async def test_product_stop_does_not_block_sibling_products_at_any_preparation_stage(self):
        for scope, target in (("piece", "p1"), ("item", "i1")):
            row = {**self.row, "scope": scope, "target_ids": [target],
                   "enforcement": "completion_required", "activation_state": "active",
                   "activated_piece_stages": {"p1": "preparation"}}
            for stage in ("preparation", "preparation_receiving", "assembly_labeling"):
                with self.assertRaises(HTTPException):
                    enforce_instruction_rows([row], stage=stage, actor_id="a",
                                             piece_id="p1", order_item_id="i1")
                self.assertEqual(enforce_instruction_rows([row], stage=stage, actor_id="b",
                                             piece_id="p2", order_item_id="i2"), [])

    async def test_private_thread_idempotency_and_no_execution(self):
        tasks = await ensure_instruction_tasks(self.db, "owner", self.row)
        own = next(t for t in tasks if t["assignee_id"] == "a")
        other = next(t for t in tasks if t["assignee_id"] == "b")
        path = f"/order-tracking-notes/instructions/n/tasks/{own['id']}/comments"
        data = {"body": "الملاحظة واضحة", "client_request_id": "request_1234567890"}
        for _ in range(2): self.assertEqual(self.client.post(path, json=data).status_code, 200)
        self.assertEqual(self.db.raw[COMMENTS].count_documents({}), 1)
        self.assertEqual(self.db.raw[TASKS].count_documents({"status": "submitted"}), 0)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one()["status"], "active")
        self.assertEqual(self.client.post(path, json={**data, "body": "changed"}).status_code, 409)
        self.assertEqual(self.client.get(f"/order-tracking-notes/instructions/n/tasks/{other['id']}/comments").status_code, 404)
        self.actor = self.db.raw.users.find_one({"id": "owner"})
        self.assertEqual(self.client.get(path).status_code, 404)
        self.actor = self.db.raw.users.find_one({"id": "creator"})
        self.assertEqual(self.client.get(path).json()["items"][0]["body"], data["body"])
        self.assertEqual(self.db.raw[NOTIFICATIONS].find_one()["recipient_id"], "creator")

    async def test_reassignment_removes_previous_employee_thread_access(self):
        tasks = await ensure_instruction_tasks(self.db, "owner", self.row)
        own = next(t for t in tasks if t["assignee_id"] == "a")
        self.db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p1"}, {"$set": {"responsible_employee_id": "b"}})
        self.assertEqual(self.client.get(f"/order-tracking-notes/instructions/n/tasks/{own['id']}/comments").status_code, 404)

    async def test_urgent_progress_reports_actual_product_to_requester_once(self):
        note = {**self.row, "action_type": "urgent_preparation", "priority": "urgent", "target_stages": ["preparation", "preparation_receiving"]}
        await refresh_instruction_progress(self.db, note)
        self.assertEqual(self.db.raw[NOTIFICATIONS].count_documents({}), 0)
        self.db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p1"}, {"$set": {"status": "ready_for_employee_receipt", "execution_status": "ready"}})
        await refresh_instruction_progress(self.db, note)
        await refresh_instruction_progress(self.db, note)
        rows = list(self.db.raw[NOTIFICATIONS].find({}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recipient_id"], "creator")
        self.assertEqual(rows[0]["metadata"]["piece_id"], "p1")
        self.assertEqual(rows[0]["metadata"]["current"]["status"], "ready_for_employee_receipt")

    def test_notes_never_block_even_legacy_required_approval(self):
        for action in ("general", "urgent_preparation", "delivery_instruction"):
            result = enforce_instruction_rows([{**self.row, "action_type": action, "status": "waiting_customer_service_approval", "enforcement": "completion_required"}],
                stage="preparation", actor_id="a", order_wide=True)
            self.assertEqual(len(result), 1)

    def test_activated_hold_persists_and_future_piece_is_not_stopped_early(self):
        row = {**self.row, "activation_state": "active", "activated_piece_stages": {"p1": "preparation"}, "enforcement": "completion_required"}
        self.assertTrue(instruction_applies_to(row, stage="assembly_labeling", piece_id="p1"))
        self.assertFalse(instruction_applies_to(row, stage="reviewed", piece_id="p2"))
        with self.assertRaises(HTTPException): enforce_instruction_rows([row], stage="assembly_labeling", actor_id="a", piece_id="p1")

    def test_stage_error_and_snapshot_do_not_expose_other_worker_discussion(self):
        row = {**self.row, "enforcement": "completion_required", "proposed_changes": [{"requested": "private B"}], "submitted_result": "private B", "rejection_note": "private B"}
        snapshot = instruction_snapshot(row)
        self.assertNotIn("private B", str(snapshot))
        with self.assertRaises(HTTPException) as caught:
            enforce_instruction_rows([row], stage="preparation", actor_id="a", piece_id="p1")
        public = caught.exception.detail["instructions"][0]
        self.assertEqual(public["target_ids"], ["p1"])
        self.assertNotIn("private B", str(public))


if __name__ == "__main__": unittest.main()
