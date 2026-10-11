"""Capability-only race tests. Parser acceptance is stubbed here, tested separately on Linux."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
import fulfillment_v2_routes as routes
import shipping_print_document as documents
import shipping_pdf_sandbox as sandbox
import shipping_capability_security as security
from order_engine import shipping_label_service as shipping
from test_shipping_phase1_policy import scenario, OWNER, NUMBER
from test_shipping_print_document import pdf_bytes

VERIFY = documents.verify_and_store


@pytest.fixture
async def capability_client(scenario, monkeypatch):
    db, state = scenario
    monkeypatch.setattr(documents, "verify_and_store", VERIFY)
    monkeypatch.setattr(documents, "_download", AsyncMock(return_value=pdf_bytes("AWB-1")))
    monkeypatch.setattr(sandbox, "verify_pdf", AsyncMock(return_value=None))
    async def user():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(routes.make_fulfillment_v2_router(db, user), prefix="/api")
    app.add_middleware(security.CapabilityResponseHeadersMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://mezansalla.com") as client:
        response = await client.post(f"/api/fulfillment-v2/completed/{NUMBER}/carrier-label/refresh")
        assert response.status_code == 200, response.text
        yield db, state, client, response.json()["label_url"]


@pytest.mark.asyncio
async def test_expiry_during_final_owner_fence_cannot_serve_pdf(capability_client, monkeypatch):
    db, state, client, url = capability_client
    original = shipping._assert_current_print_status
    expiry = security.assert_document_unexpired
    final_fences = 0
    clock = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(security, "assert_document_unexpired", lambda document: expiry(document, now=clock["now"]))
    async def fence(*args, **kwargs):
        nonlocal final_fences
        result = await original(*args, **kwargs)
        if kwargs.get("expected") is not None:
            final_fences += 1
            if final_fences == 2:
                clock["now"] = datetime.now(timezone.utc) + timedelta(seconds=301)
        return result
    monkeypatch.setattr(shipping, "_assert_current_print_status", fence)
    result = await client.get(url)
    assert final_fences == 2
    assert result.status_code == 410
    assert result.json()["detail"]["reason_code"] == "shipping_document_expired"
    assert result.headers["cache-control"] == "no-store, private"
    assert not (await db.order_review_workflows.find_one({"user_id": OWNER}))["carrier_label_ready"]


@pytest.mark.asyncio
async def test_duplicate_download_does_not_duplicate_provider_reads_or_revoke_first(capability_client, monkeypatch):
    db, state, client, url = capability_client
    entered, release = asyncio.Event(), asyncio.Event()
    resolve = shipping._resolve_order
    async def held(*args):
        entered.set()
        await asyncio.wait_for(release.wait(), 3)
        return await resolve(*args)
    monkeypatch.setattr(shipping, "_resolve_order", held)
    first = asyncio.create_task(client.get(url))
    await asyncio.wait_for(entered.wait(), 3)
    before = len(state["calls"])
    second = await client.get(url)
    assert second.status_code == 429
    assert len(state["calls"]) == before
    release.set()
    result = await first
    assert result.status_code == 200, result.text
    assert result.content.startswith(b"%PDF-")
    assert (await db[documents.COLLECTION].find_one({}))["read_attempts"] == 1
    assert all(method == "GET" for method, _ in state["calls"])


@pytest.mark.asyncio
async def test_download_fetches_payload_once_after_metadata_and_admission(capability_client, monkeypatch):
    db, state, client, url = capability_client
    original = documents.load_document
    modes = []

    async def load(*args, **kwargs):
        modes.append(kwargs.get("metadata_only", False))
        row = await original(*args, **kwargs)
        if kwargs.get("metadata_only"):
            assert "bytes" not in row
        return row

    monkeypatch.setattr(documents, "load_document", load)
    response = await client.get(url)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert modes == [True, False]
    assert all(method == "GET" for method, _ in state["calls"])


@pytest.mark.asyncio
async def test_storage_timeout_never_publishes_success(scenario, monkeypatch):
    db, state = scenario
    monkeypatch.setattr(documents, "verify_and_store", VERIFY)
    monkeypatch.setattr(documents, "_download", AsyncMock(return_value=pdf_bytes("AWB-1")))
    monkeypatch.setattr(sandbox, "verify_pdf", AsyncMock())
    original = sandbox.retained_io

    async def timeout(coroutine, seconds=None):
        if seconds == documents.STORE_SECONDS:
            coroutine.close()
            raise TimeoutError()
        return await original(coroutine, seconds)

    monkeypatch.setattr(sandbox, "retained_io", timeout)
    async def user():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(routes.make_fulfillment_v2_router(db, user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://mezansalla.com") as client:
        response = await client.post(f"/api/fulfillment-v2/completed/{NUMBER}/carrier-label/refresh")
    assert response.status_code == 503
    assert response.json()["detail"]["reason_code"] == "shipping_document_store_timeout"
    assert not (await db.order_review_workflows.find_one({"user_id": OWNER}))["carrier_label_ready"]
    assert await db[documents.COLLECTION].count_documents({}) == 0
    assert all(method == "GET" for method, _ in state["calls"])
