"""Outbox/account tests with real Mongo query semantics and no provider writes."""
import os
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
        self.db.raw[DEVICES].insert_one({"_id": "device", "kind": "expo", "actor_id": "staff", "merchant_id": "owner", "session_id": "session", "generation": "g1", "active": True, "token": "ExpoPushToken[example]"})
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


if __name__ == "__main__": unittest.main()
