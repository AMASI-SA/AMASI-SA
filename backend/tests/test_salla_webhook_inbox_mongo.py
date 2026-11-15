"""Real Mongo acceptance for durable Salla delivery and worker fencing.

Requires an explicit loopback replica set in MZ2_TEST_MONGO_URI. Every test
uses its own disposable database and real persistence/encryption. No production
fallback and no queue/claim mocks are allowed in this suite.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

import customer_identity
from salla_integration import webhook_inbox as inbox


AT = datetime(2026, 11, 15, tzinfo=timezone.utc)


def event(revision=1):
    return {"event": "order.updated", "merchant": "synthetic-store", "data": {
        "id": "synthetic-native-order", "revision": revision,
        "customer": {"name": "Private Synthetic Customer", "email": "private@example.invalid"},
        "items": [{"id": "line", "options": [{"name": "Size", "value": str(revision)}]}],
    }}


class SallaWebhookInboxMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not self.uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        parsed = urlsplit(self.uri)
        self.assertEqual(parsed.scheme, "mongodb")
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertIsNotNone(parsed.port)
        self.assertIsNone(parsed.username)
        self.assertIsNone(parsed.password)
        self.assertIn(parsed.path, ("", "/"))
        self.client = AsyncIOMotorClient(self.uri, tz_aware=True, serverSelectionTimeoutMS=5000)
        self.other_client = AsyncIOMotorClient(self.uri, tz_aware=True, serverSelectionTimeoutMS=5000)
        self.db = self.client["test_salla_webhook_inbox_" + uuid4().hex]
        self.other_db = self.other_client[self.db.name]
        self.addCleanup(self.client.close)
        self.addCleanup(self.other_client.close)

        async def drop_fixture():
            self.assertTrue(self.db.name.startswith("test_salla_webhook_inbox_"))
            await self.client.drop_database(self.db.name)
        self.addAsyncCleanup(drop_fixture)
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.private_key = Fernet.generate_key().decode()
        environment = patch.dict(os.environ, {"MEZAN_CUSTOMER_PII_ENC_KEY": self.private_key})
        environment.start()
        self.addCleanup(environment.stop)
        encryption = patch.object(customer_identity, "_fernet", None)
        encryption.start()
        self.addCleanup(encryption.stop)
        await self.db.salla_integrations.insert_one({"store_id": "synthetic-store",
            "user_id": "synthetic-owner", "updated_at": AT})
        await inbox.ensure_indexes(self.db)

    async def test_concurrent_duplicates_and_later_choices_are_durable_and_distinct(self):
        receipts = await asyncio.gather(*(
            inbox.enqueue_verified_event(self.db if i % 2 else self.other_db, event(), now=AT)
            for i in range(40)
        ))
        self.assertEqual(sum(not row["duplicate"] for row in receipts), 1)
        self.assertEqual(await self.db[inbox.COLLECTION].count_documents({}), 1)
        row = await self.other_db[inbox.COLLECTION].find_one({"_id": receipts[0]["job_id"]})
        self.assertEqual(row["delivery_count"], 40)
        self.assertEqual(customer_identity.decrypt_private_payload(row["payload_ciphertext"]), event())
        self.assertNotIn("private@example.invalid", repr(row))
        changed = await inbox.enqueue_verified_event(self.other_db, event(2), now=AT)
        self.assertNotEqual(changed["job_id"], row["_id"])
        calls = []

        async def processor(_, body):
            calls.append(body["data"]["revision"])
            return {"ok": True}

        self.assertEqual((await inbox.run_once(self.db, now=AT, processor=processor))["done"], 2)
        self.assertCountEqual(calls, [1, 2])
        for body in (event(), event(2)):
            duplicate = await inbox.enqueue_verified_event(self.other_db, body, now=AT + timedelta(days=90))
            self.assertTrue(duplicate["duplicate"])
        self.assertEqual((await inbox.run_once(self.other_db, now=AT + timedelta(days=90), processor=processor))["claimed"], 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(await self.db[inbox.COLLECTION].count_documents({"payload_ciphertext": {"$exists": True}}), 0)
        indexes = await self.db[inbox.COLLECTION].index_information()
        self.assertTrue(all("expireAfterSeconds" not in value for value in indexes.values()))

    async def test_two_clients_serialize_business_work_without_duplicate_claims(self):
        for revision in (1, 2):
            await inbox.enqueue_verified_event(self.db, event(revision))
        entered, finish = asyncio.Event(), asyncio.Event()
        active = 0
        maximum_active = 0
        calls = []

        async def processor(_, body):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            calls.append(body["data"]["revision"])
            entered.set()
            try:
                await finish.wait()
                return {"ok": True}
            finally:
                active -= 1

        first = asyncio.create_task(inbox.run_once(self.db, limit=1, processor=processor))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            contenders = await asyncio.gather(*(
                inbox.run_once(self.other_db, processor=processor) for _ in range(8)))
            self.assertTrue(all(result["lease_busy"] for result in contenders))
            self.assertEqual(len(calls), 1)
        finally:
            finish.set()
            await first
        self.assertEqual((await inbox.run_once(self.other_db, processor=processor))["done"], 1)
        self.assertEqual(maximum_active, 1)
        self.assertCountEqual(calls, [1, 2])
        self.assertEqual(await self.db[inbox.COLLECTION].count_documents({"state": "done"}), 2)

    async def test_actual_process_death_retains_claim_and_restart_recovers_after_expiry(self):
        receipt = await inbox.enqueue_verified_event(self.db, event(), now=AT)
        # Exit the process inside the processor, bypassing cancellation/finally.
        # The queue row and lease must remain durable for a different process.
        program = """
import asyncio, os, sys
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from salla_integration.webhook_inbox import run_once
async def crash(db, body):
    os._exit(23)
async def main():
    client = AsyncIOMotorClient(sys.argv[1], tz_aware=True)
    await run_once(client[sys.argv[2]], now=datetime(2026, 11, 15, tzinfo=timezone.utc), processor=crash)
asyncio.run(main())
"""
        process = await asyncio.create_subprocess_exec(sys.executable, "-B", "-c", program,
            self.uri, self.db.name, env={**os.environ, "MEZAN_CUSTOMER_PII_ENC_KEY": self.private_key},
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 15)
            self.assertEqual(process.returncode, 23, stderr.decode())
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        row = await self.other_db[inbox.COLLECTION].find_one({"_id": receipt["job_id"]})
        self.assertEqual(row["state"], "processing")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(customer_identity.decrypt_private_payload(row["payload_ciphertext"]), event())
        calls = []

        async def processor(_, body):
            calls.append(body)
            return {"ok": True}

        before_expiry = await inbox.run_once(self.other_db, now=AT, processor=processor)
        self.assertTrue(before_expiry["lease_busy"])
        self.assertFalse(calls)
        recovered = await inbox.run_once(self.other_db,
            now=row["lease_until"] + timedelta(milliseconds=1), processor=processor)
        self.assertEqual(recovered["done"], 1)
        self.assertEqual(len(calls), 1)
        row = await self.db[inbox.COLLECTION].find_one({"_id": row["_id"]})
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(row["state"], "done")

    async def test_lost_lease_fences_late_completion_and_retains_recoverable_payload(self):
        receipt = await inbox.enqueue_verified_event(self.db, event(), now=AT)

        async def stale_processor(_, body):
            await self.other_db[inbox.LEASES_COLLECTION].update_one({"_id": inbox.WORKER_LEASE_ID},
                {"$set": {"lease_token": "synthetic-replacement"}})
            return {"ok": True}

        result = await inbox.run_once(self.db, now=AT, processor=stale_processor)
        self.assertTrue(result["lease_lost"])
        self.assertEqual(result["done"], 0)
        row = await self.other_db[inbox.COLLECTION].find_one({"_id": receipt["job_id"]})
        self.assertEqual(row["state"], "processing")
        self.assertIn("payload_ciphertext", row)
        lease = await self.other_db[inbox.LEASES_COLLECTION].find_one({"_id": inbox.WORKER_LEASE_ID})
        self.assertEqual(lease["lease_token"], "synthetic-replacement")
        self.assertNotIn("released_at", lease)

        async def recovered_processor(_, body):
            return {"ok": True}

        result = await inbox.run_once(self.other_db,
            now=row["lease_until"] + timedelta(milliseconds=1), processor=recovered_processor)
        self.assertEqual(result["done"], 1)

    async def test_transient_failure_and_owner_change_preserve_retry_or_quarantine(self):
        receipt = await inbox.enqueue_verified_event(self.db, event(), now=AT)

        async def failed_processor(_, body):
            raise RuntimeError("synthetic-private-provider-response")

        result = await inbox.run_once(self.db, now=AT, processor=failed_processor)
        self.assertEqual(result["retry"], 1)
        row = await self.db[inbox.COLLECTION].find_one({"_id": receipt["job_id"]})
        self.assertEqual(row["last_error_code"], "processing_failed")
        self.assertNotIn("synthetic-private-provider-response", repr(row))
        duplicate = await inbox.enqueue_verified_event(self.other_db, event(), now=AT + timedelta(seconds=1))
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual((await inbox.run_once(self.other_db, now=AT + timedelta(seconds=1), processor=failed_processor))["claimed"], 0)
        await self.db.salla_integrations.update_one({"store_id": "synthetic-store"},
            {"$set": {"user_id": "different-owner", "updated_at": AT + timedelta(seconds=1)}})
        quarantined = await inbox.run_once(self.other_db, now=row["next_attempt_at"], processor=failed_processor)
        self.assertEqual(quarantined["quarantined"], 1)
        row = await self.db[inbox.COLLECTION].find_one({"_id": receipt["job_id"]})
        self.assertEqual(row["user_id"], "synthetic-owner")
        self.assertEqual(row["last_error_code"], "owner_binding_changed")
        self.assertEqual(customer_identity.decrypt_private_payload(row["payload_ciphertext"]), event())

    async def test_mongo_rejected_persistence_cannot_acknowledge_an_event(self):
        # Real server rejection at the acceptance write boundary, with no
        # simulated collection or handler result substituted for persistence.
        await self.db.command({"collMod": inbox.COLLECTION,
            "validator": {"event_name": "synthetic.reject.all"}, "validationAction": "error"})
        with self.assertRaises(OperationFailure):
            await inbox.enqueue_verified_event(self.db, event())
        self.assertEqual(await self.db[inbox.COLLECTION].count_documents({}), 0)
