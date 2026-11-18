"""Supplier receipt enables Ready independently of component/source matching."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import preparation_piece_operations as operations
from tests.test_preparation_piece_operations import (
    _AssemblySearchCollection,
    _canonical_assembly_source,
)


OWNER = "merchant-1"
NUMBER = "10452"
LINE = f"salla:{NUMBER}:supplier-line"


class SearchDB(dict):
    def __init__(self, source, pieces, *, receipt_mode="optional"):
        super().__init__({
            operations.WORKFLOWS: _AssemblySearchCollection(row={
                "user_id": OWNER, "order_number": NUMBER, "stage": "in_progress",
            }),
            operations.PIECES: _AssemblySearchCollection(rows=pieces),
            "unified_orders": source,
        })
        self.settings = _AssemblySearchCollection(row={"operational_flow": {
            "preparation_employee_receipt_mode": receipt_mode,
            "supplier_assignment_mode": "required",
        }})


@pytest.fixture
def search_context(monkeypatch):
    monkeypatch.setattr(operations, "_current_assembly_order", AsyncMock(
        return_value=SimpleNamespace(
            status="in_progress", status_native="in_progress", items=[
                SimpleNamespace(order_item_id=LINE, quantity=2, options=[]),
            ],
            created_at=None, shipping=SimpleNamespace(company=None),
        ),
    ))
    source = _canonical_assembly_source(NUMBER)
    source.row["raw_by_source"]["salla_direct"]["date"] = "2026-11-01T09:00:00+00:00"
    source.row["raw_by_source"]["salla_direct"]["items"] = [{"id": "supplier-line", "quantity": 2}]
    received = {
        "piece_id": "received", "user_id": OWNER, "order_number": NUMBER,
        "order_item_id": LINE, "unit_index": 1,
        "status": operations.PIECE_STATUS_RECEIVED,
        "execution_status": "received_from_supplier",
        "supplier_dispatch_status": "received",
        "received_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "services": [], "responsible_employee_id": "employee",
    }
    waiting = {
        "piece_id": "waiting", "user_id": OWNER, "order_number": NUMBER,
        "order_item_id": LINE, "unit_index": 2,
        "status": operations.PIECE_STATUS_IN_PROGRESS,
        "supplier_dispatch_status": "sent", "services": [],
    }
    return source, received, waiting


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["component_pending", "requires_authoritative_refresh"])
async def test_received_piece_can_be_ready_while_waiting_piece_retains_receipt_hold(search_context, flag):
    source, received, waiting = search_context
    source.row["g47_salla_snapshot"] = {flag: True}
    before_source, before_pieces = deepcopy(source.row), deepcopy([received, waiting])
    db = SearchDB(source, [received, waiting])

    result = await operations._assembly_search(db, user_id=OWNER, query=NUMBER)

    rows = {row["piece_id"]: row for row in result["pieces"]}
    assert rows["received"]["can_mark_ready"] is True
    assert rows["received"]["assembly_blocker_code"] is None
    assert rows["received"].get("assembly_blocker_message") is None
    assert rows["waiting"]["can_mark_ready"] is False
    assert rows["waiting"]["assembly_blocker_code"] == "assembly_piece_supplier_receipt_required"
    # The supplier policy reuses the canonical source read; it does not add
    # per-piece source queries or change either the source or receipt evidence.
    assert source.find_one_queries == [{"user_id": OWNER, "order_number": NUMBER}] * 2
    assert source.row == before_source
    assert [received, waiting] == before_pieces


@pytest.mark.asyncio
async def test_source_reconciliation_does_not_change_supplier_receipt_readiness(search_context):
    source, received, waiting = search_context
    source.row["g47_salla_snapshot"] = {"component_pending": True}
    db = SearchDB(source, [received, waiting])
    before = await operations._assembly_search(db, user_id=OWNER, query=NUMBER)
    before_rows = {row["piece_id"]: row for row in before["pieces"]}
    assert before_rows["received"]["can_mark_ready"] is True
    assert before_rows["waiting"]["can_mark_ready"] is False

    source.row["g47_salla_snapshot"]["component_pending"] = False
    after = await operations._assembly_search(db, user_id=OWNER, query=NUMBER)

    rows = {row["piece_id"]: row for row in after["pieces"]}
    assert rows["received"]["can_mark_ready"] is True
    assert rows["received"]["assembly_blocker_code"] is None
    assert rows["received"].get("assembly_blocker_message") is None
    assert rows["waiting"]["can_mark_ready"] is False
    assert rows["waiting"]["assembly_blocker_code"] == "assembly_piece_supplier_receipt_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_change", ["conflicting_status", "missing_provider_status", "completed"])
async def test_unproven_or_ineligible_status_is_not_reported_as_only_a_source_hold(search_context, source_change):
    source, received, _ = search_context
    source.row["g47_salla_snapshot"] = {"component_pending": True}
    if source_change == "conflicting_status":
        source.row["order_status"] = "delivered"
    elif source_change == "missing_provider_status":
        source.row["raw_by_source"]["salla_direct"].pop("status")
    else:
        source.row = _canonical_assembly_source(NUMBER, "completed").row
        source.row["g47_salla_snapshot"] = {"component_pending": True}

    result = await operations._assembly_search(SearchDB(source, [received]), user_id=OWNER, query=NUMBER)

    row = result["pieces"][0]
    assert row["can_mark_ready"] is False
    assert row["assembly_blocker_code"] == "assembly_order_not_ready"
    assert row.get("assembly_blocker_message") is None


@pytest.mark.asyncio
async def test_required_employee_receipt_is_not_hidden_by_source_message(search_context):
    source, received, _ = search_context
    source.row["g47_salla_snapshot"] = {"component_pending": True}

    result = await operations._assembly_search(
        SearchDB(source, [received], receipt_mode="required"), user_id=OWNER, query=NUMBER,
    )

    row = result["pieces"][0]
    assert row["can_mark_ready"] is False
    assert row["assembly_blocker_code"] == "assembly_piece_preparation_receipt_required"
    assert row.get("assembly_blocker_message") is None


@pytest.mark.asyncio
async def test_required_employee_custody_and_complete_services_enable_supplier_ready(search_context):
    source, received, _ = search_context
    source.row["g47_salla_snapshot"] = {
        "component_pending": True, "requires_authoritative_refresh": True,
    }
    received.update({
        "status": operations.PIECE_STATUS_READY_FOR_ASSEMBLY,
        "preparation_receipt_status": "received",
        "preparation_received_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "preparation_received_by": "employee",
        "services": [{"service_id": "embroidery", "status": "completed"}],
    })

    result = await operations._assembly_search(
        SearchDB(source, [received], receipt_mode="required"), user_id=OWNER, query=NUMBER,
    )

    row = result["pieces"][0]
    assert row["can_mark_ready"] is True
    assert row["assembly_blocker_code"] is None
    assert source.row["g47_salla_snapshot"]["component_pending"] is True
    assert source.row["g47_salla_snapshot"]["requires_authoritative_refresh"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"active_hold_id": "hold-1"},
    {"services": [{"service_id": "embroidery", "status": "pending"}]},
    {"received_at": None},
    {"supplier_dispatch_status": "sent"},
])
async def test_source_bypass_does_not_open_incomplete_or_held_supplier_piece(search_context, change):
    source, received, _ = search_context
    source.row["g47_salla_snapshot"] = {"component_pending": True}
    received.update(change)

    result = await operations._assembly_search(
        SearchDB(source, [received]), user_id=OWNER, query=NUMBER,
    )

    assert result["pieces"][0]["can_mark_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"services": [{"service_id": "embroidery", "status": "pending"}]},
    {"received_at": None},
])
async def test_clean_source_does_not_hide_incomplete_supplier_evidence_after_employee_receipt(search_context, change):
    source, received, _ = search_context
    received.update({
        "status": operations.PIECE_STATUS_READY_FOR_ASSEMBLY,
        "preparation_receipt_status": "received",
        "preparation_received_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "preparation_received_by": "employee",
        **change,
    })

    result = await operations._assembly_search(
        SearchDB(source, [received], receipt_mode="required"), user_id=OWNER, query=NUMBER,
    )

    assert result["pieces"][0]["can_mark_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["removed_line", "removed_unit", "foreign_reference"])
async def test_current_membership_blocks_received_piece_without_source_approval_gate(search_context, change):
    source, received, _ = search_context
    if change == "removed_line":
        source.row["raw_by_source"]["salla_direct"]["items"][0]["id"] = "replacement-line"
    elif change == "removed_unit":
        received["unit_index"] = 3
    else:
        source.row["raw_by_source"]["salla_direct"]["reference_id"] = "other-order"

    result = await operations._assembly_search(SearchDB(source, [received]), user_id=OWNER, query=NUMBER)

    assert result["pieces"][0]["can_mark_ready"] is False
    assert result["pieces"][0]["assembly_blocker_code"] == "assembly_order_not_ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage, expected", [
    ("reviewed", True), ("in_progress", True), ("ready_to_ship", True), ("completed", False),
])
async def test_received_ready_retry_is_available_for_unfinished_workflow(search_context, stage, expected):
    source, received, _ = search_context
    source.row["g47_salla_snapshot"] = {"component_pending": True}
    received.update({
        "status": operations.PIECE_STATUS_READY_FOR_ASSEMBLY,
        "preparation_receipt_status": "received",
        "preparation_received_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "preparation_received_by": "employee", "assembly_status": "ready",
    })
    db = SearchDB(source, [received], receipt_mode="required")
    db[operations.WORKFLOWS].row["stage"] = stage

    result = await operations._assembly_search(db, user_id=OWNER, query=NUMBER)

    row = result["pieces"][0]
    assert row["can_mark_ready"] is expected
    assert row["assembly_blocker_code"] == (None if expected else "assembly_piece_already_ready")
