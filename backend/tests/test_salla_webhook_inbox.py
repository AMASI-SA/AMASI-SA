"""Queue acceptance at the persistence/processor boundaries with synthetic data.

Real Mongo concurrency/lease cases live in the companion acceptance suite. This
fast suite uses mongomock's Mongo operations, real encryption, and controllable
processor delays; it never opens a provider or production connection.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockCollection
import pytest

import customer_identity
from salla_integration import webhook_inbox as inbox


AT = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def private_key(monkeypatch):
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(customer_identity, "_fernet", None)
    # mongomock_motor delegates with_options to a synchronous Collection. Keep
    # the asynchronous test adapter while verifying the production requirement.
    def with_options(collection, **options):
        assert options["write_concern"].document == {"w": "majority", "j": True, "wtimeout": 5000}
        return collection
    monkeypatch.setattr(AsyncMongoMockCollection, "with_options", with_options, raising=False)


def event(*, name="order.updated", merchant="store-a", revision=1):
    return {"event": name, "merchant": merchant, "created_at": "2026-11-15T00:00:00Z",
            "data": {"id": "native-order", "reference_id": "synthetic-order",
                     "revision": revision, "customer": {"name": "Private Synthetic Customer",
                     "email": "synthetic-private@example.invalid"},
                     "items": [{"id": "line-a", "options": [{"name": "Size", "value": "M"}]}]}}


async def database(*, owner="owner-a"):
    db = AsyncMongoMockClient(tz_aware=True).db
    if owner:
        await db.salla_integrations.insert_one({"store_id": "store-a", "user_id": owner,
                                               "updated_at": AT})
    await inbox.ensure_indexes(db)
    return db


async def saved(db, receipt):
    return await db[inbox.COLLECTION].find_one({"_id": receipt["job_id"]})


@pytest.mark.asyncio
async def test_concurrent_deliveries_commit_one_encrypted_job_before_ack():
    db = await database()
    body = event()
    body["data"]["access_token"] = "synthetic-private-token"
    results = await asyncio.gather(*(inbox.enqueue_verified_event(db, body, now=AT) for _ in range(24)))
    assert sum(not result["duplicate"] for result in results) == 1
    assert all(result["queued"] for result in results)
    assert await db[inbox.COLLECTION].count_documents({}) == 1
    row = await saved(db, results[0])
    assert row["state"] == "pending" and row["attempts"] == 0
    assert row["user_id"] == "owner-a" and row["delivery_count"] == 24
    assert row["verified_before_enqueue"] is True
    for secret in ("Private Synthetic Customer", "synthetic-private@example.invalid", "synthetic-private-token"):
        assert secret not in repr(row)
    restored = customer_identity.decrypt_private_payload(row["payload_ciphertext"])
    assert restored["data"]["customer"] == body["data"]["customer"]
    assert restored["data"]["access_token"] == "[REDACTED]"
    assert body["data"]["access_token"] == "synthetic-private-token"  # input not modified


@pytest.mark.asyncio
async def test_hash_keeps_changed_choices_source_time_event_and_merchant_distinct():
    db = await database()
    bodies = [event()]
    choices = deepcopy(bodies[0])
    choices["data"]["items"][0]["options"][0]["value"] = "L"
    bodies.append(choices)
    time = deepcopy(bodies[0])
    time["created_at"] = "2026-11-15T00:00:01Z"
    bodies.extend([time, event(name="order.created"), event(merchant="store-b")])
    ids = {(await inbox.enqueue_verified_event(db, body, now=AT))["job_id"] for body in bodies}
    assert len(ids) == len(bodies)
    reordered = {key: bodies[0][key] for key in reversed(list(bodies[0]))}
    assert (await inbox.enqueue_verified_event(db, reordered, now=AT))["duplicate"]


@pytest.mark.asyncio
async def test_secret_presence_and_rotation_do_not_create_new_business_work():
    db = await database()
    body = event()
    first = await inbox.enqueue_verified_event(db, body, now=AT)
    body["data"]["ACCESS_TOKEN"] = "synthetic-first-token"
    second = await inbox.enqueue_verified_event(db, body, now=AT)
    body["data"]["ACCESS_TOKEN"] = "synthetic-rotated-token"
    third = await inbox.enqueue_verified_event(db, body, now=AT)
    assert first["job_id"] == second["job_id"] == third["job_id"]
    assert not first["duplicate"] and second["duplicate"] and third["duplicate"]


@pytest.mark.asyncio
async def test_abandoned_cart_changes_do_not_collapse_after_pii_redaction():
    db = await database()
    first = event(name="abandoned.cart.updated")
    second = deepcopy(first)
    second["data"]["customer"]["email"] = "another-private@example.invalid"
    a = await inbox.enqueue_verified_event(db, first, now=AT)
    b = await inbox.enqueue_verified_event(db, second, now=AT)
    assert a["job_id"] != b["job_id"]
    rows = await db[inbox.COLLECTION].find({}).to_list(length=3)
    assert "private@example.invalid" not in repr(rows)
    assert customer_identity.decrypt_private_payload(rows[1]["payload_ciphertext"]) == second


@pytest.mark.asyncio
async def test_missing_encryption_key_fails_before_any_queue_write(monkeypatch):
    db = await database()
    monkeypatch.delenv("MEZAN_CUSTOMER_PII_ENC_KEY", raising=False)
    monkeypatch.delenv("SALLA_TOKEN_ENC_KEY", raising=False)
    monkeypatch.setattr(customer_identity, "_fernet", None)
    with pytest.raises(RuntimeError):
        await inbox.enqueue_verified_event(db, event())
    assert await db[inbox.COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [[], {}, {"event": "order.updated", "merchant": True},
    {"event": "order.updated", "merchant": {}}, {"event": "order.updated", "merchant": ""},
    {"event": "order.updated\nforged-log", "merchant": "store-a"},
    {"event": "app.store.authorize", "merchant": "store-a", "data": {"access_token": "secret"}}])
async def test_invalid_or_lifecycle_payload_is_never_queued(invalid):
    db = await database()
    with pytest.raises(inbox.WebhookInboxInvalidEvent):
        await inbox.enqueue_verified_event(db, invalid)
    assert await db[inbox.COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_recursive_payload_has_fixed_validation_failure_before_persistence():
    db = await database()
    body = event()
    body["data"]["recursive"] = body
    with pytest.raises(inbox.WebhookInboxInvalidEvent, match="invalid_payload"):
        await inbox.enqueue_verified_event(db, body)
    assert await db[inbox.COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_unacknowledged_write_cannot_return_success():
    db = await database()

    class UnacknowledgedDB:
        salla_integrations = db.salla_integrations
        def __getitem__(self, _):
            collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(acknowledged=False)))
            collection.with_options = lambda **options: collection
            return collection

    with pytest.raises(RuntimeError, match="write_not_acknowledged"):
        await inbox.enqueue_verified_event(UnacknowledgedDB(), event())


@pytest.mark.asyncio
async def test_completed_duplicate_never_reexecutes_or_restores_private_payload():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event(), now=AT)
    processor = AsyncMock(return_value={"ok": True})
    result = await inbox.run_once(db, now=AT, processor=processor)
    assert result["done"] == 1
    assert processor.await_count == 1
    assert (await saved(db, receipt))["state"] == "done"
    assert "payload_ciphertext" not in await saved(db, receipt)
    duplicate = await inbox.enqueue_verified_event(db, event(), now=AT + timedelta(days=60))
    assert duplicate["duplicate"] is True
    assert (await inbox.run_once(db, now=AT + timedelta(days=60), processor=processor))["claimed"] == 0
    assert processor.await_count == 1
    row = await saved(db, receipt)
    assert row["state"] == "done" and row["delivery_count"] == 2
    assert "payload_ciphertext" not in row


@pytest.mark.asyncio
async def test_transient_failure_retains_event_and_retries_only_when_due():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event(), now=AT)
    processor = AsyncMock(side_effect=[RuntimeError("private secret provider body"), {"ok": True}])
    first = await inbox.run_once(db, now=AT, processor=processor)
    assert first["retry"] == 1 and first["done"] == 0
    row = await saved(db, receipt)
    assert row["last_error_code"] == "processing_failed"
    assert "private secret provider body" not in repr(row)
    assert row["state"] == "retry" and row["attempts"] == 1
    assert customer_identity.decrypt_private_payload(row["payload_ciphertext"]) == event()
    await inbox.enqueue_verified_event(db, event(), now=AT + timedelta(seconds=1))
    assert (await inbox.run_once(db, now=AT + timedelta(seconds=1), processor=processor))["claimed"] == 0
    final = await inbox.run_once(db, now=row["next_attempt_at"], processor=processor)
    assert final["done"] == 1 and processor.await_count == 2
    assert (await saved(db, receipt))["attempts"] == 2


@pytest.mark.asyncio
async def test_unresolved_tenant_is_bound_before_processing_and_known_tenant_cannot_rebind():
    db = await database(owner=None)
    receipt = await inbox.enqueue_verified_event(db, event(), now=AT)
    processor = AsyncMock(return_value={"ok": True})
    assert (await inbox.run_once(db, now=AT, processor=processor))["retry"] == 1
    row = await saved(db, receipt)
    assert row["last_error_code"] == "owner_not_bound"
    processor.assert_not_awaited()
    await db.salla_integrations.insert_one({"store_id": "store-a", "user_id": "owner-a"})
    assert (await inbox.run_once(db, now=row["next_attempt_at"], processor=processor))["done"] == 1
    assert (await saved(db, receipt))["user_id"] == "owner-a"

    later = row["next_attempt_at"] + timedelta(seconds=1)
    next_receipt = await inbox.enqueue_verified_event(db, event(revision=2), now=later)
    await db.salla_integrations.update_one({"store_id": "store-a"}, {"$set": {"user_id": "owner-b"}})
    assert (await inbox.run_once(db, now=later, processor=processor))["quarantined"] == 1
    row = await saved(db, next_receipt)
    assert row["last_error_code"] == "owner_binding_changed" and row["user_id"] == "owner-a"
    assert "payload_ciphertext" in row
    assert processor.await_count == 1
    await inbox.enqueue_verified_event(db, event(revision=2), now=AT + timedelta(days=1))
    assert (await saved(db, next_receipt))["user_id"] == "owner-a"


@pytest.mark.asyncio
async def test_attempt_limit_quarantines_without_losing_payload_or_tight_retry():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event(), now=AT)
    await db[inbox.COLLECTION].update_one({"_id": receipt["job_id"]}, {"$set": {"attempts": inbox.MAX_ATTEMPTS - 1}})
    processor = AsyncMock(side_effect=RuntimeError("synthetic transient failure"))
    result = await inbox.run_once(db, now=AT, processor=processor)
    assert result["quarantined"] == 1
    row = await saved(db, receipt)
    assert row["state"] == "quarantined" and row["last_error_code"] == "attempt_limit"
    assert "payload_ciphertext" in row
    assert (await inbox.run_once(db, now=AT + timedelta(days=1), processor=processor))["claimed"] == 0
    assert processor.await_count == 1


@pytest.mark.asyncio
async def test_two_replicas_cannot_process_different_jobs_concurrently():
    db = await database()
    await inbox.enqueue_verified_event(db, event(revision=1))
    await inbox.enqueue_verified_event(db, event(revision=2))
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def processor(_, body):
        calls.append(body["data"]["revision"])
        started.set()
        await finish.wait()
        return {"ok": True}

    first = asyncio.create_task(inbox.run_once(db, limit=1, processor=processor))
    await asyncio.wait_for(started.wait(), 1)
    second = await inbox.run_once(db, processor=processor)
    assert second["lease_busy"] is True and len(calls) == 1
    finish.set()
    assert (await first)["done"] == 1
    assert (await inbox.run_once(db, processor=processor))["done"] == 1
    assert sorted(calls) == [1, 2]


@pytest.mark.asyncio
async def test_process_death_claim_recovers_after_lease_expiry():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event(), now=AT)
    expires = AT + timedelta(seconds=inbox.LEASE_SECONDS)
    await db[inbox.LEASES_COLLECTION].insert_one({"_id": inbox.WORKER_LEASE_ID,
        "lease_token": "dead-process", "epoch": 1, "lease_until": expires})
    await db[inbox.COLLECTION].update_one({"_id": receipt["job_id"]}, {"$set": {
        "state": "processing", "attempts": 1, "claim_token": "dead-claim", "lease_until": expires}})
    processor = AsyncMock(return_value={"ok": True})
    assert (await inbox.run_once(db, now=AT, processor=processor))["lease_busy"] is True
    processor.assert_not_awaited()
    result = await inbox.run_once(db, now=expires + timedelta(milliseconds=1), processor=processor)
    assert result["done"] == 1 and processor.await_count == 1
    assert (await saved(db, receipt))["attempts"] == 2


@pytest.mark.asyncio
async def test_lease_loss_cancels_work_and_cannot_settle_or_release_another_owner():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event())
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def processor(_, body):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(inbox.run_once(db, processor=processor,
        processing_timeout=1, lease_seconds=2, heartbeat_interval=0.01))
    await asyncio.wait_for(started.wait(), 1)
    await db[inbox.LEASES_COLLECTION].update_one({"_id": inbox.WORKER_LEASE_ID},
        {"$set": {"lease_token": "replacement-token"}})
    result = await task
    assert cancelled.is_set() and result["lease_lost"] and result["done"] == 0
    row = await saved(db, receipt)
    assert row["state"] == "processing" and "completed_at" not in row
    lease = await db[inbox.LEASES_COLLECTION].find_one({"_id": inbox.WORKER_LEASE_ID})
    assert lease["lease_token"] == "replacement-token" and "released_at" not in lease


@pytest.mark.asyncio
async def test_completion_rechecks_ownership_even_before_heartbeat_runs():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event())

    async def processor(_, body):
        await db[inbox.LEASES_COLLECTION].update_one({"_id": inbox.WORKER_LEASE_ID},
            {"$set": {"lease_token": "replacement-token"}})
        return {"ok": True}

    result = await inbox.run_once(db, processor=processor)
    assert result["lease_lost"] and result["done"] == 0
    assert (await saved(db, receipt))["state"] == "processing"


@pytest.mark.asyncio
async def test_delayed_claim_cannot_start_business_work_after_ownership_changed(monkeypatch):
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event())
    original = AsyncMongoMockCollection.find_one_and_update

    async def delayed_claim(collection, *args, **kwargs):
        row = await original(collection, *args, **kwargs)
        if collection.name == inbox.COLLECTION and row:
            await db[inbox.LEASES_COLLECTION].update_one({"_id": inbox.WORKER_LEASE_ID},
                {"$set": {"lease_token": "replacement-token"}})
        return row

    monkeypatch.setattr(AsyncMongoMockCollection, "find_one_and_update", delayed_claim)
    processor = AsyncMock(return_value={"ok": True})
    result = await inbox.run_once(db, processor=processor)
    assert result["lease_lost"] and result["done"] == 0
    processor.assert_not_awaited()
    assert (await saved(db, receipt))["state"] == "processing"


@pytest.mark.asyncio
async def test_timeout_keeps_renewing_lease_until_work_acknowledges_cancellation():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event())
    cancelled, allow_cleanup = asyncio.Event(), asyncio.Event()

    async def processor(_, body):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await allow_cleanup.wait()
            raise

    task = asyncio.create_task(inbox.run_once(db, processor=processor,
        processing_timeout=0.02, lease_seconds=0.12, heartbeat_interval=0.01))
    await asyncio.wait_for(cancelled.wait(), 1)
    await asyncio.sleep(0.15)  # More than the original lease, while cleanup is pending.
    assert not task.done()
    contender = await inbox.run_once(db, processor=AsyncMock(return_value={"ok": True}))
    assert contender["lease_busy"] is True
    allow_cleanup.set()
    result = await task
    assert result["retry"] == 1
    row = await saved(db, receipt)
    assert row["state"] == "retry" and row["last_error_code"] == "processing_timeout"


@pytest.mark.asyncio
async def test_parent_cancellation_releases_only_after_processor_stops():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event())
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def processor(_, body):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(inbox.run_once(db, processor=processor))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    row = await saved(db, receipt)
    assert row["state"] == "retry" and row["last_error_code"] == "worker_interrupted"


@pytest.mark.asyncio
async def test_unreadable_payload_is_retained_for_review_without_processor_calls():
    db = await database()
    receipt = await inbox.enqueue_verified_event(db, event(), now=AT)
    await db[inbox.COLLECTION].update_one({"_id": receipt["job_id"]},
        {"$set": {"payload_ciphertext": b"synthetic-invalid-ciphertext"}})
    processor = AsyncMock(return_value={"ok": True})
    result = await inbox.run_once(db, now=AT, processor=processor)
    assert result["quarantined"] == 1
    assert (await saved(db, receipt))["last_error_code"] == "payload_unreadable"
    processor.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_dispatch_enforces_strict_result_and_pinned_owner(monkeypatch):
    db = await database()
    await inbox.enqueue_verified_event(db, event(), now=AT)
    from salla_integration import easy_mode_webhook
    dispatch = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(easy_mode_webhook, "dispatch_event", dispatch)
    assert (await inbox.run_once(db, now=AT))["done"] == 1
    dispatch.assert_awaited_once_with(db, event(), raise_on_processing_error=True,
                                    expected_user_id="owner-a")
