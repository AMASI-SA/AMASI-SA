"""Notification previews must describe the saved event and its actual author."""
from datetime import datetime, timezone

import operational_instruction_notifications as notifications
import order_tracking_notes_routes as routes
import test_operational_instruction_notifications as fixtures
import test_instruction_edit_flow as edit_fixtures


class PreviewSnapshotTests(fixtures.InstructionNotificationTests):
    async def test_snapshot_freezes_action_message_and_verified_sender(self):
        self.db.raw.users.update_one({"id": "cs"}, {"$set": {"name": "مرسل الاختبار"}})
        original = {**self.instruction, "action_type": "edit_product", "note": "يرجى تعديل اللون"}
        await notifications.emit_instruction_notification(
            self.db, merchant_id="owner", instruction=original,
            event_type="created", recipient_ids={"a"})
        saved = self.db.raw[notifications.NOTIFICATIONS].find_one({"recipient_id": "a"})
        self.assertEqual(saved.get("action_type"), "edit_product")
        self.assertEqual(saved.get("event_message_version"), 1)
        self.assertEqual(saved["sender_name"], "مرسل الاختبار")
        self.assertEqual(saved["message"], "يرجى تعديل اللون")
        self.db.raw.users.update_one({"id": "cs"}, {"$set": {"name": "اسم لاحق"}})
        await notifications.emit_instruction_notification(
            self.db, merchant_id="owner",
            instruction={**original, "action_type": "cancel_order", "note": "تعليمات لاحقة"},
            event_type="created", recipient_ids={"a"})
        after = self.db.raw[notifications.NOTIFICATIONS].find_one({"id": saved["id"]})
        self.assertEqual(after, saved)

    async def test_registry_linked_sender_name_is_available_without_legacy_created_by(self):
        self.db.raw.users.insert_one({"id": "registry-sender", "role": "employee", "name": "الموظف المرتبط"})
        self.db.raw.mezan_employees_v2.insert_one({"account_user_id": "registry-sender", "user_id": "owner"})
        await notifications.emit_instruction_notification(
            self.db, merchant_id="owner", instruction=self.instruction,
            event_type="comment", recipient_ids={"a"}, sender_id="registry-sender")
        saved = self.db.raw[notifications.NOTIFICATIONS].find_one({"recipient_id": "a"})
        self.assertEqual(saved["sender_id"], "registry-sender")
        self.assertEqual(saved["sender_name"], "الموظف المرتبط")

    async def test_foreign_sender_name_never_enters_the_snapshot(self):
        self.db.raw.users.insert_one({"id": "foreign-sender", "created_by": "other-store", "name": "اسم خارج النطاق"})
        await notifications.emit_instruction_notification(
            self.db, merchant_id="owner", instruction=self.instruction,
            event_type="comment", recipient_ids={"a"}, sender_id="foreign-sender")
        saved = self.db.raw[notifications.NOTIFICATIONS].find_one({"recipient_id": "a"})
        self.assertEqual(saved["sender_id"], "__system__")
        self.assertEqual(saved["sender_name"], "تحديثات النظام")
        self.assertNotIn("اسم خارج النطاق", str(saved))

    async def test_approval_submission_snapshots_the_actual_response(self):
        submitted = {**self.instruction, "status": "waiting_customer_service_approval",
            "submitted_by": "a", "submitted_result": "تمت مراجعة المقاس المطلوب",
            "submitted_at": datetime.now(timezone.utc)}
        await routes._record_approval_submission(
            self.db, merchant_id="owner", instruction=submitted,
            actor_id="a", actor_name="موظف الاختبار")
        saved = list(self.db.raw[notifications.NOTIFICATIONS].find({"event_type": "submitted"}))
        self.assertTrue(saved)
        self.assertTrue(all(row["message"] == "تمت مراجعة المقاس المطلوب" for row in saved))
        self.assertTrue(all(row["sender_id"] == "a" for row in saved))
        self.assertEqual(submitted["note"], "stop")

    async def test_empty_submission_uses_an_event_summary_instead_of_the_old_request(self):
        submitted = {**self.instruction, "status": "waiting_customer_service_approval",
            "submitted_by": "a", "submitted_result": "",
            "submitted_at": datetime.now(timezone.utc)}
        await routes._record_approval_submission(
            self.db, merchant_id="owner", instruction=submitted,
            actor_id="a", actor_name="موظف الاختبار")
        saved = list(self.db.raw[notifications.NOTIFICATIONS].find({"event_type": "submitted"}))
        self.assertTrue(saved)
        self.assertTrue(all(row["message"] == "تم إرسال نتيجة التنفيذ للمراجعة." for row in saved))


class CompletionPreviewSnapshotTests(edit_fixtures.EditFlowTests):
    async def test_employee_completion_keeps_actual_sender_and_response(self):
        for actor, name in [("cs", "مرسل التعليمات"), ("a", "منفذ أول"), ("b", "منفذ ثان")]:
            self.db.raw.users.update_one({"id": actor}, {"$set": {"name": name}})
        tasks, digest = await self.sent()
        for task in tasks:
            self.select(task["assignee_id"])
            response = self.post("tasks/submit", {"task_ids": [task["id"]],
                "confirmation": "modified", "note": "تم تنفيذ تعديل المقاس", "revision": digest})
            self.assertEqual(response.status_code, 200, response.text)
        last_actor = tasks[-1]["assignee_id"]
        saved = list(self.db.raw[notifications.NOTIFICATIONS].find({"event_type": "completed"}))
        self.assertTrue(saved)
        self.assertTrue(all(row["sender_id"] == last_actor for row in saved))
        self.assertTrue(all(row["message"] == "تم تنفيذ تعديل المقاس" for row in saved))
        self.assertTrue(all(row.get("event_message_version") == 1 for row in saved))


# Reuse the established route fixtures without rerunning their inherited cases.
for cls, base in [(PreviewSnapshotTests, fixtures.InstructionNotificationTests),
                  (CompletionPreviewSnapshotTests, edit_fixtures.EditFlowTests)]:
    for name in dir(base):
        if name.startswith("test_") and name not in cls.__dict__:
            setattr(cls, name, None)
del cls, base, name
