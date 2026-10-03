"""Refresh atomicity on an explicitly configured disposable loopback replica set."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os
from urllib.parse import urlparse
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import supplier_receiving_routes as receiving


@pytest_asyncio.fixture
async def env(monkeypatch):
    uri = os.environ.get("BUILD37_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("BUILD37_TEST_MONGO_URI must identify isolated Mongo")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    db = client["build37_refresh_test_" + uuid.uuid4().hex]
    context = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False,
               "permissions": {receiving.RECEIVE_PERMISSION}}
    async def actor(*args):
        return context
    monkeypatch.setattr(receiving, "_actor_context", actor)
    router = receiving.make_supplier_receiving_router(db, lambda: {})
    refresh = next(r.endpoint for r in router.routes if r.path.endswith("/{session_id}/refresh"))
    await db[receiving.SESSIONS].insert_one({"user_id": "owner", "id": "session-1",
        "status": "open", "opened_by": "employee", "supplier_snapshot": {"id": "supplier-1"}})
    await db[receiving.PRODUCTS].insert_one({"user_id": "owner", "id": "product-1",
        "salla_product_id": "salla-1"})
    await db[receiving.COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "salla-1",
        "base_cost": 10})
    await db[receiving.RESOURCES].insert_one({"user_id": "owner", "id": "service-1", "kind": "service",
        "name": "Tailoring", "unit_cost": 3, "status": "active", "requires_preparation": True})
    await db[receiving.PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": "owner",
        "salla_product_id": "salla-1", "resource_id": "service-1", "supplier_invoice_required": True})
    try:
        yield db, refresh, context
    finally:
        await client.drop_database(db.name)
        client.close()


async def add_scan(db, piece_id, options=None):
    piece = {"user_id": "owner", "piece_id": piece_id, "product_id": "product-1",
        "sku": "SKU-1", "supplier_receiving_session_id": "session-1", "services": [],
        "product_options_snapshot": options}
    await db[receiving.PIECES].insert_one(deepcopy(piece))
    await db[receiving.RECEIVING_EVENTS].insert_one({**piece, "event_type": "supplier_piece_scanned",
        "session_id": "session-1", "occurred_at": datetime.now(timezone.utc)})
    return piece


@pytest.mark.asyncio
async def test_refresh_empty_session(env):
    db, refresh, _ = env
    result = await refresh("session-1", {})
    assert result["ok"] and result["refreshed"] and result["scans"] == []


@pytest.mark.asyncio
async def test_refresh_failure_rolls_back_session_and_all_pieces(env):
    db, refresh, _ = env
    await add_scan(db, "piece-bad", [{"name": "invalid"}])
    good = await add_scan(db, "piece-good", {})
    before = await db[receiving.SESSIONS].find_one({"id": "session-1"})
    with pytest.raises(HTTPException) as caught:
        await refresh("session-1", {})
    assert caught.value.status_code == 422
    assert await db[receiving.PIECES].find_one({"piece_id": "piece-good"}, {"_id": 0}) == good
    assert await db[receiving.SESSIONS].find_one({"id": "session-1"}) == before


@pytest.mark.asyncio
async def test_repeated_and_concurrent_refresh_keeps_piece_identity_and_costs(env):
    db, refresh, _ = env
    await add_scan(db, "piece-1", {})
    results = await asyncio.gather(refresh("session-1", {}), refresh("session-1", {}))
    results.append(await refresh("session-1", {}))
    for result in results:
        assert [s["piece_id"] for s in result["scans"]] == ["piece-1"]
        scan = result["scans"][0]
        assert scan["reference_product_unit_price_halalas"] == 1000
        assert len(scan["invoice_services"]) == 1
        assert scan["invoice_services"][0]["reference_unit_price_halalas"] == 300
    assert await db[receiving.RECEIVING_EVENTS].count_documents({}) == 1
    assert await db[receiving.SUPPLIER_INVOICES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_other_employee_cannot_refresh(env):
    db, refresh, context = env
    context["actor_id"] = "other"
    with pytest.raises(HTTPException) as caught:
        await refresh("session-1", {})
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_closed_session_rejected_without_writes(env):
    db, refresh, _ = env
    await db[receiving.SESSIONS].update_one({"id": "session-1"}, {"$set": {"status": "closed"}})
    before = await db[receiving.SESSIONS].find_one({"id": "session-1"})
    with pytest.raises(HTTPException) as caught:
        await refresh("session-1", {})
    assert caught.value.status_code == 409
    assert await db[receiving.SESSIONS].find_one({"id": "session-1"}) == before
