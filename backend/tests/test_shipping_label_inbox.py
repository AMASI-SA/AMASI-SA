"""Verified label receipt survives processing failure without provider recovery."""
from datetime import datetime, timedelta, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

from salla_integration import shipping_label_inbox as inbox
from salla_shipping import CURRENT_SHIPPING


@pytest.fixture
async def db(monkeypatch):
    database = AsyncMongoMockClient(tz_aware=True).label_inbox
    await database.salla_integrations.insert_one({"user_id": "owner", "store_id": "merchant"})
    async def owner(scoped, _owner, callback, **kwargs):
        return await callback(scoped)
    monkeypatch.setattr(inbox, "operational_owner", owner)
    return database


def event():
    return {"event": "order.updated", "merchant": "merchant", "data": {
        "id": "123", "reference_id": "3001", "updated_at": "2026-10-17T00:00:00Z",
        "customer": {"name": "Not stored", "mobile": "private"},
        "shipping": {"company": "iMile", "shipment": {"id": "shipment",
            "tracking_number": "AWB", "status": "created", "label": "https://labels.test/current.pdf"}},
    }}


async def seed(db):
    await db.unified_orders.insert_one({"user_id": "owner", "order_number": "3001",
        "order_id": "123", "shipping_company": "iMile"})


@pytest.mark.asyncio
async def test_receipt_survives_restart_and_order_arriving_later(db):
    key = await inbox.enqueue_verified_label(db, event())
    assert key == await inbox.enqueue_verified_label(db, event())
    assert await db[inbox.COLLECTION].count_documents({}) == 1
    row = await db[inbox.COLLECTION].find_one({})
    assert "customer" not in str(row) and "private" not in str(row)
    now = datetime.now(timezone.utc)
    await inbox.run_once(db, now=now)
    assert (await db[inbox.COLLECTION].find_one({}))["state"] == "pending"
    await seed(db)
    # A new worker invocation uses only durable state, no in-memory event.
    await inbox.run_once(db, now=now + timedelta(minutes=10))
    order = await db.unified_orders.find_one({})
    assert order["shipping_label_url"] == "https://labels.test/current.pdf"
    assert (await db[inbox.COLLECTION].find_one({}))["state"] == "done"
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_failed_persistence_retries_and_does_not_ack_done(db, monkeypatch):
    await seed(db)
    await inbox.enqueue_verified_label(db, event())
    original = inbox.operational_owner
    async def fail(*args, **kwargs):
        raise RuntimeError("transient database failure")
    monkeypatch.setattr(inbox, "operational_owner", fail)
    now = datetime.now(timezone.utc)
    await inbox.run_once(db, now=now)
    assert (await db[inbox.COLLECTION].find_one({}))["state"] == "pending"
    monkeypatch.setattr(inbox, "operational_owner", original)
    await inbox.run_once(db, now=now + timedelta(minutes=10))
    assert (await db[inbox.COLLECTION].find_one({}))["state"] == "done"


@pytest.mark.asyncio
async def test_old_label_never_replaces_cancelled_or_other_order(db):
    await seed(db)
    await db.unified_orders.update_one({}, {"$set": {
        "salla_shipment_id": "shipment", "shipping_label_url": None,
        CURRENT_SHIPPING: {"company_name": "iMile", "shipment_id": "shipment", "status": "cancelled",
            "carrier_updated_at": "2026-10-17T01:00:00+00:00", "shipment_updated_at": "2026-10-17T01:00:00+00:00"},
    }})
    await inbox.enqueue_verified_label(db, event())
    await inbox.run_once(db)
    assert not (await db.unified_orders.find_one({})).get("shipping_label_url")
    assert (await db[inbox.COLLECTION].find_one({}))["state"] == "done"


@pytest.mark.asyncio
async def test_older_pdf_waits_for_received_newer_invalidation(db):
    await seed(db)
    old_key = await inbox.enqueue_verified_label(db, event())
    cancellation = event()
    cancellation["data"]["updated_at"] = "2026-10-17T01:00:00Z"
    cancellation["data"]["shipping"]["shipment"]["status"] = "cancelled"
    await inbox.enqueue_verified_label(db, cancellation)
    old = await db[inbox.COLLECTION].find_one({"_id": old_key})
    assert await inbox._apply(db, old) == "done"
    assert not (await db.unified_orders.find_one({})).get("shipping_label_url")


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", ["2026-10-17T00:00:00Z", "2026-10-17T01:00:00Z"])
async def test_shipment_cancellation_without_saved_baseline_blocks_old_label(db, timestamp):
    await seed(db)
    await inbox.enqueue_verified_label(db, event())
    cancellation = {"event": "order.shipment.cancelled", "merchant": "merchant", "data": {
        "id": "shipment", "order_reference_id": "3001", "order_id": "123", "updated_at": timestamp,
    }}
    await inbox.enqueue_verified_label(db, cancellation)
    now = datetime.now(timezone.utc)
    await inbox.run_once(db, now=now)
    await inbox.run_once(db, now=now + timedelta(minutes=10))
    assert not (await db.unified_orders.find_one({})).get("shipping_label_url")
    tombstone = await db[inbox.COLLECTION].find_one({"observation.status": "cancelled"})
    assert tombstone["state"] == "done" and "completed_at" not in tombstone


@pytest.mark.asyncio
async def test_owner_relink_does_not_apply_queued_label(db):
    await seed(db)
    await inbox.enqueue_verified_label(db, event())
    await db.salla_integrations.update_one({}, {"$set": {"user_id": "other-owner"}})
    await inbox.run_once(db)
    assert not (await db.unified_orders.find_one({})).get("shipping_label_url")
    assert (await db[inbox.COLLECTION].find_one({}))["state"] == "paused"


@pytest.mark.asyncio
async def test_capture_persists_label_before_failed_order_processing(db, monkeypatch):
    from salla_integration import webhook_event_capture as capture
    await seed(db)
    async def fail_order(*args):
        assert await db[inbox.COLLECTION].count_documents({}) == 1
        raise RuntimeError("order processing failed")
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook", fail_order)
    result = await capture.capture_unknown_event(db, event(), known_events=())
    assert result["order_sync"]["synced"] is False
    await inbox.run_once(db)
    assert (await db.unified_orders.find_one({}))["shipping_label_url"] == "https://labels.test/current.pdf"


@pytest.mark.asyncio
async def test_failed_durable_receipt_is_not_acknowledged(db, monkeypatch):
    from salla_integration import webhook_event_capture as capture
    async def unavailable(*args):
        raise RuntimeError("inbox unavailable")
    monkeypatch.setattr(inbox, "enqueue_verified_label", unavailable)
    with pytest.raises(RuntimeError, match="inbox unavailable"):
        await capture.capture_unknown_event(db, event(), known_events=())


@pytest.mark.asyncio
async def test_crashed_claim_recovers_after_lease_expiry(db):
    await seed(db)
    await inbox.enqueue_verified_label(db, event())
    now = datetime.now(timezone.utc)
    await db[inbox.COLLECTION].update_one({}, {"$set": {"claim": "crashed", "lease_until": now + timedelta(seconds=60)}})
    await inbox.run_once(db, now=now)
    assert not (await db.unified_orders.find_one({})).get("shipping_label_url")
    await inbox.run_once(db, now=now + timedelta(seconds=61))
    assert (await db[inbox.COLLECTION].find_one({}))["state"] == "done"


@pytest.mark.asyncio
async def test_parallel_workers_apply_one_claim(db, monkeypatch):
    import asyncio
    await seed(db)
    await inbox.enqueue_verified_label(db, event())
    original = inbox._apply
    calls = []
    async def apply(database, item):
        calls.append(item["_id"])
        await asyncio.sleep(0)
        return await original(database, item)
    monkeypatch.setattr(inbox, "_apply", apply)
    await asyncio.gather(inbox.run_once(db), inbox.run_once(db))
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancelled", "replacement"])
async def test_failed_invalidation_is_durable_and_blocks_delayed_old_label(db, change):
    await seed(db)
    old = event()
    await inbox.enqueue_verified_label(db, old)
    newer = event()
    newer["data"]["updated_at"] = "2026-10-17T01:00:00Z"
    shipment = newer["data"]["shipping"]["shipment"]
    shipment.pop("label")
    shipment.pop("tracking_number")
    shipment["status"] = "cancelled" if change == "cancelled" else "creating"
    if change == "replacement":
        shipment["id"] = "replacement"
    await inbox.enqueue_verified_label(db, newer)
    assert await db[inbox.COLLECTION].count_documents({}) == 2
    await inbox.run_once(db)
    row = await db.unified_orders.find_one({})
    assert not row.get("shipping_label_url")
    # Duplicate old delivery cannot resurrect it after recovery.
    await inbox.enqueue_verified_label(db, old)
    await inbox.run_once(db)
    assert not (await db.unified_orders.find_one({})).get("shipping_label_url")


@pytest.mark.asyncio
async def test_each_batch_claim_uses_current_time(db, monkeypatch):
    await seed(db)
    await inbox.enqueue_verified_label(db, event())
    second = event()
    second["data"]["updated_at"] = "2026-10-17T01:00:00Z"
    await inbox.enqueue_verified_label(db, second)
    tick = [datetime.now(timezone.utc)]
    class Clock:
        @staticmethod
        def now(zone):
            return tick[0]
    monkeypatch.setattr(inbox, "datetime", Clock)
    async def apply(database, item):
        assert item["lease_until"] > tick[0]
        tick[0] += timedelta(seconds=75)
        return "done"
    monkeypatch.setattr(inbox, "_apply", apply)
    await inbox.run_once(db)
