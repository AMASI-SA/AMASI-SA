"""Recover a failed local CAPI stage without replaying stale purchase facts.

Queue persistence, strict dispatch, audit, owner binding and the real local CAPI
outbox helper run together. Only core order processing and unrelated observers
are controlled collaborators; no conversion provider request is performed.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockCollection
from pymongo.errors import AutoReconnect
import pytest
import pytest_asyncio

import customer_identity
from integrations_control_center import snapchat_capi_purchases as capi
from salla_integration import webhook_event_capture as capture, webhook_inbox as inbox


NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
OWNER = "synthetic-owner"
MERCHANT = "synthetic-store"
REFERENCE = "synthetic-order"


def raw_order(*, status="processing", amount=10, email="first@example.test", sku="old-sku"):
    return {
        "id": "synthetic-native-order", "reference_id": REFERENCE,
        "created_at": (NOW - timedelta(hours=1)).isoformat(),
        "updated_at": (NOW - timedelta(minutes=20)).isoformat(),
        "status": {"slug": status, "name": status},
        "customer": {"id": "synthetic-customer", "email": email},
        "amounts": {"total": {"amount": amount, "currency": "SAR"}},
        "items": [{"id": "line-one", "product_id": "product-one", "sku": sku, "quantity": 1}],
    }


def event():
    return {"event": "order.created", "merchant": MERCHANT, "data": raw_order()}


@pytest_asyncio.fixture
async def isolated(monkeypatch):
    monkeypatch.setenv("MEZAN_SNAPCHAT_CAPI_ENABLED", "true")
    monkeypatch.setenv("SALLA_ORDERS_V3_SHADOW_ENABLED", "false")
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(customer_identity, "_fernet", None)
    monkeypatch.setattr(capi, "_utcnow", lambda: NOW)
    monkeypatch.setattr(AsyncMongoMockCollection, "with_options",
                        lambda self, **options: self, raising=False)
    monkeypatch.setattr("salla_integration.shipping_label_inbox.enqueue_verified_label", AsyncMock())
    monkeypatch.setattr("salla_integration.payment_update_inbox.enqueue_verified_payment",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(capture, "sync_shipment_payload_from_verified_webhook",
                        AsyncMock(return_value={"attempted": False, "reason": "not_shipment_event"}))
    core = AsyncMock(side_effect=[
        {"synced": True}, {"synced": False, "reason": "stale_salla_snapshot"},
    ])
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook", core)
    db = AsyncMongoMockClient(tz_aware=True).db
    await db.salla_integrations.insert_one({"store_id": MERCHANT, "user_id": OWNER})
    return db, core


async def fail_first_outbox_write(db, monkeypatch):
    """Inject a real write-boundary failure, leaving the CAPI helper intact."""
    collection_type = type(db[capi.OUTBOX_COLLECTION])
    update = collection_type.update_one
    failed = False

    async def write(scoped, *args, **kwargs):
        nonlocal failed
        if scoped.name == capi.OUTBOX_COLLECTION and not failed:
            failed = True
            raise AutoReconnect("synthetic outbox storage interruption")
        return await update(scoped, *args, **kwargs)

    monkeypatch.setattr(collection_type, "update_one", write)
    receipt = await inbox.enqueue_verified_event(db, event(), now=NOW)
    first = await inbox.run_once(db, now=NOW, limit=1)
    assert first["retry"] == 1 and first["done"] == 0, first
    assert failed, "The actual CAPI outbox write must have been reached"
    audit = await db.salla_webhook_event_captures.find_one({})
    assert audit["snapchat_capi"]["reason"] == "enqueue_failed"
    assert audit["processing_retry_required"] is True
    assert await db[capi.OUTBOX_COLLECTION].count_documents({}) == 0
    row = await db[inbox.COLLECTION].find_one({"_id": receipt["job_id"]})
    return row


async def set_current(db, raw, **extra):
    await db.unified_orders.insert_one({
        "user_id": OWNER, "order_number": REFERENCE,
        "order_status_slug": raw["status"]["slug"],
        "raw_by_source": {"salla_direct": deepcopy(raw)}, **extra,
    })


@pytest.mark.asyncio
async def test_failed_outbox_retries_from_newer_active_canonical_source(isolated, monkeypatch):
    db, core = isolated
    job = await fail_first_outbox_write(db, monkeypatch)
    current = raw_order(amount=75, email="newer@example.test", sku="newer-sku")
    current["updated_at"] = (NOW - timedelta(minutes=5)).isoformat()
    await set_current(db, current)
    second = await inbox.run_once(db, now=job["next_attempt_at"], limit=1)
    assert second["done"] == 1 and second["retry"] == 0, second
    outbox = await db[capi.OUTBOX_COLLECTION].find_one({})
    assert outbox["user_id"] == OWNER and outbox["status"] == "pending"
    assert outbox["payload"]["custom_data"]["value"] == 75
    assert outbox["payload"]["custom_data"]["content_ids"] == ["newer-sku"]
    assert outbox["payload"]["user_data"]["em"] == hashlib.sha256(b"newer@example.test").hexdigest()
    assert await db[capi.OUTBOX_COLLECTION].count_documents({}) == 1
    audit = await db.salla_webhook_event_captures.find_one({})
    assert audit["snapchat_capi"]["recovered_from_current_source"] is True
    assert audit["processing_retry_required"] is False
    await inbox.enqueue_verified_event(db, event(), now=job["next_attempt_at"])
    assert (await inbox.run_once(db, now=job["next_attempt_at"], limit=1))["claimed"] == 0
    assert core.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_exists", [False, True])
async def test_newer_cancellation_never_revives_purchase_and_cancels_pending(
    isolated, monkeypatch, pending_exists,
):
    db, _ = isolated
    job = await fail_first_outbox_write(db, monkeypatch)
    if pending_exists:
        assert (await capi.enqueue_snapchat_purchase_from_salla_event(db, event()))["queued"] is True
    cancelled = raw_order(status="cancelled")
    cancelled["updated_at"] = (NOW - timedelta(minutes=5)).isoformat()
    await set_current(db, cancelled)
    second = await inbox.run_once(db, now=job["next_attempt_at"], limit=1)
    assert second["done"] == 1 and second["retry"] == 0, second
    audit = await db.salla_webhook_event_captures.find_one({})
    assert audit["snapchat_capi"]["reason"] == "order_ineligible"
    assert audit["snapchat_capi"]["cancelled_pending"] == int(pending_exists)
    assert audit["processing_retry_required"] is False
    rows = await db[capi.OUTBOX_COLLECTION].find({}).to_list(10)
    assert len(rows) == int(pending_exists)
    if rows:
        assert rows[0]["status"] == "cancelled" and rows[0]["payload"] is None


@pytest.mark.asyncio
async def test_newer_light_status_cancellation_overrides_older_active_raw(isolated, monkeypatch):
    db, _ = isolated
    job = await fail_first_outbox_write(db, monkeypatch)
    await set_current(db, raw_order(), order_status_slug="cancelled",
                      last_salla_direct_status_source_updated_at=(NOW - timedelta(minutes=1)).isoformat())
    second = await inbox.run_once(db, now=job["next_attempt_at"], limit=1)
    assert second["done"] == 1, second
    assert await db[capi.OUTBOX_COLLECTION].count_documents({}) == 0
    audit = await db.salla_webhook_event_captures.find_one({})
    assert audit["snapchat_capi"]["reason"] == "order_ineligible"


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["missing", "foreign_owner", "wrong_reference", "no_raw", "invalid_raw"])
async def test_unproven_current_source_keeps_failed_stage_retryable(isolated, monkeypatch, condition):
    db, _ = isolated
    job = await fail_first_outbox_write(db, monkeypatch)
    if condition != "missing":
        current = {"user_id": OWNER, "order_number": REFERENCE,
                   "raw_by_source": {"salla_direct": raw_order()}}
        if condition == "foreign_owner":
            current["user_id"] = "unrelated-owner"
        elif condition == "wrong_reference":
            current["raw_by_source"]["salla_direct"]["reference_id"] = "unrelated-order"
        elif condition == "no_raw":
            current.pop("raw_by_source")
        else:
            current["raw_by_source"]["salla_direct"] = "not-an-order"
        await db.unified_orders.insert_one(current)
    second = await inbox.run_once(db, now=job["next_attempt_at"], limit=1)
    assert second["retry"] == 1 and second["done"] == 0, second
    saved_job = await db[inbox.COLLECTION].find_one({"_id": job["_id"]})
    assert saved_job["state"] == "retry" and saved_job["attempts"] == 2
    assert saved_job["payload_ciphertext"]
    audit = await db.salla_webhook_event_captures.find_one({})
    assert audit["snapchat_capi"]["reason"] == "enqueue_failed"
    assert audit["processing_retry_required"] is True
    assert await db[capi.OUTBOX_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_ordinary_stale_event_without_prior_outbox_failure_is_noop(isolated, monkeypatch):
    db, _ = isolated
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook",
                        AsyncMock(return_value={"synced": False, "reason": "stale_salla_snapshot"}))
    await set_current(db, raw_order(amount=75))
    await inbox.enqueue_verified_event(db, event(), now=NOW)
    result = await inbox.run_once(db, now=NOW, limit=1)
    assert result["done"] == 1 and result["retry"] == 0, result
    assert await db[capi.OUTBOX_COLLECTION].count_documents({}) == 0
    audit = await db.salla_webhook_event_captures.find_one({})
    assert audit["snapchat_capi"]["reason"] == "order_not_synced"


@pytest.mark.asyncio
async def test_optional_shadow_outage_is_audited_without_core_retry(isolated, monkeypatch):
    db, core = isolated
    monkeypatch.setattr("salla_orders_v3.worker.shadow_enabled", lambda: True)
    shadow = AsyncMock(side_effect=RuntimeError("synthetic private shadow error"))
    monkeypatch.setattr("salla_orders_v3.ingestion.capture_verified_order_event", shadow)
    await inbox.enqueue_verified_event(db, event(), now=NOW)
    result = await inbox.run_once(db, now=NOW, limit=1)
    assert result["done"] == 1 and result["retry"] == 0, result
    audit = await db.salla_webhook_event_captures.find_one({})
    assert audit["orders_v3_shadow"]["reason"] == "shadow_capture_failed"
    assert audit["orders_v3_shadow"]["error_type"] == "RuntimeError"
    assert audit["processing_retry_required"] is False
    assert "synthetic private shadow error" not in repr(audit)
    assert await db[capi.OUTBOX_COLLECTION].count_documents({"status": "pending"}) == 1
    assert (await inbox.run_once(db, now=NOW, limit=1))["claimed"] == 0
    core.assert_awaited_once()
    shadow.assert_awaited_once()
