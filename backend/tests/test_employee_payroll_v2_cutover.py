from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from employee_payroll_status import (
    contract_salary_row,
    employee_salary_rows,
    salary_active_on,
    transition_suspensions,
)
from expenses_routes import compute_operating_expenses_for_day
from liabilities_routes import _compute_employee_accrual


ROOT = Path(__file__).resolve().parents[2]


def _contract(state="active", periods=None):
    return {
        "id": "contract-1",
        "legacy_salary_id": "legacy-salary-1",
        "employee_id": "employee-1",
        "monthly_amount": 3100,
        "effective_from": "2026-01-01",
        "payroll_state": state,
        "status": "active" if state == "active" else "paused",
        "suspension_periods": periods or [],
    }


def _employee(state="active"):
    return {
        "id": "employee-1",
        "display_name": "موظف الاختبار",
        "status": state,
    }


def test_unpaid_leave_stops_on_first_day_and_resume_has_no_backfill():
    left = transition_suspensions(
        [],
        target_state="unpaid_leave",
        effective_date=date(2026, 1, 10),
        period_id="leave-1",
        changed_at="2026-01-10T00:00:00+00:00",
        changed_by="owner",
    )
    returned = transition_suspensions(
        left,
        target_state="active",
        effective_date=date(2026, 1, 20),
        period_id="unused",
        changed_at="2026-01-20T00:00:00+00:00",
        changed_by="owner",
    )
    row = contract_salary_row(_contract(periods=returned), _employee())

    assert salary_active_on(row, date(2026, 1, 9)) is True
    assert salary_active_on(row, date(2026, 1, 10)) is False
    assert salary_active_on(row, date(2026, 1, 19)) is False
    assert salary_active_on(row, date(2026, 1, 20)) is True

    accrual = _compute_employee_accrual(row, today=date(2026, 1, 31))
    assert accrual["days_worked"] == 21
    assert accrual["accrued"] == 2100.0


def test_open_leave_stops_future_daily_accrual():
    periods = transition_suspensions(
        [],
        target_state="unpaid_leave",
        effective_date=date(2026, 1, 10),
        period_id="leave-1",
        changed_at="2026-01-10T00:00:00+00:00",
        changed_by="owner",
    )
    row = contract_salary_row(
        _contract(state="unpaid_leave", periods=periods),
        _employee(state="unpaid_leave"),
    )

    assert _compute_employee_accrual(row, today=date(2026, 1, 31))["accrued"] == 900.0
    assert _compute_employee_accrual(row, today=date(2026, 2, 28))["accrued"] == 900.0


def test_migrated_inactive_contract_keeps_last_paid_day_without_legacy_read():
    contract = _contract(state="inactive")
    contract["effective_to"] = "2026-01-09"
    contract.pop("suspension_periods")
    row = contract_salary_row(contract, _employee(state="inactive"))

    assert salary_active_on(row, date(2026, 1, 9)) is True
    assert salary_active_on(row, date(2026, 1, 10)) is False
    assert _compute_employee_accrual(row, today=date(2026, 1, 31))["accrued"] == 900.0


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, _limit):
        return list(self.rows)

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, _projection):
        rows = self.rows
        if query.get("user_id"):
            rows = [row for row in rows if row.get("user_id", query["user_id"]) == query["user_id"]]
        if query.get("id", {}).get("$in"):
            ids = set(query["id"]["$in"])
            rows = [row for row in rows if row.get("id") in ids]
        if query.get("category", {}).get("$in"):
            categories = set(query["category"]["$in"])
            rows = [row for row in rows if row.get("category") in categories]
        if isinstance(query.get("date"), str):
            rows = [row for row in rows if row.get("date") == query["date"]]
        return _Cursor(rows)


class _Db:
    def __init__(self):
        self.accessed = []
        self.collections = {
            "mezan_employee_salary_contracts_v2": _Collection([_contract()]),
            "mezan_employees_v2": _Collection([_employee()]),
        }

    def __getitem__(self, name):
        self.accessed.append(name)
        return self.collections[name]

    def __getattr__(self, name):
        return self[name]


def test_runtime_salary_loader_never_reads_legacy_employee_salaries():
    db = _Db()
    rows = asyncio.run(employee_salary_rows(db, "owner"))

    assert rows[0]["id"] == "legacy-salary-1"
    assert rows[0]["monthly_amount"] == 3100
    assert db.accessed == [
        "mezan_employee_salary_contracts_v2",
        "mezan_employees_v2",
    ]
    assert "operating_salaries" not in db.accessed


def test_dashboard_expense_uses_v2_employee_salary_and_ignores_old_employee_row():
    db = _Db()
    db.collections.update({
        "operating_salaries": _Collection([
            {
                "user_id": "owner",
                "id": "forbidden-old-employee",
                "category": "employee",
                "monthly_amount": 999999,
                "status": "active",
            },
            {
                "user_id": "owner",
                "id": "household-1",
                "category": "household",
                "monthly_amount": 310,
                "status": "active",
            },
        ]),
        "operating_rentals": _Collection([]),
        "operating_prepaid_expenses": _Collection([]),
        "operating_daily_expenses": _Collection([]),
    })

    result = asyncio.run(compute_operating_expenses_for_day(
        db, "owner", date(2026, 1, 5),
    ))

    assert result["salaries_employee"] == 100.0
    assert result["salaries_household"] == 10.0
    assert result["salaries_total_daily"] == 110.0


def test_payroll_consumers_have_zero_direct_legacy_employee_salary_reads():
    for relative in (
        "backend/liabilities_routes.py",
        "backend/financial_position_ssot.py",
        "backend/operational_reports_routes.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "db.operating_salaries" not in source

    dashboard = (ROOT / "backend/dashboard_v2_routes.py").read_text(encoding="utf-8")
    assert '"employee_salaries": "mezan_employee_salary_contracts_v2"' in dashboard
