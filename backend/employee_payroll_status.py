"""Employee OS payroll authority and unpaid-leave calendar policy.

Runtime payroll reads only Employee OS V2 identities and salary contracts.
The legacy salary id is retained as a compatibility key for historical
liabilities and ledger entries, but no employee salary value or status is read
from ``operating_salaries``.

Suspension ranges are half-open: ``started_on`` is the first unpaid day and
``returned_on`` is the first paid day after leave. An open range has no
``returned_on``.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any


EMPLOYEES_COLLECTION = "mezan_employees_v2"
SALARY_CONTRACTS_COLLECTION = "mezan_employee_salary_contracts_v2"
PAYROLL_STATES = {"active", "unpaid_leave", "inactive"}


def _date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return None


def normalized_suspensions(value: Any) -> list[dict[str, Any]]:
    """Return valid, ordered payroll suspension ranges."""
    rows: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        started = _date(item.get("started_on"))
        returned = _date(item.get("returned_on"))
        if not started or (returned and returned < started):
            continue
        row = deepcopy(item)
        row["started_on"] = started.isoformat()
        row["returned_on"] = returned.isoformat() if returned else None
        row["reason"] = str(item.get("reason") or "inactive").strip()
        rows.append(row)
    return sorted(rows, key=lambda row: (row["started_on"], row.get("id") or ""))


def suspension_history(
    contract: dict[str, Any] | None,
    employee: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read V2 history, with one compatibility range for migrated stops.

    Existing migrated contracts stored ``effective_to`` as the last paid day.
    Until that employee is edited in Employee OS, synthesize an open range
    starting on the following day so the verified historical accrual remains
    unchanged without consulting the legacy row.
    """
    contract = contract or {}
    rows = normalized_suspensions(contract.get("suspension_periods"))
    if rows:
        return rows
    state = str(
        contract.get("payroll_state")
        or (employee or {}).get("status")
        or "active"
    ).strip()
    effective_to = _date(contract.get("effective_to"))
    if state != "active" and effective_to:
        return [{
            "id": f"cutover:{contract.get('id') or 'contract'}",
            "started_on": (effective_to + timedelta(days=1)).isoformat(),
            "returned_on": None,
            "reason": state,
            "source": "employee_v2_cutover",
        }]
    employee_stopped_on = _date((employee or {}).get("updated_at"))
    if state != "active" and employee_stopped_on:
        return [{
            "id": f"cutover:{contract.get('id') or 'contract'}",
            "started_on": employee_stopped_on.isoformat(),
            "returned_on": None,
            "reason": state,
            "source": "employee_v2_status_cutover",
        }]
    return []


def open_suspension(periods: Any) -> dict[str, Any] | None:
    rows = normalized_suspensions(periods)
    return next((row for row in reversed(rows) if not row.get("returned_on")), None)


def transition_suspensions(
    periods: Any,
    *,
    target_state: str,
    effective_date: date,
    period_id: str,
    changed_at: str,
    changed_by: str,
) -> list[dict[str, Any]]:
    """Open/close one unpaid range while retaining every prior leave."""
    if target_state not in PAYROLL_STATES:
        raise ValueError("employee_status_invalid")
    rows = normalized_suspensions(periods)
    open_indexes = [index for index, row in enumerate(rows) if not row.get("returned_on")]
    if len(open_indexes) > 1:
        raise ValueError("employee_payroll_multiple_open_suspensions")

    if target_state == "active":
        if open_indexes:
            index = open_indexes[0]
            started = _date(rows[index].get("started_on"))
            if started and effective_date < started:
                raise ValueError("employee_payroll_return_before_leave")
            rows[index] = {
                **rows[index],
                "returned_on": effective_date.isoformat(),
                "returned_at": changed_at,
                "returned_by": changed_by,
            }
        return rows

    if open_indexes:
        index = open_indexes[0]
        rows[index] = {
            **rows[index],
            "reason": target_state,
            "updated_at": changed_at,
            "updated_by": changed_by,
        }
        return rows

    rows.append({
        "id": period_id,
        "started_on": effective_date.isoformat(),
        "returned_on": None,
        "reason": target_state,
        "created_at": changed_at,
        "created_by": changed_by,
    })
    return normalized_suspensions(rows)


def contract_salary_row(
    contract: dict[str, Any],
    employee: dict[str, Any],
) -> dict[str, Any]:
    """Expose a V2 contract in the stable shape used by payroll consumers."""
    state = str(
        employee.get("status")
        or contract.get("payroll_state")
        or "active"
    ).strip()
    if state not in PAYROLL_STATES:
        state = "inactive"
    compatibility_id = str(
        contract.get("legacy_salary_id") or contract.get("id") or ""
    ).strip()
    return {
        "id": compatibility_id,
        "contract_id": contract.get("id"),
        "employee_v2_id": employee.get("id"),
        "name": employee.get("display_name") or employee.get("name") or "",
        "category": "employee",
        "country": employee.get("country") or "saudi",
        "monthly_amount": round(float(contract.get("monthly_amount") or 0), 2),
        "start_date": contract.get("effective_from") or employee.get("hire_date"),
        "effective_to": contract.get("effective_to"),
        "status": "active" if state == "active" else "stopped",
        "payroll_state": state,
        "payroll_suspension_periods": suspension_history(contract, employee),
        "accrual_mode": contract.get("accrual_mode") or "monthly",
        "accrual_start_date": (
            contract.get("accrual_start_date")
            or contract.get("effective_from")
            or employee.get("hire_date")
        ),
        "payroll_source": "mezan_employee_salary_contracts_v2",
    }


def salary_suspended_on(salary: dict[str, Any], day: date) -> bool:
    for period in normalized_suspensions(salary.get("payroll_suspension_periods")):
        started = _date(period.get("started_on"))
        returned = _date(period.get("returned_on"))
        if started and started <= day and (not returned or day < returned):
            return True
    return False


def salary_active_on(salary: dict[str, Any], day: date) -> bool:
    """Whether one V2 salary contributes cost/accrual on this calendar day."""
    started = _date(salary.get("start_date"))
    if started and day < started:
        return False
    periods = normalized_suspensions(salary.get("payroll_suspension_periods"))
    if periods:
        return not salary_suspended_on(salary, day)
    if str(salary.get("status") or "active") == "active":
        return True
    effective_to = _date(salary.get("effective_to"))
    return bool(effective_to and day <= effective_to)


def payable_days(salary: dict[str, Any], start: date, end: date) -> int:
    if end < start:
        return 0
    return sum(
        salary_active_on(salary, date.fromordinal(ordinal))
        for ordinal in range(start.toordinal(), end.toordinal() + 1)
    )


async def employee_salary_rows(db: Any, user_id: str) -> list[dict[str, Any]]:
    """Load authoritative employee salaries exclusively from Employee OS V2."""
    contracts = await db[SALARY_CONTRACTS_COLLECTION].find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(10000)
    employee_ids = [
        str(row.get("employee_id") or "").strip()
        for row in contracts
        if str(row.get("employee_id") or "").strip()
    ]
    if not employee_ids:
        return []
    employees = await db[EMPLOYEES_COLLECTION].find(
        {"user_id": user_id, "id": {"$in": employee_ids}}, {"_id": 0}
    ).to_list(max(len(employee_ids), 1))
    employees_by_id = {
        str(row.get("id") or "").strip(): row
        for row in employees
        if str(row.get("id") or "").strip()
    }
    return [
        contract_salary_row(contract, employees_by_id[employee_id])
        for contract in contracts
        if (employee_id := str(contract.get("employee_id") or "").strip())
        in employees_by_id
        and float(contract.get("monthly_amount") or 0) > 0
    ]


async def find_employee_salary(
    db: Any,
    user_id: str,
    salary_id: str,
) -> dict[str, Any] | None:
    """Resolve a V2 contract by contract id or historical compatibility id."""
    normalized_id = str(salary_id or "").strip()
    if not normalized_id:
        return None
    contract = await db[SALARY_CONTRACTS_COLLECTION].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"id": normalized_id},
                {"legacy_salary_id": normalized_id},
            ],
        },
        {"_id": 0},
    )
    if not contract:
        return None
    employee = await db[EMPLOYEES_COLLECTION].find_one(
        {"user_id": user_id, "id": contract.get("employee_id")},
        {"_id": 0},
    )
    return contract_salary_row(contract, employee) if employee else None
