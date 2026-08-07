from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from preparation_supplier_dispatch import (
    ASSIGNMENT_STATUS_UNASSIGNED,
    DISPATCH_STATUS_PARTIAL,
    DISPATCH_STATUS_READY,
    DISPATCH_STATUS_SENT,
    RejectPreparationPiecesRequest,
    employee_workspace_summary,
    make_preparation_supplier_dispatch_router,
    piece_is_available_for_supplier_dispatch,
    plan_piece_selections,
    supplier_dispatch_blocker,
    supplier_dispatch_lines,
    supplier_receiving_dispatch_blocker,
)


def _piece(piece_id, group_key="product:1", unit_index=1, **patch):
    return {
        "piece_id": piece_id,
        "group_key": group_key,
        "order_number": "3001",
        "order_item_id": "item-1",
        "unit_index": unit_index,
        "status": "assigned",
        "services": [{"service_id": "engrave", "service_name": "نحت", "status": "pending"}],
        **patch,
    }


def test_product_quantity_selection_chooses_stable_physical_units():
    pieces = [
        _piece("piece-3", unit_index=3),
        _piece("piece-1", unit_index=1),
        _piece("piece-2", unit_index=2),
        _piece("other-1", group_key="product:2", unit_index=1),
    ]

    planned = plan_piece_selections(
        pieces,
        [
            {"group_key": "product:1", "quantity": 2},
            {"group_key": "product:2", "quantity": 1},
        ],
    )

    assert [row["piece_id"] for row in planned] == [
        "piece-1",
        "piece-2",
        "other-1",
    ]


def test_rejected_or_already_sent_piece_is_not_available_for_another_dispatch():
    assert piece_is_available_for_supplier_dispatch(_piece("available")) is True
    assert piece_is_available_for_supplier_dispatch(
        _piece("rejected", assignment_status=ASSIGNMENT_STATUS_UNASSIGNED)
    ) is False
    assert piece_is_available_for_supplier_dispatch(
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT)
    ) is False
    assert piece_is_available_for_supplier_dispatch(
        _piece("partial", supplier_dispatch_status=DISPATCH_STATUS_PARTIAL)
    ) is True


def test_supplier_must_offer_an_unfinished_service_on_the_piece():
    piece = _piece("piece-1")
    matching = {
        "id": "supplier-1",
        "service_links": [{"service_id": "engrave"}],
    }
    mismatch = {
        "id": "supplier-2",
        "service_links": [{"service_id": "paint"}],
    }

    assert supplier_dispatch_blocker(piece, matching) is None
    assert supplier_dispatch_blocker(piece, mismatch)["code"] == (
        "supplier_dispatch_service_mismatch"
    )


def test_supplier_file_contains_only_services_offered_by_selected_supplier():
    piece = _piece(
        "piece-1",
        services=[
            {"service_id": "engrave", "service_name": "نحت", "status": "pending"},
            {"service_id": "paint", "service_name": "طلاء", "status": "pending"},
        ],
    )

    lines = supplier_dispatch_lines(
        [piece],
        {"service_links": [{"service_id": "engrave"}]},
    )

    assert [row["service_name"] for row in lines[0]["services"]] == ["نحت"]


def test_new_dispatch_governance_requires_the_same_supplier_at_receiving():
    legacy_piece = _piece("legacy")
    governed_piece = _piece(
        "governed",
        supplier_dispatch_status=DISPATCH_STATUS_READY,
        supplier_id="supplier-1",
        supplier_name="مورد الحفر",
    )

    assert supplier_receiving_dispatch_blocker(legacy_piece, "supplier-2") is None
    assert supplier_receiving_dispatch_blocker(governed_piece, "supplier-1") is None
    mismatch = supplier_receiving_dispatch_blocker(governed_piece, "supplier-2")
    assert mismatch["code"] == "supplier_piece_dispatched_to_different_supplier"
    assert mismatch["expected_supplier_name"] == "مورد الحفر"


def test_dispatch_router_exposes_employee_supplier_and_manager_workflow():
    router = make_preparation_supplier_dispatch_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/supplier-dispatch-v1/workspace", "GET") in routes
    assert ("/supplier-dispatch-v1/dispatches", "POST") in routes
    assert ("/supplier-dispatch-v1/rejections", "POST") in routes
    assert ("/supplier-dispatch-v1/manager/unassigned", "GET") in routes
    assert ("/supplier-dispatch-v1/manager/reassign", "POST") in routes
    assert (
        "/supplier-dispatch-v1/dispatches/{dispatch_id}/ready",
        "POST",
    ) in routes


def test_returning_assignment_requires_a_meaningful_employee_reason():
    with pytest.raises(ValidationError):
        RejectPreparationPiecesRequest(
            client_request_id="return-request-1",
            file_number="PF-100",
            selections=[{"group_key": "product:1", "quantity": 2}],
            reason=" ",
        )

    payload = RejectPreparationPiecesRequest(
        client_request_id="return-request-2",
        file_number="PF-100",
        selections=[{"group_key": "product:1", "quantity": 2}],
        reason="ليس من اختصاصي",
    )
    assert payload.reason == "ليس من اختصاصي"


def test_employee_summary_uses_products_orders_and_all_assigned_pieces():
    files = [
        {
            "is_new": True,
            "available_quantity": 3,
            "sent_quantity": 2,
            "ready_quantity": 1,
            "received_quantity": 2,
            "products": [
                {"available_quantity": 3, "sent_quantity": 0, "ready_quantity": 0},
                {"available_quantity": 0, "sent_quantity": 2, "ready_quantity": 1},
            ],
        },
    ]
    pieces = [
        _piece("waiting"),
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT),
        _piece("received-1", order_number="3001", status="received"),
        _piece("received-2", order_number="3001", status="received"),
        _piece("handed-off", order_number="3002", status="received", branch_handoff_at="done"),
    ]

    summary = employee_workspace_summary(files, pieces)

    assert summary["waiting_review_products"] == 1
    assert summary["in_progress_products"] == 1
    assert summary["received_orders_awaiting_branch_handoff"] == 1
    assert summary["received_pieces_awaiting_branch_handoff"] == 2
    assert summary["total_assigned_pieces"] == 4
