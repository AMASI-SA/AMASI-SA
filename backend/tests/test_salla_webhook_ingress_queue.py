"""Public verified ingress acknowledges durable work without running it inline.

The HTTP router, signature verification, queue persistence and queue consumer are
real. Provider/business collaborators are blocked only in responsiveness tests;
the paused-owner integration suite covers canonical transactions on real Mongo.
No server bootstrap, live credentials, provider requests or production database.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
import sys
import types
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockCollection
import pytest
import pytest_asyncio

import customer_identity
from salla_integration import easy_mode_webhook as easy
from salla_integration import webhook_event_capture as capture
from salla_integration.routes import attach_salla_routes


SECRET = "synthetic-queue-http-signature-key"


def event(number="order-a", *, name="order.created", merchant="synthetic-store"):
    return {
        "event": name,
        "merchant": merchant,
        "created_at": "2026-01-01T00:00:01Z",
        "data": {
            "id": "native-" + number,
            "reference_id": number,
            "updated_at": "2026-01-01T00:00:01Z",
            "status": {"slug": "processing"},
            "customer": {"name": "Synthetic customer", "email": "test@example.test"},
            "items": [{"id": "line-a", "product_id": "product-a", "quantity": 1,
                       "options": [{"name": "Size", "value": "Synthetic size"}]}],
        },
    }


@pytest_asyncio.fixture
async def harness(monkeypatch):
    monkeypatch.setenv("SALLA_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SALLA_ORDERS_V3_SHADOW_ENABLED", "false")
    monkeypatch.setenv("MEZAN_SNAPCHAT_CAPI_ENABLED", "false")
    monkeypatch.setattr(customer_identity, "_fernet", None)
    # mongomock_motor otherwise returns a synchronous collection from this
    # accessor. Real-Mongo tests exercise the requested durability concern.
    monkeypatch.setattr(AsyncMongoMockCollection, "with_options",
                        lambda self, **options: self, raising=False)
    db = AsyncMongoMockClient(tz_aware=True).db
    await db.salla_integrations.insert_one({"store_id": "synthetic-store", "user_id": "owner"})
    fake_server = types.ModuleType("server")

    async def current_user():
        raise AssertionError("Verified ingress must not depend on browser authentication")

    fake_server.current_user = current_user
    # Only the route factory's unrelated authenticated-route dependency is fake.
    with monkeypatch.context() as context:
        context.setitem(sys.modules, "server", fake_server)
        router = APIRouter()
        attach_salla_routes(router, db)
    app = FastAPI()
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://isolated.test") as client:
        yield db, client


async def post(client, body=None, *, raw=None, strategy="Signature", valid=True):
    if raw is None:
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    headers = {"content-type": "application/json", "x-salla-security-strategy": strategy}
    if strategy == "Token":
        headers["x-salla-token"] = SECRET if valid else "invalid"
    else:
        headers["x-salla-signature"] = (
            hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest() if valid else "0" * 64
        )
    return await client.post("/salla/webhooks/app", content=raw, headers=headers)


@pytest.mark.asyncio
async def test_business_processing_cannot_hold_verified_http_ack(harness, monkeypatch):
    db, client = harness
    release = asyncio.Event()
    heavy_started = asyncio.Event()

    async def blocked_business(*args, **kwargs):
        heavy_started.set()
        await release.wait()
        return {"attempted": True, "synced": True}

    monkeypatch.setattr(capture, "sync_order_from_verified_webhook", blocked_business)
    request = asyncio.create_task(post(client, event()))
    try:
        done, _ = await asyncio.wait({request}, timeout=0.25)
        assert request in done, "HTTP acknowledgement waited for unfinished business processing"
        response = request.result()
        assert response.status_code == 200, response.text
        assert response.json()["queued"] is True
        assert response.json()["stored"] is True
        assert not heavy_started.is_set(), "Business processing started in the HTTP process"
        assert await db.unified_orders.count_documents({}) == 0
        assert await db.salla_webhook_inbox.count_documents({}) == 1
    finally:
        release.set()
        await request


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"[]", b"null", b'"text"', b"{broken"])
async def test_verified_non_object_or_invalid_json_is_rejected(harness, raw):
    db, client = harness
    response = await post(client, raw=raw)
    assert response.status_code == 400, response.text
    assert await db.salla_webhook_inbox.count_documents({}) == 0
    assert await db.salla_webhook_event_captures.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["Signature", "Token"])
async def test_invalid_credentials_fail_before_any_persistence(harness, strategy):
    db, client = harness
    response = await post(client, event(), strategy=strategy, valid=False)
    assert response.status_code == 401, response.text
    assert await db.salla_webhook_inbox.count_documents({}) == 0
    assert await db.salla_webhook_event_captures.count_documents({}) == 0


@pytest.mark.asyncio
async def test_absent_webhook_secret_never_accepts_work(harness, monkeypatch):
    db, client = harness
    monkeypatch.delenv("SALLA_WEBHOOK_SECRET")
    response = await post(client, event())
    assert response.status_code == 503, response.text
    assert await db.salla_webhook_inbox.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("name", (
    "app.store.authorize", "app.installed", "app.updated", "app.uninstalled",
))
async def test_lifecycle_events_keep_existing_direct_dispatch(harness, monkeypatch, name):
    db, client = harness
    handler = AsyncMock(return_value={"ok": True, "stored": True, "event": name})
    monkeypatch.setattr(easy, easy.EVENT_HANDLERS[name], handler)
    payload = event(name=name)
    response = await post(client, payload)
    assert response.status_code == 200, response.text
    handler.assert_awaited_once_with(db, payload)
    assert await db.salla_webhook_inbox.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("event", ""), ("event", "order created"), ("event", []),
    ("merchant", None), ("merchant", True), ("merchant", {}), ("merchant", ""),
])
async def test_invalid_event_metadata_is_400_without_persistence(harness, field, value):
    db, client = harness
    payload = event()
    payload[field] = value
    response = await post(client, payload)
    assert response.status_code == 400, response.text
    assert await db.salla_webhook_inbox.count_documents({}) == 0
    assert await db.salla_webhook_event_captures.count_documents({}) == 0


@pytest.mark.asyncio
async def test_oversized_verified_event_is_413_without_persistence(harness, monkeypatch):
    from salla_integration import webhook_inbox as inbox

    db, client = harness
    monkeypatch.setattr(inbox, "MAX_PAYLOAD_BYTES", 1024)
    payload = event()
    payload["data"]["description"] = "x" * 1024
    response = await post(client, payload)
    assert response.status_code == 413, response.text
    assert response.json()["detail"]["code"] == "salla_webhook_payload_too_large"
    assert await db.salla_webhook_inbox.count_documents({}) == 0


@pytest.mark.asyncio
async def test_ack_waits_for_durable_save_and_retains_only_encrypted_payload(harness, monkeypatch):
    db, client = harness
    entered = asyncio.Event()
    saved = asyncio.Event()
    collection = db.salla_webhook_inbox
    collection_type = type(collection)
    persist = collection_type.update_one

    async def delayed_save(scoped, *args, **kwargs):
        if scoped.name == collection.name:
            entered.set()
            await saved.wait()
        return await persist(scoped, *args, **kwargs)

    # Motor collection access creates wrappers, so intercept the class method
    # only for this collection instead of patching one temporary wrapper.
    monkeypatch.setattr(collection_type, "update_one", delayed_save)
    payload = event()
    request = asyncio.create_task(post(client, payload))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not request.done(), "200 must not precede durable storage"
        assert await collection.count_documents({}) == 0
        saved.set()
        response = await request
        assert response.status_code == 200, response.text
        assert response.json()["queued"] is True
        row = await collection.find_one({})
        assert row is not None
        assert customer_identity.decrypt_private_payload(row["payload_ciphertext"]) == payload
        persisted = json.dumps(row, default=str)
        assert "Synthetic customer" not in persisted
        assert "test@example.test" not in persisted
        assert "Synthetic size" not in persisted
    finally:
        saved.set()
        await request


@pytest.mark.asyncio
async def test_storage_failure_returns_retryable_503_without_false_success(harness, monkeypatch):
    from pymongo.errors import AutoReconnect

    db, client = harness
    collection_type = type(db.salla_webhook_inbox)
    persist = collection_type.update_one

    async def unavailable(scoped, *args, **kwargs):
        if scoped.name == "salla_webhook_inbox":
            raise AutoReconnect("synthetic unavailable private token")
        return await persist(scoped, *args, **kwargs)

    monkeypatch.setattr(collection_type, "update_one", unavailable)
    response = await post(client, event())
    assert response.status_code == 503, response.text
    assert "synthetic unavailable private token" not in response.text
    assert await db.salla_webhook_inbox.count_documents({}) == 0
    assert await db.unified_orders.count_documents({}) == 0


@pytest.mark.asyncio
async def test_token_strategy_persists_business_event_without_processing(harness, monkeypatch):
    db, client = harness
    business = AsyncMock(side_effect=AssertionError("HTTP must never execute order work"))
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook", business)
    response = await post(client, event(), strategy="Token")
    assert response.status_code == 200, response.text
    assert response.json()["queued"] is True
    business.assert_not_awaited()
    assert await db.salla_webhook_inbox.count_documents({}) == 1


@pytest.mark.asyncio
async def test_burst_and_health_remain_responsive_while_worker_waits_then_all_work_finishes(harness):
    from salla_integration import webhook_inbox as inbox

    db, client = harness
    first = await post(client, event("burst-0"))
    assert first.status_code == 200, first.text
    entered = asyncio.Event()
    release = asyncio.Event()
    processed = []

    async def process(scoped, payload):
        assert scoped is db
        processed.append(payload["data"]["reference_id"])
        if len(processed) == 1:
            entered.set()
            await release.wait()
        return {"ok": True}

    worker = asyncio.create_task(inbox.run_once(db, limit=10, processor=process,
                                              processing_timeout=5))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        # 150 deliveries of 50 logical events, including the in-flight event.
        payloads = [event(f"burst-{i}") for i in range(50)]
        requests = [post(client, payloads[i % 50]) for i in range(150)]
        responses, healthy = await asyncio.wait_for(
            asyncio.gather(asyncio.gather(*requests), client.get("/health")), timeout=2
        )
        assert healthy.status_code == 200 and healthy.json() == {"ok": True}
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json()["queued"] for response in responses)
        assert processed == ["burst-0"], "Ingress must never start additional work"
        assert await db[inbox.COLLECTION].count_documents({}) == 50
        release.set()
        first_batch = await worker
        assert first_batch["done"] == 10, first_batch
        for _ in range(4):
            batch = await inbox.run_once(db, limit=10, processor=process)
            assert batch["done"] == 10, batch
        assert sorted(processed) == sorted(f"burst-{i}" for i in range(50))
        assert await db[inbox.COLLECTION].count_documents({"state": "done"}) == 50
        assert await db[inbox.COLLECTION].count_documents({"state": {"$ne": "done"}}) == 0
        # Redelivery after completion increments audit delivery evidence only.
        repeats = await asyncio.gather(*(post(client, payloads[i]) for i in range(10)))
        assert all(response.status_code == 200 for response in repeats)
        no_work = await inbox.run_once(db, limit=10, processor=process)
        assert no_work["claimed"] == 0, no_work
        assert len(processed) == 50
    finally:
        release.set()
        await worker


@pytest.mark.asyncio
async def test_distinct_updates_for_same_order_are_not_deduplicated(harness):
    from salla_integration import webhook_inbox as inbox

    db, client = harness
    created = event("same-order")
    status = deepcopy(created)
    status["event"] = "order.status.updated"
    status["data"]["status"] = {"slug": "in_progress"}
    options = deepcopy(created)
    options["event"] = "order.updated"
    options["data"]["items"][0]["options"] = [{"name": "Size", "value": "New choice"}]
    later = deepcopy(options)
    later["data"]["updated_at"] = "2026-01-01T00:00:02Z"
    seen = []

    async def process(_, payload):
        seen.append(payload)
        return {"ok": True}

    payloads = [created, status, options, later]
    for payload in payloads * 2:
        response = await post(client, payload)
        assert response.status_code == 200, response.text
    assert await db[inbox.COLLECTION].count_documents({}) == 4
    stats = await inbox.run_once(db, limit=10, processor=process)
    assert stats["done"] == 4, stats
    assert {json.dumps(value, sort_keys=True) for value in seen} == {
        json.dumps(value, sort_keys=True) for value in payloads
    }


@pytest.mark.asyncio
async def test_delayed_creation_options_survive_queue_without_regressing_newer_order(harness, monkeypatch):
    from salla_integration import webhook_inbox as inbox
    from tests import test_salla_stale_creation_options as choices

    db, client = harness
    before, incoming = choices.fixture()
    await db.salla_integrations.update_one({"store_id": "synthetic-store"},
                                          {"$set": {"user_id": choices.OWNER}})
    await db.unified_orders.insert_one(deepcopy(before))
    module, provider, full_upsert = choices.install_fence(monkeypatch)
    payload = {"event": "order.created", "merchant": "synthetic-store", "data": incoming}
    response = await post(client, payload)
    assert response.status_code == 200, response.text
    assert response.json()["queued"] is True
    unchanged = await choices.read_order(db)
    assert unchanged == before, "Ingress must not write the canonical order"
    stats = await inbox.run_once(db, limit=1)
    assert stats["done"] == 1, stats
    after = await choices.read_order(db)
    assert after["order_status_slug"] == before["order_status_slug"]
    assert after["raw_by_source"]["salla_direct"]["updated_at"] == choices.NEWER
    for index, product in enumerate(after["products"]):
        assert product["options_raw"] == incoming["items"][index]["options"]
        assert product["order_item_id"] == before["products"][index]["order_item_id"]
    saved = await db.salla_webhook_event_captures.find_one({})
    assert saved["order_mutation_scope"] == "customer_options_only"
    assert saved["order_sync"]["synced"] is False
    assert saved["order_sync"]["customer_options_enrichment"]["applied"] is True
    repeated = await post(client, payload)
    assert repeated.status_code == 200, repeated.text
    no_work = await inbox.run_once(db, limit=1)
    assert no_work["claimed"] == 0, no_work
    assert await choices.read_order(db) == after
    provider.assert_not_awaited()
    full_upsert.assert_not_awaited()
    module.auto_route_instant_order.assert_not_awaited()
