from store_delivery_reassignment_routes import (
    _assignment_status_filter,
    _delivery_duration_report,
)


def test_current_status_maps_to_active_delivery_states():
    assert _assignment_status_filter("current") == {
        "$in": ["assigned", "out_for_delivery"]
    }


def test_delivery_duration_report_uses_assignment_to_delivery_time():
    rows = [
        {
            "assigned_at": "2026-08-22T00:00:00+00:00",
            "delivered_at": "2026-08-22T01:00:00+00:00",
        },
        {
            "assigned_at": "2026-08-22T00:00:00Z",
            "delivered_at": "2026-08-22T03:00:00Z",
        },
    ]

    assert _delivery_duration_report(rows) == {
        "measured_count": 2,
        "average_delivery_seconds": 7200.0,
        "fastest_delivery_seconds": 3600.0,
        "longest_delivery_seconds": 10800.0,
    }


def test_delivery_duration_report_ignores_missing_invalid_and_reversed_times():
    rows = [
        {"assigned_at": None, "delivered_at": "2026-08-22T01:00:00Z"},
        {"assigned_at": "bad", "delivered_at": "2026-08-22T01:00:00Z"},
        {
            "assigned_at": "2026-08-22T02:00:00Z",
            "delivered_at": "2026-08-22T01:00:00Z",
        },
    ]

    assert _delivery_duration_report(rows) == {
        "measured_count": 0,
        "average_delivery_seconds": None,
        "fastest_delivery_seconds": None,
        "longest_delivery_seconds": None,
    }
