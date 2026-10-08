"""Batch reads preserve board membership and first-raw-record ambiguity."""
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient
import preparation_piece_operations as operations
from tests.test_order_engine_service import make_raw


async def seed(db, number, status="in_progress", owner="owner"):
    date = "2026-10-01T10:00:00+03:00"
    raw = make_raw(number, date)
    raw["status"]["slug"] = status
    await db.unified_orders.insert_one({"user_id": owner, "order_number": number,
        "order_date": date, "raw_by_source": {"salla_direct": raw}})
    await db[operations.WORKFLOWS].insert_one({"user_id": owner, "order_number": number,
        "stage": "ready_to_ship", "preparation_piece_count": 1, "updated_at": date})
    await db[operations.PIECES].insert_one({"user_id": owner, "order_number": number,
        "piece_id": number + "-piece", "status": "ready_for_assembly", "assembly_status": "ready"})


async def board(db, **kwargs):
    return await operations._assembly_order_board(db, user_id="owner", state="in_progress",
        limit=kwargs.pop("limit", 100), offset=kwargs.pop("offset", 0), **kwargs)


@pytest.mark.asyncio
async def test_501_orders_use_two_bounded_batches_with_same_total_and_page(monkeypatch):
    db = AsyncMongoMockClient().batch_board
    for index in range(501):
        await seed(db, str(index).zfill(4))
    bulk = AsyncMock(wraps=operations.get_orders)
    single = AsyncMock(wraps=operations.get_order)
    monkeypatch.setattr(operations, "get_orders", bulk)
    monkeypatch.setattr(operations, "get_order", single)
    result = await board(db, offset=498, limit=3)
    assert result["total"] == 501
    assert [r["order_number"] for r in result["items"]] == ["0498", "0499", "0500"]
    assert [len(c.kwargs["order_numbers"]) for c in bulk.await_args_list] == [500, 1]
    assert single.await_count == 0
    assert all(r["ready_count"] == r["total_count"] == 1 for r in result["items"])


@pytest.mark.asyncio
async def test_duplicates_preserve_first_raw_record_even_when_malformed(monkeypatch):
    db = AsyncMongoMockClient().duplicates
    await db.unified_orders.insert_one({"user_id": "owner", "order_number": "bad-first",
        "order_date": "2026-10-01", "raw_by_source": {"salla_direct": None}})
    await seed(db, "bad-first")
    await seed(db, "completed-first", status="completed")
    row = await db.unified_orders.find_one({"order_number": "completed-first"})
    row.pop("_id")
    row["raw_by_source"]["salla_direct"]["status"]["slug"] = "in_progress"
    await db.unified_orders.insert_one(row)
    await seed(db, "normal")
    single = AsyncMock(wraps=operations.get_order)
    monkeypatch.setattr(operations, "get_order", single)
    result = await board(db)
    assert [r["order_number"] for r in result["items"]] == ["normal"]
    assert {c.kwargs["order_number"] for c in single.await_args_list} == {"bad-first", "completed-first"}


@pytest.mark.asyncio
async def test_query_and_tenant_filter_exclude_foreign_duplicates(monkeypatch):
    db = AsyncMongoMockClient().tenant_board
    await seed(db, "123")
    await seed(db, "123", owner="other")
    await seed(db, "456")
    single = AsyncMock(wraps=operations.get_order)
    bulk = AsyncMock(wraps=operations.get_orders)
    monkeypatch.setattr(operations, "get_order", single)
    monkeypatch.setattr(operations, "get_orders", bulk)
    result = await board(db, query=" #12 ")
    assert result["total"] == 1
    assert result["items"][0]["order_number"] == "123"
    assert bulk.await_args.kwargs["order_numbers"] == ["123"]
    assert single.await_count == 0


@pytest.mark.asyncio
async def test_empty_and_missing_raw_orders_remain_absent():
    db = AsyncMongoMockClient().missing_board
    assert (await board(db))["total"] == 0
    await seed(db, "missing")
    await db.unified_orders.delete_many({})
    assert (await board(db))["items"] == []
