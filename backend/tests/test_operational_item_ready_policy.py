"""Direct and internal operational Ready use only the current Salla status."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import preparation_piece_operations as operations
import stock_component_consumption_service as stock
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from review_local_policy import LOCAL_COMPLETION_MODE
from stock_component_consumption_service import LOCATIONS, PLANS, UNITS
from tests.test_supplier_receipt_ready_policy import (
    LINE, NUMBER, OWNER, WHEN, current_order, received_piece, source_order, stock_snapshot,
)


def operational_item(identity="op:wrap"):
    return {
        "operational_item_id": identity, "name": "Synthetic wrapping",
        "source_order_item_id": LINE, "item_type": "internal_operational",
        "linked_specs": [{"name": "Wrapping", "value": "Special"}],
        "preparation_status": "pending", "blocks_order_completion": True,
    }


@pytest.fixture
def database(monkeypatch):
    client = AsyncMongoMockClient()
    db = client["operational_status_only"]

    async def current(database, *, user_id, order_number):
        source = await database.unified_orders.find_one({
            "user_id": user_id, "order_number": order_number,
        })
        if source is None:
            return None
        order = current_order(quantity=1, status=source.get("order_status") or "unknown")
        order.order_number = order_number
        return order

    monkeypatch.setattr(operations, "_current_assembly_order", current)
    async def owner_callback(database, owner, callback):
        assert owner == OWNER
        return await callback(database)
    monkeypatch.setattr(stock, "operational_owner", owner_callback)
    monkeypatch.setattr(operations, "call_salla", AsyncMock(side_effect=AssertionError("No provider write")))
    monkeypatch.setattr(operations, "sync_completed_carrier_label", AsyncMock(side_effect=AssertionError("No label IO")))
    yield db
    client.close()


async def seed(db, *, case="source_pending", last=False):
    source = source_order(quantity=1)
    if case != "source_pending":
        source["g47_salla_snapshot"].update(component_pending=False, requires_authoritative_refresh=False)
    workflow = {
        "user_id": OWNER, "order_number": NUMBER, "stage": "in_progress", "revision": 0,
        "operational_items": [operational_item()],
    }
    if not last:
        workflow["operational_items"].append(operational_item("op:other-work"))
    if case == "review_proof_missing":
        workflow["completion_mode"] = LOCAL_COMPLETION_MODE
        workflow["review_completion_operation_id"] = "missing-approval"
    elif case == "unknown_review_mode":
        workflow["completion_mode"] = "unrecognized-local-mode"
    elif case == "reviewed_stage":
        workflow["stage"] = "reviewed"
    elif case == "stale_local_stage":
        workflow["stage"] = "pending_review"
    elif case == "cancelled_component_plan":
        await db[PLANS].insert_one({
            "_id": "cancelled-plan", "user_id": OWNER, "order_id": NUMBER, "state": "cancelled",
        })
    elif case == "stage_instruction":
        await db[ORDER_TRACKING_INSTRUCTIONS].insert_one({
            "id": "assembly-instruction", "user_id": OWNER, "order_number": NUMBER,
            "scope": "order", "status": "active", "action_type": "edit_order",
            "target_stages": ["assembly_labeling"], "enforcement": "completion_required",
        })
    await db.unified_orders.insert_one(source)
    await db[operations.WORKFLOWS].insert_one(workflow)
    await db.settings.insert_one({"user_id": OWNER, "operational_flow": {
        "preparation_employee_receipt_mode": "required", "supplier_assignment_mode": "required",
    }})
    if last:
        await db[operations.PIECES].insert_one(received_piece(1, ready=True))
    await db[LOCATIONS].insert_one({
        "user_id": OWNER, "id": "warehouse", "occupancy": {"total_quantity": 20},
    })


async def mark(db, *, owner=OWNER, identity="op:wrap"):
    return await operations._mark_assembly_piece_ready_in_transaction(
        db, user_id=owner, piece_id=identity, client_request_id="operational-ready-request",
        actor_id="assembly-worker", actor_name="Synthetic worker",
    )


EXTRA_GATES = [
    "source_pending", "review_proof_missing", "unknown_review_mode", "reviewed_stage",
    "stale_local_stage", "cancelled_component_plan", "stage_instruction",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", EXTRA_GATES)
async def test_in_progress_operational_card_is_open_despite_other_order_gates(database, case):
    await seed(database, case=case)
    before = await stock_snapshot(database)
    source = await database.unified_orders.find_one({})

    result = await operations._assembly_search(
        database, user_id=OWNER, query=NUMBER, actor_id="assembly-worker",
    )

    card = next(row for row in result["pieces"] if row["piece_id"] == "op:wrap")
    assert card["can_mark_ready"] is True
    assert card["assembly_blocker_code"] is None
    assert card.get("assembly_blocker_message") is None
    assert await stock_snapshot(database) == before
    assert await database.unified_orders.find_one({}) == source


@pytest.mark.asyncio
@pytest.mark.parametrize("case", EXTRA_GATES)
async def test_in_progress_operational_ready_saves_and_retries_without_stock_or_source_changes(database, case):
    await seed(database, case=case)
    before_stock = await stock_snapshot(database)
    before_source = await database.unified_orders.find_one({})
    before_instructions = await database[ORDER_TRACKING_INSTRUCTIONS].find({}).to_list(None)

    first = await mark(database)
    retry = await mark(database)

    assert first["ok"] is True and first["idempotent"] is False
    assert retry["ok"] is True and retry["idempotent"] is True
    assert first["piece"]["assembly_ready"] is True
    assert first["progress"]["order_completed"] is False
    assert first["progress"]["ready_count"] == 1
    assert first["progress"]["total_count"] == 2
    assert await database[operations.PIECE_EVENTS].count_documents({
        "event_type": "operational_assembly_item_marked_ready", "piece_id": "op:wrap",
    }) == 1
    assert await database[operations.PIECES].count_documents({}) == 0
    assert await database[operations.SHIPPING_BATCHES].count_documents({}) == 0
    assert await stock_snapshot(database) == before_stock
    assert await database.unified_orders.find_one({}) == before_source
    assert await database[ORDER_TRACKING_INSTRUCTIONS].find({}).to_list(None) == before_instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    "completed", "shipped", "delivering", "delivered", "cancelled", "refunded",
    "under_review", "pending", "unknown", None,
])
async def test_any_status_other_than_in_progress_blocks_card_and_write(database, status):
    await seed(database)
    await database.unified_orders.update_one({}, {"$set": {"order_status": status, "order_status_slug": status,
        "raw_by_source.salla_direct.status": {"slug": status}}})
    before = await database[operations.WORKFLOWS].find_one({})

    result = await operations._assembly_search(database, user_id=OWNER, query=NUMBER)
    card = next(row for row in result["pieces"] if row["piece_id"] == "op:wrap")
    assert card["can_mark_ready"] is False
    assert card["assembly_blocker_code"] == "assembly_salla_in_progress_required"
    with pytest.raises(HTTPException) as error:
        await mark(database)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "assembly_salla_in_progress_required"
    assert await database[operations.WORKFLOWS].find_one({}) == before
    assert await database[operations.PIECE_EVENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_status_is_revalidated_after_an_open_card_is_read(database):
    await seed(database)
    result = await operations._assembly_search(database, user_id=OWNER, query=NUMBER)
    assert next(row for row in result["pieces"] if row["piece_id"] == "op:wrap")["can_mark_ready"] is True
    await database.unified_orders.update_one({}, {"$set": {"order_status": "cancelled"}})
    with pytest.raises(HTTPException) as error:
        await mark(database)
    assert error.value.detail["code"] == "assembly_salla_in_progress_required"
    assert await database[operations.PIECE_EVENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_another_merchant_cannot_complete_the_operational_item(database):
    await seed(database)
    before = deepcopy(await database[operations.WORKFLOWS].find_one({}))
    with pytest.raises(HTTPException) as error:
        await mark(database, owner="foreign-owner")
    assert error.value.status_code == 404
    assert await database[operations.WORKFLOWS].find_one({}) == before
    assert await database[operations.PIECE_EVENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_last_operational_item_can_finish_an_eligible_supplier_order(database):
    await seed(database, last=True)
    before = await stock_snapshot(database)

    result = await mark(database)

    assert result["piece"]["assembly_ready"] is True
    assert result["progress"]["order_completed"] is True
    assert await database[operations.SHIPPING_BATCHES].count_documents({}) == 1
    assert await stock_snapshot(database) == before


@pytest.mark.asyncio
async def test_last_operational_ready_is_saved_while_order_completion_instruction_stays_blocked(database):
    await seed(database, case="stage_instruction", last=True)
    result = await mark(database)
    assert result["piece"]["assembly_ready"] is True
    assert result["progress"]["order_completed"] is False
    assert result["progress"]["ready_count"] == result["progress"]["total_count"] == 2
    assert await database[operations.SHIPPING_BATCHES].count_documents({}) == 0
    assert await database[ORDER_TRACKING_INSTRUCTIONS].count_documents({"status": "active"}) == 1


async def seed_direct(db, *, case="source_pending", last=False):
    await seed(db, case=case)
    await db[operations.WORKFLOWS].update_one({}, {"$set": {
        "operational_items": [] if last else [operational_item("op:other-work")],
        "items": [{"order_item_id": LINE, "product_id": "synthetic-direct-product",
                   "preparation_route": "direct_assembly", "quantity": 1,
                   "direct_assembly_piece_ids": ["direct:ready"]}],
    }})


@pytest.mark.asyncio
@pytest.mark.parametrize("case", EXTRA_GATES)
async def test_direct_preparation_card_and_ready_use_only_current_status(database, case):
    await seed_direct(database, case=case)
    before_stock = await stock_snapshot(database)
    before_source = await database.unified_orders.find_one({})
    before_instructions = await database[ORDER_TRACKING_INSTRUCTIONS].find({}).to_list(None)

    result = await operations._assembly_search(database, user_id=OWNER, query=NUMBER, actor_id="assembly-worker")
    card = next(row for row in result["pieces"] if row["piece_id"] == "direct:ready")
    assert card["can_mark_ready"] is True and card["assembly_blocker_code"] is None
    first = await mark(database, identity="direct:ready")
    retry = await mark(database, identity="direct:ready")
    assert first["piece"]["assembly_ready"] is True
    assert first["idempotent"] is False and retry["idempotent"] is True
    assert first["progress"]["order_completed"] is False
    assert await database[operations.PIECE_EVENTS].count_documents({
        "piece_id": "direct:ready", "event_type": "direct_assembly_product_marked_ready",
    }) == 1
    assert await stock_snapshot(database) == before_stock
    assert await database.unified_orders.find_one({}) == before_source
    assert await database[ORDER_TRACKING_INSTRUCTIONS].find({}).to_list(None) == before_instructions
    assert await database[operations.SHIPPING_BATCHES].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    "completed", "shipped", "delivering", "delivered", "cancelled", "refunded",
    "under_review", "pending", "unknown", None,
])
async def test_direct_preparation_is_closed_for_every_other_status(database, status):
    await seed_direct(database)
    await database.unified_orders.update_one({}, {"$set": {
        "order_status": status, "order_status_slug": status,
        "raw_by_source.salla_direct.status": {"slug": status},
    }})
    before = await database[operations.WORKFLOWS].find_one({})
    result = await operations._assembly_search(database, user_id=OWNER, query=NUMBER)
    assert next(row for row in result["pieces"] if row["piece_id"] == "direct:ready")["can_mark_ready"] is False
    with pytest.raises(HTTPException) as error:
        await mark(database, identity="direct:ready")
    assert error.value.detail["code"] == "assembly_salla_in_progress_required"
    assert await database[operations.WORKFLOWS].find_one({}) == before
    assert await database[operations.PIECE_EVENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_last_direct_ready_is_saved_without_completing_a_blocked_material_contract(database):
    await seed_direct(database, last=True)
    first = await mark(database, identity="direct:ready")
    assert first["piece"]["assembly_ready"] is True
    assert first["progress"]["ready_count"] == first["progress"]["total_count"] == 1
    assert first["progress"]["order_completed"] is False
    assert await database[operations.SHIPPING_BATCHES].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["op:wrap", "direct:ready"])
async def test_revalidated_authorization_remains_required_inside_owner_transaction(database, monkeypatch, identity):
    if identity.startswith("direct"):
        await seed_direct(database)
    else:
        await seed(database)
    async def reject(_database):
        raise HTTPException(403, detail={"code": "fulfillment_permission_required"})
    async def owner_callback(db, owner, callback):
        return await callback(db)
    monkeypatch.setattr(operations, "operational_owner", owner_callback)
    with pytest.raises(HTTPException) as error:
        await operations._mark_assembly_piece_ready(
            database, user_id=OWNER, piece_id=identity, client_request_id="unauthorized",
            actor_id="assembly-worker", actor_name="Synthetic worker", authorize=reject,
        )
    assert error.value.status_code == 403
    assert await database[operations.PIECE_EVENTS].count_documents({}) == 0
