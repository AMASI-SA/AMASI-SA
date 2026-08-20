import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ai_store_access_contract import ROLE_CATALOG, effective_permissions
from fulfillment_experiment_routes import (
    STOP_TYPE_LABELS,
    hold_piece_patch,
    make_fulfillment_experiment_router,
    release_piece_update,
)
from preparation_piece_operations import (
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_BLOCKED,
    PIECE_STATUS_CANCELLED,
    PIECE_STATUS_IN_PROGRESS,
)
from supplier_receiving_routes import supplier_invoice_experiment_run_id


def test_customer_service_and_preparation_roles_have_only_their_stop_scope():
    preparation = set(effective_permissions({
        "role_key": "preparation_operator",
        "enabled": True,
    }))
    customer_service = set(effective_permissions({
        "role_key": "customer_service",
        "enabled": True,
    }))

    assert "preparation.assigned.stop" in preparation
    assert "fulfillment.stop.manage" not in preparation
    assert "fulfillment.stop.manage" in customer_service
    assert "preparation.assigned.work" not in customer_service
    assert "fulfillment.experiment.reset" in ROLE_CATALOG["owner"]


@pytest.mark.parametrize(
    ("stop_type", "expected_status"),
    [
        ("cancel", PIECE_STATUS_CANCELLED),
        ("edit", PIECE_STATUS_BLOCKED),
        ("note", PIECE_STATUS_BLOCKED),
        ("employee", PIECE_STATUS_BLOCKED),
    ],
)
def test_each_stop_type_fails_closed_with_a_visible_reason(stop_type, expected_status):
    now = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)
    patch = hold_piece_patch(
        hold_id="hold-1",
        stop_type=stop_type,
        note="بانتظار تأكيد العميل",
        actor={"id": "employee-1", "name": "موظف التجهيز"},
        stopped_at=now,
    )

    assert patch["status"] == expected_status
    assert patch["active_hold_id"] == "hold-1"
    assert patch["hold_stop_label"] == STOP_TYPE_LABELS[stop_type]
    assert patch["block_reason"] == "بانتظار تأكيد العميل"
    assert patch["salla_updated"] is False
    assert patch["qoyod_updated"] is False


def test_release_restores_the_exact_operational_state_before_the_stop():
    released_at = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
    update = release_piece_update(
        {
            "status": PIECE_STATUS_IN_PROGRESS,
            "execution_status": "supplier_receiving_draft",
        },
        released_at,
    )

    assert update["$set"]["status"] == PIECE_STATUS_IN_PROGRESS
    assert update["$set"]["execution_status"] == "supplier_receiving_draft"
    assert update["$set"]["updated_at"] == released_at
    assert "active_hold_id" in update["$unset"]
    assert "hold_note" in update["$unset"]


def test_release_defaults_to_assigned_only_when_old_state_was_missing():
    update = release_piece_update({}, datetime.now(timezone.utc))
    assert update["$set"]["status"] == PIECE_STATUS_ASSIGNED
    assert update["$set"]["execution_status"] == "assigned"


def test_supplier_invoice_accepts_one_experiment_run_and_rejects_mixing():
    assert supplier_invoice_experiment_run_id([
        {"experiment_mode": True, "experiment_run_id": "run-1"},
        {"experiment_mode": True, "experiment_run_id": "run-1"},
    ]) == "run-1"
    assert supplier_invoice_experiment_run_id([{}]) is None

    with pytest.raises(HTTPException) as mixed:
        supplier_invoice_experiment_run_id([
            {"experiment_mode": True, "experiment_run_id": "run-1"},
            {},
        ])
    assert mixed.value.status_code == 409
    assert mixed.value.detail["code"] == "supplier_receiving_experiment_mode_mismatch"

    with pytest.raises(HTTPException) as multiple_runs:
        supplier_invoice_experiment_run_id([
            {"experiment_mode": True, "experiment_run_id": "run-1"},
            {"experiment_mode": True, "experiment_run_id": "run-2"},
        ])
    assert multiple_runs.value.detail["code"] == "supplier_receiving_experiment_mode_mismatch"


def test_reset_is_atomic_and_never_reverses_supplier_or_accounting_records():
    source = inspect.getsource(make_fulfillment_experiment_router)

    assert "with_transaction(finalize)" in source
    assert "fulfillment_experiment_atomic_transaction_required" in source
    assert "archived_allocation_snapshot" in source
    assert '"financial_writes_allowed": False' in source
    assert '"salla_writes_allowed": False' in source
    assert '"salla_status_writes_allowed": True' in source
    assert "SUPPLIER_INVOICES" not in source
    assert "ledger" not in source.casefold()


def test_router_exposes_state_reset_hold_and_release_operations():
    router = make_fulfillment_experiment_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/fulfillment-experiments-v1/orders/{order_number}", "GET") in routes
    assert ("/fulfillment-experiments-v1/orders/{order_number}/reset", "POST") in routes
    assert ("/fulfillment-experiments-v1/orders/{order_number}/holds", "POST") in routes
    assert ("/fulfillment-experiments-v1/holds/{hold_id}/release", "POST") in routes
