"""Read-only mobile queue contracts against a synthetic loopback Mongo DB."""
import os
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

import order_review_routes as review
import preparation_supplier_dispatch as dispatch
import supplier_dispatch_waiting_policy as waiting
import mobile_operations_monitoring_routes as monitoring


@pytest_asyncio.fixture
async def queue_db():
    uri = os.environ.get("WAITING_TEST_MONGO_URL", "mongodb://127.0.0.1:27017")
    assert urlparse(uri).hostname in {"127.0.0.1", "localhost"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    await client.admin.command("ping")
    db = client["mobile_review_queue_" + uuid4().hex]
    try:
        yield db
    finally:
        await client.drop_database(db.name)
        client.close()


def order(number, *, owner="queue-owner", current="under_review", native=None, customer=None, payment=None):
    return {
        "user_id": owner, "order_number": number, "order_date": "2026-10-15",
        "order_status": current,
        "raw_by_source": {"salla_direct": {
            "id": "salla-" + number, "reference_id": number,
            "date": "2026-10-15T10:00:00+00:00",
            "status": {"slug": current, "name": native or current},
            "customer": customer or {"full_name": "Synthetic customer"},
            "payment_method": payment or "credit_card",
            "amounts": {"total": {"amount": 10, "currency": "SAR"}},
            "items": [{"id": "item-" + number, "product_id": "p-1", "name": "Synthetic product", "quantity": 1}],
        }},
    }


@pytest_asyncio.fixture
async def queue_client(queue_db, monkeypatch):
    async def user():
        return {"id": "queue-employee", "created_by": "queue-owner", "role": "operations"}

    async def no_provider(*args, **kwargs):
        raise AssertionError("Queue reads cannot call Salla")

    monkeypatch.setattr(review, "call_salla", no_provider)
    monkeypatch.setattr(review, "refresh_order_from_salla", no_provider)
    monkeypatch.setattr(review, "schedule_salla_auto_sync", no_provider)
    app = FastAPI()
    app.include_router(review.make_order_review_router(queue_db, user))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_pending_search_applies_to_full_queue_before_page_and_count(queue_db, queue_client):
    rows = [order(f"{n:03d}") for n in range(1, 26)]
    for row in rows[:13]:
        row["raw_by_source"]["salla_direct"]["customer"]["full_name"] = "Needle customer"
    rows += [order("999", owner="foreign", customer={"full_name": "Needle customer"}),
             order("998", current="completed", customer={"full_name": "Needle customer"})]
    await queue_db.unified_orders.insert_many(rows)
    await queue_db[review.WORKFLOWS].insert_many([
        {"user_id": "queue-owner", "order_number": "013", "stage": "reviewed"},
        {"user_id": "foreign", "order_number": "012", "stage": "reviewed"},
    ])
    before = await queue_db.unified_orders.find({}, {"_id": 0}).to_list(None)

    default = (await queue_client.get("/order-reviews-v1/pages")).json()
    assert default["total_count"] == 24
    assert [row["order_number"] for row in default["items"]] == [f"{n:03d}" for n in range(25, 15, -1)]
    for page, expected in ((1, [f"{n:03d}" for n in range(12, 2, -1)]), (2, ["002", "001"]), (3, [])):
        response = await queue_client.get("/order-reviews-v1/pages", params={"page": page, "limit": 10, "search": "  NEEDLE  "})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["search_applied"] == "NEEDLE"
        assert payload["total_count"] == 12
        assert [row["order_number"] for row in payload["items"]] == expected
    assert await queue_db.unified_orders.find({}, {"_id": 0}).to_list(None) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("search", ["001", "عميل مميز", "551234567", "bank-x", "+.(literal)"])
async def test_pending_search_matches_number_name_mobile_payment_as_literal(queue_db, queue_client, search):
    await queue_db.unified_orders.insert_many([
        order("001", customer={"full_name": "عميل مميز +.(literal)", "mobile": 551234567}, payment={"name": "BANK-X"}),
        order("002"),
    ])
    response = await queue_client.get("/order-reviews-v1/pages", params={"search": search})
    assert response.status_code == 200, response.text
    assert [row["order_number"] for row in response.json()["items"]] == ["001"]
    assert response.json()["total_count"] == 1


@pytest.mark.asyncio
async def test_pending_search_supports_canonical_fallbacks_and_empty_query(queue_db, queue_client):
    row = order("001")
    row["raw_by_source"]["salla_direct"].pop("customer")
    row["raw_by_source"]["salla_direct"].pop("payment_method")
    row.update(customer_name="Fallback Name", customer_mobile="0508765432", payment_method="Fallback Bank")
    await queue_db.unified_orders.insert_one(row)
    for value in ("Fallback Name", "0508765432", "Fallback Bank", "   "):
        response = await queue_client.get("/order-reviews-v1/pages", params={"search": value})
        assert response.status_code == 200, response.text
        assert response.json()["search_applied"] == value.strip()
        assert response.json()["total_count"] == 1
    assert (await queue_client.get("/order-reviews-v1/pages", params={"search": "x" * 101})).status_code == 422


@pytest.mark.asyncio
async def test_pending_search_matches_joined_customer_name(queue_db, queue_client):
    await queue_db.unified_orders.insert_many([
        order("001", customer={"first_name": "سارة", "last_name": "التجريبية"}), order("002"),
    ])
    response = await queue_client.get("/order-reviews-v1/pages", params={"search": "سارة التجريبية"})
    assert response.status_code == 200, response.text
    assert [row["order_number"] for row in response.json()["items"]] == ["001"]
    assert response.json()["total_count"] == 1


@pytest.mark.asyncio
async def test_reviewed_pagination_uses_only_mezan_stage_and_current_tenant(queue_db, queue_client):
    await queue_db.unified_orders.insert_many([
        order(f"{n:03d}", current="under_review") for n in range(1, 7)
    ] + [order("007", current="reviewed"), order("008", current="reviewed"), order("999", owner="foreign")])
    await queue_db[review.WORKFLOWS].insert_many([
        {"user_id": "queue-owner", "order_number": f"{n:03d}", "stage": "reviewed",
         "reviewed_at": "2026-10-15T12:00:00+00:00", "revision": 2, "items": [{"order_item_id": f"item-{n:03d}"}]}
        for n in range(1, 7)
    ] + [
        {"user_id": "queue-owner", "order_number": "007", "stage": "in_progress"},
        {"user_id": "foreign", "order_number": "999", "stage": "reviewed"},
    ])
    before = await queue_db[review.WORKFLOWS].find({}, {"_id": 0}).to_list(None)
    for page, expected, next_page in ((1, ["006", "005"], 2), (2, ["004", "003"], 3), (3, ["002", "001"], None), (4, [], None)):
        response = await queue_client.get("/order-reviews-v1/reviewed", params={"page": page, "limit": 2})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_count"] == 6
        assert body["page"] == page and body["next_page"] == next_page
        assert [row["order_number"] for row in body["items"]] == expected
        assert all(row["stage"] == "reviewed" and row["status"] == "under_review" for row in body["items"])
    old = await queue_client.get("/order-reviews-v1/reviewed", params={"limit": 100})
    assert len(old.json()["items"]) == 6
    assert old.json()["next_page"] is None
    assert old.json()["items"][0]["items"] == [{"order_item_id": "item-006"}]
    assert (await queue_client.get("/order-reviews-v1/reviewed?page=0")).status_code == 422
    assert await queue_db[review.WORKFLOWS].find({}, {"_id": 0}).to_list(None) == before


@pytest.mark.asyncio
async def test_reviewed_total_and_later_page_extend_beyond_legacy_limit(queue_db, queue_client):
    await queue_db.unified_orders.insert_many([order(f"{n:03d}") for n in range(1, 124)])
    await queue_db[review.WORKFLOWS].insert_many([
        {"user_id": "queue-owner", "order_number": f"{n:03d}", "stage": "reviewed", "reviewed_at": "2026-10-15T12:00:00+00:00"}
        for n in range(1, 124)
    ])
    count = (await queue_client.get("/order-reviews-v1/reviewed?page=1&limit=1")).json()
    assert count["total_count"] == 123 and count["next_page"] == 2
    later = (await queue_client.get("/order-reviews-v1/reviewed?page=3&limit=50")).json()
    assert later["total_count"] == 123 and later["next_page"] is None
    assert [row["order_number"] for row in later["items"]] == [f"{n:03d}" for n in range(23, 0, -1)]
    assert len((await queue_client.get("/order-reviews-v1/reviewed")).json()["items"]) == 50


@pytest.mark.asyncio
async def test_reviewed_reports_unhydrated_workflows_without_changing_membership(queue_db, queue_client):
    await queue_db.unified_orders.insert_one(order("001"))
    await queue_db[review.WORKFLOWS].insert_many([
        {"user_id": "queue-owner", "order_number": number, "stage": "reviewed"}
        for number in ("001", "orphan", "")
    ])
    payload = (await queue_client.get("/order-reviews-v1/reviewed?limit=100")).json()
    assert payload["total_count"] == 3
    assert payload["skipped_invalid"] == 2
    assert [row["order_number"] for row in payload["items"]] == ["001"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "delivered", "تم التنفيذ", "تم التوصيل", "shipping", "shipped", "delivering", "out_for_delivery", "جاري التوصيل", "تم الشحن"])
async def test_terminal_annotation_uses_current_order_not_stale_piece_or_custom_status(queue_db, status):
    row = order("001", current=status)
    row["raw_by_source"]["salla_direct"]["status"] = {"slug": "under_review", "name": "بانتظار المراجعة", "customized": {"name": "تم المراجعة"}}
    await queue_db.unified_orders.insert_one(row)
    original = {"piece_id": "p1", "order_number": "001", "group_key": "g1", "status": "received", "supplier_dispatch_status": "received", "preparation_terminal": False}
    pieces = await waiting.annotate_waiting_pieces(queue_db, user_id="queue-owner", pieces=[deepcopy(original)])
    assert pieces[0]["preparation_terminal"] is True
    assert pieces[0]["order_status_native"] == status
    assert original["preparation_terminal"] is False
    for mapper in (dispatch._piece_products, dispatch._group_piece_products):
        product = mapper(pieces)[0]
        assert product["preparation_terminal"] is True
        assert product["received_quantity"] == 1, "Supplier receipt/history projection must remain intact"


@pytest.mark.asyncio
async def test_terminal_annotation_retains_active_unknown_and_foreign_pieces(queue_db):
    current = order("001", current="reviewed")
    current["raw_by_source"]["salla_direct"]["status"] = {"slug": "shipped", "name": "تم الشحن"}
    await queue_db.unified_orders.insert_many([current, order("002", owner="foreign", current="completed"), order("003", current="mystery")])
    pieces = [{"piece_id": f"p{n}", "order_number": f"{n:03d}", "group_key": "g1", "preparation_terminal": True} for n in range(1, 5)]
    annotated = await waiting.annotate_waiting_pieces(queue_db, user_id="queue-owner", pieces=pieces)
    assert all(piece["preparation_terminal"] is False for piece in annotated)
    assert annotated[1]["order_status_native"] is None
    assert pieces[0]["preparation_terminal"] is True
    grouped = dispatch._group_piece_products([annotated[0], {**annotated[0], "preparation_terminal": True}])
    assert grouped[0]["preparation_terminal"] is False, "Mixed product groups retain their active pieces"


@pytest.mark.parametrize("piece_grain", [False, True])
def test_live_progress_counts_exclude_terminal_without_rewriting_history(piece_grain):
    pieces = [
        {"piece_id": "active", "group_key": "g1", "batch_id": "batch", "status": "in_progress", "supplier_dispatch_status": "sent", "preparation_terminal": False},
        {"piece_id": "terminal-sent", "group_key": "g1", "batch_id": "batch", "status": "in_progress", "supplier_dispatch_status": "sent", "preparation_terminal": True},
        {"piece_id": "terminal-ready", "group_key": "g2", "batch_id": "batch", "status": "ready_for_receipt", "supplier_dispatch_status": "ready", "preparation_terminal": True},
        {"piece_id": "terminal-received", "group_key": "g3", "batch_id": "batch", "status": "received", "supplier_dispatch_status": "received", "preparation_terminal": True},
    ]
    before = deepcopy(pieces)
    file = dispatch._file_view({}, pieces, piece_grain=piece_grain)
    summary = dispatch.employee_workspace_summary([file], pieces)
    assert summary["in_progress_pieces"] == 1
    assert summary["in_progress_products"] == 1
    assert summary["sent"] == 2 and summary["ready"] == 1 and summary["received"] == 1
    assert summary["received_pieces_awaiting_branch_handoff"] == 1
    assert file["sent_quantity"] == 2 and file["received_quantity"] == 1
    assert sum(row["quantity"] for row in file["products"]) == 4
    monitored = monitoring._live_preparation_metrics(pieces, start=datetime(2026, 10, 1, tzinfo=timezone.utc), end=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert monitored["in_progress_count"] == 1
    assert monitored["received_count"] == 1
    assert pieces == before
    assert monitoring._monitoring_piece_view(pieces[1])["preparation_terminal"] is True
