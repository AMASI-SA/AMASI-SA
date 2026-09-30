from preparation_supplier_dispatch import (
    _piece_products,
    employee_workspace_summary,
)


def test_piece_projection_exposes_receiver_custody_fields():
    piece = {
        "piece_id": "piece-1",
        "group_key": "product-1",
        "product_id": "product-1",
        "product_name": "دقلة ولادي",
        "sku": "AMS10836",
        "order_number": "279106741",
        "unit_index": 1,
        "status": "ready_for_assembly",
        "supplier_dispatch_status": "received",
        "branch_handoff_at": "2026-09-30T01:00:00+00:00",
        "branch_handoff_by": "employee-receiver",
        "branch_handoff_by_name": "موظف الاستلام",
        "preparation_received_at": "2026-09-30T01:00:00+00:00",
        "preparation_received_by_name": "موظف الاستلام",
        "assembly_status": "pending",
        "assembly_ready_at": None,
        "assembly_ready_by_name": None,
        "product_options_snapshot": {"اللون": "بيج"},
    }

    row = _piece_products([piece])[0]

    assert row["piece_id"] == "piece-1"
    assert row["branch_handoff_at"] == "2026-09-30T01:00:00+00:00"
    assert row["branch_handoff_by"] == "employee-receiver"
    assert row["branch_handoff_by_name"] == "موظف الاستلام"
    assert row["preparation_received_by_name"] == "موظف الاستلام"
    assert row["assembly_status"] == "pending"
    assert row["product_options"] == {"اللون": "بيج"}


def test_employee_workspace_summary_separates_supplier_received_and_receiver_custody():
    supplier_received = {
        "piece_id": "piece-supplier",
        "order_number": "1001",
        "status": "received",
        "supplier_dispatch_status": "received",
        "branch_handoff_at": None,
    }
    with_receiver = {
        "piece_id": "piece-receiver",
        "order_number": "1002",
        "status": "ready_for_assembly",
        "supplier_dispatch_status": "received",
        "branch_handoff_at": "2026-09-30T01:00:00+00:00",
        "assembly_status": "pending",
    }
    assembly_done = {
        "piece_id": "piece-ready",
        "order_number": "1003",
        "status": "ready_for_assembly",
        "supplier_dispatch_status": "received",
        "branch_handoff_at": "2026-09-30T01:00:00+00:00",
        "assembly_status": "ready",
    }

    summary = employee_workspace_summary([], [
        supplier_received,
        with_receiver,
        assembly_done,
    ])

    assert summary["received_pieces_awaiting_branch_handoff"] == 1
    assert summary["received_orders_awaiting_branch_handoff"] == 1
    assert summary["receiving_employee_pieces"] == 1
    assert summary["receiving_employee_orders"] == 1


def test_receiver_custody_does_not_include_cancelled_or_assembly_ready_piece():
    rows = [
        {
            "piece_id": "cancelled",
            "order_number": "2001",
            "status": "cancelled",
            "branch_handoff_at": "2026-09-30T01:00:00+00:00",
            "assembly_status": "pending",
        },
        {
            "piece_id": "ready",
            "order_number": "2002",
            "status": "ready_for_assembly",
            "branch_handoff_at": "2026-09-30T01:00:00+00:00",
            "assembly_status": "ready",
        },
    ]

    summary = employee_workspace_summary([], rows)

    assert summary["receiving_employee_pieces"] == 0
    assert summary["receiving_employee_orders"] == 0
