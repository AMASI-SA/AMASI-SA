from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import mobile_preparation_execution_routes as execution


def test_assigned_employee_can_start_file(monkeypatch):
    monkeypatch.setattr(
        execution,
        "_now",
        lambda: datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )
    row = execution.preparation_execution_file_view(
        {
            "id": "batch-1",
            "assignment_status": "assigned_not_started",
            "responsible_employee_id": "employee-1",
            "required_completion_at": datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
        },
        actor_id="employee-1",
        can_override=False,
    )

    assert row["status_label"] == "مسند ولم يبدأ"
    assert row["can_start"] is True
    assert row["overdue"] is False


def test_other_employee_cannot_start_assigned_file():
    with pytest.raises(HTTPException) as error:
        execution._assert_file_access(
            {"responsible_employee_id": "employee-1"},
            actor={"id": "employee-2", "role": "employee"},
        )

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "preparation_file_not_assigned_to_actor"


def test_owner_can_override_file_assignment():
    execution._assert_file_access(
        {"responsible_employee_id": "employee-1"},
        actor={"id": "owner-1", "role": "owner", "is_owner": True},
    )


def test_overdue_file_is_reported(monkeypatch):
    monkeypatch.setattr(
        execution,
        "_now",
        lambda: datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
    )
    row = execution.preparation_execution_file_view(
        {
            "id": "batch-1",
            "assignment_status": "assigned_not_started",
            "responsible_employee_id": "employee-1",
            "required_completion_at": datetime(2026, 8, 7, 11, 59, tzinfo=timezone.utc),
        },
        actor_id="employee-1",
        can_override=False,
    )

    assert row["overdue"] is True


def test_started_file_is_idempotent_and_cannot_start_again(monkeypatch):
    monkeypatch.setattr(
        execution,
        "_now",
        lambda: datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
    )
    row = execution.preparation_execution_file_view(
        {
            "id": "batch-1",
            "assignment_status": "in_progress",
            "responsible_employee_id": "employee-1",
            "execution_started_at": datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc),
        },
        actor_id="employee-1",
        can_override=False,
    )

    assert row["status_label"] == "قيد التنفيذ"
    assert row["can_start"] is False
