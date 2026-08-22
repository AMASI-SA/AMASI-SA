from datetime import datetime, timedelta, timezone

from mobile_operations_monitoring_routes import (
    resolve_monitoring_range,
    summarize_courier,
    summarize_preparation_employee,
)
from preparation_piece_operations import (
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
    PIECE_STATUS_READY_FOR_ASSEMBLY,
)


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


def test_default_range_is_rolling_30_days():
    now = _dt(22, 2)
    start, end = resolve_monitoring_range(now=now)
    assert end == now
    assert start == now - timedelta(days=30)


def test_preparation_average_uses_only_valid_completed_pieces_in_range():
    start = _dt(1)
    end = _dt(22)
    pieces = [
        {
            "piece_id": "p1",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assigned_at": _dt(2),
            "started_at": _dt(2, 1),
            "completed_at": _dt(2, 2),
        },
        {
            "piece_id": "p2",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assigned_at": _dt(3),
            "started_at": _dt(3, 1),
            "completed_at": _dt(3, 3),
        },
        {
            "piece_id": "bad-missing-start",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "completed_at": _dt(4, 2),
        },
        {
            "piece_id": "outside",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "started_at": _dt(1),
            "completed_at": _dt(22),
        },
    ]

    result = summarize_preparation_employee(pieces, start=start, end=end)

    assert result["completed_count"] == 3
    assert result["measured_count"] == 2
    assert result["average_preparation_seconds"] == 5400


def test_current_custody_counts_are_not_limited_by_selected_range():
    start = _dt(10)
    end = _dt(22)
    pieces = [
        {
            "piece_id": "assigned-old",
            "status": PIECE_STATUS_ASSIGNED,
            "assigned_at": _dt(2),
        },
        {
            "piece_id": "working-now",
            "status": PIECE_STATUS_IN_PROGRESS,
            "assigned_at": _dt(12),
            "started_at": _dt(13),
        },
        {
            "piece_id": "ready",
            "status": PIECE_STATUS_READY_FOR_RECEIPT,
            "assigned_at": _dt(14),
            "started_at": _dt(14, 1),
        },
    ]

    result = summarize_preparation_employee(pieces, start=start, end=end)

    assert result["current_held_pieces"] == 3
    assert result["ready_not_handed_off_pieces"] == 1
    assert result["pending_review_count"] == 2
    assert result["in_progress_count"] == 2


def test_courier_average_uses_out_for_delivery_to_delivered_only():
    start = _dt(1)
    end = _dt(22)
    rows = [
        {
            "id": "a1",
            "status": "delivered",
            "active": True,
            "assigned_at": _dt(2, 8),
            "out_for_delivery_at": _dt(2, 9),
            "delivered_at": _dt(2, 10),
        },
        {
            "id": "a2",
            "status": "delivered",
            "active": True,
            "assigned_at": _dt(3, 7),
            "out_for_delivery_at": _dt(3, 8),
            "delivered_at": _dt(3, 10),
        },
        {
            "id": "a3",
            "status": "delivered",
            "active": True,
            "assigned_at": _dt(4, 7),
            "delivered_at": _dt(4, 10),
        },
        {
            "id": "current-assigned",
            "status": "assigned",
            "active": True,
            "assigned_at": _dt(20),
        },
        {
            "id": "current-road",
            "status": "out_for_delivery",
            "active": True,
            "assigned_at": _dt(20),
            "out_for_delivery_at": _dt(21),
        },
    ]

    result = summarize_courier(rows, start=start, end=end)

    assert result["delivered_count"] == 3
    assert result["measured_count"] == 2
    assert result["average_delivery_seconds"] == 5400
    assert result["assigned_count"] == 1
    assert result["out_for_delivery_count"] == 1
    assert result["current_delivery_count"] == 2
    assert result["average_assignment_cycle_seconds"] == 9000
    assert result["assignment_cycle_measured_count"] == 3


def test_courier_delivery_outside_selected_period_is_not_counted():
    result = summarize_courier(
        [
            {
                "status": "delivered",
                "assigned_at": _dt(1),
                "out_for_delivery_at": _dt(1, 1),
                "delivered_at": _dt(5),
            }
        ],
        start=_dt(10),
        end=_dt(22),
    )
    assert result["delivered_count"] == 0
    assert result["measured_count"] == 0
    assert result["average_delivery_seconds"] is None


def test_invalid_or_excessive_range_is_rejected():
    now = _dt(22)
    try:
        resolve_monitoring_range(from_at=now, to_at=now)
    except ValueError as exc:
        assert str(exc) == "operations_monitoring_range_invalid"
    else:
        raise AssertionError("expected invalid range")

    try:
        resolve_monitoring_range(
            from_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            to_at=now,
        )
    except ValueError as exc:
        assert str(exc) == "operations_monitoring_range_too_large"
    else:
        raise AssertionError("expected excessive range")
