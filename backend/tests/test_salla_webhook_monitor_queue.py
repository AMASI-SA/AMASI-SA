"""Owner-scoped unfinished queue visibility and redacted worker diagnostics."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import sys
import types
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockCollection
import pytest

import customer_identity
from salla_integration import webhook_inbox as inbox
from salla_integration.webhook_monitor_routes import _queue_snapshot, attach_salla_webhook_monitor_routes


AT = datetime(2026, 11, 15, tzinfo=timezone.utc)


async def queue_fixture():
    db = AsyncMongoMockClient(tz_aware=True).db
    rows = []
    for identity, state, minutes, lease_offset in (
        ("pending", "pending", 5, -300),
        ("processing-live", "processing", 4, 60),
        ("processing-expired", "processing", 4, -30),
        ("retry", "retry", 3, -180),
        ("quarantine", "quarantined", 10, -600),
        ("completed-tombstone", "done", 100000, -600),
    ):
        rows.append({"_id": identity, "state": state, "user_id": "owner-a", "merchant_id": "store-a",
            "created_at": AT - timedelta(minutes=minutes),
            "lease_until": AT + timedelta(seconds=lease_offset),
            "payload_ciphertext": b"synthetic-private-ciphertext",
            "last_error_code": "synthetic-private-error-must-not-be-returned"})
    for identity, owner, merchant in (
        ("other-owner-same-store", "owner-b", "store-a"),
        ("same-owner-other-store", "owner-a", "store-b"),
        ("unbound-owner", None, "store-a"),
    ):
        rows.append({"_id": identity, "state": "retry", "user_id": owner, "merchant_id": merchant,
            "created_at": AT - timedelta(days=2), "payload": {"email": "private@example.invalid"}})
    await db[inbox.COLLECTION].insert_many(rows)
    return db


@pytest.mark.asyncio
async def test_queue_summary_is_owner_and_merchant_scoped_with_ages_and_no_payload(monkeypatch):
    db = await queue_fixture()
    original = AsyncMongoMockCollection.aggregate
    matches = []

    def observe_query(collection, pipeline, *args, **kwargs):
        if collection.name == inbox.COLLECTION:
            matches.append(pipeline[0]["$match"])
        return original(collection, pipeline, *args, **kwargs)

    monkeypatch.setattr(AsyncMongoMockCollection, "aggregate", observe_query)
    result = await _queue_snapshot(db, user_id="owner-a", merchant_id="store-a", now=AT)
    assert matches == [{"user_id": "owner-a", "merchant_id": "store-a",
                        "state": {"$in": ["pending", "processing", "retry", "quarantined"]}}]
    assert result == {"pending": 1, "processing": 2, "retry": 1, "quarantined": 1,
        "backlog": 5, "oldest_unfinished_at": AT - timedelta(minutes=10),
        "oldest_unfinished_age_seconds": 600, "expired_processing_count": 1,
        "oldest_expired_processing_age_seconds": 30}
    assert "private" not in repr(result) and "payload" not in repr(result)
    assert "owner-b" not in repr(result) and "store-b" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("merchant", [None, ""])
async def test_no_merchant_returns_zero_without_querying_inbox(merchant):
    class NoDatabaseRead:
        def __getitem__(self, name):
            raise AssertionError("unbound monitor queried another merchant's queue")

    summary = await _queue_snapshot(NoDatabaseRead(), user_id="owner-a", merchant_id=merchant, now=AT)
    assert summary["backlog"] == summary["expired_processing_count"] == 0
    assert summary["oldest_unfinished_at"] is None
    assert all(summary[state] == 0 for state in ("pending", "processing", "retry", "quarantined"))


@pytest.mark.asyncio
async def test_real_monitor_route_uses_authenticated_identity_not_query_parameters():
    db = await queue_fixture()
    await db.salla_integrations.insert_many([
        {"user_id": "owner-a", "store_id": "store-a", "status": "connected"},
        {"user_id": "owner-b", "store_id": "store-b", "status": "connected"},
    ])

    async def current_user():
        return {"id": "owner-a", "role": "owner"}

    server = types.ModuleType("server")
    server.current_user = current_user
    router = APIRouter()
    with patch.dict(sys.modules, {"server": server}):
        attach_salla_webhook_monitor_routes(router, db)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated.test") as client:
        response = await client.get("/salla/webhook-monitor?user_id=owner-b&merchant_id=store-b")
    assert response.status_code == 200
    data = response.json()
    assert data["merchant_id"] == "store-a" and data["queue"]["backlog"] == 5
    assert data["queue"]["quarantined"] == 1
    assert "private" not in response.text and "ciphertext" not in response.text


@pytest.mark.asyncio
async def test_worker_logs_only_fixed_retry_and_quarantine_counts(monkeypatch, caplog):
    monkeypatch.setattr(AsyncMongoMockCollection, "with_options", lambda collection, **options: collection,
                        raising=False)
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(customer_identity, "_fernet", None)
    db = AsyncMongoMockClient(tz_aware=True).db
    await db.salla_integrations.insert_one({"store_id": "store-a", "user_id": "owner-a"})
    body = {"event": "order.updated", "merchant": "store-a", "data": {
        "id": "synthetic-order", "email": "synthetic-private@example.invalid"}}
    await inbox.enqueue_verified_event(db, body, now=AT)
    last_attempt = await inbox.enqueue_verified_event(db, {**body, "created_at": "later"}, now=AT)
    await db[inbox.COLLECTION].update_one({"_id": last_attempt["job_id"]},
        {"$set": {"attempts": inbox.MAX_ATTEMPTS - 1}})
    from salla_integration import easy_mode_webhook
    monkeypatch.setattr(easy_mode_webhook, "dispatch_event",
        AsyncMock(side_effect=RuntimeError("synthetic-private provider exception body")))
    with caplog.at_level(logging.WARNING, logger="salla.webhook_inbox"):
        task = await inbox.start_worker(db)
        try:
            async with asyncio.timeout(2):
                while "salla_webhook_inbox.quarantined count=1" not in caplog.text:
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    messages = [(record.levelname, record.getMessage()) for record in caplog.records
                if record.name == "salla.webhook_inbox"]
    assert messages == [("WARNING", "salla_webhook_inbox.retry_scheduled count=1"),
                        ("ERROR", "salla_webhook_inbox.quarantined count=1")]
    assert "synthetic-private" not in caplog.text
    assert "owner-a" not in caplog.text and "store-a" not in caplog.text
    assert await db[inbox.COLLECTION].count_documents({"state": "retry"}) == 1
    assert await db[inbox.COLLECTION].count_documents({"state": "quarantined"}) == 1
