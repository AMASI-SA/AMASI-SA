from datetime import datetime, timezone

from preparation_supervision_routes import _detail_for_employee, _employee_rows


def _piece(piece_id: str, *, status: str, dispatch: str = "", handed_off_at=None):
    return {
        "piece_id": piece_id,
        "user_id": "merchant-1",
        "responsible_employee_id": "employee-1",
        "responsible_employee_name": "أحمد",
        "status": status,
        "supplier_dispatch_status": dispatch,
        "preparation_received_at": handed_off_at,
        "experiment_archived_at": None,
        "file_number": "PF-1",
        "order_number": "1001",
        "product_name": "منتج",
        "unit_index": 1,
    }


def test_supervision_card_metrics_are_piece_state_driven():
    month_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    next_month = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [
        # Still with employee and not sent to supplier -> waiting review.
        _piece("p1", status="in_progress"),
        # Sent to supplier, still responsibility of employee -> with employee only.
        _piece("p2", status="in_progress", dispatch="sent"),
        # Received from supplier but not handed to next employee -> separate ready metric.
        _piece("p3", status="received", dispatch="received"),
        # Handed to next employee this month -> monthly delivered only.
        _piece(
            "p4",
            status="ready_for_assembly",
            dispatch="received",
            handed_off_at=datetime(2026, 8, 15, 8, tzinfo=timezone.utc),
        ),
        # Historical delivery outside current month must not count this month.
        _piece(
            "p5",
            status="ready_for_assembly",
            dispatch="received",
            handed_off_at=datetime(2026, 7, 31, 8, tzinfo=timezone.utc),
        ),
    ]

    cards = _employee_rows(rows, month_start=month_start, next_month=next_month)
    assert len(cards) == 1
    card = cards[0]
    assert card["with_employee"] == 2
    assert card["waiting_review"] == 1
    assert card["ready_not_handed_off"] == 1
    assert card["delivered_this_month"] == 1


def test_employee_detail_is_read_only_and_disjoint_for_current_buckets():
    month_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    next_month = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [
        _piece("p1", status="assigned"),
        _piece("p2", status="in_progress", dispatch="sent"),
        _piece("p3", status="received", dispatch="received"),
    ]
    detail = _detail_for_employee(
        rows,
        employee_id="employee-1",
        month_start=month_start,
        next_month=next_month,
    )
    assert detail is not None
    assert detail["read_only"] is True
    assert detail["summary"]["with_employee"] == 2
    assert len(detail["sections"]["waiting_review"]) == 1
    assert len(detail["sections"]["in_progress"]) == 1
    assert len(detail["sections"]["ready_not_handed_off"]) == 1
    assert all(row["read_only"] for rows in detail["sections"].values() for row in rows)
