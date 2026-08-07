from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import mobile_reviewed_preparation_routes as mobile_routes


def test_required_completion_is_normalized_from_riyadh_to_utc(monkeypatch):
    monkeypatch.setattr(
        mobile_routes,
        "_now",
        lambda: datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )

    utc_value, riyadh_value, display = mobile_routes._parse_required_completion_at(
        "2026-08-07T10:30:00+03:00",
    )

    assert utc_value.isoformat() == "2026-08-07T07:30:00+00:00"
    assert riyadh_value.isoformat() == "2026-08-07T10:30:00+03:00"
    assert display == "2026/08/07 10:30 AM"


def test_naive_required_completion_is_interpreted_as_riyadh(monkeypatch):
    monkeypatch.setattr(
        mobile_routes,
        "_now",
        lambda: datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )

    utc_value, riyadh_value, _ = mobile_routes._parse_required_completion_at(
        "2026-08-07T09:00:00",
    )

    assert utc_value.isoformat() == "2026-08-07T06:00:00+00:00"
    assert riyadh_value.isoformat() == "2026-08-07T09:00:00+03:00"


def test_required_completion_must_be_in_future(monkeypatch):
    monkeypatch.setattr(
        mobile_routes,
        "_now",
        lambda: datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(HTTPException) as error:
        mobile_routes._parse_required_completion_at("2026-08-06T14:00:00+03:00")

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "required_completion_at_must_be_future"


def test_mobile_file_response_never_moves_order_to_in_progress():
    result = mobile_routes._file_response(
        {
            "id": "batch-1",
            "status": "ready",
            "selected_product_count": 2,
            "allocated_quantity": 7,
            "order_count": 4,
        },
        {
            "file_number": "PF-20260806-0001",
            "file_title": "دفعة السلاسل",
            "file_name": "دفعة السلاسل.pdf",
            "responsible_employee_id": "employee-1",
            "responsible_employee_name": "موظف التجهيز",
            "required_completion_at": datetime(2026, 8, 7, 7, 30, tzinfo=timezone.utc),
            "required_completion_at_riyadh": "2026-08-07T10:30:00+03:00",
            "required_completion_display": "2026/08/07 10:30 AM",
        },
    )

    assert result["ok"] is True
    assert result["status"] == "assigned_not_started"
    assert result["moved_to_in_progress"] is False
    assert result["responsible_employee_id"] == "employee-1"
    assert result["required_completion_display"] == "2026/08/07 10:30 AM"
