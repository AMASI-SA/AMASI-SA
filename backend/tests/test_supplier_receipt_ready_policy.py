"""Supplier Ready policy; in-memory writes supplement the real-Mongo CI gate."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import preparation_piece_operations as operations
import stock_component_consumption_service as stock
from review_local_policy import LOCAL_COMPLETION_MODE
from stock_component_consumption_service import LOCATIONS, PLANS, UNITS
from supplier_receipt_ready_policy import (
    POLICY,
    supplier_completion_covered,
    supplier_current_piece_blocker,
    supplier_piece_received,
    supplier_ready_eligible,
    supplier_status,
)


OWNER = "supplier-policy-owner"
NUMBER = "supplier-policy-order"
LINE = f"salla:{NUMBER}:line"
WHEN = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)
MEMBERSHIP_FAILURES = [
    "raw_reference_mismatch", "missing_native_id", "source_other_owner",
    "source_other_order", "workflow_other_owner", "workflow_other_order",
    "removed_canonical_line", "nonintegral_unit", "zero_unit", "unit_over_quantity",
    "current_quantity_decreased", "nonintegral_quantity", "zero_quantity",
]


def source_order(*, quantity=2, status="in_progress"):
    return {
        "user_id": OWNER, "order_number": NUMBER,
        "order_status": status, "order_status_slug": status,
        "g47_salla_snapshot": {
            "revision": 91, "component_pending": True,
            "requires_authoritative_refresh": True,
        },
        "raw_by_source": {"salla_direct": {
            "id": "internal-supplier-policy", "reference_id": NUMBER,
            "date": "2026-11-18T09:00:00+00:00",
            "status": {"slug": status},
            "items": [{"id": "line", "quantity": quantity}],
        }},
    }


def received_piece(index=1, *, ready=False):
    return {
        "piece_id": f"supplier-piece-{index}", "user_id": OWNER,
        "order_number": NUMBER, "order_item_id": LINE, "unit_index": index,
        "status": "ready_for_assembly", "supplier_dispatch_status": "received",
        "execution_status": "received_from_supplier", "received_at": WHEN,
        "preparation_receipt_status": "received", "preparation_received_at": WHEN,
        "preparation_received_by": "preparation-worker",
        "services": [{"service_id": "embroidery", "status": "completed"}],
        "assembly_status": "ready" if ready else "pending",
        **({"assembly_ready_basis": POLICY} if ready else {}),
    }


def workflow():
    return {"user_id": OWNER, "order_number": NUMBER, "stage": "in_progress", "revision": 0}


def current_order(*, quantity=2, status="in_progress", paid=True):
    return SimpleNamespace(
        status=status, status_native=status, created_at=None,
        items=[SimpleNamespace(order_item_id=LINE, quantity=quantity, options=[])],
        payment=SimpleNamespace(method="card", status="paid" if paid else "pending",
                                collection_status="paid" if paid else "unpaid"),
        shipping=SimpleNamespace(company=None, address=SimpleNamespace(city="Riyadh", street="Synthetic street")),
    )


def invalid_membership(case):
    """Use a clean component source so the old Ready path cannot mask a failure."""
    source, work, piece = source_order(), workflow(), received_piece()
    source["g47_salla_snapshot"].update(component_pending=False, requires_authoritative_refresh=False)
    raw = source["raw_by_source"]["salla_direct"]
    if case == "raw_reference_mismatch":
        raw["reference_id"] = "other-order"
    elif case == "missing_native_id":
        raw.pop("id")
    elif case == "source_other_owner":
        source["user_id"] = "other-owner"
    elif case == "source_other_order":
        source["order_number"] = "other-order"
    elif case == "workflow_other_owner":
        work["user_id"] = "other-owner"
    elif case == "workflow_other_order":
        work["order_number"] = "other-order"
    elif case == "removed_canonical_line":
        raw["items"][0]["id"] = "replacement-line"
    elif case == "nonintegral_unit":
        piece["unit_index"] = 1.5
    elif case == "zero_unit":
        piece["unit_index"] = 0
    elif case == "unit_over_quantity":
        piece["unit_index"] = 3
    elif case == "current_quantity_decreased":
        piece["unit_index"] = 2
        raw["items"][0]["quantity"] = 1
    elif case == "nonintegral_quantity":
        raw["items"][0]["quantity"] = 1.5
    elif case == "zero_quantity":
        raw["items"][0]["quantity"] = 0
    else:
        raise AssertionError(f"Unknown membership case: {case}")
    return source, work, piece


def test_supplier_policy_keeps_source_and_receipt_evidence_unchanged():
    source, work, piece = source_order(), workflow(), received_piece()
    before = deepcopy((source, work, piece))

    assert supplier_status(source) == "in_progress"
    assert supplier_ready_eligible(source, work, piece, current_order=current_order()) is True

    assert (source, work, piece) == before


@pytest.mark.parametrize("case", MEMBERSHIP_FAILURES)
def test_supplier_ready_requires_current_tenant_order_line_and_unit_membership(case):
    source, work, piece = invalid_membership(case)
    before = deepcopy((source, work, piece))

    # All receipt, service, payment and status facts remain otherwise eligible.
    assert supplier_status(source) == "in_progress"
    assert supplier_piece_received(piece) is True
    assert supplier_current_piece_blocker(source, work, piece) == "assembly_order_not_ready"
    assert supplier_ready_eligible(source, work, piece, current_order=current_order()) is False
    assert (source, work, piece) == before


@pytest.mark.parametrize("status", ["cancelled", "completed", "delivered", "shipped", "under_review"])
def test_supplier_receipt_never_replaces_current_in_progress_status(status):
    source = source_order(status=status)
    assert supplier_ready_eligible(source, workflow(), received_piece(), current_order=current_order()) is False


@pytest.mark.parametrize("change", ["missing_provider", "conflicting_root", "conflicting_provider", "missing_source"])
def test_supplier_status_requires_coherent_provider_and_local_evidence(change):
    source = source_order()
    if change == "missing_provider":
        source["raw_by_source"]["salla_direct"].pop("status")
    elif change == "conflicting_root":
        source["order_status"] = "delivered"
    elif change == "conflicting_provider":
        source["raw_by_source"]["salla_direct"]["status_slug"] = "cancelled"
    else:
        source = None

    assert supplier_status(source) is None
    assert supplier_ready_eligible(source, workflow(), received_piece()) is False


@pytest.mark.parametrize("change", [
    {"supplier_dispatch_status": "sent"},
    {"received_at": None},
    {"preparation_receipt_status": "pending"},
    {"preparation_received_at": None},
    {"preparation_received_by": ""},
    {"services": [{"service_id": "embroidery", "status": "pending"}]},
    {"active_hold_id": "supplier-hold"},
    {"status": "cancelled"},
    {"experiment_archived_at": WHEN},
    {"virtual_kind": "direct_assembly"},
    {"virtual_kind": "operational"},
])
def test_supplier_piece_requires_real_complete_custody(change):
    piece = {**received_piece(), **change}
    assert supplier_piece_received(piece) is False
    assert supplier_ready_eligible(source_order(), workflow(), piece) is False


def test_optional_employee_receipt_does_not_manufacture_employee_custody():
    piece = received_piece()
    piece.update(status="received", preparation_receipt_status="pending",
                 preparation_received_at=None, preparation_received_by=None)
    before = deepcopy(piece)

    assert supplier_piece_received(piece) is False
    assert supplier_piece_received(piece, preparation_optional=True) is True
    assert supplier_ready_eligible(source_order(), workflow(), piece, preparation_optional=True) is True
    assert piece == before


@pytest.mark.parametrize("change", [
    {"supplier_dispatch_status": "sent"}, {"received_at": None},
    {"services": [{"service_id": "embroidery", "status": "pending"}]},
    {"active_hold_id": "hold"}, {"status": "cancelled"},
])
def test_optional_employee_receipt_preserves_supplier_service_and_hold_guards(change):
    assert supplier_piece_received({**received_piece(), **change}, preparation_optional=True) is False


def test_local_review_payment_remains_required_after_component_policy_change():
    work = {**workflow(), "completion_mode": LOCAL_COMPLETION_MODE}
    assert supplier_ready_eligible(source_order(), work, received_piece(), current_order=current_order()) is True
    assert supplier_ready_eligible(source_order(), work, received_piece(), current_order=current_order(paid=False)) is False
    assert supplier_ready_eligible(source_order(), work, received_piece(), current_order=None) is False


@pytest.mark.parametrize("change", [{"stage": "cancelled"}, {"completion_mode": "unknown-mode"}])
def test_supplier_policy_does_not_open_invalid_workflow(change):
    assert supplier_ready_eligible(source_order(), {**workflow(), **change}, received_piece()) is False


def test_completed_current_units_are_covered_without_component_plan_or_source_ack():
    source = source_order()
    pieces = [received_piece(1, ready=True), received_piece(2, ready=True)]
    before = deepcopy((source, pieces))

    assert supplier_completion_covered(source, pieces, owner=OWNER, number=NUMBER) is True
    assert (source, pieces) == before


def test_supplier_completion_includes_ready_direct_and_operational_work():
    direct = {"piece_id": "direct-2", "order_number": NUMBER, "order_item_id": LINE,
              "unit_index": 2, "virtual_kind": "direct_assembly", "assembly_status": "ready"}
    note = {"piece_id": "gift-note", "order_number": NUMBER,
            "virtual_kind": "operational", "assembly_status": "ready"}
    pieces = [received_piece(1, ready=True), direct, note]
    assert supplier_completion_covered(source_order(), pieces, owner=OWNER, number=NUMBER) is True
    for index in (1, 2):
        incomplete = deepcopy(pieces)
        incomplete[index]["assembly_status"] = "pending"
        assert supplier_completion_covered(source_order(), incomplete, owner=OWNER, number=NUMBER) is False


@pytest.mark.parametrize("case", [
    "missing_unit", "duplicate_unit", "extra_unit", "unready_unit", "foreign_owner",
    "foreign_order", "unreceived_unit", "pending_service", "held_unit", "archived_unit",
    "missing_policy", "changed_current_quantity", "new_current_product", "missing_current_items",
])
def test_supplier_completion_requires_exact_current_ready_coverage(case):
    source = source_order()
    pieces = [received_piece(1, ready=True), received_piece(2, ready=True)]
    if case == "missing_unit":
        pieces.pop()
    elif case == "duplicate_unit":
        pieces[1]["unit_index"] = 1
    elif case == "extra_unit":
        pieces.append(received_piece(3, ready=True))
    elif case == "unready_unit":
        pieces[1]["assembly_status"] = "pending"
    elif case == "foreign_owner":
        pieces[1]["user_id"] = "other-owner"
    elif case == "foreign_order":
        pieces[1]["order_number"] = "other-order"
    elif case == "unreceived_unit":
        pieces[1]["received_at"] = None
    elif case == "pending_service":
        pieces[1]["services"][0]["status"] = "pending"
    elif case == "held_unit":
        pieces[1]["active_hold_id"] = "new-hold"
    elif case == "archived_unit":
        pieces[1]["experiment_archived_at"] = WHEN
    elif case == "missing_policy":
        for piece in pieces:
            piece.pop("assembly_ready_basis")
    elif case == "changed_current_quantity":
        source["raw_by_source"]["salla_direct"]["items"][0]["quantity"] = 3
    elif case == "new_current_product":
        source["raw_by_source"]["salla_direct"]["items"].append({"id": "new-line", "quantity": 1})
    else:
        source["raw_by_source"]["salla_direct"].pop("items")

    assert supplier_completion_covered(source, pieces, owner=OWNER, number=NUMBER) is False


@pytest.fixture
def policy_database(monkeypatch):
    """No socket or owner-transaction claim: exercise actual body/storage effects."""
    client = AsyncMongoMockClient()
    db = client["supplier_ready_policy_unit"]
    monkeypatch.setattr(operations, "_current_assembly_order", AsyncMock(return_value=current_order()))
    monkeypatch.setattr(operations, "enforce_stage_instructions", AsyncMock())

    async def invoke_owner_callback(database, owner, callback):
        assert owner == OWNER
        return await callback(database)

    # The outer body is already called directly in these unit tests. Retain the
    # real stock preflight, replacing only its replica-set transaction boundary.
    monkeypatch.setattr(stock, "operational_owner", invoke_owner_callback)
    for name in ("_consume_piece_components", "_assert_ready_piece_components", "build_order_fulfillment_decision"):
        monkeypatch.setattr(operations, name, AsyncMock(side_effect=AssertionError(
            "Supplier receipt Ready must not invoke component execution or rebuild fulfillment")))
    yield db
    client.close()


async def seed_policy_database(db, *, quantity=2, complete_first=False):
    await db.unified_orders.insert_one(source_order(quantity=quantity))
    await db[operations.WORKFLOWS].insert_one(workflow())
    await db.settings.insert_one({"user_id": OWNER, "operational_flow": {
        "preparation_employee_receipt_mode": "required", "supplier_assignment_mode": "required",
    }})
    pieces = [received_piece(1, ready=complete_first)]
    if complete_first:
        pieces.append(received_piece(2))
    await db[operations.PIECES].insert_many(pieces)
    await db[PLANS].insert_one({"_id": "cancelled-plan", "user_id": OWNER, "order_id": NUMBER, "state": "cancelled"})
    await db[UNITS].insert_one({"user_id": OWNER, "plan_id": "cancelled-plan", "order_line_id": LINE,
                              "unit_index": 1, "state": "reserved"})
    await db[LOCATIONS].insert_one({"user_id": OWNER, "id": "warehouse", "occupancy": {"total_quantity": 20}})


async def stock_snapshot(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(100)
            for name in (PLANS, UNITS, LOCATIONS)}


async def mark(db, piece_id="supplier-piece-1", *, owner=OWNER):
    return await operations._mark_assembly_piece_ready_in_transaction(
        db, user_id=owner, piece_id=piece_id, client_request_id="supplier-ready-request",
        actor_id="assembly-worker", actor_name="Synthetic worker",
    )


@pytest.mark.asyncio
async def test_existing_ready_supplier_piece_promotes_reviewed_workflow_on_retry(policy_database):
    db = policy_database
    await seed_policy_database(db, quantity=1)
    await db[operations.PIECES].update_one({}, {"$set": {"assembly_status": "ready"}})
    await db[operations.WORKFLOWS].update_one({}, {"$set": {"stage": "reviewed"}})
    before_stock = await stock_snapshot(db)

    result = await mark(db)

    assert result["idempotent"] is True
    assert result["progress"]["order_completed"] is True
    saved = await db[operations.WORKFLOWS].find_one({})
    assert saved["stage"] == "completed"
    assert saved["assembly_completion_basis"] == POLICY
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == 1
    assert await db[operations.PIECE_EVENTS].count_documents({}) == 0
    assert await stock_snapshot(db) == before_stock


class RejectWritesDatabase:
    """Fail on any write attempt, including a no-op update of an existing row."""
    def __init__(self, database):
        self.database = database

    def __getitem__(self, name):
        collection = self.database[name]

        class ReadOnlyCollection:
            def __getattr__(self, operation):
                if operation in {
                    "insert_one", "insert_many", "update_one", "update_many", "replace_one",
                    "delete_one", "delete_many", "find_one_and_update", "find_one_and_replace",
                    "find_one_and_delete", "bulk_write",
                }:
                    raise AssertionError(f"Rejected supplier membership attempted {name}.{operation}")
                return getattr(collection, operation)

        return ReadOnlyCollection()

    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", MEMBERSHIP_FAILURES)
async def test_supplier_ready_write_rejects_noncurrent_membership_before_any_write(policy_database, case):
    db = policy_database
    await seed_policy_database(db)
    source, work, piece = invalid_membership(case)
    await db.unified_orders.replace_one({}, source)
    await db[operations.WORKFLOWS].replace_one({}, work)
    await db[operations.PIECES].replace_one({}, piece)
    names = await db.list_collection_names()
    before = {name: await db[name].find({}).to_list(None) for name in names}

    with pytest.raises(HTTPException) as error:
        await mark(RejectWritesDatabase(db))

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "assembly_order_not_ready"
    assert {name: await db[name].find({}).to_list(None)
            for name in await db.list_collection_names()} == before
    assert await db[operations.PIECE_EVENTS].count_documents({}) == 0
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_supplier_progress_cannot_complete_a_truncated_ten_thousand_piece_scan(policy_database):
    db = policy_database
    await seed_policy_database(db, quantity=10000)
    pieces = [received_piece(index, ready=True) for index in range(1, 10002)]
    before_stock = await stock_snapshot(db)
    before_source = await db.unified_orders.find_one({})

    async def scanned_pieces(length):
        # Exercise the requested scan boundary without inserting 10,001 rows.
        # A limit of 10,000 would hide the extra unit and wrongly complete.
        return pieces[:length]

    class PieceScanDatabase:
        def __getitem__(self, name):
            if name == operations.PIECES:
                return SimpleNamespace(find=lambda *args, **kwargs: SimpleNamespace(to_list=scanned_pieces))
            return db[name]

    result = await operations._assembly_progress(
        PieceScanDatabase(), user_id=OWNER, order_number=NUMBER,
        actor_id="assembly-worker", actor_name="Synthetic worker", now=WHEN,
        eligible_order=current_order(quantity=10000),
    )

    assert result["order_completed"] is False
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == 0
    saved = await db[operations.WORKFLOWS].find_one({})
    assert saved["assembly_piece_count"] == 10001
    assert saved["assembly_status"] == "in_progress"
    assert saved["stage"] == "in_progress"
    assert "assembly_delivery" not in saved
    assert await stock_snapshot(db) == before_stock
    assert await db.unified_orders.find_one({}) == before_source


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_state", ["cancelled", "missing", "stale"])
async def test_ready_write_and_retry_preserve_pending_source_and_stock(policy_database, plan_state):
    db = policy_database
    await seed_policy_database(db)
    if plan_state == "missing":
        await db[PLANS].delete_many({})
    elif plan_state == "stale":
        await db[PLANS].update_one({}, {"$set": {"state": "accepted", "generation": "stale"}})
    before_stock = await stock_snapshot(db)
    before_source = await db.unified_orders.find_one({})

    first = await mark(db)
    retry = await mark(db)

    assert first["ok"] is True and first["idempotent"] is False
    assert retry["ok"] is True and retry["idempotent"] is True
    assert first["progress"]["order_completed"] is False
    assert await stock_snapshot(db) == before_stock
    assert await db.unified_orders.find_one({}) == before_source
    saved = await db[operations.PIECES].find_one({"piece_id": "supplier-piece-1"})
    assert saved["assembly_status"] == "ready"
    assert saved["assembly_ready_basis"] == POLICY
    assert await db[operations.PIECE_EVENTS].count_documents({"event_type": "assembly_piece_marked_ready"}) == 1
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_last_supplier_piece_completes_exact_current_order_without_stock_consumption(policy_database):
    db = policy_database
    await seed_policy_database(db, complete_first=True)
    before_stock = await stock_snapshot(db)
    before_source = await db.unified_orders.find_one({})

    result = await mark(db, "supplier-piece-2")

    assert result["progress"]["order_completed"] is True
    saved = await db[operations.WORKFLOWS].find_one({})
    assert saved["assembly_status"] == "completed"
    assert saved["assembly_completion_basis"] == POLICY
    assert await db[operations.SHIPPING_BATCHES].count_documents({}) == 1
    assert await stock_snapshot(db) == before_stock
    assert await db.unified_orders.find_one({}) == before_source


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "completed", "delivered"])
async def test_supplier_ready_write_retains_terminal_status_guard(policy_database, status):
    db = policy_database
    await seed_policy_database(db)
    await db.unified_orders.replace_one({"order_number": NUMBER}, source_order(status=status))
    before = await db[operations.PIECES].find_one({})
    before_stock = await stock_snapshot(db)

    with pytest.raises(HTTPException) as error:
        await mark(db)

    assert error.value.status_code == 409
    assert await db[operations.PIECES].find_one({}) == before
    assert await stock_snapshot(db) == before_stock
    assert await db[operations.PIECE_EVENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_supplier_ready_write_cannot_select_another_merchants_piece(policy_database):
    db = policy_database
    await seed_policy_database(db)
    before = await db[operations.PIECES].find_one({})

    with pytest.raises(HTTPException) as error:
        await mark(db, owner="another-merchant")

    assert error.value.status_code == 404
    assert await db[operations.PIECES].find_one({}) == before
    assert await db[operations.PIECE_EVENTS].count_documents({}) == 0
