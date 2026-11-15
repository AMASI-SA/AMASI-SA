"""Read-only assembly diagnostics must explain source holds without opening Ready."""
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
            status="in_progress", status_native="in_progress", items=[],
            created_at=None, shipping=SimpleNamespace(company=None),
        ),
    ))
    source = _canonical_assembly_source(NUMBER)
    received = {
        "piece_id": "received", "order_number": NUMBER,
        "status": operations.PIECE_STATUS_RECEIVED,
        "execution_status": "received_from_supplier",
        "supplier_dispatch_status": "received",
        "received_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "services": [], "responsible_employee_id": "employee",
    }
    waiting = {
        "piece_id": "waiting", "order_number": NUMBER,
        "status": operations.PIECE_STATUS_IN_PROGRESS,
        "supplier_dispatch_status": "sent", "services": [],
    }
    return source, received, waiting


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["component_pending", "requires_authoritative_refresh"])
async def test_received_piece_explains_source_hold_and_waiting_piece_retains_receipt_hold(search_context, flag):
    source, received, waiting = search_context
    source.row["g47_salla_snapshot"] = {flag: True}
    before_source, before_pieces = deepcopy(source.row), deepcopy([received, waiting])
    db = SearchDB(source, [received, waiting])

    result = await operations._assembly_search(db, user_id=OWNER, query=NUMBER)

    rows = {row["piece_id"]: row for row in result["pieces"]}
    assert rows["received"]["can_mark_ready"] is False
    assert rows["received"]["assembly_blocker_code"] == "assembly_source_reconciliation_pending"
    assert "بيانات الطلب" in rows["received"]["assembly_blocker_message"]
    assert rows["waiting"]["can_mark_ready"] is False
    assert rows["waiting"]["assembly_blocker_code"] == "assembly_piece_supplier_receipt_required"
    # The existing search reads canonical status and the Salla admin link;
    # explaining the hold does not add a third source query or per-piece reads.
    assert source.find_one_queries == [{"user_id": OWNER, "order_number": NUMBER}] * 2
    assert source.row == before_source
    assert [received, waiting] == before_pieces


@pytest.mark.asyncio
async def test_source_hold_clearing_only_opens_the_received_piece(search_context):
    source, received, waiting = search_context
    source.row["g47_salla_snapshot"] = {"component_pending": True}
    db = SearchDB(source, [received, waiting])
    before = await operations._assembly_search(db, user_id=OWNER, query=NUMBER)
    assert not any(row["can_mark_ready"] for row in before["pieces"])

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
