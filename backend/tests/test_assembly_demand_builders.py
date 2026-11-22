"""Assembly pages read saved generations; the job retains current board policy."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
import httpx
from mongomock_motor import AsyncMongoMockClient
import pytest

import assembly_board_read_models as models
import demand_read_store as store
import preparation_piece_operations as pieces
from order_engine.repository import MongoAssemblyBoardRepository, MongoOrderRepository
from order_engine.service import get_orders
from test_fulfillment_read_performance import seed_queue
from test_assembly_read_diagnostics import ReadOnlyObservedDB
from test_order_engine_mapper import salla_order_payload


async def database(name):
    db = AsyncMongoMockClient()[name]
    await db.users.insert_one({"id": "owner", "role": "owner"})
    return db


@pytest.mark.asyncio
async def test_one_worker_materialization_preserves_all_pages_and_fresh_canonical_membership(monkeypatch):
    db = await database("assembly_worker_policy")
    numbers = await seed_queue(db, "in_progress", 205)
    await seed_queue(db, "in_progress", 3, owner="another-owner")
    await db.unified_orders.update_many({}, {"$set": {"raw_by_source.salla_direct.timeline": [
        {"status": "cancelled", "message": "Historical display text is not current status"},
    ]}})
    source = ReadOnlyObservedDB(db)
    spec = await models.make_spec(source, user={"id": "owner"}, state="in_progress")
    assert await models.authorize_spec(source, spec)
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    result = await models.build_projection(source, spec, AsyncMock())
    # Use the original full-payload reader as the independent board oracle.
    with monkeypatch.context() as original:
        original.setattr(pieces, "MongoAssemblyBoardRepository", MongoOrderRepository)
        canonical = await pieces._assembly_order_board(source, user_id="owner", state="in_progress", limit=20, offset=30)
    assert result["header"]["total"] == 205
    assert result["rows"][30:50] == canonical["items"]
    assert [row["order_number"] for row in result["rows"]] == numbers
    assert set(result["header"]) == set(canonical) - {"items", "limit", "offset"}
    assert before == {name: await db[name].find({}).to_list(None) for name in before}
    await db.unified_orders.update_many({"user_id": "owner"}, {"$set": {"order_status": "cancelled",
        "raw_by_source.salla_direct.status": "cancelled"}})
    refreshed = await models.build_projection(source, spec, AsyncMock())
    assert refreshed["header"]["total"] == 0 and refreshed["rows"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_fields", [
    {},
    {"payment_method": "cod", "status": "under_review", "payment_actions": {
        "remaining_action": {"has_remaining_amount": False, "remaining_amount": None}}},
    {"payment_actions": {"remaining_action": {"has_remaining_amount": False, "paid_amount": 135}},
        "remaining_amount": 50, "payment_collection_status": "partial"},
    {"status": "cancelled", "cancelled_at": "2026-11-01T09:00:00Z"},
    {"status": "completed", "completed_at": "2026-11-01T09:00:00Z",
        "updated_at": "2026-11-01T09:30:00Z"},
])
async def test_assembly_projection_preserves_every_canonical_fact_except_unused_timeline(salla_order_payload, payment_fields):
    db = await database("assembly_canonical_projection")
    raw = {**salla_order_payload, **payment_fields, "timeline": [{"event": "synthetic detail history"}]}
    raw["metadata"] = {"source": "snapchat", "campaign": {"id": "synthetic-campaign"},
                       "unrelated": {"preserved": True}}
    raw["shipments"][0]["history"] = [{"status": "created", "id": "synthetic-shipment-history"}]
    document = {"user_id": "owner", "order_number": raw["reference_id"],
        "order_date": "2026-11-01T08:00:00Z", "order_status": "in_progress",
        "shipping_company": "Synthetic stored carrier", "shipping_city": "Synthetic stored city",
        "raw_by_source": {"salla_direct": raw}}
    await db.unified_orders.insert_one(document)
    before = await db.unified_orders.find_one({"user_id": "owner"})
    params = {"user_id": "owner", "order_numbers": [raw["reference_id"]]}
    full_rows = await MongoOrderRepository(db).get_salla_orders(**params)
    board_rows = await MongoAssemblyBoardRepository(db).get_salla_orders(**params)
    full_raw = deepcopy(full_rows[0].salla_raw)
    full_raw.pop("timeline")
    assert board_rows[0].salla_raw == full_raw
    assert board_rows[0].native_payment_source == full_rows[0].native_payment_source
    full = (await get_orders(MongoOrderRepository(db), **params))[raw["reference_id"]]
    board = (await get_orders(MongoAssemblyBoardRepository(db), **params))[raw["reference_id"]]
    assert board.model_dump(exclude={"timeline"}) == full.model_dump(exclude={"timeline"})
    assert board.timeline == [] and full.timeline == raw["timeline"]
    assert await db.unified_orders.find_one({"user_id": "owner"}) == before


@pytest.mark.asyncio
async def test_existing_and_opt_in_http_pages_do_not_materialize_board_or_include_page_in_key(monkeypatch):
    db = await database("assembly_saved_pages")
    app = FastAPI()
    async def user():
        return {"id": "owner", "role": "owner"}
    app.include_router(pieces.make_preparation_piece_operations_router(db, user))
    heavy = AsyncMock(side_effect=AssertionError("HTTP hydrated assembly"))
    monkeypatch.setattr(pieces, "_assembly_order_board", heavy)
    seen = []
    saved = {"header": {"state": "in_progress", "total": 205, "group_counts": {}},
             "total": 205, "snapshot": "saved-generation", "generated_at": datetime.now(timezone.utc)}
    async def demand(database, spec, consumer_id, **options):
        seen.append(deepcopy(spec))
        return {"status": "ready", "key": store.projection_key(spec)}
    async def read_ready(database, spec, *, offset, limit, snapshot):
        return {**saved, "items": [{"order_number": str(index)} for index in range(offset, offset + limit)]}
    release = AsyncMock()
    monkeypatch.setattr(store, "demand", demand)
    monkeypatch.setattr(store, "read_ready", read_ready)
    monkeypatch.setattr(store, "release", release)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        original = await client.get("/preparation-work-v1/assembly/orders", params={"state": "in_progress", "limit": 20, "offset": 30})
        opt_in = await client.get("/preparation-work-v1/assembly/orders", params={"state": "in_progress", "limit": 5, "offset": 100, "demand": True},
                                  headers={"X-Mezan-Read-Consumer": "synthetic-reader-123"})
    assert original.status_code == opt_in.status_code == 200
    assert original.json()["items"][0]["order_number"] == "30"
    assert opt_in.json()["items"][0]["order_number"] == "100"
    assert original.json()["total"] == opt_in.json()["total"] == 205
    assert original.json()["snapshot"] == opt_in.json()["snapshot"] == "saved-generation"
    assert seen[0] == seen[1]
    assert "limit" not in seen[0]["request"]["query"] and "offset" not in seen[0]["request"]["query"]
    heavy.assert_not_awaited()
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_cold_compatible_read_keeps_original_contract_while_request_is_alive(monkeypatch):
    db = await database("assembly_cold_deadline")
    clock = iter([0, 20, 40])
    monkeypatch.setattr(models, "time", SimpleNamespace(monotonic=lambda: next(clock, 40)))
    demand = AsyncMock(return_value={"status": "preparing", "key": "a" * 64})
    monkeypatch.setattr(store, "demand", demand)
    monkeypatch.setattr(store, "read_ready", AsyncMock(side_effect=[None, None, {
        "header": {"state": "in_progress", "total": 1}, "items": [{"order_number": "1000"}],
        "snapshot": "ready-generation", "generated_at": datetime.now(timezone.utc),
    }]))
    monkeypatch.setattr(models.asyncio, "sleep", AsyncMock())
    release = AsyncMock()
    monkeypatch.setattr(store, "release", release)
    response = await models.demand_response(db, user={"id": "owner"}, state="in_progress", query="", offset=0,
        limit=20, consumer_id=None, compatible=True)
    assert response.status_code == 200
    assert demand.await_count == 3
    assert len({call.args[2] for call in demand.await_args_list}) == 1
    release.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [True, False])
async def test_compatible_request_departure_releases_its_only_interest(monkeypatch, disconnect):
    db = await database("assembly_request_departure")
    monkeypatch.setattr(store, "demand", AsyncMock(return_value={"status": "preparing", "key": "a" * 64}))
    monkeypatch.setattr(store, "read_ready", AsyncMock(return_value=None))
    if not disconnect:
        monkeypatch.setattr(models.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))
    release = AsyncMock()
    monkeypatch.setattr(store, "release", release)
    request = type("Request", (), {"is_disconnected": AsyncMock(return_value=disconnect)})()
    with pytest.raises(HTTPException if disconnect else asyncio.CancelledError) as pending:
        await models.demand_response(db, user={"id": "owner"}, state="in_progress", query="", offset=0,
            limit=20, consumer_id=None, compatible=True, request=request)
    if disconnect:
        assert pending.value.status_code == 499
        assert pending.value.detail["code"] == "read_consumer_disconnected"
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_employee_authority_is_rebuilt_and_revocation_stops_saved_read():
    db = await database("assembly_live_native_grant")
    await db.users.insert_one({"id": "staff", "role": "employee", "created_by": "owner"})
    await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": "owner", "user_id": "staff",
        "enabled": True, "permissions": ["app.page.assembly_shipping"]})
    principal = {"id": "owner", "role": "owner", "_mobile_actor_id": "staff", "_session_client": "amasi_mobile"}
    spec = await models.make_spec(db, user=principal, state="in_progress")
    assert spec["actor_id"] == "staff" and spec["owner_id"] == "owner"
    assert await models.authorize_spec(db, spec)
    await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$set": {"permissions": []}})
    with pytest.raises(HTTPException) as denied:
        await models.authorize_spec(db, spec)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_saved_display_never_authorizes_mutation_or_another_tenant():
    db = await database("assembly_spec_scope")
    spec = await models.make_spec(db, user={"id": "owner"}, state="completed")
    forged = {**spec, "request": {**spec["request"], "method": "POST"}}
    with pytest.raises(HTTPException):
        await models.authorize_spec(db, forged)
    with pytest.raises(HTTPException):
        await models.authorize_spec(db, {**spec, "owner_id": "another-owner"})
