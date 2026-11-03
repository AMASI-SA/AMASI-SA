"""Outbox/account tests with real Mongo query semantics and no provider writes."""
import os
import hashlib
import base64
import unittest
from datetime import timedelta
from unittest.mock import patch

import mongomock
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from operational_push import (DEVICES, OUTBOX, now, enqueue_operational_push,
    deliver_one, validate_subscription, make_operational_push_router, PushSession)


class Cursor:
    def __init__(self, cursor): self.cursor = cursor
    def sort(self, *args): self.cursor = self.cursor.sort(*args); return self
    async def to_list(self, count): return list(self.cursor.limit(count))


class Collection:
    def __init__(self, collection): self.collection = collection
    def find(self, *args, **kwargs): return Cursor(self.collection.find(*args, **kwargs))
    def __getattr__(self, key):
        async def call(*args, **kwargs): return getattr(self.collection, key)(*args, **kwargs)
        return call


class DB:
    def __init__(self): self.raw = mongomock.MongoClient(tz_aware=True).db
    def __getitem__(self, key): return Collection(self.raw[key])


class PushTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DB()
        self.env = patch.dict(os.environ, {"OPERATIONAL_PUSH_ENABLED": "1"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.db.raw.users.insert_one({"id": "staff"})
        self.db.raw.order_review_auth_sessions_v2.insert_one({"_id": "session", "actor_id": "staff", "user_id": "owner", "state": "active", "expires_at": now() + timedelta(days=1)})
        self.db.raw[DEVICES].insert_one({"_id": "device", "kind": "expo", "actor_id": "staff", "merchant_id": "owner", "session_id": "session", "generation": "g1", "installation_id": "android_installation_12345678", "active": True, "token": "ExpoPushToken[example]"})
        self.sent = []

    async def sender(self, device, payload, **kwargs):
        self.sent.append((device, payload))
        return {"status": "provider_accepted"}

    async def test_idempotent_enqueue_and_generic_payload(self):
        for _ in range(2):
            await enqueue_operational_push(self.db, recipient_id="staff", event_id="event", order_number="customer-order", instruction_id="instruction")
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 1)
        await deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("customer-order", str(self.sent[0][1]))
        self.assertEqual(self.sent[0][1]["instruction_id"], "instruction")
        self.assertFalse(await deliver_one(self.db, self.sender))

    async def test_revoked_session_never_sends(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.db.raw.order_review_auth_sessions_v2.update_one({"_id": "session"}, {"$set": {"state": "revoked"}})
        await deliver_one(self.db, self.sender)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "discarded")

    async def test_reassigned_device_does_not_receive_previous_account_job(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {"actor_id": "other", "generation": "g2"}})
        await deliver_one(self.db, self.sender)
        self.assertEqual(self.sent, [])

    async def test_retry_persisted_and_not_reported_delivered(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        async def fail(*args, **kwargs): raise TimeoutError("sensitive token")
        await deliver_one(self.db, fail)
        row = self.db.raw[OUTBOX].find_one()
        self.assertEqual(row["status"], "retry")
        self.assertEqual(row["error"], "transport_error")
        self.assertGreater(row["next_at"], now())

    async def test_invalid_device_is_deactivated(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        async def invalid(*args, **kwargs): return {"status": "invalid_device"}
        await deliver_one(self.db, invalid)
        self.assertFalse(self.db.raw[DEVICES].find_one()["active"])

    async def test_registration_requires_bound_session_and_unsubscribe_own_only(self):
        actor = {"id": "other"}
        app = FastAPI()
        app.include_router(make_operational_push_router(self.db, lambda: actor))
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {"installation_id": "installation_123456"}})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/operational-notifications/devices/register", json={"installation_id": "installation_123456", "kind": "expo", "token": "ExpoPushToken[example]"})
            self.assertEqual(response.status_code, 409)
            await client.post("/operational-notifications/devices/unregister", json={"installation_id": "installation_123456"})
        self.assertTrue(self.db.raw[DEVICES].find_one()["active"])

    def test_subscription_rejects_local_and_credentialed_urls(self):
        for endpoint in ("http://127.0.0.1/push", "https://localhost/push", "https://fcm.googleapis.com.attacker.test/push", "https://user:pass@fcm.googleapis.com/push", "https://fcm.googleapis.com:8443/push"):
            with self.assertRaises(ValueError): validate_subscription({"endpoint": endpoint})

    def test_subscription_keys_accept_padding_and_reject_oversized_input_before_decode(self):
        keys = {name: base64.urlsafe_b64encode(bytes(length)).decode()
                for name, length in (("p256dh", 65), ("auth", 16))}
        value = {"endpoint": "https://fcm.googleapis.com/push", "keys": keys}
        self.assertEqual(validate_subscription(value)["keys"], keys)
        value["keys"] = {name: raw.rstrip("=") for name, raw in keys.items()}
        self.assertEqual(validate_subscription(value)["keys"], value["keys"])
        for name in keys:
            with self.subTest(name=name):
                value["keys"] = {**keys, name: "-" * 100000 + "!"}
                with patch("operational_push.base64.urlsafe_b64decode", wraps=base64.urlsafe_b64decode) as decode:
                    with self.assertRaisesRegex(ValueError, "invalid_push_key"):
                        validate_subscription(value)
                    self.assertTrue(all(len(call.args[0]) <= 88 for call in decode.call_args_list))

    async def test_expo_ticket_is_not_delivery_proof(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        async def ticket(*args, **kwargs): return {"status": "receipt_pending", "ticket": "receipt1"}
        await deliver_one(self.db, ticket)
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "receipt_pending")

    async def test_receipt_error_resends_but_transport_error_keeps_ticket(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.db.raw[OUTBOX].update_one({}, {"$set": {"ticket": "old"}})
        async def retry(*args, **kwargs): return {"status": "retry", "clear_ticket": True}
        await deliver_one(self.db, retry)
        self.assertNotIn("ticket", self.db.raw[OUTBOX].find_one())
        self.db.raw[OUTBOX].update_one({}, {"$set": {"ticket": "keep", "next_at": now()}})
        async def network(*args, **kwargs): raise TimeoutError()
        await deliver_one(self.db, network)
        self.assertEqual(self.db.raw[OUTBOX].find_one()["ticket"], "keep")

    def test_web_provider_cannot_follow_redirect(self):
        with patch("requests.Session.request") as transport:
            PushSession().post("https://fcm.googleapis.com/test", allow_redirects=True)
            self.assertIs(transport.call_args.kwargs["allow_redirects"], False)

    def relogin_same_phone(self, generation="g2"):
        session_id = "session_" + generation
        self.db.raw.order_review_auth_sessions_v2.insert_one({
            "_id": session_id, "actor_id": "staff", "user_id": "owner",
            "state": "active", "expires_at": now() + timedelta(days=1),
        })
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {
            "generation": generation, "session_id": session_id,
            "installation_id": "android_installation_12345678",
        }})

    async def test_same_unread_event_not_resent_on_each_relogin(self):
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {
            "installation_id": "android_installation_12345678",
        }})
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="instruction_event_1")
        await deliver_one(self.db, self.sender)
        for generation in ("g2", "g3", "g4"):
            self.relogin_same_phone(generation)
            await enqueue_operational_push(self.db, recipient_id="staff", event_id="instruction_event_1")
            await enqueue_operational_push(self.db, recipient_id="staff", event_id="instruction_event_1")
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(await deliver_one(self.db, self.sender))
        # A genuinely new update still reaches the same phone exactly once.
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="instruction_event_2")
        await deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 2)

    async def test_receipt_pending_ticket_does_not_trigger_replay_on_relogin(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        async def ticket(*args, **kwargs):
            return {"status": "receipt_pending", "ticket": "expo-provider-accepted"}
        await deliver_one(self.db, ticket)
        self.relogin_same_phone()
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 1)
        self.assertTrue(self.db.raw[OUTBOX].find_one().get("ticket"))

    async def test_unsubmitted_pending_event_rebinds_to_new_session(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.relogin_same_phone()
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        jobs = list(self.db.raw[OUTBOX].find())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["generation"], "g2")
        await deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 1)

    async def test_transient_unsent_retry_can_rebind_without_duplicates(self):
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        async def fail(*args, **kwargs):
            raise TimeoutError("network")
        await deliver_one(self.db, fail)
        self.assertEqual(self.db.raw[OUTBOX].find_one()["status"], "retry")
        self.relogin_same_phone()
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        await deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 1)

    async def test_rotated_expo_token_with_same_installation_does_not_replay(self):
        installation = "android_installation_12345678"
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {
            "installation_id": installation,
        }})
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        await deliver_one(self.db, self.sender)
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {"active": False}})
        self.db.raw[DEVICES].insert_one({
            "_id": "rotated_token", "kind": "expo",
            "actor_id": "staff", "merchant_id": "owner",
            "session_id": "session", "generation": "g2",
            "installation_id": installation, "active": True,
            "token": "ExpoPushToken[rotated]",
        })
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 1)
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="new_event")
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 2)
        await deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 2)

    def insert_legacy_job(self, event, status="pending"):
        moment = now()
        self.db.raw[OUTBOX].insert_one({
            "_id": hashlib.sha256(f"{event}:device:g1".encode()).hexdigest(),
            "device_id": "device", "generation": "g1", "actor_id": "staff",
            "event_id": event, "instruction_id": "instruction", "priority": "normal",
            "status": status, "attempts": 0, "created_at": moment,
            "next_at": moment, "expires_at": moment + timedelta(days=2),
        })

    async def test_migration_refresh_does_not_duplicate_queued_or_claimed_legacy_delivery(self):
        for status in ("pending", "retry"):
            self.insert_legacy_job(status, status)
            await enqueue_operational_push(self.db, recipient_id="staff", event_id=status)

            async def refresh_during_send(device, payload, **kwargs):
                # A second refresh happens after the worker claims the old row
                # but before the provider ticket exists.
                await enqueue_operational_push(self.db, recipient_id="staff", event_id=payload["event_id"])
                return await self.sender(device, payload, **kwargs)

            self.assertTrue(await deliver_one(self.db, refresh_during_send))
            self.assertFalse(await deliver_one(self.db, self.sender))
            self.assertEqual(self.db.raw[OUTBOX].count_documents({"event_id": status}), 1)
        self.assertEqual([payload["event_id"] for _, payload in self.sent], ["pending", "retry"])

    async def test_unsent_legacy_job_from_revoked_generation_does_not_block_current_session(self):
        self.insert_legacy_job("rotated")
        self.relogin_same_phone()
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="rotated")
        self.assertTrue(await deliver_one(self.db, self.sender))
        self.assertTrue(await deliver_one(self.db, self.sender))
        self.assertFalse(await deliver_one(self.db, self.sender))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0]["generation"], "g2")

    async def test_legacy_generation_key_provider_acceptance_is_respected(self):
        self.db.raw[OUTBOX].insert_one({
            "_id": "old_generation_hash", "device_id": "device",
            "generation": "g1", "actor_id": "staff", "event_id": "event",
            "status": "provider_accepted",
            "expires_at": now() + timedelta(days=2),
        })
        self.relogin_same_phone()
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 1)

    async def test_other_physical_installation_receives_same_event(self):
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {
            "installation_id": "android_installation_one",
        }})
        self.db.raw[DEVICES].insert_one({
            "_id": "second_phone", "kind": "expo",
            "actor_id": "staff", "merchant_id": "owner",
            "session_id": "session", "generation": "g1",
            "installation_id": "android_installation_two",
            "active": True, "token": "ExpoPushToken[second]",
        })
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 2)
        await deliver_one(self.db, self.sender)
        await deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 2)

    async def test_old_unread_inbox_event_does_not_become_new_phone_alert(self):
        await enqueue_operational_push(
            self.db, recipient_id="staff", event_id="old_event",
            created_at=now() - timedelta(days=3))
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 0)

    async def test_new_user_on_same_phone_can_receive_separate_event(self):
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {
            "installation_id": "android_installation_shared",
        }})
        await enqueue_operational_push(self.db, recipient_id="staff", event_id="event")
        await deliver_one(self.db, self.sender)
        self.db.raw.users.insert_one({"id": "other"})
        self.db.raw.order_review_auth_sessions_v2.insert_one({
            "_id": "other_session", "actor_id": "other", "user_id": "owner",
            "state": "active", "expires_at": now() + timedelta(days=1),
        })
        self.db.raw[DEVICES].update_one({"_id": "device"}, {"$set": {
            "actor_id": "other", "session_id": "other_session", "generation": "other_g1",
        }})
        await enqueue_operational_push(self.db, recipient_id="other", event_id="event")
        await deliver_one(self.db, self.sender)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.db.raw[OUTBOX].count_documents({}), 2)


if __name__ == "__main__": unittest.main()
