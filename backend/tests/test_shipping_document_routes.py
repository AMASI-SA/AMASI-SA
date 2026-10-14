"""Build44's existing refresh -> unauthenticated label URL download contract."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import fulfillment_v2_routes as routes
import shipping_print_document as documents
from test_shipping_phase1_policy import scenario, OWNER, NUMBER
from test_shipping_print_document import pdf_bytes

VERIFY = documents.verify_and_store


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier,code", [("SMSA", "smsa"), ("iMile", "imile")])
async def test_build44_downloads_same_verified_pdf_and_rechecks_current_order(scenario, monkeypatch, carrier, code):
    db, state = scenario
    from salla_shipping import CURRENT_SHIPPING
    state["shipment"].update(courier_name=carrier, courier_id=code)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        f"{CURRENT_SHIPPING}.company_name": carrier, f"{CURRENT_SHIPPING}.company_code": code}})
    blob = pdf_bytes("AWB-1")
    download = AsyncMock(return_value=blob)
    monkeypatch.setattr(documents, "verify_and_store", VERIFY)
    monkeypatch.setattr(documents, "_download", download)
    async def user():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(routes.make_fulfillment_v2_router(db, user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://mezansalla.com") as client:
        result = await client.post(f"/api/fulfillment-v2/completed/{NUMBER}/carrier-label/refresh")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["ready"] and body["assembly_completion_confirmed"] and body["order_status_completed"]
        url = body["label_url"]
        # Source content can change after validation; Build44 receives frozen PDF.
        download.return_value = b"<html>login</html>"
        printed = await client.get(url)
        assert printed.status_code == 200, printed.text
        assert printed.content == blob and printed.headers["content-type"] == "application/pdf"
        assert printed.headers["cache-control"] == "no-store, private"
        assert download.await_count == 1
        # Advance only admission fixtures so this assertion exercises a fresh
        # terminal provider observation, not the separate cooldown boundary.
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        await db[documents.COLLECTION].update_many({}, {"$set": {"read_available_at": past}})
        await db.shipping_document_read_slots.update_many({}, {"$set": {"available_at": past}})
        state["order"]["status"] = {"slug": "delivered"}
        rejected = await client.get(url)
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "shipping_snapshot_changed"
    assert all(method == "GET" for method, _ in state["calls"])


@pytest.mark.asyncio
async def test_expired_and_wrong_order_capability_never_reads_provider(scenario, monkeypatch):
    db, state = scenario
    monkeypatch.setattr(documents, "_download", AsyncMock(return_value=pdf_bytes("AWB-1")))
    minted = await VERIFY(db, OWNER, NUMBER, {"ready": True, "shipment_id": "shipment-1",
        "tracking_number": "AWB-1", "courier_name": "SMSA", "status": "created",
        "label_url": "https://labels.test/current.pdf"})
    async def user():
        raise AssertionError("Capability downloads must not require an Android auth change")
    app = FastAPI()
    app.include_router(routes.make_fulfillment_v2_router(db, user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://mezansalla.com") as client:
        wrong = await client.get(minted["label_url"].replace(NUMBER, "another-order"))
        assert wrong.status_code == 404
        await db[documents.COLLECTION].update_many({}, {"$set": {
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
        expired = await client.get(minted["label_url"])
        assert expired.status_code in {404, 410}  # TTL deletion and expiry both fail closed.
    assert state["calls"] == []


@pytest.mark.asyncio
async def test_html_is_rejected_by_existing_build44_refresh_contract(scenario, monkeypatch):
    db, state = scenario
    monkeypatch.setattr(documents, "verify_and_store", VERIFY)
    monkeypatch.setattr(documents, "_download", AsyncMock(return_value=b"<html>login failed</html>"))
    async def user():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(routes.make_fulfillment_v2_router(db, user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://example.test") as client:
        result = await client.post(f"/fulfillment-v2/completed/{NUMBER}/carrier-label/refresh")
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "shipping_snapshot_changed"
    assert result.json()["detail"]["reason_code"] == "shipping_document_not_pdf"
    assert not (await db.order_review_workflows.find_one({"user_id": OWNER}))["carrier_label_ready"]

@pytest.mark.asyncio
async def test_terminal_writer_during_final_blob_reload_blocks_pdf(scenario, monkeypatch):
    db, state = scenario
    monkeypatch.setattr(documents, "verify_and_store", VERIFY)
    monkeypatch.setattr(documents, "_download", AsyncMock(return_value=pdf_bytes("AWB-1")))
    async def user():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(routes.make_fulfillment_v2_router(db, user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://mezansalla.com") as client:
        result = await client.post(f"/api/fulfillment-v2/completed/{NUMBER}/carrier-label/refresh")
        assert result.status_code == 200, result.text
        original = documents.load_document
        calls = 0
        async def load(*args):
            nonlocal calls
            row = await original(*args)
            calls += 1
            if calls == 2:
                await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
                    "order_status_slug": "delivered", "order_status": "delivered",
                    "raw_by_source.salla_direct.status": {"slug": "delivered"}}})
            return row
        monkeypatch.setattr(documents, "load_document", load)
        rejected = await client.get(result.json()["label_url"])
        assert calls == 2
        assert rejected.status_code == 409
        assert rejected.headers["content-type"] == "application/json"
        assert not (await db.order_review_workflows.find_one({"user_id": OWNER}))["carrier_label_ready"]
    assert all(method == "GET" for method, _ in state["calls"])
