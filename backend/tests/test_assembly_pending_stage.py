"""Card hints describe the first missing stage without changing Ready authority."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import preparation_piece_operations as operations
from supplier_receiving_routes import supplier_service_completion_update
from tests.test_assembly_source_pending_message import SearchDB, OWNER, NUMBER, LINE
from tests.test_preparation_piece_operations import _canonical_assembly_source


NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


def assigned_piece(**changes):
    return {
        "piece_id": "piece-1", "user_id": OWNER, "order_number": NUMBER,
        "order_item_id": LINE, "unit_index": 1,
        "responsible_employee_id": "employee-1", "responsible_employee_name": "هلال",
        "assignment_status": "assigned", "status": "assigned",
        "execution_status": "not_started", "services": [],
        **changes,
    }


def received_piece(**changes):
    return assigned_piece(**{
        "status": "received", "execution_status": "received_from_supplier",
        "supplier_id": "supplier-1", "supplier_name": "المورد السابق",
        "supplier_dispatch_status": "received", "received_at": NOW,
        **changes,
    })


def custody_piece(**changes):
    return received_piece(**{
        "status": "ready_for_assembly", "preparation_receipt_status": "received",
        "preparation_received_at": NOW, "preparation_received_by": "assembly-employee",
        **changes,
    })


def partial_piece():
    """Use the real invoice producer, including removal of received_at."""
    piece = assigned_piece(
        supplier_id="supplier-1", supplier_dispatch_status="sent", received_at=NOW,
        services=[
            {"service_id": "sewing", "service_name": "الخياطة", "status": "pending", "required_quantity": 1},
            {"service_id": "name", "service_name": "تطريز الاسم", "status": "pending", "required_quantity": 1},
        ],
    )
    update = supplier_service_completion_update(
        piece=piece,
        invoice_line={"services": [{"service_id": "sewing", "service_name": "الخياطة"}]},
        session={"id": "session-1", "supplier_snapshot": {"id": "supplier-1", "company_name": "المورد السابق"}},
        actor={"id": "receiving-employee", "name": "شهاب"},
        invoice_id="invoice-1", completed_at=NOW,
    )
    piece.update(update["$set"])
    for key in update.get("$unset", {}):
        piece.pop(key, None)
    for key, value in update.get("$push", {}).items():
        piece.setdefault(key, []).append(value)
    return piece


@pytest.fixture
def search(monkeypatch):
    async def read(pieces, *, status="in_progress", receipt_mode="required", source_change=None,
                   workflow_change=None, native_label=None, actor_id=""):
        source = _canonical_assembly_source(NUMBER, status)
        raw = source.row["raw_by_source"]["salla_direct"]
        raw.update({"date": "2026-11-01T09:00:00+00:00", "items": [{"id": "supplier-line", "quantity": 2}]})
        if native_label:
            raw["status"]["name"] = native_label
        if source_change:
            source_change(source.row)
        monkeypatch.setattr(operations, "_current_assembly_order", AsyncMock(return_value=SimpleNamespace(
            status=status, status_native=status,
            items=[SimpleNamespace(order_item_id=LINE, quantity=2, options=[])],
            created_at=None, shipping=SimpleNamespace(company=None),
        )))
        db = SearchDB(source, pieces, receipt_mode=receipt_mode)
        db[operations.WORKFLOWS].row.update(workflow_change or {})
        before = deepcopy((source.row, pieces, db[operations.WORKFLOWS].row))
        result = await operations._assembly_search(db, user_id=OWNER, query=NUMBER, actor_id=actor_id)
        assert (source.row, pieces, db[operations.WORKFLOWS].row) == before
        # Existing two source reads and one settings read; a hint adds no I/O.
        assert source.find_one_queries == [{"user_id": OWNER, "order_number": NUMBER}] * 2
        assert db.settings.find_one_queries == [{"user_id": OWNER}]
        return result["pieces"]
    return read


@pytest.mark.asyncio
@pytest.mark.parametrize("piece, expected", [
    (assigned_piece(responsible_employee_id=None, assignment_status="unassigned_after_rejection"), "employee_assignment"),
    (assigned_piece(), "supplier_assignment"),
    (assigned_piece(supplier_id="supplier-1", supplier_dispatch_status="sent"), "supplier_receipt"),
    (received_piece(), "preparation_receipt"),
])
async def test_earliest_product_stage_precedes_ineligible_salla_status(search, piece, expected):
    row, = await search([piece], status="under_review", native_label="انتظار المراجعة")
    assert row["assembly_pending_stage"]["code"] == expected
    assert row["can_mark_ready"] is False
    assert row["assembly_blocker_code"] == "assembly_order_not_ready"


@pytest.mark.asyncio
async def test_committed_partial_product_receipt_names_remaining_service(search):
    piece = partial_piece()
    assert "received_at" not in piece
    row, = await search([piece], status="under_review", native_label="انتظار المراجعة")
    hint = row["assembly_pending_stage"]
    assert hint["code"] == "service_receipt"
    assert "تطريز الاسم" in hint["title"]
    assert "إسناد" not in hint["title"]
    assert "المورد السابق" not in str(hint)
    assert row["can_mark_ready"] is False
    assert row["assembly_blocker_code"] == "assembly_order_not_ready"


@pytest.mark.asyncio
async def test_next_service_receiving_draft_does_not_erase_committed_product_receipt(search):
    piece = partial_piece()
    piece.update(execution_status="supplier_receiving_draft", supplier_receiving_session_id="next-draft")
    row, = await search([piece])
    assert row["assembly_pending_stage"]["code"] == "service_receipt"
    assert "تطريز الاسم" in row["assembly_pending_stage"]["title"]
    assert row["assembly_blocker_code"] == "assembly_piece_supplier_receipt_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes, expected", [
    ({"responsible_employee_id": None, "assignment_status": "unassigned_after_rejection",
      "supplier_dispatch_status": None, "supplier_id": None}, "employee_assignment"),
    ({"assignment_status": "assigned", "status": "assigned", "execution_status": "not_started",
      "supplier_dispatch_status": None, "supplier_id": None}, "supplier_assignment"),
    ({"status": "in_progress", "execution_status": "sent_to_supplier",
      "supplier_dispatch_status": "sent", "supplier_id": "new-supplier"}, "supplier_receipt"),
])
async def test_old_receipt_history_cannot_skip_current_rejection_or_redispatch(search, changes, expected):
    piece = partial_piece()
    piece.update(changes)
    row, = await search([piece])
    assert row["assembly_pending_stage"]["code"] == expected
    assert row["can_mark_ready"] is False


@pytest.mark.asyncio
async def test_first_supplier_draft_is_not_a_product_receipt(search):
    piece = assigned_piece(
        supplier_id="supplier-1", supplier_dispatch_status="sent", execution_status="supplier_receiving_draft",
        supplier_receiving_session_id="draft-1", receipt_event_id="draft-event",
        services=[{"service_name": "تطريز الاسم", "status": "completed"}],
    )
    row, = await search([piece])
    assert row["assembly_pending_stage"]["code"] == "supplier_receipt"


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_mode,piece", [
    ("optional", received_piece()), ("required", custody_piece()),
])
async def test_only_complete_applicable_stages_show_actual_salla_status(search, receipt_mode, piece):
    row, = await search([piece], status="under_review", native_label="انتظار المراجعة", receipt_mode=receipt_mode)
    assert row["assembly_pending_stage"] == {
        "code": "salla_status", "title": "المنتج مكتمل — حالة الطلب غير مؤهلة",
        "detail": "سلة: انتظار المراجعة · حوّله لقيد التنفيذ",
    }
    assert row["can_mark_ready"] is False
    assert row["assembly_blocker_code"] == "assembly_order_not_ready"


@pytest.mark.asyncio
async def test_quantity_complete_service_uses_existing_authoritative_predicate(search):
    piece = custody_piece(services=[{
        "service_name": "تطريز الاسم", "status": "pending", "required_quantity": 2, "completed_quantity": 2,
    }])
    row, = await search([piece], status="under_review")
    assert row["assembly_pending_stage"]["code"] == "salla_status"
    assert "بانتظار المراجعة" in row["assembly_pending_stage"]["detail"]


@pytest.mark.asyncio
async def test_direct_and_operational_cards_skip_nonapplicable_supplier_stages(search):
    rows = await search([], status="under_review", workflow_change={
        "items": [{"order_item_id": LINE, "quantity": 1, "preparation_route": "direct_assembly"}],
        "operational_items": [{"operational_item_id": "operational-1", "name": "تجهيز بطاقة"}],
    })
    assert len(rows) == 2
    assert all(row["assembly_pending_stage"]["code"] == "salla_status" for row in rows)
    assert all(row["assembly_blocker_code"] == "assembly_salla_in_progress_required" for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("piece", [received_piece(), custody_piece(assembly_status="ready")])
async def test_existing_ready_or_actionable_piece_has_no_new_pending_stage(search, piece):
    row, = await search([piece], receipt_mode="optional")
    assert row["assembly_pending_stage"] is None
    assert row["can_mark_ready"] is True


@pytest.mark.asyncio
async def test_hold_has_precedence_over_missing_employee_or_status(search):
    row, = await search([assigned_piece(active_hold_id="hold-1", responsible_employee_id=None)], status="under_review")
    assert row["assembly_pending_stage"]["code"] == "stopped"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda source: source.update(order_status="cancelled"),
    lambda source: source["raw_by_source"]["salla_direct"].pop("status"),
    lambda source: source["raw_by_source"]["salla_direct"]["items"][0].update(id="removed-line"),
])
async def test_unknown_or_removed_current_source_never_claims_product_complete(search, change):
    row, = await search([custody_piece()], status="under_review", source_change=change)
    assert row["assembly_pending_stage"]["code"] == "evidence_unknown"
    assert "المنتج مكتمل" not in row["assembly_pending_stage"]["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("piece", [
    received_piece(received_at=None),
    assigned_piece(supplier_id="supplier-1", supplier_dispatch_status="partial_received", execution_status="awaiting_remaining_services"),
    received_piece(services=None),
    custody_piece(preparation_received_at=None),
])
async def test_incomplete_receipt_or_service_evidence_never_claims_complete(search, piece):
    row, = await search([piece], status="under_review")
    assert row["assembly_pending_stage"]["code"] in {"evidence_unknown", "supplier_receipt", "preparation_receipt"}
    assert "المنتج مكتمل" not in row["assembly_pending_stage"]["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("piece, detail", [
    (assigned_piece(), "كلّم هلال يسند المنتج للمورد"),
    (assigned_piece(supplier_id="supplier-1", supplier_dispatch_status="sent"),
     "كلّم هلال يستلم المنتج من المورد"),
    (partial_piece(), "كلّم هلال يستلم خدمة تطريز الاسم من المورد"),
])
async def test_short_reason_guides_actual_assigned_employee(search, piece, detail):
    row, = await search([piece])
    assert row["assembly_pending_stage"]["detail"] == detail


@pytest.mark.asyncio
@pytest.mark.parametrize("name, expected", [("عرفات", "عرفات"), (None, "موظف التجهيز"), ("—", "موظف التجهيز")])
async def test_service_guidance_uses_real_name_or_explicit_generic_fallback(search, name, expected):
    piece = partial_piece()
    piece["responsible_employee_name"] = name
    row, = await search([piece])
    assert row["assembly_pending_stage"]["detail"] == f"كلّم {expected} يستلم خدمة تطريز الاسم من المورد"
    assert "إسناد" not in row["assembly_pending_stage"]["detail"]


@pytest.mark.asyncio
async def test_unassigned_piece_does_not_guide_to_stale_employee_name(search):
    piece = partial_piece()
    piece.update(assignment_status="unassigned_after_rejection", responsible_employee_id=None)
    row, = await search([piece])
    assert row["assembly_pending_stage"] == {
        "code": "employee_assignment", "title": "لم يُسند المنتج لموظف التجهيز",
    }


@pytest.mark.asyncio
async def test_source_reconciliation_message_precedes_inferred_stage(search):
    # Legacy ready_for_assembly alone is insufficient receipt proof; keep the
    # existing source hold rather than guessing a missing supplier assignment.
    row, = await search([assigned_piece(status="ready_for_assembly")], source_change=(
        lambda source: source.update(g47_salla_snapshot={"component_pending": True})
    ))
    assert row["assembly_blocker_code"] == "assembly_source_reconciliation_pending"
    assert row["assembly_pending_stage"]["code"] == "source_reconciliation"


@pytest.mark.asyncio
async def test_in_progress_alias_disagreement_never_instructs_convert_to_same_status(search):
    row, = await search([custody_piece()], status="processing")
    assert row["can_mark_ready"] is False
    assert row["assembly_pending_stage"] == {
        "code": "evidence_unknown", "title": "حالة الطلب في سلة غير مؤكدة",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment_status", [None, "assigned"])
async def test_missing_employee_metadata_is_unknown_without_explicit_unassignment(search, assignment_status):
    row, = await search([assigned_piece(responsible_employee_id=None, assignment_status=assignment_status)])
    assert row["assembly_pending_stage"]["code"] == "evidence_unknown"
    assert "لم يُسند" not in row["assembly_pending_stage"]["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"status": "assigned"}, {"execution_status": "sent_to_supplier"}])
async def test_contradictory_receipt_state_cannot_claim_only_service_is_missing(search, changes):
    piece = received_piece(services=[{"service_name": "تطريز الاسم", "status": "pending"}], **changes)
    row, = await search([piece], status="under_review")
    assert row["assembly_pending_stage"]["code"] == "evidence_unknown"


@pytest.mark.asyncio
async def test_existing_instruction_block_gets_short_truthful_explanation(search, monkeypatch):
    instruction = {
        "id": "instruction-1", "status": "active", "scope": "order",
        "target_stages": ["assembly_labeling"], "action_type": "order_adjustment",
        "enforcement": "completion_required", "body": "راجع تعليمات الطلب",
    }
    monkeypatch.setattr(operations, "active_stage_instructions", AsyncMock(return_value=[instruction]))
    row, = await search([custody_piece()], actor_id="assembly-employee")
    assert row["assembly_pending_stage"] == {
        "code": "instructions", "title": "بانتظار تنفيذ تعليمات الطلب",
    }
    assert row["can_mark_ready"] is False
    assert row["assembly_blocker_code"] == "customer_service_instruction_action_required"
    assert row["instructions"][0]["id"] == "instruction-1"


@pytest.mark.asyncio
async def test_removed_current_partial_line_does_not_guide_service_receipt(search):
    row, = await search([partial_piece()], source_change=(
        lambda source: source["raw_by_source"]["salla_direct"]["items"][0].update(id="replacement-line")
    ))
    assert row["assembly_pending_stage"]["code"] == "evidence_unknown"
    assert "يستلم" not in str(row["assembly_pending_stage"])


@pytest.mark.asyncio
async def test_removed_current_direct_line_does_not_claim_product_complete(search):
    row, = await search([], status="under_review", workflow_change={
        "items": [{"order_item_id": "salla:10452:removed-line", "quantity": 1, "preparation_route": "direct_assembly"}],
    })
    assert row["assembly_pending_stage"]["code"] == "evidence_unknown"
    assert "المنتج مكتمل" not in row["assembly_pending_stage"]["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", [3, 1.5])
async def test_partial_receipt_guidance_requires_current_whole_unit(search, unit):
    piece = partial_piece()
    piece["unit_index"] = unit
    row, = await search([piece])
    assert row["assembly_pending_stage"]["code"] == "evidence_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("piece", [assigned_piece(), partial_piece(), custody_piece(assembly_status="ready")])
async def test_cancelled_order_never_requests_assignment_receipt_or_reopening(search, piece):
    row, = await search([piece], status="cancelled", native_label="ملغى")
    assert row["assembly_pending_stage"] == {
        "code": "salla_status", "title": "الطلب ملغي", "detail": "لا يحتاج شحن",
    }
    assert row["can_mark_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status,label", [
    ("completed", "تم التنفيذ"), ("shipped", "تم الشحن"),
    ("delivering", "جاري التوصيل"), ("delivered", "تم التوصيل"),
])
async def test_advanced_status_precedes_missing_stages_without_reverting_order(search, status, label):
    row, = await search([assigned_piece()], status=status, native_label=label)
    assert row["assembly_pending_stage"] == {"code": "salla_status", "title": f"حالة الطلب: {label}"}
    assert "قيد التنفيذ" not in str(row["assembly_pending_stage"])
    assert "يسند" not in str(row["assembly_pending_stage"])
    assert "يستلم" not in str(row["assembly_pending_stage"])
    assert row["can_mark_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status,label", [
    ("out_for_delivery", "جاري التوصيل"), ("returned", "مسترجع"),
    ("restored", "تم الاسترجاع"), ("refunded", "مسترجع"),
])
async def test_existing_terminal_aliases_keep_actual_native_name_without_reopening(search, status, label):
    row, = await search([assigned_piece()], status=status, native_label=label)
    assert row["assembly_pending_stage"] == {"code": "salla_status", "title": f"حالة الطلب: {label}"}


@pytest.mark.asyncio
async def test_canceled_alias_has_same_no_shipping_guidance(search):
    row, = await search([assigned_piece()], status="canceled", native_label="ملغى")
    assert row["assembly_pending_stage"] == {
        "code": "salla_status", "title": "الطلب ملغي", "detail": "لا يحتاج شحن",
    }


@pytest.mark.asyncio
async def test_pending_payment_never_recommends_forced_status_transition(search):
    row, = await search([custody_piece()], status="pending_payment", native_label="بانتظار الدفع")
    assert row["assembly_pending_stage"] == {
        "code": "salla_status", "title": "حالة الطلب: بانتظار الدفع", "detail": "تحقق من حالة الدفع",
    }
    assert row["can_mark_ready"] is False


@pytest.mark.asyncio
async def test_terminal_slug_without_native_name_uses_existing_arabic_label(search):
    row, = await search([assigned_piece()], status="out_for_delivery", source_change=(
        lambda source: source["raw_by_source"]["salla_direct"]["status"].pop("name")
    ))
    assert row["assembly_pending_stage"] == {"code": "salla_status", "title": "حالة الطلب: جاري التوصيل"}
