"""Bounded, read-only executive operating basis for one Riyadh store day.

Dashboard V2 uses gross SAR sales from its configured order cohort and replaces
legacy operating costs with Employee OS/household/charity salaries plus V2
recurring obligations. Reuse those policies without invoking a dashboard,
product, advertising or refresh path. The caller allocates with the unrounded
ratio and rounds the resulting campaign amount only at the monetary boundary.
"""
from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
from dashboard_v2_routes import _matches_any
from employee_payroll_status import (
    EMPLOYEES_COLLECTION, SALARY_CONTRACTS_COLLECTION, contract_salary_row,
    salary_amount_on,
)
from expenses_routes import _daily_from_monthly, _salary_active_on
from order_currency import (
    SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields_cooperative,
    summarize_orders_sar_cooperative,
)
from recurring_obligations_routes import (
    INVOICES, OBLIGATIONS, obligation_active_on, obligation_daily_amount,
)


STORE_TIMEZONE = "Asia/Riyadh"
MAX_STORE_ORDERS = 4000
MAX_PAYROLL_ROWS = 1000
MAX_OBLIGATIONS = 1000
MAX_INVOICES = 5000
BASIS_TIMEOUT_SECONDS = 3.0

ORDER_PROJECTION = {**SALLA_RAW_CURRENCY_PROJECTION, **{key: 1 for key in (
    "order_status", "total_amount", "total", "amounts", "currency",
    "original_currency", "original_total_amount", "accounting_currency",
    "total_amount_sar", "exchange_rate_to_sar", "exchange_rate",
    "currency_conversion_status",
)}}
SALARY_PROJECTIONS = {
    SALARY_CONTRACTS_COLLECTION: {"_id": 0, **{key: 1 for key in (
        "id", "employee_id", "monthly_amount", "currency", "effective_from", "start_date",
        "effective_to", "salary_revisions", "suspension_periods", "payroll_state",
        "accrual_mode", "accrual_start_date",
    )}},
    EMPLOYEES_COLLECTION: {"_id": 0, **{key: 1 for key in (
        "id", "status", "hire_date", "updated_at", "archived", "is_archived",
        "deleted", "is_deleted",
    )}},
}


class _Unavailable(ValueError):
    pass


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


async def _read(db, name, query, projection, limit):
    rows = await db[name].find(query, projection).max_time_ms(1500).limit(limit + 1).to_list(limit + 1)
    if len(rows) > limit:
        raise _Unavailable("operating_basis_read_limit")
    return rows


async def _gather(*reads):
    tasks = [asyncio.create_task(read) for read in reads]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _employee_salary_rows_on(db, owner, day, budget):
    """Apply the shared employee identity/calendar policy before cost validation."""
    contracts = await _read(db, SALARY_CONTRACTS_COLLECTION, {"user_id": owner},
        SALARY_PROJECTIONS[SALARY_CONTRACTS_COLLECTION], MAX_PAYROLL_ROWS)
    employee_ids = [str(row.get("employee_id") or "").strip() for row in contracts
        if str(row.get("employee_id") or "").strip()]
    if not employee_ids:
        return []
    employees = await _read(db, EMPLOYEES_COLLECTION,
        {"user_id": owner, "id": {"$in": employee_ids}}, SALARY_PROJECTIONS[EMPLOYEES_COLLECTION],
        min(len(employee_ids), MAX_PAYROLL_ROWS))
    employees_by_id = {str(row.get("id") or "").strip(): row for row in employees
        if str(row.get("id") or "").strip()
        and not any(row.get(flag) for flag in ("archived", "is_archived", "deleted", "is_deleted"))}
    seen, salaries = set(), []
    for contract in contracts:
        await budget.checkpoint()
        employee_id = str(contract.get("employee_id") or "").strip()
        if not employee_id or employee_id not in employees_by_id:
            continue
        if employee_id in seen:
            raise ValueError("employee_salary_multiple_contracts")
        seen.add(employee_id)
        employee = employees_by_id[employee_id]
        # Explicit hire/stop/leave dates can prove a contract contributes nothing
        # before malformed monetary history is inspected. The untouched contract
        # below still supplies revision-derived start dates and all actual amounts.
        calendar_row = contract_salary_row({**contract, "monthly_amount": None,
            "salary_revisions": []}, employee)
        if not _salary_active_on(calendar_row, day):
            continue
        salary = contract_salary_row(contract, employee)
        if not _salary_active_on(salary, day):
            continue
        if (str(contract.get("currency") or "SAR").upper() != "SAR"
                or not salary["salary_revisions"]):
            raise _Unavailable("operating_basis_cost_missing")
        salaries.append(salary)
    return salaries


async def _salary_total(db, owner, day, budget):
    employee_rows, other_rows = await _gather(
        _employee_salary_rows_on(db, owner, day, budget),
        _read(db, "operating_salaries", {"user_id": owner,
            "category": {"$in": ["household", "charity"]}},
            {"_id": 0, "category": 1, "status": 1, "start_date": 1, "monthly_amount": 1},
            MAX_PAYROLL_ROWS),
    )
    totals = defaultdict(float)
    for row in employee_rows + other_rows:
        await budget.checkpoint()
        if not _salary_active_on(row, day):
            continue
        category = str(row.get("category") or "").strip()
        monthly = _number(salary_amount_on(row, day) if category == "employee" else row.get("monthly_amount"))
        if monthly is None or monthly < 0:
            raise _Unavailable("operating_basis_cost_missing")
        totals[category] += _daily_from_monthly(monthly, day)
    # Same grouping and rounding boundary as the executive legacy salary seam.
    return round(totals["employee"] + totals["household"] + totals["charity"], 2)


async def _recurring_total(db, owner, day, budget):
    rows = await _read(db, OBLIGATIONS, {"user_id": owner}, {"_id": 0,
        **{key: 1 for key in ("id", "expense_type", "status", "start_date", "stopped_at",
            "auto_renew", "cycle", "custom_end_date", "period_amount", "estimation_basis")}}, MAX_OBLIGATIONS)
    invoices = await _read(db, INVOICES,
        {"user_id": owner, "obligation_id": {"$in": [str(row.get("id") or "").strip() for row in rows]}},
        {"_id": 0, "obligation_id": 1, "amount": 1, "period_start": 1, "period_end": 1},
        MAX_INVOICES) if rows else []
    by_obligation = defaultdict(list)
    for invoice in invoices:
        await budget.checkpoint()
        by_obligation[str(invoice.get("obligation_id") or "").strip()].append(invoice)
    totals = defaultdict(float)
    for row in rows:
        await budget.checkpoint()
        if not obligation_active_on(row, day):
            continue
        daily = _number(obligation_daily_amount(row,
            by_obligation.get(str(row.get("id") or "").strip(), []), day))
        # The executive calculator gives valid utilities without an invoice or
        # configured estimate a zero contribution. Allocate that same reported
        # operating total; do not add a separate completeness rule here.
        if daily is None or daily < 0:
            raise _Unavailable("operating_basis_cost_missing")
        if daily == 0:
            continue
        totals[str(row.get("expense_type") or "other").strip()] += daily
    return round(sum(totals.values()), 2)


async def _sales_total(db, owner, day, settings, budget):
    included = settings.get("report_included_statuses") or []
    if not isinstance(included, list) or any(not isinstance(value, str) for value in included):
        raise _Unavailable("operating_basis_settings_invalid")
    query = {"user_id": owner, "order_date": {"$gte": day.isoformat(), "$lte": day.isoformat()}}
    if settings.get("hide_inferred_date_orders"):
        query["order_date_inferred"] = {"$ne": True}
    orders = await _read(db, "unified_orders", query, ORDER_PROJECTION, MAX_STORE_ORDERS)
    selected = []
    for order in orders:
        await budget.checkpoint()
        if _matches_any(order.get("order_status", ""), included):
            selected.append(order)
    await hydrate_order_currency_fields_cooperative(selected, (), checkpoint=budget.checkpoint)
    return await summarize_orders_sar_cooperative(selected, checkpoint=budget.checkpoint)


async def _basis(db, owner, day):
    settings_rows = await _read(db, "settings", {"user_id": owner},
        {"_id": 0, "report_included_statuses": 1, "hide_inferred_date_orders": 1}, 1)
    budget = CPUWorkBudget(PARSER_BUDGET_MS)
    sales, salary, recurring = await _gather(
        _sales_total(db, owner, day, settings_rows[0] if settings_rows else {}, budget),
        _salary_total(db, owner, day, budget),
        _recurring_total(db, owner, day, budget),
    )
    expenses = round(salary + recurring, 2)
    total = _number(sales.get("total_sar"))
    if sales.get("conversion_complete") is not True or total is None:
        return {"complete": False, "ratio": None, "expenses_sar": expenses,
            "sales_sar": None, "reasons": ["operating_basis_sales_currency_unverified"]}
    if total <= 0:
        return {"complete": False, "ratio": None, "expenses_sar": expenses,
            "sales_sar": total, "reasons": ["operating_basis_zero_sales"]}
    ratio = expenses / total
    if not math.isfinite(ratio):
        raise _Unavailable("operating_basis_cost_missing")
    return {"complete": True, "ratio": ratio, "expenses_sar": expenses,
        "sales_sar": total, "reasons": []}


async def read_operating_basis(db: Any, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Return one read-only basis for the hover batch, including explicit gaps."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    day = instant.astimezone(ZoneInfo(STORE_TIMEZONE)).date()
    result = {"complete": False, "ratio": None, "expenses_sar": None, "sales_sar": None,
        "date": day.isoformat(), "timezone": STORE_TIMEZONE, "reasons": []}
    if not isinstance(user_id, str) or not user_id.strip():
        result["reasons"] = ["operating_basis_owner_invalid"]
        return result
    try:
        result.update(await asyncio.wait_for(_basis(db, user_id, day), timeout=BASIS_TIMEOUT_SECONDS))
    except asyncio.TimeoutError:
        result["reasons"] = ["operating_basis_timeout"]
    except _Unavailable as exc:
        result["reasons"] = [str(exc)]
    except Exception:
        result["reasons"] = ["operating_basis_unavailable"]
    return result
