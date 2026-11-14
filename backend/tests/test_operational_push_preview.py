"""Synthetic delivery regressions; no database, device or provider writes."""
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx

from operational_push import DEVICES, OUTBOX, deliver_one, enqueue_operational_push, now, send_to_provider
from test_operational_push import DB


NOTIFICATIONS = "operational_instruction_notifications_v1"
ACCESS = "mezan_mobile_app_access_v1"
DIRECTION_MARKS = dict.fromkeys(map(ord, "\u200f\u2066\u2067\u2068\u2069"))


def visible(value):
    return value.translate(DIRECTION_MARKS)


class PushPreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.reset_data()

    def reset_data(self):
        self.db = DB()
        self.sent = []
        self.db.raw.users.insert_one({"id": "staff", "created_by": "owner", "role": "employee"})
        self.db.raw[ACCESS].insert_one({
            "owner_user_id": "owner", "user_id": "staff", "enabled": True,
            "permissions": ["app.page.my_products"],
        })
        self.db.raw.order_review_auth_sessions_v2.insert_one({
            "_id": "session", "actor_id": "staff", "user_id": "owner", "state": "active",
            "expires_at": now() + timedelta(days=1),
        })
        self.db.raw[DEVICES].insert_one({
            "_id": "device", "kind": "expo", "actor_id": "staff", "merchant_id": "owner",
            "session_id": "session", "generation": "g1", "installation_id": "synthetic_installation_1234",
            "active": True, "token": "ExpoPushToken[synthetic]",
        })
        self.db.raw[NOTIFICATIONS].insert_one({
            "_id": "event", "id": "event", "merchant_id": "owner", "recipient_id": "staff",
            "instruction_id": "instruction", "sender_id": "synthetic_author",
            "sender_name": "موظف تجريبي", "event_type": "comment", "action_type": "urgent_preparation",
            "event_message_version": 1, "title": "تعليق جديد على المنتج", "order_number": "TEST-501",
            "message": "نفذ التعديل في العبوة الزرقاء", "priority": "normal", "read_at": None,
            "created_at": now(),
        })

    async def queue(self):
        await enqueue_operational_push(
            self.db, recipient_id="staff", event_id="event", instruction_id="instruction",
            order_number="IGNORED-TRANSIENT-ORDER",
        )

    async def sender(self, device, payload, *, ticket=None):
        self.sent.append({"device": device, "payload": payload, "ticket": ticket})
        return {"status": "provider_accepted"}

    async def deliver(self):
        await self.queue()
        self.assertTrue(await deliver_one(self.db, self.sender))

    async def test_saved_comment_sender_action_order_and_actual_message(self):
        await self.deliver()
        payload = self.sent[0]["payload"]
        self.assertEqual(visible(payload["title"]), "موظف تجريبي — تعليق جديد · استعجال التجهيز")
        self.assertEqual(visible(payload["body"]), "نفذ التعديل في العبوة الزرقاء\nالطلب TEST-501")
        self.assertTrue(payload["title"].startswith("\u200f"))
        self.assertTrue(payload["body"].startswith("\u200f"))
        self.assertIn("\u2066TEST-501\u2069", payload["body"])
        self.assertEqual(set(payload), {"title", "body", "event_id", "instruction_id", "priority", "notice_id"})
        self.assertEqual(payload["notice_id"], payload["event_id"])
        self.assertNotIn("IGNORED-TRANSIENT-ORDER", json.dumps(payload))
        self.assertEqual(self.db.raw[NOTIFICATIONS].count_documents({"read_at": None}), 1)

    async def test_snapshot_survives_later_instruction_change_and_reassignment(self):
        self.db.raw.mezan_order_tracking_instructions_v1.insert_one({
            "id": "instruction", "user_id": "owner", "note": "UNRELATED_LATER_NOTE",
            "created_by": "different_author", "action_type": "cancel_order", "status": "completed",
        })
        self.db.raw.operational_instruction_tasks_v1.insert_one({
            "merchant_id": "owner", "instruction_id": "instruction", "assignee_id": "new_employee",
        })
        await self.deliver()
        payload = self.sent[0]["payload"]
        self.assertIn("نفذ التعديل", payload["body"])
        self.assertIn("استعجال التجهيز", payload["title"])
        self.assertNotIn("UNRELATED_LATER_NOTE", json.dumps(payload))
        self.assertEqual(self.db.raw.operational_instruction_tasks_v1.count_documents({}), 1)

    async def test_progress_uses_system_and_saved_stage_change_not_old_note_or_employee(self):
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {
            "event_type": "progress", "sender_name": "WRONG_RESPONSIBLE_EMPLOYEE",
            "message": "OLD_REQUEST_NOTE_MUST_NOT_APPEAR", "metadata": {
                "employee_name": "RESPONSIBLE_NOT_AUTHOR", "previous": {"stage": "preparation"},
                "current": {"stage": "assembly_labeling"}, "private": "PRIVATE_METADATA",
            },
        }})
        await self.deliver()
        payload = self.sent[0]["payload"]
        self.assertIn("تحديثات النظام", payload["title"])
        self.assertIn("انتقل المنتج من التجهيز إلى التجميع والعنونة", visible(payload["body"]))
        serialized = json.dumps(payload)
        for forbidden in ("OLD_REQUEST_NOTE", "WRONG_RESPONSIBLE", "RESPONSIBLE_NOT_AUTHOR", "PRIVATE_METADATA"):
            self.assertNotIn(forbidden, serialized)

    async def test_progress_without_known_transition_and_acknowledgment_use_event_summary(self):
        for event in ("progress", "acknowledged"):
            with self.subTest(event=event):
                self.reset_data()
                self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {
                    "event_type": event, "message": "OLD_INSTRUCTION_NOT_A_REPLY",
                    "metadata": {"current": {"stage": "unknown"}},
                }})
                await self.deliver()
                body = self.sent[0]["payload"]["body"]
                self.assertNotIn("OLD_INSTRUCTION", body)
                self.assertIn("تحديث تقدم تجهيز المنتج" if event == "progress" else "تم الاطلاع على التعليمات", body)

    async def test_legacy_completion_text_is_not_presented_as_new_response(self):
        for event in ("submitted", "approved", "completed", "closed"):
            with self.subTest(event=event):
                self.reset_data()
                self.db.raw[NOTIFICATIONS].update_one({}, {
                    "$set": {"event_type": event, "message": "OLD_REQUEST_NOT_ACTUAL_RESPONSE"},
                    "$unset": {"event_message_version": ""},
                })
                await self.deliver()
                self.assertNotIn("OLD_REQUEST", self.sent[0]["payload"]["body"])
                if event != "submitted":
                    self.assertIn("تحديثات النظام", self.sent[0]["payload"]["title"])

    async def test_versioned_completion_uses_actual_saved_response(self):
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {
            "event_type": "completed", "message": "تم تنفيذ التعديل المطلوب", "event_message_version": 1,
        }})
        await self.deliver()
        self.assertIn("تم تنفيذ التعديل المطلوب", self.sent[0]["payload"]["body"])

    async def test_edit_action_uses_the_existing_arabic_task_name(self):
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {"action_type": "edit_product"}})
        await self.deliver()
        self.assertIn("إيقاف لتعديل منتج", self.sent[0]["payload"]["title"])

    async def test_created_edit_preview_leads_with_required_action_and_actual_message(self):
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {
            "event_type": "created", "action_type": "edit_product",
            "message": "يرجى تغيير لون العبوة إلى الأزرق",
        }})
        await self.deliver()
        payload = self.sent[0]["payload"]
        self.assertEqual(visible(payload["title"]), "موظف تجريبي — إيقاف لتعديل منتج")
        self.assertEqual(visible(payload["body"]), "يرجى تغيير لون العبوة إلى الأزرق\nالطلب TEST-501")

    async def test_existing_foreign_mismatched_or_read_event_suppresses_new_send(self):
        cases = [("merchant_id", "foreign"), ("recipient_id", "other"),
                 ("instruction_id", "other_instruction"), ("id", "other_event"), ("read_at", now())]
        for key, value in cases:
            with self.subTest(field=key):
                self.reset_data()
                self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {key: value}})
                await self.deliver()
                self.assertEqual(self.sent, [])
                self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "discarded")

    async def test_job_device_actor_or_explicit_merchant_mismatch_suppresses_send(self):
        for key in ("actor_id", "merchant_id"):
            with self.subTest(field=key):
                self.reset_data()
                await self.queue()
                self.db.raw[OUTBOX].update_one({}, {"$set": {key: "foreign"}})
                self.assertTrue(await deliver_one(self.db, self.sender))
                self.assertEqual(self.sent, [])

    async def test_disabled_deleted_or_changed_merchant_cannot_receive_rich_content(self):
        for patch_fields in ({"disabled": True}, {"is_active": False}, {"deleted_at": now()},
                             {"created_by": "other_merchant"}):
            with self.subTest(fields=tuple(patch_fields)):
                self.reset_data()
                await self.queue()
                self.db.raw.users.update_one({}, {"$set": patch_fields})
                self.assertTrue(await deliver_one(self.db, self.sender))
                self.assertEqual(self.sent, [])

    async def test_disabled_empty_or_absent_native_access_blocks_rich_preview(self):
        for change in ("disabled", "empty", "absent"):
            with self.subTest(change=change):
                self.reset_data()
                if change == "absent":
                    self.db.raw[ACCESS].delete_many({})
                else:
                    self.db.raw[ACCESS].update_one({}, {"$set": {
                        "enabled" if change == "disabled" else "permissions": False if change == "disabled" else [],
                    }})
                await self.deliver()
                self.assertEqual(self.sent, [])

    async def test_owner_and_store_driver_keep_published_notification_access_exceptions(self):
        for role in ("owner", "store_driver"):
            with self.subTest(role=role):
                self.reset_data()
                self.db.raw.users.update_one({}, {"$set": {"role": role}})
                self.db.raw[ACCESS].delete_many({})
                if role == "owner":
                    self.db.raw[DEVICES].update_one({}, {"$set": {"merchant_id": "staff"}})
                    self.db.raw.order_review_auth_sessions_v2.update_one({}, {"$set": {"user_id": "staff"}})
                    self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {"merchant_id": "staff"}})
                await self.deliver()
                self.assertIn("نفذ التعديل", self.sent[0]["payload"]["body"])

    async def test_registry_linked_employee_uses_the_current_real_merchant_resolver(self):
        self.db.raw.users.update_one({}, {"$unset": {"created_by": ""}})
        self.db.raw.mezan_employees_v2.insert_one({"account_user_id": "staff", "user_id": "owner"})
        await self.deliver()
        self.assertIn("نفذ التعديل", self.sent[0]["payload"]["body"])

    async def test_account_switch_during_real_preview_hydration_prevents_submission(self):
        access_collection = self.db.raw[ACCESS]
        read_access = access_collection.find_one

        def rebind_during_access(*args, **kwargs):
            result = read_access(*args, **kwargs)
            self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {
                "actor_id": "other_staff", "generation": "g2", "session_id": "other_session",
                "token": "ExpoPushToken[rebound]", "installation_id": "rebound_installation_1234",
            }})
            return result

        with patch.object(access_collection, "find_one", side_effect=rebind_during_access) as lookup:
            await self.deliver()
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "discarded")

    async def test_logout_during_real_preview_hydration_prevents_submission(self):
        access_collection = self.db.raw[ACCESS]
        read_access = access_collection.find_one

        def revoke_during_access(*args, **kwargs):
            result = read_access(*args, **kwargs)
            self.db.raw.order_review_auth_sessions_v2.update_one(
                {"_id": "session"}, {"$set": {"state": "revoked"}},
            )
            return result

        with patch.object(access_collection, "find_one", side_effect=revoke_during_access) as lookup:
            await self.deliver()
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "discarded")

    async def test_true_missing_legacy_snapshot_preserves_existing_generic_dispatch(self):
        self.db.raw[NOTIFICATIONS].delete_many({})
        self.db.raw[ACCESS].delete_many({})
        self.db.raw.users.update_one({}, {"$unset": {"created_by": ""}})
        await self.deliver()
        payload = self.sent[0]["payload"]
        self.assertEqual(payload["body"], "لديك تحديث على مهمة. افتح التطبيق للاطلاع والتأكيد.")
        self.assertNotIn("TEST-501", json.dumps(payload))
        self.assertEqual(self.db.raw[NOTIFICATIONS].count_documents({}), 0)

    async def test_capable_device_receives_the_same_scoped_conversation_count(self):
        self.db.raw[DEVICES].update_one({}, {"$set": {"notification_presentation": "amasi_android_rtl_v1"}})
        calls = []
        async def count(db, **scope):
            self.assertIs(db, self.db)
            calls.append(scope)
            return 27
        with patch("operational_instruction_conversations.conversation_unread_count", side_effect=count):
            await self.deliver()
        self.assertEqual(calls, [{"merchant_id": "owner", "actor_id": "staff"}])
        self.assertEqual(self.sent[0]["payload"]["unread_count"], 27)

    async def test_account_change_during_conversation_count_prevents_submission(self):
        self.db.raw[DEVICES].update_one({}, {"$set": {"notification_presentation": "amasi_android_rtl_v1"}})
        async def count(db, **scope):
            self.db.raw[DEVICES].update_one({}, {"$set": {"actor_id": "other", "generation": "g2"}})
            return 27
        with patch("operational_instruction_conversations.conversation_unread_count", side_effect=count):
            await self.deliver()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "discarded")

    async def test_missing_legacy_snapshot_does_not_gain_a_private_count(self):
        self.db.raw[DEVICES].update_one({}, {"$set": {"notification_presentation": "amasi_android_rtl_v1"}})
        self.db.raw[NOTIFICATIONS].delete_many({})
        with patch("operational_instruction_conversations.conversation_unread_count", side_effect=AssertionError("must not count")):
            await self.deliver()
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("unread_count", self.sent[0]["payload"])

    async def test_web_dispatch_keeps_generic_text_even_with_a_snapshot(self):
        self.db.raw[DEVICES].update_one({}, {"$set": {"kind": "web"}})
        await self.deliver()
        self.assertEqual(self.sent[0]["payload"]["body"], "لديك تحديث على مهمة. افتح التطبيق للاطلاع والتأكيد.")
        self.assertNotIn("TEST-501", json.dumps(self.sent[0]["payload"]))

    async def test_receipt_polling_does_not_hydrate_read_snapshot_or_recheck_preview_access(self):
        await self.queue()
        self.db.raw[OUTBOX].update_one({}, {"$set": {"ticket": "synthetic_ticket"}})
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {"read_at": now()}})
        self.db.raw[ACCESS].update_one({}, {"$set": {"enabled": False}})
        with patch.object(self.db.raw[NOTIFICATIONS], "find_one", side_effect=AssertionError("receipt must not hydrate")):
            self.assertTrue(await deliver_one(self.db, self.sender))
        self.assertEqual(self.sent[0]["ticket"], "synthetic_ticket")
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "provider_accepted")

    async def test_receipt_rejection_revalidates_unread_before_any_resend(self):
        await self.queue()
        self.db.raw[OUTBOX].update_one({}, {"$set": {"ticket": "synthetic_ticket"}})
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {"read_at": now()}})

        async def receipt_error(device, payload, *, ticket=None):
            self.sent.append({"ticket": ticket})
            return {"status": "retry", "clear_ticket": True, "error": "provider_error"}

        await deliver_one(self.db, receipt_error)
        self.db.raw[OUTBOX].update_one({}, {"$set": {"next_at": now()}})
        await deliver_one(self.db, self.sender)
        self.assertEqual(self.sent, [{"ticket": "synthetic_ticket"}])
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "discarded")

    async def test_unicode_controls_whitespace_and_private_fields_are_not_exported(self):
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {
            "sender_name": "\u202e موظف\x00\n تجريبي \u2069", "message": "\u202e نفذ\tالتعديل\r\n الآن \u2069",
            "order_number": "TEST\u202e-501", "private_key": "PRIVATE_CREDENTIAL",
            "metadata": {"evidence": "PRIVATE_EVIDENCE", "token": "PRIVATE_TOKEN"},
        }})
        await self.deliver()
        payload = self.sent[0]["payload"]
        self.assertIn("موظف تجريبي", visible(payload["title"]))
        self.assertIn("نفذ التعديل الآن", visible(payload["body"]))
        self.assertNotIn("\u202e", payload["title"] + payload["body"])
        self.assertNotIn("\x00", payload["title"] + payload["body"])
        self.assertNotIn("PRIVATE_", json.dumps(payload))
        for text in (payload["title"], payload["body"]):
            self.assertEqual(text.count("\u2069"), text.count("\u2068") + text.count("\u2066"))

    async def test_malformed_legacy_text_values_are_not_stringified_into_preview(self):
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {
            "sender_name": {"secret": "PRIVATE_NAME"}, "message": {"secret": "PRIVATE_MESSAGE"},
            "order_number": ["PRIVATE_ORDER"], "action_type": {"secret": "PRIVATE_ACTION"},
        }})
        await self.deliver()
        payload = self.sent[0]["payload"]
        self.assertIn("تحديثات النظام", payload["title"])
        self.assertIn("تعليق جديد على المنتج", payload["body"])
        self.assertNotIn("PRIVATE_", json.dumps(payload))

    async def test_oversized_identifier_fails_before_provider_submission(self):
        device = self.db.raw[DEVICES].find_one()
        payload = {"title": "تنبيه", "body": "تحديث", "event_id": "synthetic" * 1024,
                   "instruction_id": "instruction", "priority": "normal"}
        client_class = httpx.AsyncClient

        def provider(request):
            self.fail("oversized payload must not be submitted")

        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(provider), **kwargs)

        with patch("operational_push.httpx.AsyncClient", side_effect=client):
            result = await send_to_provider(device, payload)
        self.assertEqual(result, {"status": "failed", "error": "payload_too_large"})

    async def test_provider_payload_stays_below_four_kib_and_keeps_sound_channel_and_priority(self):
        self.db.raw[NOTIFICATIONS].update_one({}, {"$set": {
            "sender_name": "تجريبي😀" * 2000, "message": "نص عربي😀é " * 2000,
            "order_number": "SYNTHETIC-" * 2000,
        }})
        await self.deliver()
        payload = self.sent[0]["payload"]
        payload["priority"] = "urgent"
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(200, json={"data": {"status": "ok", "id": "synthetic_ticket"}})

        client_class = httpx.AsyncClient
        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(provider), **kwargs)

        with patch("operational_push.httpx.AsyncClient", side_effect=client):
            result = await send_to_provider(self.sent[0]["device"], payload)
        self.assertEqual(result["status"], "receipt_pending")
        self.assertLess(len(requests[0].content), 4096)
        sent = json.loads(requests[0].content)
        self.assertEqual(sent["channelId"], "operational-instructions")
        self.assertEqual(sent["sound"], "default")
        self.assertEqual(sent["priority"], "high")
        self.assertEqual(sent["ttl"], 3600)
        self.assertNotIn("badge", sent)
        self.assertNotIn("unread_count", sent["data"])
        self.assertIn("…", sent["body"])
        sent["title"].encode("utf-8", errors="strict")
        sent["body"].encode("utf-8", errors="strict")


if __name__ == "__main__":
    unittest.main()
