from datetime import datetime, timezone
from preparation_supplier_dispatch import _piece_products


def test_employee_assignment_date_is_not_supplier_or_file_date():
    assigned = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
    piece = {"id": "chronology-piece", "assigned_at": assigned,
             "sent_at": datetime(2026, 11, 5, tzinfo=timezone.utc),
             "supplier_dispatch_status": "sent"}
    assert _piece_products([piece])[0]["assigned_at"] == assigned


def test_missing_employee_date_does_not_invent_a_date():
    assert _piece_products([{"id": "legacy", "sent_at": "2026-11-05"}])[0]["assigned_at"] is None
