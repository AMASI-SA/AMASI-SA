"""Real HTTP + isolated Mongo tests for the waiting-review/dispatch status fence."""
import os
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

import preparation_supplier_dispatch as dispatch
import supplier_dispatch_waiting_policy as policy

OWNER = {"id": "waiting-owner", "role": "owner", "name": "Synthetic owner"}
ALIASES = ["under_review", "under review", "waiting_review", "waiting review",
           "pending_review", "pending review", "بانتظار المراجعة", "بإنتظار المراجعة", "انتظار المراجعة"]
BLOCKED = ["reviewed", "تم المراجعة", "تمت المراجعة", "processing", "in_progress", "قيد التنفيذ",
           "جاري التنفيذ", "completed", "تم التنفيذ", "تم التوصيل", "shipping", "shipped",
           "جاري التوصيل", "cancelled", "ملغي", "ملغى", "refunded", "returned", "مسترجع", "", "mystery"]


class ObservedCollection:
    def __init__(self, db, collection):
        self.db, self.collection = db, collection

    def find(self, *args, **kwargs):
        self.db.reads.append((self.collection.name, deepcopy(args[0])))
        return self.collection.find(*args, **kwargs)

    def __getattr__(self, name):
        method = getattr(self.collection, name)
        if name in {"insert_one", "insert_many", "update_one", "update_many", "replace_one", "delete_one",
                    "delete_many", "find_one_and_update", "bulk_write", "create_index"}:
            async def write(*args, **kwargs):
                self.db.writes.append((self.collection.name, name))
                return await method(*args, **kwargs)
            return write
        return method


class ObservedDB:
    def __init__(self, raw):
        self.raw, self.reads, self.writes = raw, [], []

    def __getitem__(self, name):
        return ObservedCollection(self, self.raw[name])

    def __getattr__(self, name):
        return self[name]


@pytest_asyncio.fixture
async def db():
    url = os.environ.get("WAITING_TEST_MONGO_URL", "mongodb://127.0.0.1:27017")
    assert urlparse(url).hostname in {"127.0.0.1", "localhost"}, "Synthetic loopback Mongo only"
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    await client.admin.command("ping")
    raw = client["build20_waiting_test_" + uuid.uuid4().hex]
    try:
        yield ObservedDB(raw)
    finally:
        await client.drop_database(raw.name)
        client.close()


async def seed_order(db, number="A", current="under_review", raw_status=None, tenant=None):
    raw_status = current if raw_status is None else raw_status
    row = {"user_id": tenant or OWNER["id"], "order_number": number,
           "order_date": "2026-09-26T08:00:00+00:00", "order_status": current,
           "raw_by_source": {"salla_direct": {
               "id": "salla-" + number, "reference_id": number,
               "date": {"date": "2026-09-26T08:00:00+00:00"},
               "status": {"slug": raw_status, "name": raw_status},
               "customer": {"full_name": "Synthetic customer"},
               "amounts": {"total": {"amount": 10, "currency": "SAR"}},
               "items": [{"id": "line-" + number, "quantity": 1,
                          "product": {"id": "p", "name": "Synthetic product", "sku": "P-1"}}],
           }}}
    await db.raw.unified_orders.replace_one({"user_id": row["user_id"], "order_number": number}, row, upsert=True)


async def seed_piece(db, number="A", piece_id=None, **overrides):
    row = {"user_id": OWNER["id"], "piece_id": piece_id or "piece-" + number,
           "order_number": number, "order_item_id": "line-" + number, "unit_index": 1,
           "group_key": "product:P-1", "batch_id": "b1", "file_number": "PF-1",
           "responsible_employee_id": OWNER["id"], "status": "assigned",
           "product_id": "p", "product_name": "Synthetic product", "sku": "P-1",
           "services": [{"service_id": "engrave", "status": "pending"}],
           "order_status": "under_review", "waiting_review_eligible": True, **overrides}
    await db.raw[dispatch.PIECES].insert_one(deepcopy(row))
    await db.raw[dispatch.REGISTRY].update_one({"user_id": OWNER["id"], "file_number": "PF-1"}, {"$set": {
        "batch_id": "b1", "status": "ready", "execution_status": "assigned"}}, upsert=True)
    await db.raw[dispatch.MEZAN_SUPPLIERS_V2].update_one({"user_id": OWNER["id"], "id": "supplier"}, {"$set": {
        "company_name": "Synthetic supplier", "service_ids": ["engrave"], "status": "active"}}, upsert=True)
    return row


async def request(db, method="GET", payload=None, grain="piece"):
    app = FastAPI()
    app.include_router(dispatch.make_preparation_supplier_dispatch_router(db, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        if method == "GET":
            return await client.get("/supplier-dispatch-v1/workspace", params={"limit": 100, "grain": grain})
        return await client.post("/supplier-dispatch-v1/dispatches", json=payload)


def payload(*numbers):
    return {"client_request_id": "request-" + uuid.uuid4().hex, "supplier_id": "supplier",
            "file_number": "PF-1", "selections": [{"group_key": "piece:piece-" + n, "quantity": 1} for n in numbers]}


@pytest.mark.parametrize("status", ALIASES + BLOCKED)
@pytest.mark.asyncio
async def test_workspace_current_status_only_not_piece_snapshot(db, status):
    await seed_order(db, current=status)
    original = await seed_piece(db)
    reply = await request(db)
    assert reply.status_code == 200, reply.text
    work = reply.json()
    row = work["files"][0]["products"][0]
    expected = status in ALIASES
    assert row["waiting_review_eligible"] is expected
    assert row["available_quantity"] == int(expected)
    assert work["summary"]["waiting_review_pieces"] == int(expected)
    assert work["summary"]["waiting_review_products"] == int(expected)
    assert row["piece_id"] == original["piece_id"]
    assert row["order_status_native"] == (status or None)
    assert len([x for x in db.reads if x[0] == "unified_orders"]) == 1
    saved = await db.raw[dispatch.PIECES].find_one({"piece_id": original["piece_id"]}, {"_id": 0})
    assert saved == original, "Workspace must not mutate stored pieces or their history"


@pytest.mark.asyncio
async def test_missing_or_foreign_order_fails_closed(db):
    await seed_piece(db)
    await seed_order(db, tenant="other-owner")
    row = (await request(db)).json()["files"][0]["products"][0]
    assert row["waiting_review_eligible"] is False
    assert row["available_quantity"] == 0
    assert row["order_status"] is None and row["order_status_native"] is None
    query = next(q for c, q in db.reads if c == "unified_orders")
    assert query["user_id"] == OWNER["id"]


@pytest.mark.parametrize("away", ["reviewed", "processing"])
@pytest.mark.asyncio
async def test_transition_and_return_reuses_same_piece_and_assignment(db, away):
    await seed_order(db)
    before = await seed_piece(db)
    first = (await request(db)).json()
    assert first["summary"]["waiting_review_pieces"] == 1
    await seed_order(db, current=away, raw_status="under_review")
    second = (await request(db)).json()
    assert second["files"][0]["products"][0]["waiting_review_eligible"] is False
    assert second["summary"]["available_to_send"] == 0
    await seed_order(db)
    third = (await request(db)).json()
    assert third["summary"]["waiting_review_pieces"] == 1
    assert third["files"][0]["products"][0]["piece_id"] == before["piece_id"]
    assert await db.raw[dispatch.PIECES].count_documents({}) == 1
    assert await db.raw[dispatch.PIECES].find_one({"piece_id": before["piece_id"]}, {"_id": 0}) == before


@pytest.mark.parametrize("grain", ["piece", "product"])
@pytest.mark.asyncio
async def test_mixed_product_file_filtered_before_waiting_aggregation(db, grain):
    await seed_order(db, "A"); await seed_piece(db, "A")
    await seed_order(db, "B", "processing"); await seed_piece(db, "B")
    result = (await request(db, grain=grain)).json()
    file = result["files"][0]
    assert file["piece_count"] == 2 and sum(p["quantity"] for p in file["products"]) == 2
    assert file["available_quantity"] == result["summary"]["waiting_review_pieces"] == 1
    assert result["summary"]["waiting_review_products"] == 1
    assert sum(p["quantity"] for p in file["waiting_products"]) == 1
    assert file["waiting_products"][0]["order_numbers"] == ["A"]
    assert all(p["waiting_review_eligible"] is True for p in file["waiting_products"])
    assert len([x for x in db.reads if x[0] == "unified_orders"]) == 1


@pytest.mark.asyncio
async def test_operational_progress_supplier_accounts_and_history_unchanged(db):
    for n, stage in [("S", "sent"), ("R", "ready"), ("D", "received")]:
        await seed_order(db, n, "completed")
        await seed_piece(db, n, supplier_id="supplier", supplier_name="Synthetic supplier",
                         supplier_dispatch_id="dispatch-old", supplier_dispatch_status=stage,
                         sent_to_supplier_by_id=OWNER["id"])
    await db.raw[dispatch.DISPATCHES].insert_one({"id": "dispatch-old", "user_id": OWNER["id"],
                                                "sent_by_id": OWNER["id"], "supplier_id": "supplier", "status": "sent"})
    result = (await request(db)).json()
    assert result["summary"]["waiting_review_pieces"] == 0
    assert result["summary"]["sent"] == result["summary"]["ready"] == result["summary"]["received"] == 1
    account = result["supplier_accounts"][0]
    assert len(account["products"]) == 3 and len(account["dispatches"]) == 1
    assert account["sent_quantity"] == account["ready_quantity"] == account["received_quantity"] == 1


@pytest.mark.parametrize("status", ["reviewed", "processing", "completed", "cancelled", "refunded", "mystery", "", None])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.asyncio
async def test_dispatch_revalidates_and_rejects_entire_request_with_zero_writes(db, monkeypatch, status, mixed):
    await seed_order(db); await seed_piece(db)
    if mixed:
        await seed_order(db, "B"); await seed_piece(db, "B")
    assert (await request(db)).json()["summary"]["waiting_review_pieces"] == (2 if mixed else 1)
    if status is None:
        await db.raw.unified_orders.delete_one({"order_number": "A"})
    else:
        await seed_order(db, current=status, raw_status=status)
    before = {n: await db.raw[n].find({}, {"_id": 0}).to_list(None) for n in await db.raw.list_collection_names()}
    db.reads.clear(); db.writes.clear()
    calls = AsyncMock(wraps=policy.order_service.get_orders)
    monkeypatch.setattr(policy.order_service, "get_orders", calls)
    result = await request(db, "POST", payload(*( ["A", "B"] if mixed else ["A"] )))
    assert result.status_code == 409, result.text
    detail = result.json()["detail"]
    assert detail["code"] == "supplier_dispatch_order_not_under_review"
    assert detail["order_numbers"] == ["A"]
    assert detail["dispatch_created"] is False and detail["pdf_created"] is False
    assert detail["pieces_modified"] == detail["events_created"] == 0
    assert db.writes == [], "No writes (including index/dispatch/event/workflow/artifact) before the status fence"
    assert calls.await_count == 1
    assert calls.call_args.kwargs["order_numbers"] == (["A", "B"] if mixed else ["A"])
    assert len([x for x in db.reads if x[0] == "unified_orders"]) == 1
    after = {n: await db.raw[n].find({}, {"_id": 0}).to_list(None) for n in await db.raw.list_collection_names()}
    assert after == before


@pytest.mark.asyncio
async def test_allowed_dispatch_preserves_existing_success_and_idempotency(db):
    await seed_order(db); await seed_piece(db)
    body = payload("A")
    reply = await request(db, "POST", body)
    assert reply.status_code == 201 and reply.json()["ok"] is True, reply.text
    assert await db.raw[dispatch.DISPATCHES].count_documents({}) == 1
    assert (await db.raw[dispatch.PIECES].find_one({"piece_id": "piece-A"}))["supplier_dispatch_status"] == "sent"
    await seed_order(db, current="processing")
    db.writes.clear()
    replay = await request(db, "POST", body)
    assert replay.json()["dispatch"]["id"] == reply.json()["dispatch"]["id"]
    assert db.writes == []


@pytest.mark.asyncio
async def test_batch_100_pieces_reads_20_unique_orders_once(db, monkeypatch):
    pieces = []
    for i in range(20):
        await seed_order(db, str(i))
        pieces.extend({"piece_id": f"{i}-{j}", "order_number": str(i)} for j in range(5))
    calls = AsyncMock(wraps=policy.order_service.get_orders)
    monkeypatch.setattr(policy.order_service, "get_orders", calls)
    rows = await policy.annotate_waiting_pieces(db, user_id=OWNER["id"], pieces=pieces)
    assert len(rows) == 100 and all(p["waiting_review_eligible"] for p in rows)
    assert calls.await_count == 1 and len(calls.call_args.kwargs["order_numbers"]) == 20
    assert len([x for x in db.reads if x[0] == "unified_orders"]) == 1


@pytest.mark.asyncio
async def test_empty_workspace_does_not_query_orders(db, monkeypatch):
    call = AsyncMock(side_effect=AssertionError("No order query needed"))
    monkeypatch.setattr(policy.order_service, "get_orders", call)
    assert await policy.annotate_waiting_pieces(db, user_id=OWNER["id"], pieces=[]) == []
    call.assert_not_called()
