from preparation_supplier_dispatch import _piece_products, employee_workspace_summary


def _piece(**overrides):
    row = {
        "piece_id": "p1",
        "group_key": "g1",
        "order_number": "289000001",
        "product_name": "منتج",
        "sku": "SKU-1",
        "supplier_dispatch_status": "received",
        "status": "ready_for_assembly",
        "product_options_snapshot": {"اللون": "أسود"},
        "specifications_snapshot": [{"name": "ملاحظة", "value": "اختبار"}],
        "assigned_at": "2026-09-30T08:00:00+03:00",
        "branch_handoff_at": "2026-09-30T10:00:00+03:00",
        "branch_handoff_status": "received_by_branch",
        "preparation_received_at": "2026-09-30T10:00:00+03:00",
        "preparation_received_by_name": "موظف الاستلام",
        "assembly_status": "pending",
    }
    row.update(overrides)
    return row


def test_piece_projection_exposes_existing_handoff_facts_without_inference():
    row = _piece_products([_piece()])[0]
    assert row["piece_id"] == "p1"
    assert row["assigned_at"] == "2026-09-30T08:00:00+03:00"
    assert row["branch_handoff_at"] == "2026-09-30T10:00:00+03:00"
    assert row["branch_handoff_status"] == "received_by_branch"
    assert row["preparation_received_by_name"] == "موظف الاستلام"
    assert row["assembly_status"] == "pending"
    assert row["handoff_awaiting_assembly"] is True
    assert row["product_options"] == {"اللون": "أسود"}


def test_assembly_ready_piece_leaves_receiving_employee_bucket():
    row = _piece_products([_piece(assembly_status="ready")])[0]
    assert row["handoff_awaiting_assembly"] is False


def test_no_handoff_never_enters_receiving_employee_bucket():
    row = _piece_products([_piece(branch_handoff_at=None, assembly_status="pending")])[0]
    assert row["handoff_awaiting_assembly"] is False


def test_workspace_summary_counts_physical_handoff_pieces_and_orders():
    pieces = [
        _piece(piece_id="p1", order_number="1"),
        _piece(piece_id="p2", order_number="1"),
        _piece(piece_id="p3", order_number="2"),
        _piece(piece_id="p4", order_number="3", assembly_status="ready"),
        _piece(piece_id="p5", order_number="4", branch_handoff_at=None),
    ]
    summary = employee_workspace_summary([], pieces)
    assert summary["handoff_pieces_awaiting_assembly"] == 3
    assert summary["handoff_orders_awaiting_assembly"] == 2
