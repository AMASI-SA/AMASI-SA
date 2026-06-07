"""Iter-91 Phase 2 — order_adjustments diff helpers.

Unit-level tests for `_summarise_items`, `_diff_items` and
`_record_order_adjustment` from salla_integration.sync.

The full resync_single_order Salla API path is integration-tested
through the existing test_order_status_update_iter87 suite.
"""
import sys
import pytest

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient

from salla_integration.sync import (
    _summarise_items,
    _diff_items,
    _record_order_adjustment,
)


@pytest.fixture
def mongo_db():
    mongo = open("/app/backend/.env").read().split("MONGO_URL=")[1].split("\n")[0].strip('"')
    client = AsyncIOMotorClient(mongo)
    db = client["test_database"]
    return db


def test_summarise_items_uses_sku_first():
    p = [{"sku": "ABC", "product_id": "1", "name": "Test", "quantity": 2, "price": 10}]
    out = _summarise_items(p)
    assert out[0]["key"] == "ABC"
    assert out[0]["quantity"] == 2.0
    assert out[0]["price"] == 10.0


def test_summarise_items_falls_back_to_product_id():
    p = [{"product_id": "ID-1", "name": "X", "quantity": 1, "price": 5}]
    out = _summarise_items(p)
    assert out[0]["key"] == "ID-1"


def test_summarise_items_falls_back_to_name():
    p = [{"name": "Only-Name", "quantity": 3, "price": 4.5}]
    out = _summarise_items(p)
    assert out[0]["key"] == "Only-Name"


def test_summarise_items_drops_keyless():
    p = [{"quantity": 1, "price": 1}]
    assert _summarise_items(p) == []


def test_diff_identifies_added_items():
    old = [{"key": "A", "name": "A", "quantity": 1, "price": 10}]
    new = [
        {"key": "A", "name": "A", "quantity": 1, "price": 10},
        {"key": "B", "name": "B", "quantity": 2, "price": 5},
    ]
    d = _diff_items(old, new)
    assert len(d["added"]) == 1
    assert d["added"][0]["key"] == "B"
    assert d["removed"] == []
    assert d["modified"] == []


def test_diff_identifies_removed_items():
    old = [
        {"key": "A", "name": "A", "quantity": 1, "price": 10},
        {"key": "B", "name": "B", "quantity": 2, "price": 5},
    ]
    new = [{"key": "A", "name": "A", "quantity": 1, "price": 10}]
    d = _diff_items(old, new)
    assert d["added"] == []
    assert len(d["removed"]) == 1
    assert d["removed"][0]["key"] == "B"


def test_diff_identifies_modified_qty_or_price():
    old = [{"key": "A", "name": "A", "quantity": 1, "price": 10}]
    new = [{"key": "A", "name": "A", "quantity": 3, "price": 10}]
    d = _diff_items(old, new)
    assert len(d["modified"]) == 1
    mod = d["modified"][0]
    assert mod["before"]["quantity"] == 1
    assert mod["after"]["quantity"] == 3


def test_diff_returns_empty_when_identical():
    items = [{"key": "A", "name": "A", "quantity": 1, "price": 10}]
    d = _diff_items(items, items)
    assert d == {"added": [], "removed": [], "modified": []}


UID = "test-iter91-adjustments"


@pytest.mark.asyncio
async def test_record_adjustment_skips_when_no_change(mongo_db):
    await mongo_db.order_adjustments.delete_many({"user_id": UID})
    before = {"total_amount": 100, "products": [
        {"sku": "A", "name": "A", "quantity": 1, "price": 100}
    ]}
    after = {"total_amount": 100, "products": [
        {"sku": "A", "name": "A", "quantity": 1, "price": 100}
    ]}
    row = await _record_order_adjustment(
        mongo_db, UID, "O1-NOCHANGE", before, after
    )
    assert row is None
    assert await mongo_db.order_adjustments.count_documents(
        {"user_id": UID, "order_number": "O1-NOCHANGE"}
    ) == 0


@pytest.mark.asyncio
async def test_record_adjustment_writes_on_total_change(mongo_db):
    await mongo_db.order_adjustments.delete_many({"user_id": UID})
    before = {"total_amount": 500, "products": [], "total_product_cost": 100}
    after = {"total_amount": 300, "products": [], "total_product_cost": 50}
    row = await _record_order_adjustment(
        mongo_db, UID, "O2-TOTAL", before, after
    )
    assert row is not None
    assert row["delta_total"] == -200
    assert row["delta_cogs"] == -50
    assert row["total_changed"] is True
    assert row["items_changed"] is False
    n = await mongo_db.order_adjustments.count_documents(
        {"user_id": UID, "order_number": "O2-TOTAL"}
    )
    assert n == 1


@pytest.mark.asyncio
async def test_record_adjustment_writes_on_item_removal(mongo_db):
    await mongo_db.order_adjustments.delete_many({"user_id": UID})
    before = {
        "total_amount": 200,
        "products": [
            {"sku": "A", "name": "A", "quantity": 1, "price": 100},
            {"sku": "B", "name": "B", "quantity": 1, "price": 100},
        ],
        "total_product_cost": 60,
    }
    after = {
        "total_amount": 100,
        "products": [{"sku": "A", "name": "A", "quantity": 1, "price": 100}],
        "total_product_cost": 30,
    }
    row = await _record_order_adjustment(
        mongo_db, UID, "O3-ITEM-REMOVED", before, after
    )
    assert row is not None
    assert row["items_changed"] is True
    assert len(row["items_diff"]["removed"]) == 1
    assert row["items_diff"]["removed"][0]["key"] == "B"
    assert row["delta_total"] == -100


@pytest.mark.asyncio
async def test_record_adjustment_returns_none_when_before_missing(mongo_db):
    out = await _record_order_adjustment(
        mongo_db, UID, "X", None, {"total_amount": 10}
    )
    assert out is None
    out2 = await _record_order_adjustment(
        mongo_db, UID, "X", {"total_amount": 10}, None
    )
    assert out2 is None
