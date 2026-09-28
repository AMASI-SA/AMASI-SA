"""HTTP/Mongo verification; seeds only a disposable loopback test database."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import supplier_receiving_routes as receiving
from supplier_invoice_history import own_closed_invoice_query

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)

def session(i, supplier="supplier-a", actor="employee-a", merchant="tenant-a", **extra):
    return {"id": f"session-{i}", "user_id": merchant, "opened_by": actor,
            "client_request_id": f"request-{i}", "reference": f"SR-{i}",
            "opened_by_name": actor, "opened_at": BASE, "status": "closed",
            "supplier_id": supplier,
            "supplier_snapshot": {"id": supplier, "company_name": supplier},
            "closed_at": BASE + timedelta(minutes=i), "scan_count": i + 1,
            "supplier_invoice": {"id": f"invoice-{i}", "invoice_number": f"SI-{i}", "share_confirmed": False}, **extra}

@pytest_asyncio.fixture
async def history(monkeypatch):
    uri = os.environ.get("BUILD20_INVOICE_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Disposable Mongo is required for HTTP history tests")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1"}
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
    db = mongo["test_build26_private_history_" + uuid.uuid4().hex]
    user = {"id": "employee-a", "merchant": "tenant-a", "owner": False}
    async def current_user():
        return user
    async def context(_db, principal):
        return {"merchant_id": principal["merchant"], "actor_id": principal["id"],
                "is_owner": principal["owner"], "permissions": [receiving.RECEIVE_PERMISSION]}
    monkeypatch.setattr(receiving, "_actor_context", context)
    app = FastAPI()
    app.include_router(receiving.make_supplier_receiving_router(db, current_user))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        try:
            yield db, client, user
        finally:
            await mongo.drop_database(db.name)
            mongo.close()


def test_personal_history_never_expands_to_colleagues_even_for_owner():
    query = own_closed_invoice_query({"merchant_id": "m", "actor_id": "a", "is_owner": True})
    assert query["user_id"] == "m" and query["opened_by"] == "a" and query["status"] == "closed"

@pytest.mark.parametrize("context", [{}, {"merchant_id": "m"}, {"actor_id": "a"}])
def test_missing_actor_or_tenant_is_fail_closed(context):
    with pytest.raises(HTTPException) as exc:
        own_closed_invoice_query(context)
    assert exc.value.status_code == 403

@pytest.mark.asyncio
async def test_suppliers_order_by_latest_own_invoice_not_name_or_colleague_activity(history):
    db, client, _ = history
    await db[receiving.SESSIONS].insert_many([
        session(1, "z-supplier"), session(5, "z-supplier"), session(3, "a-supplier"),
        session(99, "a-supplier", actor="employee-b"), session(100, "foreign", merchant="tenant-b"),
        session(101, "open-only", status="open"), session(102, "cancelled", status="cancelled"),
        session(103, "no-invoice", supplier_invoice={}),
    ])
    response = await client.get("/supplier-receiving-v1/invoice-history/suppliers")
    assert response.status_code == 200
    page = response.json()
    assert page["actor_id"] == "employee-a" and page["scope"] == "actor_only" and page["total"] == 2
    assert [(r["supplier_id"], r["invoice_count"]) for r in page["items"]] == [("z-supplier", 2), ("a-supplier", 1)]
    assert page["read_only"] is True and page["financial_writes"] is False
    await db[receiving.SESSIONS].insert_one(session(110, "a-supplier"))
    refreshed = (await client.get("/supplier-receiving-v1/invoice-history/suppliers")).json()
    assert refreshed["items"][0]["supplier_id"] == "a-supplier"

@pytest.mark.asyncio
async def test_supplier_counts_are_complete_beyond_catalog_limit_and_invoice_pages_newest_first(history):
    db, client, _ = history
    await db[receiving.SESSIONS].insert_many([session(i) for i in range(205)] + [session(500, actor="employee-b")])
    summary = (await client.get("/supplier-receiving-v1/invoice-history/suppliers")).json()
    assert summary["items"][0]["invoice_count"] == 205
    rows, offset = [], 0
    while offset is not None:
        page = (await client.get(f"/supplier-receiving-v1/invoice-history/suppliers/supplier-a?limit=20&offset={offset}")).json()
        assert page["total"] == 205
        rows.extend(page["items"])
        offset = page["next_offset"]
    assert [r["invoice_id"] for r in rows] == [f"invoice-{i}" for i in reversed(range(205))]
    assert rows[0]["product_count"] == 205
    assert rows[0]["invoice_date"].endswith("+00:00")

@pytest.mark.asyncio
async def test_duplicate_supplier_names_do_not_merge_and_inactive_snapshot_is_retained(history):
    db, client, _ = history
    await db[receiving.SESSIONS].insert_many([
        session(1, "id-1", supplier_snapshot={"id": "id-1", "company_name": "أحمد"}),
        session(2, "id-2", supplier_snapshot={"id": "id-2", "company_name": "أحمد"}),
    ])
    page = (await client.get("/supplier-receiving-v1/invoice-history/suppliers")).json()
    assert page["total"] == 2 and [r["supplier_id"] for r in page["items"]] == ["id-2", "id-1"]

@pytest.mark.asyncio
async def test_supplier_pagination_counts_all_groups_before_limit(history):
    db, client, _ = history
    await db[receiving.SESSIONS].insert_many([session(i, f"supplier-{i:03}") for i in range(25)])
    first = (await client.get("/supplier-receiving-v1/invoice-history/suppliers?limit=20")).json()
    second = (await client.get("/supplier-receiving-v1/invoice-history/suppliers?offset=20&limit=20")).json()
    assert first["total"] == second["total"] == 25
    assert first["next_offset"] == 20 and second["next_offset"] is None
    assert len(first["items"]) == 20 and len(second["items"]) == 5

@pytest.mark.asyncio
async def test_history_and_catalog_switch_with_authenticated_employee_and_never_accept_actor_parameter(history):
    db, client, user = history
    await db[receiving.SESSIONS].insert_many([session(1), session(2, actor="employee-b")])
    page = (await client.get("/supplier-receiving-v1/invoice-history/suppliers/supplier-a?actor_id=employee-b")).json()
    assert [r["invoice_id"] for r in page["items"]] == ["invoice-1"]
    catalog_response = await client.get("/supplier-receiving-v1/catalog")
    assert catalog_response.status_code == 200
    assert [r["id"] for r in catalog_response.json()["sessions"]] == ["session-1"]
    user["id"] = "employee-b"
    page = (await client.get("/supplier-receiving-v1/invoice-history/suppliers/supplier-a")).json()
    assert [r["invoice_id"] for r in page["items"]] == ["invoice-2"]

@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/pdf", "/share-evidence"])
async def test_foreign_employee_invoice_cannot_be_read_by_guessed_id_or_file_url(history, suffix):
    db, client, _ = history
    await db[receiving.SUPPLIER_INVOICES].insert_one({"id": "foreign-invoice", "user_id": "tenant-a", "supplier_approved_by": "employee-b", "invoice_number": "PRIVATE"})
    response = await client.get("/supplier-receiving-v1/invoices/foreign-invoice" + suffix)
    assert response.status_code == 404 and "PRIVATE" not in response.text

@pytest.mark.asyncio
async def test_other_supplier_returns_no_colleague_invoices(history):
    db, client, _ = history
    await db[receiving.SESSIONS].insert_one(session(1, "colleague-only", actor="employee-b"))
    page = (await client.get("/supplier-receiving-v1/invoice-history/suppliers/colleague-only")).json()
    assert page["items"] == [] and page["total"] == 0
