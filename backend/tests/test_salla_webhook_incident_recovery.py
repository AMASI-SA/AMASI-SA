"""Incident retry grants: read-only planning and real replica-set transactions.

The Mongo class requires the existing explicit loopback fixture. Local absence
is reported as a skip; the required CI sets its URI and rejects every skip.
No database/credential fallback or production/provider calls are permitted.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
from uuid import uuid4

from bson import json_util
from cryptography.fernet import Fernet
from fastapi import FastAPI
import httpx
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

import customer_identity
from salla_integration import webhook_incident_recovery as recovery
from salla_integration import webhook_inbox as inbox


NOW = datetime(2026, 11, 15, 17, tzinfo=timezone.utc)
CREATED = datetime(2026, 11, 15, 14, 10, 37, tzinfo=timezone.utc)
END = "2026-11-15T16:00:00Z"
USER = {"id": "synthetic-incident-owner", "role": "owner"}
RUNTIME = {"release_id": "rg5-" + "a" * 64, "source_git_sha": "b" * 40}
REQUEST = {"incident_end": END, "batch_limit": 50}


def document(number=1, **changes):
    digest = hashlib.sha256(f"synthetic-event-{number}".encode()).hexdigest()
    row = {"_id": digest, "event_hash": digest, "event_name": "order.updated",
           "merchant_id": recovery.MERCHANT_ID, "user_id": USER["id"],
           "payload_ciphertext": b"opaque-encrypted-synthetic-event",
           "verified_before_enqueue": True, "state": "quarantined", "attempts": 8,
           "last_error_code": "attempt_limit", "created_at": CREATED,
           "first_received_at": CREATED, "last_received_at": CREATED,
           "delivery_count": 1, "started_at": CREATED + timedelta(minutes=20),
           "last_attempt_at": CREATED + timedelta(minutes=21),
           "quarantined_at": CREATED + timedelta(minutes=21),
           "next_attempt_at": CREATED + timedelta(minutes=20),
           "lease_until": CREATED + timedelta(minutes=21)}
    row.update(changes)
    return row


def service(db, **kwargs):
    return recovery.IncidentRecovery(db, clock=lambda: NOW,
                                     runtime_reader=lambda: RUNTIME.copy(), **kwargs)


def apply_request(preview):
    return {key: copy.deepcopy(preview[key]) for key in ("plan", "plan_sha256")}


async def seed_binding(db):
    await db.salla_integrations.insert_one({"store_id": recovery.MERCHANT_ID,
        "user_id": USER["id"], "updated_at": CREATED})


@pytest.mark.parametrize("value,expected", [
    ("", False), ("false", False), ("0", False), ("TRUE", False),
    ("True", False), (" true ", False), ("yes", False), ("true", True), ("1", True),
])
def test_flag_requires_exact_explicit_values(value, expected):
    with patch.dict(os.environ, {recovery.FLAG: value}):
        assert recovery.enabled() is expected


@pytest.mark.parametrize("body", [
    {}, {"incident_end": END}, {"incident_end": END, "batch_limit": True},
    {"incident_end": END, "batch_limit": 0}, {"incident_end": END, "batch_limit": 51},
    {"incident_end": END, "batch_limit": "1"},
    {"incident_end": "2026-11-15T16:00:00", "batch_limit": 1},
    {"incident_end": "2026-11-15T17:00:01Z", "batch_limit": 1},
    {"incident_end": recovery.INCIDENT_START, "batch_limit": 1},
    {"incident_end": "2026-11-15T13:00:00Z", "batch_limit": 1},
    {"incident_end": "2026-11-15T17:00:00+01:00", "batch_limit": 1},
    {"incident_end": END, "batch_limit": 1, "merchant_id": "another-store"},
])
@pytest.mark.asyncio
async def test_invalid_dry_run_never_touches_database(body):
    db = SimpleNamespace(salla_integrations=SimpleNamespace(find_one=AsyncMock()))
    with patch.dict(os.environ, {recovery.FLAG: "true"}):
        with pytest.raises(recovery.RecoveryError, match="invalid_request"):
            await service(db).dry_run(USER, body)
    db.salla_integrations.find_one.assert_not_awaited()


def test_duration_cannot_expand_beyond_the_fixed_incident_day():
    with pytest.raises(recovery.RecoveryError, match="invalid_request"):
        recovery._window("2026-11-16T14:07:22Z", 1, NOW + timedelta(days=2))


@pytest.mark.parametrize("change", [
    {"verified_identity_available": False}, {"backend_runtime_source_verified": False},
    {"release_control_source_bound": False}, {"critical_file_hashes_match": False},
    {"frontend_build_verified": False}, {"protocol_version": 4}, {"protocol_version": 5.0},
    {"release_id": "unverified"}, {"source_git_sha": "short"},
])
def test_runtime_requires_complete_validated_v5_boot_identity(change):
    identity = {**RUNTIME, "protocol_version": 5,
        "verified_identity_available": True, "backend_runtime_source_verified": True,
        "release_control_source_bound": True, "critical_file_hashes_match": True,
        "frontend_build_verified": True}
    module = SimpleNamespace(BOOT_RELEASE_IDENTITY=identity)
    with patch.dict(sys.modules, {"release_identity": module}):
        assert recovery._runtime_identity() == RUNTIME
        module.BOOT_RELEASE_IDENTITY = {**identity, **change}
        with pytest.raises(recovery.RecoveryError, match="runtime_unverified"):
            recovery._runtime_identity()


class PlanningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncMongoMockClient(tz_aware=True)
        self.db = self.client.test_salla_incident_unit
        await seed_binding(self.db)
        await self.db[inbox.COLLECTION].insert_many([document(2), document(1)])
        self.environment = patch.dict(os.environ, {recovery.FLAG: "true"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.svc = service(self.db)

    async def test_plan_is_deterministic_metadata_only_and_does_not_write(self):
        before = await self.db[inbox.COLLECTION].find({}).to_list(None)
        preview = await self.svc.dry_run(USER, {"incident_end": END, "batch_limit": 1})
        again = await self.svc.dry_run(USER, {"incident_end": END, "batch_limit": 1})
        self.assertEqual(preview, again)
        self.assertEqual(preview["summary"], {
            "incident_start": recovery.INCIDENT_START, "incident_end": "2026-11-15T16:00:00.000Z",
            "selected_count": 1, "eligible_total": 2, "remaining": 1,
            "state": "quarantined", "attempts": 8,
        })
        self.assertEqual(preview["plan"]["rows"][0]["job_id"], min(row["_id"] for row in before))
        self.assertEqual(before, await self.db[inbox.COLLECTION].find({}).to_list(None))
        self.assertEqual(await self.db[inbox.LEASES_COLLECTION].count_documents({}), 0)
        rendered = json.dumps(preview)
        self.assertNotIn("payload_ciphertext", rendered)
        self.assertNotIn("opaque-encrypted", rendered)
        self.assertNotIn("customer", rendered)
        self.assertEqual(recovery._plan_hash(preview["plan"]), preview["plan_sha256"])

    async def test_disabled_read_only_config_and_invalid_runtime(self):
        with patch.dict(os.environ, {recovery.FLAG: "false"}):
            self.assertFalse((await self.svc.config(USER))["enabled"])
            with self.assertRaisesRegex(recovery.RecoveryError, "recovery_disabled"):
                await self.svc.dry_run(USER, REQUEST)
        def invalid_runtime():
            raise recovery.RecoveryError("runtime_unverified")
        self.svc.runtime_reader = invalid_runtime
        config = await self.svc.config(USER)
        self.assertIsNone(config["runtime"])
        self.assertFalse(config["runtime_validated"])
        with self.assertRaisesRegex(recovery.RecoveryError, "runtime_unverified"):
            await self.svc.dry_run(USER, REQUEST)

    async def test_queue_metadata_is_owner_scoped_and_reports_done_and_grant_progress(self):
        await self.db[inbox.COLLECTION].insert_many([
            document(40, state="done", completed_at=NOW,
                     **{recovery.MARKER: {"incident_id": recovery.INCIDENT_ID}}),
            document(41, state="retry", **{recovery.MARKER: {"incident_id": recovery.INCIDENT_ID}}),
            document(42, state="done", user_id="different-owner", completed_at=NOW + timedelta(days=1)),
            document(43, state="done", merchant_id="different-merchant", completed_at=NOW + timedelta(days=1)),
        ])
        before = await self.db[inbox.COLLECTION].find({}).to_list(None)
        metadata = (await self.svc.config(USER))["queue"]
        self.assertEqual(metadata["counts"], {"pending": 0, "retry": 1, "processing": 0,
                                             "quarantined": 2, "done": 1})
        self.assertEqual(metadata["grant_counts"], {"pending": 0, "retry": 1, "processing": 0,
                                                   "quarantined": 0, "done": 1})
        self.assertEqual(metadata["latest_completed_at"], "2026-11-15T17:00:00.000Z")
        self.assertEqual(metadata["total_granted"], 2)
        self.assertEqual(before, await self.db[inbox.COLLECTION].find({}).to_list(None))
        self.assertNotIn("payload_ciphertext", json.dumps(metadata))

    async def test_multiple_merchant_bindings_refuse_maintenance_without_changing_them(self):
        await self.db.salla_integrations.insert_one({"store_id": int(recovery.MERCHANT_ID),
            "user_id": "another-owner", "updated_at": NOW})
        with self.assertRaisesRegex(recovery.RecoveryError, "merchant_binding_ambiguous"):
            await self.svc.dry_run(USER, REQUEST)
        self.assertEqual(await self.db.salla_integrations.count_documents({}), 2)

    async def test_large_ciphertext_batch_is_bounded_without_decryption_or_writes(self):
        before = await self.db[inbox.COLLECTION].find({}).to_list(None)
        one_payload_bytes = len(before[0]["payload_ciphertext"])
        with patch.object(recovery, "MAX_BATCH_CIPHERTEXT_BYTES", one_payload_bytes):
            with self.assertRaisesRegex(recovery.RecoveryError, "batch_payload_limit"):
                await self.svc.dry_run(USER, REQUEST)
            single = await self.svc.dry_run(USER, {"incident_end": END, "batch_limit": 1})
            self.assertEqual(single["summary"]["selected_count"], 1)
        self.assertEqual(before, await self.db[inbox.COLLECTION].find({}).to_list(None))

    async def test_only_actual_bound_owner_can_plan(self):
        for user in ({"id": USER["id"], "role": "admin"},
                     {"id": USER["id"], "role": "employee"},
                     {"id": USER["id"], "is_owner": "true"}, {}, None):
            with self.subTest(user=user), self.assertRaisesRegex(recovery.RecoveryError, "owner_only"):
                await self.svc.dry_run(user, REQUEST)
        with self.assertRaisesRegex(recovery.RecoveryError, "merchant_owner_mismatch"):
            await self.svc.dry_run({"id": "other-owner", "role": "owner"}, REQUEST)
        self.assertEqual((await self.svc.dry_run(
            {"id": USER["id"], "is_owner": True}, REQUEST))["summary"]["selected_count"], 2)

    async def test_plan_hash_runtime_owner_types_and_scope_refuse_before_lease(self):
        preview = await self.svc.dry_run(USER, REQUEST)
        mutations = [
            (lambda p: p.update(owner_id="another-owner"), "invalid_plan"),
            (lambda p: p.update(merchant_id="another-merchant"), "invalid_plan"),
            (lambda p: p.update(incident_start="2026-11-14T00:00:00Z"), "invalid_plan"),
            (lambda p: p.update(version=True), "invalid_plan"),
            (lambda p: p.update(eligible_total=True), "invalid_plan"),
            (lambda p: p.update(batch_limit=True), "invalid_request"),
            (lambda p: p.update(rows=[]), "invalid_plan"),
            (lambda p: p.update(rows=[p["rows"][0]] * 2), "invalid_plan"),
            (lambda p: p["rows"][0].update(job_id={"$ne": ""}), "invalid_plan"),
            (lambda p: p["runtime"].update(source_git_sha="c" * 40), "runtime_changed"),
        ]
        with patch.object(inbox, "_acquire_worker", new_callable=AsyncMock) as acquire:
            wrong_hash = apply_request(preview)
            wrong_hash["plan_sha256"] = "f" * 64
            with self.assertRaisesRegex(recovery.RecoveryError, "plan_hash_mismatch"):
                await self.svc.apply(USER, wrong_hash)
            for change, code in mutations:
                body = apply_request(preview)
                change(body["plan"])
                body["plan_sha256"] = recovery._plan_hash(body["plan"])
                with self.subTest(code=code), self.assertRaisesRegex(recovery.RecoveryError, code):
                    await self.svc.apply(USER, body)
            acquire.assert_not_awaited()

    async def test_busy_worker_refuses_without_touching_any_queue_row(self):
        preview = await self.svc.dry_run(USER, REQUEST)
        before = await self.db[inbox.COLLECTION].find({}).to_list(None)
        with patch.object(inbox, "_acquire_worker", new_callable=AsyncMock, return_value=None):
            with self.assertRaisesRegex(recovery.RecoveryError, "worker_lease_busy"):
                await self.svc.apply(USER, apply_request(preview))
        self.assertEqual(before, await self.db[inbox.COLLECTION].find({}).to_list(None))

    async def test_whole_row_hash_preserves_type_presence_and_payload_changes(self):
        original = document()
        base = recovery._row_hash(original)
        self.assertEqual(base, recovery._row_hash(dict(reversed(list(original.items())))))
        for changed in (document(attempts=8.0), document(delivery_count=2),
                        document(last_received_at=NOW), document(payload_ciphertext=b"changed"),
                        document(claim_token=None), document(unexpected_metadata=True)):
            self.assertNotEqual(base, recovery._row_hash(changed))

    async def test_lost_fence_refuses_without_business_or_queue_writes(self):
        writes = AsyncMock(return_value=SimpleNamespace(matched_count=0))
        db = {inbox.LEASES_COLLECTION: SimpleNamespace(update_one=writes)}
        with self.assertRaisesRegex(recovery.RecoveryError, "worker_lease_lost"):
            await service(db)._fence({"lease_token": "old", "epoch": 4}, object())
        self.assertEqual(writes.await_count, 1)

    async def test_http_contract_is_authenticated_no_store_and_sanitized(self):
        actor = USER.copy()
        async def current_user():
            return actor
        app = FastAPI()
        app.include_router(recovery.make_incident_recovery_router(self.db, current_user),
                           prefix="/api/salla")
        with patch.object(recovery, "_runtime_identity", return_value=RUNTIME.copy()), \
                patch.object(recovery, "_now", return_value=NOW):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                config = await client.get("/api/salla/webhook-incident-recovery/config")
                self.assertEqual(config.status_code, 200)
                self.assertEqual(config.headers["cache-control"], "no-store")
                self.assertEqual(config.json()["runtime"], RUNTIME)
                planned = await client.post("/api/salla/webhook-incident-recovery/dry-run", json=REQUEST)
                self.assertEqual(planned.status_code, 200)
                actor["role"] = "admin"
                rejected = await client.post("/api/salla/webhook-incident-recovery/apply",
                                             json=apply_request(planned.json()))
                self.assertEqual(rejected.status_code, 403)
                self.assertEqual(rejected.json(), {"detail": {"code": "owner_only"}})
                self.assertEqual(rejected.headers["cache-control"], "no-store")


class IncidentRecoveryMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                self.fail("Required CI loopback replica-set URI is missing")
            self.skipTest("Explicit loopback MZ2_TEST_MONGO_URI required; no production fallback")
        parsed = urlsplit(uri)
        self.assertEqual(parsed.scheme, "mongodb")
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertIsNotNone(parsed.port)
        self.assertIsNone(parsed.username)
        self.assertIsNone(parsed.password)
        self.assertIn(parsed.path, ("", "/"))
        self.client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
        self.other_client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
        self.db = self.client["test_salla_incident_recovery_" + uuid4().hex]
        self.other_db = self.other_client[self.db.name]
        self.addCleanup(self.client.close)
        self.addCleanup(self.other_client.close)
        async def cleanup():
            self.assertTrue(self.db.name.startswith("test_salla_incident_recovery_"))
            await self.client.drop_database(self.db.name)
        self.addAsyncCleanup(cleanup)
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.environment = patch.dict(os.environ, {recovery.FLAG: "true",
            "MEZAN_CUSTOMER_PII_ENC_KEY": Fernet.generate_key().decode()})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        encryption = patch.object(customer_identity, "_fernet", None)
        encryption.start()
        self.addCleanup(encryption.stop)
        await seed_binding(self.db)
        await inbox.ensure_indexes(self.db)
        self.svc = service(self.db)

    async def seeded_plan(self, count=2):
        await self.db[inbox.COLLECTION].insert_many([document(i) for i in range(count)])
        return await self.svc.dry_run(USER, REQUEST)

    async def assert_no_grants(self):
        self.assertEqual(await self.db[inbox.COLLECTION].count_documents({
            recovery.MARKER: {"$exists": True}}), 0)
        self.assertEqual(await self.db[inbox.COLLECTION].count_documents({"state": "retry"}), 0)

    async def test_real_query_excludes_every_nonincident_or_unsafe_state_and_never_writes(self):
        await self.db[inbox.COLLECTION].insert_one(document())
        variants = [
            {"merchant_id": "another-store"}, {"user_id": "another-owner"},
            {"state": "done"}, {"state": "processing"}, {"state": "retry"},
            {"attempts": 9}, {"attempts": 7}, {"attempts": 8.0}, {"attempts": True},
            {"verified_before_enqueue": False}, {"verified_before_enqueue": 1},
            {"last_error_code": "payload_unreadable"}, {"payload_ciphertext": b""},
            {"payload_ciphertext": "plaintext"}, {"payload_ciphertext": None},
            {"claim_token": None}, {"worker_token": "old"}, {"completed_at": None},
            {recovery.MARKER: None}, {"lease_until": NOW + timedelta(seconds=1)},
            {"created_at": recovery._parse_time(recovery.INCIDENT_START) - timedelta(milliseconds=1)},
            {"created_at": recovery._parse_time(END)},
        ]
        for index, changes in enumerate(variants, 10):
            await self.db[inbox.COLLECTION].insert_one(document(index, **changes))
        missing_payload = document(99)
        del missing_payload["payload_ciphertext"]
        await self.db[inbox.COLLECTION].insert_one(missing_payload)
        before = json_util.dumps(await self.db[inbox.COLLECTION].find({}).sort("_id").to_list(None))
        preview = await self.svc.dry_run(USER, REQUEST)
        self.assertEqual(preview["summary"]["selected_count"], 1)
        self.assertEqual(preview["summary"]["eligible_total"], 1)
        self.assertEqual(before, json_util.dumps(await self.db[inbox.COLLECTION].find({}).sort("_id").to_list(None)))
        self.assertEqual(await self.db[inbox.LEASES_COLLECTION].count_documents({}), 0)

    async def test_batch_commit_preserves_identity_payload_history_and_grants_once(self):
        preview = await self.seeded_plan()
        before = {row["_id"]: row async for row in self.db[inbox.COLLECTION].find({})}
        old_binding = await self.db.salla_integrations.find_one({"user_id": USER["id"]})
        with patch.object(inbox, "_process", new_callable=AsyncMock,
                          side_effect=AssertionError("Recovery must not dispatch")) as process:
            result = await self.svc.apply(USER, apply_request(preview))
            process.assert_not_awaited()
        self.assertEqual(result, {"applied_count": 2, "plan_sha256": preview["plan_sha256"],
            "state": "retry", "processing": "queued_for_existing_worker"})
        new_binding = await self.db.salla_integrations.find_one({"user_id": USER["id"]})
        self.assertEqual(new_binding.pop(recovery.BINDING_FENCE), 1)
        self.assertEqual(new_binding, old_binding)
        async for row in self.db[inbox.COLLECTION].find({}):
            original = before[row["_id"]]
            self.assertEqual(row["state"], "retry")
            self.assertEqual(row["attempts"], 0)
            self.assertEqual(row["payload_ciphertext"], original["payload_ciphertext"])
            self.assertEqual(row["event_hash"], original["event_hash"])
            self.assertEqual(row["created_at"], original["created_at"])
            self.assertEqual(row["delivery_count"], original["delivery_count"])
            marker = row[recovery.MARKER]
            self.assertEqual(marker["before"], {k: original[k] for k in recovery._HISTORY_FIELDS if k in original})
            self.assertNotIn("payload_ciphertext", marker)
            self.assertEqual(marker["plan_sha256"], preview["plan_sha256"])
            self.assertEqual(marker["runtime"], RUNTIME)
        with self.assertRaisesRegex(recovery.RecoveryError, "already_granted"):
            await self.svc.apply(USER, apply_request(preview))
        empty = await self.svc.dry_run(USER, REQUEST)
        self.assertEqual(empty["summary"]["eligible_total"], 0)

    async def test_stale_delivery_metadata_payload_or_extra_fields_refuse_whole_batch(self):
        preview = await self.seeded_plan()
        row_id = preview["plan"]["rows"][0]["job_id"]
        for change in ({"delivery_count": 2}, {"last_received_at": NOW},
                       {"payload_ciphertext": b"replacement"}, {"unexpected_field": "changed"}):
            with self.subTest(change=tuple(change)):
                original = await self.db[inbox.COLLECTION].find_one({"_id": row_id})
                await self.other_db[inbox.COLLECTION].update_one({"_id": row_id}, {"$set": change})
                with self.assertRaisesRegex(recovery.RecoveryError, "plan_stale"):
                    await self.svc.apply(USER, apply_request(preview))
                await self.assert_no_grants()
                await self.db[inbox.COLLECTION].replace_one({"_id": row_id}, original)

    async def test_concurrent_ingress_after_snapshot_aborts_prior_row_changes(self):
        preview = await self.seeded_plan()
        second = preview["plan"]["rows"][1]["job_id"]
        other = self.other_db
        class RacingRecovery(recovery.IncidentRecovery):
            async def _make_plan(self, *args, **kwargs):
                result = await super()._make_plan(*args, **kwargs)
                if kwargs.get("session") is not None:
                    await other[inbox.COLLECTION].update_one({"_id": second},
                        {"$inc": {"delivery_count": 1}, "$set": {"last_received_at": NOW}})
                return result
        racing = RacingRecovery(self.db, clock=lambda: NOW, runtime_reader=lambda: RUNTIME.copy())
        with self.assertRaisesRegex(recovery.RecoveryError, "recovery_conflict|plan_stale"):
            await racing.apply(USER, apply_request(preview))
        await self.assert_no_grants()
        self.assertEqual((await self.db[inbox.COLLECTION].find_one({"_id": second}))["delivery_count"], 2)

    async def test_live_worker_lease_is_never_overridden(self):
        preview = await self.seeded_plan()
        lease = await inbox._acquire_worker(self.other_db, NOW, inbox.LEASE_SECONDS)
        self.assertIsNotNone(lease)
        with self.assertRaisesRegex(recovery.RecoveryError, "worker_lease_busy"):
            await self.svc.apply(USER, apply_request(preview))
        await self.assert_no_grants()
        current = await self.db[inbox.LEASES_COLLECTION].find_one({"_id": inbox.WORKER_LEASE_ID})
        self.assertEqual(current, lease)

    async def test_successor_epoch_is_not_modified_or_released(self):
        preview = await self.seeded_plan()
        acquire = inbox._acquire_worker
        successor = {}
        async def replaced(db, at, seconds):
            old = await acquire(db, at, seconds)
            await self.other_db[inbox.LEASES_COLLECTION].update_one(
                inbox._worker_filter(old), {"$set": {"lease_until": NOW - timedelta(seconds=1)}})
            successor.update(await acquire(self.other_db, NOW, seconds))
            return old
        with patch.object(inbox, "_acquire_worker", side_effect=replaced):
            with self.assertRaisesRegex(recovery.RecoveryError, "worker_lease_lost"):
                await self.svc.apply(USER, apply_request(preview))
        await self.assert_no_grants()
        self.assertEqual(await self.db[inbox.LEASES_COLLECTION].find_one(
            {"_id": inbox.WORKER_LEASE_ID}), successor)

    async def test_expiry_before_final_write_fence_rolls_back_entire_batch(self):
        preview = await self.seeded_plan()
        clock = [NOW]
        class ExpiringRecovery(recovery.IncidentRecovery):
            fences = 0
            async def _fence(self, lease, session):
                self.fences += 1
                if self.fences == 2:
                    clock[0] += timedelta(seconds=inbox.LEASE_SECONDS + 1)
                return await super()._fence(lease, session)
        expiring = ExpiringRecovery(self.db, clock=lambda: clock[0], runtime_reader=lambda: RUNTIME.copy())
        with self.assertRaisesRegex(recovery.RecoveryError, "worker_lease_lost"):
            await expiring.apply(USER, apply_request(preview))
        await self.assert_no_grants()

    async def test_runtime_change_before_commit_rolls_back_entire_batch(self):
        preview = await self.seeded_plan()
        reads = [0]
        def runtime():
            reads[0] += 1
            return RUNTIME.copy() if reads[0] == 1 else {**RUNTIME, "source_git_sha": "c" * 40}
        changed = recovery.IncidentRecovery(self.db, clock=lambda: NOW, runtime_reader=runtime)
        with self.assertRaisesRegex(recovery.RecoveryError, "runtime_changed"):
            await changed.apply(USER, apply_request(preview))
        await self.assert_no_grants()

    async def test_changed_current_owner_refuses_before_grant(self):
        preview = await self.seeded_plan()
        await self.other_db.salla_integrations.update_one({"store_id": recovery.MERCHANT_ID},
            {"$set": {"user_id": "another-owner", "updated_at": NOW}})
        with self.assertRaisesRegex(recovery.RecoveryError, "merchant_owner_mismatch"):
            await self.svc.apply(USER, apply_request(preview))
        await self.assert_no_grants()

    async def test_concurrent_same_document_rebind_conflicts_and_rolls_back_grants(self):
        preview = await self.seeded_plan()
        other = self.other_db
        class RebindingRecovery(recovery.IncidentRecovery):
            changed = False
            async def _fence(self, lease, session):
                at = await super()._fence(lease, session)
                if not self.changed:
                    self.changed = True
                    await other.salla_integrations.update_one({"user_id": USER["id"]},
                        {"$set": {"user_id": "new-owner", "updated_at": NOW}})
                return at
        racing = RebindingRecovery(self.db, clock=lambda: NOW, runtime_reader=lambda: RUNTIME.copy())
        with self.assertRaisesRegex(recovery.RecoveryError, "recovery_conflict|merchant_owner_mismatch"):
            await racing.apply(USER, apply_request(preview))
        await self.assert_no_grants()
        binding = await self.db.salla_integrations.find_one({"store_id": recovery.MERCHANT_ID})
        self.assertEqual(binding["user_id"], "new-owner")
        self.assertNotIn(recovery.BINDING_FENCE, binding)

    async def test_newer_binding_committed_during_snapshot_is_seen_by_fresh_final_check(self):
        preview = await self.seeded_plan()
        other = self.other_db
        class NewBindingRecovery(recovery.IncidentRecovery):
            async def _make_plan(self, *args, **kwargs):
                result = await super()._make_plan(*args, **kwargs)
                if kwargs.get("session") is not None:
                    await other.salla_integrations.insert_one({"store_id": int(recovery.MERCHANT_ID),
                        "user_id": "new-owner", "updated_at": NOW})
                return result
        racing = NewBindingRecovery(self.db, clock=lambda: NOW, runtime_reader=lambda: RUNTIME.copy())
        with self.assertRaisesRegex(recovery.RecoveryError, "merchant_owner_mismatch|merchant_binding_ambiguous"):
            await racing.apply(USER, apply_request(preview))
        await self.assert_no_grants()
        old_binding = await self.db.salla_integrations.find_one({"user_id": USER["id"]})
        self.assertNotIn(recovery.BINDING_FENCE, old_binding)

    async def test_only_existing_worker_processes_grant_and_keeps_done_tombstone(self):
        body = {"event": "product.updated", "merchant": recovery.MERCHANT_ID,
                "data": {"id": "synthetic-product", "name": "Synthetic product"}}
        admission = await inbox.enqueue_verified_event(self.db, body, now=CREATED)
        row_id = admission["job_id"]
        await self.db[inbox.COLLECTION].update_one({"_id": row_id}, {"$set": {
            "state": "quarantined", "attempts": 8, "last_error_code": "attempt_limit",
            "quarantined_at": CREATED + timedelta(minutes=21),
            "last_attempt_at": CREATED + timedelta(minutes=21)}})
        preview = await self.svc.dry_run(USER, REQUEST)
        await self.svc.apply(USER, apply_request(preview))
        processor = AsyncMock(return_value={"ok": True})
        result = await inbox.run_once(self.db, now=NOW, processor=processor)
        self.assertEqual(result["done"], 1)
        processor.assert_awaited_once()
        settled = await self.db[inbox.COLLECTION].find_one({"_id": row_id})
        self.assertEqual(settled["state"], "done")
        self.assertEqual(settled["attempts"], 1)
        self.assertEqual(settled[recovery.MARKER]["before"]["attempts"], 8)
        self.assertNotIn("payload_ciphertext", settled)
        duplicate = await inbox.enqueue_verified_event(self.db, body, now=NOW + timedelta(days=90))
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual((await inbox.run_once(self.db, now=NOW + timedelta(days=90),
                                              processor=processor))["claimed"], 0)
        processor.assert_awaited_once()
