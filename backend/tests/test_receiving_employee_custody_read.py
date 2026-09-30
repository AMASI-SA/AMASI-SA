from preparation_supplier_dispatch import (
    DISPATCH_STATUS_RECEIVED,
    PIECE_STATUS_READY_FOR_ASSEMBLY,
    _piece_products,
    _receiving_employee_custody_active,
    employee_workspace_summary,
)


def _piece(piece_id: str, **patch):
    return {
        "piece_id": piece_id,
        "group_key": "product:1",
        "order_number": "3001",
        "order_item_id": "item-1",
        "unit_index": 1,
        "product_id": "product-1",
        "product_name": "دقلة",
        "sku": "AMS1",
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "supplier_dispatch_status": DISPATCH_STATUS_RECEIVED,
        "product_options_snapshot": {"الاسم": "محمد"},
        "specifications_snapshot": [{"name": "اللون", "value": "أسود"}],
        "services": [],
        "branch_handoff_at": "2026-09-30T08:00:00+00:00",
        "branch_handoff_by_name": "موظف الاستلام",
        "preparation_received_at": "2026-09-30T08:00:00+00:00",
        "preparation_received_by_name": "موظف الاستلام",
        "assigned_at": "2026-09-29T08:00:00+00:00",
        "assembly_status": "pending",
        **patch,
    }


def test_receiving_employee_custody_requires_real_branch_handoff():
    assert _receiving_employee_custody_active(_piece("p1")) is True
    assert _receiving_employee_custody_active(
        _piece("p2", branch_handoff_at=None, preparation_received_at=None)
    ) is False


def test_receiving_employee_custody_ends_when_assembly_accepts_piece():
    assert _receiving_employee_custody_active(
        _piece("p1", assembly_status="ready", assembly_ready_at="2026-09-30T09:00:00+00:00")
    ) is False


def test_receiving_employee_custody_does_not_relabel_unreceived_piece():
    assert _receiving_employee_custody_active(
        _piece(
            "p1",
            status="received",
            branch_handoff_at="2026-09-30T08:00:00+00:00",
        )
    ) is False


def test_piece_grain_exposes_custody_and_customer_detail_read_only_fields():
    row = _piece_products([_piece("p1")])[0]

    assert row["piece_id"] == "p1"
    assert row["receiving_employee_custody"] is True
    assert row["assigned_at"] == "2026-09-29T08:00:00+00:00"
    assert row["branch_handoff_at"] == "2026-09-30T08:00:00+00:00"
    assert row["branch_handoff_by_name"] == "موظف الاستلام"
    assert row["preparation_received_by_name"] == "موظف الاستلام"
    assert row["assembly_status"] == "pending"
    assert row["product_options"] == {"الاسم": "محمد"}
    assert row["specifications"][0]["value"] == "أسود"


def test_workspace_summary_counts_only_active_receiving_employee_custody():
    active = _piece("active", order_number="3001")
    ready = _piece(
        "ready",
        order_number="3002",
        assembly_status="ready",
        assembly_ready_at="2026-09-30T09:00:00+00:00",
    )
    no_handoff = _piece(
        "no-handoff",
        order_number="3003",
        branch_handoff_at=None,
        preparation_received_at=None,
    )

    summary = employee_workspace_summary([], [active, ready, no_handoff])

    assert summary["receiving_employee_custody_pieces"] == 1
    assert summary["receiving_employee_custody_orders"] == 1


def test_custody_read_model_has_no_mutation_side_effects():
    piece = _piece("p1")
    before = dict(piece)

    assert _receiving_employee_custody_active(piece) is True
    _piece_products([piece])

    assert piece == before
