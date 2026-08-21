import inspect

from order_tracking_notes import (
    instruction_applies_to,
    instruction_snapshot,
    instruction_targets,
)
from order_tracking_notes_routes import make_order_tracking_notes_router


def _instruction(**patch):
    return {
        "id": "instruction-1",
        "status": "active",
        "scope": "item",
        "target_ids": ["item-1", "item-2"],
        "target_stages": ["assembly_labeling"],
        "enforcement": "completion_required",
        **patch,
    }


def test_item_instruction_supports_multiple_products_and_exact_stage():
    row = _instruction()

    assert instruction_applies_to(
        row,
        stage="assembly_labeling",
        order_item_id="item-2",
    )
    assert not instruction_applies_to(
        row,
        stage="assembly_labeling",
        order_item_id="item-3",
    )
    assert not instruction_applies_to(
        row,
        stage="preparation",
        order_item_id="item-2",
    )


def test_order_wide_transition_is_blocked_by_product_instruction():
    assert instruction_applies_to(
        _instruction(),
        stage="assembly_labeling",
        order_wide=True,
    )


def test_product_instruction_timeline_does_not_attach_to_unrelated_product():
    row = _instruction(status="completed")

    assert instruction_targets(row, order_item_id="item-1")
    assert not instruction_targets(row, order_item_id="item-3")


def test_snapshot_keeps_waiting_and_rejection_state_for_employee_refresh():
    snapshot = instruction_snapshot(_instruction(
        status="waiting_customer_service_approval",
        submitted_by_name="موظف التجهيز",
        rejection_note="أعد تصوير الملصق بوضوح",
    ))

    assert snapshot["status"] == "waiting_customer_service_approval"
    assert snapshot["submitted_by_name"] == "موظف التجهيز"
    assert snapshot["rejection_note"] == "أعد تصوير الملصق بوضوح"


def test_router_source_contains_photo_and_customer_approval_transitions():
    source = inspect.getsource(make_order_tracking_notes_router)

    assert "upload_photos" in source
    assert "arrival_confirmation" in source
    assert "waiting_customer_service_approval" in source
    assert "_record_approval_submission" in source
