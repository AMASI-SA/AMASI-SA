"""Purchase list reads retain canonical balances without per-invoice queries."""
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

import purchase_invoices_routes as routes
from purchase_receiving_service import SCHEMA, OPERATIONS
from tests.test_fulfillment_read_performance import ReadCounter


async def client_response(db, monkeypatch, params=None):
    monkeypatch.setattr(routes, "get_current_user_from_db", AsyncMock(return_value={"id": "owner", "role": "owner"}))
    app = FastAPI()
    routes.attach_purchase_invoice_routes(app, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        return await client.get("/purchase-invoices", params=params or {})


async def seed_invoices(db, count=40):
    await db.users.insert_one({"id": "owner", "role": "owner"})
    for index in range(count):
        await db.purchase_invoices.insert_one({
            "user_id": "owner", "id": f"invoice-{index:04d}", "invoice_date": "2026-11-01",
            "created_at": f"2026-11-01T10:{index:04d}", "schema_version": SCHEMA,
            "approval_operation_id": f"operation-{index}", "liability_id": f"liability-{index}",
            "state": "approved", "total": 125.0,
        })
        await db[OPERATIONS].insert_one({"_id": f"operation-{index}", "user_id": "owner", "state": "completed"})
        await db.liabilities.insert_one({"user_id": "owner", "id": f"liability-{index}",
                                         "expected_amount": 125.0, "paid_amount": 25.0, "status": "partial"})


@pytest.mark.asyncio
async def test_operations_invoice_list_batches_reads_and_keeps_balances(monkeypatch):
    raw = AsyncMongoMockClient().purchase_budget
    await seed_invoices(raw)
    # Foreign records with the same logical identities must never win.
    await raw.liabilities.insert_one({"user_id": "other", "id": "liability-0", "expected_amount": 9999, "paid_amount": 0})
    await raw[OPERATIONS].insert_one({"_id": "foreign", "user_id": "other", "state": "foreign"})
    db = ReadCounter(raw)
    before = {name: await raw[name].find({}).to_list(None) for name in await raw.list_collection_names()}
    response = await client_response(db, monkeypatch, {"limit": 2000})
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 40
    assert [row["id"] for row in result["items"]] == [f"invoice-{index:04d}" for index in reversed(range(40))]
    for row in result["items"]:
        assert row["total"] == 125
        assert row["paid_amount"] == 25
        assert row["remaining_amount"] == 100
        assert row["status"] == "approved" and row["payment_status"] == "partial"
        assert row["operation"] == {"user_id": "owner", "state": "completed"}
    assert db.reads[(OPERATIONS, "find_one")] == 0
    assert db.reads[("liabilities", "find_one")] == 0
    assert db.reads[(OPERATIONS, "find")] == 1
    assert db.reads[("liabilities", "find")] == 1
    assert before == {name: await raw[name].find({}).to_list(None) for name in await raw.list_collection_names()}


@pytest.mark.asyncio
async def test_legacy_drafts_and_missing_balances_keep_existing_rules(monkeypatch):
    raw = AsyncMongoMockClient().purchase_variants
    await seed_invoices(raw, 1)
    await raw.purchase_invoices.insert_many([
        {"user_id": "owner", "id": "legacy", "total": 80, "status": "unpaid", "liability_id": "legacy-liability"},
        {"user_id": "owner", "id": "draft", "schema_version": SCHEMA, "state": "draft", "total": 80, "approval_operation_id": "missing"},
        {"user_id": "owner", "id": "missing-balance", "schema_version": SCHEMA, "state": "approved", "total": 80, "approval_operation_id": "missing", "liability_id": "foreign-only"},
    ])
    await raw.liabilities.insert_many([
        {"user_id": "owner", "id": "legacy-liability", "expected_amount": 80, "paid_amount": 90, "status": "paid"},
        {"user_id": "other", "id": "foreign-only", "expected_amount": 80, "paid_amount": 80, "status": "paid"},
    ])
    response = await client_response(ReadCounter(raw), monkeypatch)
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()["items"]}
    assert rows["legacy"]["legacy_read_only"] is True
    assert rows["legacy"]["paid_amount"] == 90
    assert rows["legacy"]["remaining_amount"] == 0 and rows["legacy"]["status"] == "paid"
    assert rows["draft"]["remaining_amount"] == 0 and rows["draft"]["operation"] is None
    assert rows["missing-balance"]["remaining_amount"] == 80
    assert rows["missing-balance"]["paid_amount"] == 0
    response = await client_response(ReadCounter(raw), monkeypatch, {"status": "partial", "limit": 2000})
    assert [row["id"] for row in response.json()["items"]] == ["invoice-0000"]
