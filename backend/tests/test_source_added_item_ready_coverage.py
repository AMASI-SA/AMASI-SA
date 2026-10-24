"""Late Salla lines cannot disappear from Ready's whole-order coverage."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient
import preparation_piece_operations as operations
import fulfillment_v2_routes as fulfillment
from review_local_policy import LOCAL_COMPLETION_MODE


def source():
    items = [SimpleNamespace(order_item_id=f"salla:100:{key}", quantity=1) for key in ("old", "new")]
    return SimpleNamespace(order_number="100", items=items,
        shipping=SimpleNamespace(address=SimpleNamespace(city="Test", street="Test")),
        model_dump=lambda **_: {"order_number": "100", "items": [vars(x) for x in items]})


def snapshot(approved=False, local=True):
    rows = [{"order_item_id": "salla:100:old", "review_status": "reviewed"}]
    if approved:
        rows.append({"order_item_id": "salla:100:new", "review_status": "reviewed", "source_addition": True})
    return {"user_id": "owner", "order_number": "100", "stage": "in_progress",
            "items": rows, "completion_mode": LOCAL_COMPLETION_MODE if local else None,
            "source_additions_reviewed": approved}


def piece(key):
    return {"id": key, "user_id": "owner", "order_number": "100", "order_item_id": f"salla:100:{key}",
            "unit_index": 1, "status": "ready_for_assembly", "assembly_status": "ready",
            "preparation_receipt_status": "received"}


def decision(configured=True):
    return {"ready_to_ship": configured, "lines": [
        {"order_item_id": f"salla:100:{key}", "quantity": 1, "configured": configured,
         "resolved_type": "instant", "requires_preparation": False} for key in ("old", "new")]}


@pytest.mark.asyncio
@pytest.mark.parametrize("approved,local,include_new,configured,expected", [
    (False, True, False, True, False),
    (True, True, False, True, False),
    (True, False, False, True, False),
    (True, True, True, True, True),
    (True, False, True, True, True),
    (True, True, True, False, False),
])
async def test_ready_requires_explicit_review_and_physical_addition(monkeypatch, approved, local, include_new, configured, expected):
    db = AsyncMongoMockClient().db
    await db[operations.WORKFLOWS].insert_one(snapshot(approved, local))
    await db[operations.PIECES].insert_one(piece("old"))
    if include_new:
        await db[operations.PIECES].insert_one(piece("new"))
    monkeypatch.setattr(operations, "build_order_fulfillment_decision", AsyncMock(return_value=decision(configured)))
    monkeypatch.setattr(operations, "enforce_stage_instructions", AsyncMock())
    result = await operations._assembly_progress(db, user_id="owner", order_number="100", actor_id="owner",
        actor_name="Test", now=datetime.now(timezone.utc), eligible_order=source())
    assert result["order_completed"] is expected
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == int(expected)
    assert (await db[operations.PIECES].find_one({"id": "old"}))["assembly_status"] == "ready"
    if not expected:
        assert (await db[operations.WORKFLOWS].find_one({}))["stage"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_auto_route_does_not_promote_source_addition(monkeypatch, approved):
    db = AsyncMongoMockClient().db
    await db[fulfillment.WORKFLOWS].insert_one(snapshot(approved, False))
    monkeypatch.setattr(fulfillment, "ensure_fulfillment_indexes", AsyncMock())
    monkeypatch.setattr(fulfillment, "reconcile_component_order_lifecycle", AsyncMock(return_value={"accepted": True}))
    builder = AsyncMock(return_value=decision())
    monkeypatch.setattr(fulfillment, "build_order_fulfillment_decision", builder)
    result = await fulfillment._auto_route_instant_order(db, user_id="owner", order=source())
    assert result["reason"] == "workflow_locked"
    assert result["promoted"] is False
    builder.assert_not_awaited()
    assert (await db[fulfillment.WORKFLOWS].find_one({}))["stage"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["stock", "warehouse", "address", "unreceived", "hold"])
async def test_expanded_ready_retains_real_fulfillment_and_custody_guards(monkeypatch, blocker):
    db = AsyncMongoMockClient().db
    await db[operations.WORKFLOWS].insert_one(snapshot(True))
    await db[operations.PIECES].insert_one(piece("old"))
    addition = piece("new")
    if blocker == "unreceived":
        addition.update(status="assigned", preparation_receipt_status="pending")
    if blocker == "hold":
        addition["active_hold_id"] = "hold"
    await db[operations.PIECES].insert_one(addition)
    latest = decision()
    if blocker in {"stock", "warehouse"}:
        latest["lines"][1].update(requires_branch_inventory=True,
            inventory_available=blocker != "stock", warehouse_ids=[] if blocker == "warehouse" else ["warehouse"])
    order = source()
    if blocker == "address":
        order.shipping = None
    monkeypatch.setattr(operations, "build_order_fulfillment_decision", AsyncMock(return_value=latest))
    monkeypatch.setattr(operations, "enforce_stage_instructions", AsyncMock())
    result = await operations._assembly_progress(db, user_id="owner", order_number="100", actor_id="owner",
        actor_name="Test", now=datetime.now(timezone.utc), eligible_order=order)
    assert result["order_completed"] is False
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_legacy_ready_does_not_hide_unreviewed_current_source_line():
    db = AsyncMongoMockClient().db
    row = snapshot(False, False)
    row["stage"] = "ready_to_ship"
    await db[operations.WORKFLOWS].insert_one(row)
    await db[operations.PIECES].insert_one(piece("old"))
    result = await operations._assembly_progress(db, user_id="owner", order_number="100", actor_id="owner",
        actor_name="Test", now=datetime.now(timezone.utc), eligible_order=source())
    assert result["order_completed"] is False
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == 0
