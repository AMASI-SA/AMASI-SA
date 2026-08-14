"""Mezan V2 recurring obligations and variable utility accruals.

This module is the authoritative source for non-payroll recurring operating
costs in Dashboard V2.  It intentionally keeps cash settlement separate from
economic expense recognition:

* fixed obligations accrue ``period amount / actual days in the cycle``;
* electricity and water use the chosen historical invoice benchmark until an
  actual invoice is entered;
* an actual utility invoice replaces the estimate only for its covered days;
* payments never create a second P&L expense in this module.

No nightly write is required.  Accruals are derived from calendar dates on
every read, so the amount advances automatically at the start of each Riyadh
day.
"""
from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, root_validator

from tz_utils import riyadh_today


OBLIGATIONS = "operating_recurring_obligations_v2"
INVOICES = "operating_recurring_invoices_v2"
WAREHOUSES = "warehouse_locations_warehouses"
EMPLOYEES = "mezan_employees_v2"

FIXED_TYPES = {
    "rent",
    "iqama_visa",
    "employee_insurance",
    "vehicle_insurance",
    "commercial_registration",
    "government_license",
    "subscription",
    "other",
}
UTILITY_TYPES = {"electricity", "water"}
EXPENSE_TYPES = FIXED_TYPES | UTILITY_TYPES
CYCLES = {"monthly": 1, "semiannual": 6, "annual": 12, "biennial": 24}
ENTITY_TYPES = {"branch", "warehouse", "employee", "vehicle", "business", "other"}
ESTIMATION_BASES = {"last_invoice", "last_3_invoices", "manual"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(_text(value))
    except (TypeError, ValueError):
        return None


def _inclusive_days(start: date, end: date) -> int:
    return max((end - start).days + 1, 1)


def _add_months(value: date, months: int) -> date:
    """Calendar month addition with end-of-month clamping."""
    index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def cycle_bounds(
    start: date,
    cycle: str,
    target: date,
    *,
    custom_end: Optional[date] = None,
) -> Optional[tuple[date, date]]:
    """Return the obligation cycle covering ``target``.

    The end is inclusive.  A six-month cycle starting 2026-07-01 therefore
    ends 2026-12-31 and the following cycle starts 2027-01-01.
    """
    if target < start:
        return None
    if cycle == "custom":
        end = custom_end or start
        return (start, end) if target <= end else None
    months = CYCLES.get(cycle)
    if not months:
        return None
    month_delta = (target.year - start.year) * 12 + target.month - start.month
    step = max(month_delta // months, 0)
    current_start = _add_months(start, step * months)
    if current_start > target:
        step = max(step - 1, 0)
        current_start = _add_months(start, step * months)
    next_start = _add_months(start, (step + 1) * months)
    while target >= next_start:
        step += 1
        current_start = next_start
        next_start = _add_months(start, (step + 1) * months)
    return current_start, next_start - timedelta(days=1)


def obligation_active_on(row: dict[str, Any], target: date) -> bool:
    state = _text(row.get("status") or "active")
    if state not in {"active", "stopped"}:
        return False
    start = _parse_day(row.get("start_date"))
    if not start or target < start:
        return False
    stopped = _parse_day(row.get("stopped_at"))
    if stopped and target >= stopped:
        return False
    if row.get("auto_renew") is False:
        bounds = cycle_bounds(
            start,
            _text(row.get("cycle")),
            target,
            custom_end=_parse_day(row.get("custom_end_date")),
        )
        if not bounds:
            return False
        first_bounds = cycle_bounds(
            start,
            _text(row.get("cycle")),
            start,
            custom_end=_parse_day(row.get("custom_end_date")),
        )
        return bool(first_bounds and target <= first_bounds[1])
    return cycle_bounds(
        start,
        _text(row.get("cycle")),
        target,
        custom_end=_parse_day(row.get("custom_end_date")),
    ) is not None


def _invoice_days(invoice: dict[str, Any]) -> int:
    start = _parse_day(invoice.get("period_start"))
    end = _parse_day(invoice.get("period_end"))
    return _inclusive_days(start, end) if start and end else 1


def _invoice_for_day(
    invoices: list[dict[str, Any]], target: date
) -> Optional[dict[str, Any]]:
    for invoice in invoices:
        start = _parse_day(invoice.get("period_start"))
        end = _parse_day(invoice.get("period_end"))
        if start and end and start <= target <= end:
            return invoice
    return None


def _historical_invoice_rate(
    row: dict[str, Any], invoices: list[dict[str, Any]], target: date
) -> float:
    eligible = [
        invoice
        for invoice in invoices
        if (_parse_day(invoice.get("period_end")) or target) < target
        and float(invoice.get("amount") or 0) > 0
    ]
    eligible.sort(key=lambda invoice: _text(invoice.get("period_end")), reverse=True)
    basis = _text(row.get("estimation_basis") or "last_3_invoices")
    if basis == "last_invoice":
        selected = eligible[:1]
    elif basis == "last_3_invoices":
        selected = eligible[:3]
    else:
        selected = []
    if selected:
        amount = sum(float(invoice.get("amount") or 0) for invoice in selected)
        days = sum(_invoice_days(invoice) for invoice in selected)
        return amount / max(days, 1)

    start = _parse_day(row.get("start_date"))
    bounds = cycle_bounds(
        start or target,
        _text(row.get("cycle")),
        target,
        custom_end=_parse_day(row.get("custom_end_date")),
    )
    estimate = float(row.get("period_amount") or 0)
    return estimate / _inclusive_days(*bounds) if bounds and estimate > 0 else 0.0


def obligation_daily_amount(
    row: dict[str, Any], invoices: list[dict[str, Any]], target: date
) -> float:
    if not obligation_active_on(row, target):
        return 0.0
    expense_type = _text(row.get("expense_type"))
    actual = _invoice_for_day(invoices, target)
    if expense_type in UTILITY_TYPES:
        if actual:
            return float(actual.get("amount") or 0) / _invoice_days(actual)
        return _historical_invoice_rate(row, invoices, target)

    start = _parse_day(row.get("start_date")) or target
    bounds = cycle_bounds(
        start,
        _text(row.get("cycle")),
        target,
        custom_end=_parse_day(row.get("custom_end_date")),
    )
    if not bounds:
        return 0.0
    return float(row.get("period_amount") or 0) / _inclusive_days(*bounds)


async def _load_obligations_and_invoices(
    db: Any, user_id: str
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    obligations = await db[OBLIGATIONS].find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(5000)
    invoices = await db[INVOICES].find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(20000)
    by_obligation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for invoice in invoices:
        by_obligation[_text(invoice.get("obligation_id"))].append(invoice)
    return obligations, by_obligation


async def compute_recurring_obligations_for_range(
    db: Any, user_id: str, from_day: date, to_day: date
) -> dict[str, Any]:
    """Compute exact recurring accruals for an inclusive date range."""
    if to_day < from_day:
        from_day, to_day = to_day, from_day
    obligations, invoice_map = await _load_obligations_and_invoices(db, user_id)
    totals: dict[str, float] = defaultdict(float)
    current = from_day
    while current <= to_day:
        for row in obligations:
            daily = obligation_daily_amount(
                row, invoice_map.get(_text(row.get("id")), []), current
            )
            if daily <= 0:
                continue
            totals[_text(row.get("expense_type")) or "other"] += daily
        current += timedelta(days=1)

    by_type = {key: round(value, 2) for key, value in totals.items()}
    rentals = totals.get("rent", 0.0)
    utilities = totals.get("electricity", 0.0) + totals.get("water", 0.0)
    renewals = sum(
        value for key, value in totals.items()
        if key not in {"rent", "electricity", "water"}
    )
    total = sum(totals.values())
    return {
        "from_date": from_day.isoformat(),
        "to_date": to_day.isoformat(),
        "total": round(total, 2),
        "rentals_total": round(rentals, 2),
        "utilities_total": round(utilities, 2),
        "renewals_total": round(renewals, 2),
        "by_type": by_type,
        "source": OBLIGATIONS,
    }


def _require_owner(user: dict[str, Any]) -> dict[str, Any]:
    role = _text(user.get("role")).casefold()
    if role != "owner" and user.get("is_owner") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_required"},
        )
    return user


class HistoricalInvoiceIn(BaseModel):
    amount: float = Field(gt=0)
    period_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @root_validator(skip_on_failure=True)
    def valid_period(cls, values: dict[str, Any]) -> dict[str, Any]:
        start = _parse_day(values.get("period_start"))
        end = _parse_day(values.get("period_end"))
        if not start or not end or end < start:
            raise ValueError("invoice_period_invalid")
        return values


class ObligationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    expense_type: str
    entity_type: str
    entity_id: Optional[str] = Field(default=None, max_length=120)
    entity_name: str = Field(min_length=1, max_length=160)
    cycle: str
    period_amount: Optional[float] = Field(default=None, ge=0)
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    custom_end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    auto_renew: bool = True
    estimation_basis: Optional[str] = None
    notes: str = Field(default="", max_length=500)
    historical_invoices: list[HistoricalInvoiceIn] = Field(default_factory=list, max_length=3)

    @root_validator(skip_on_failure=True)
    def valid_contract(cls, values: dict[str, Any]) -> dict[str, Any]:
        expense_type = _text(values.get("expense_type"))
        cycle = _text(values.get("cycle"))
        entity_type = _text(values.get("entity_type"))
        amount = float(values.get("period_amount") or 0)
        if expense_type not in EXPENSE_TYPES:
            raise ValueError("expense_type_invalid")
        if cycle not in {*CYCLES, "custom"}:
            raise ValueError("cycle_invalid")
        if entity_type not in ENTITY_TYPES:
            raise ValueError("entity_type_invalid")
        start = _parse_day(values.get("start_date"))
        end = _parse_day(values.get("custom_end_date"))
        if cycle == "custom" and (not start or not end or end < start):
            raise ValueError("custom_period_invalid")
        if expense_type in FIXED_TYPES and amount <= 0:
            raise ValueError("period_amount_required")
        basis = _text(values.get("estimation_basis"))
        if expense_type in UTILITY_TYPES:
            if basis not in ESTIMATION_BASES:
                raise ValueError("estimation_basis_invalid")
            if basis == "manual" and amount <= 0:
                raise ValueError("manual_estimate_required")
        return values


class ObligationUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    entity_type: Optional[str] = None
    entity_id: Optional[str] = Field(default=None, max_length=120)
    entity_name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    cycle: Optional[str] = None
    period_amount: Optional[float] = Field(default=None, ge=0)
    start_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    custom_end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    auto_renew: Optional[bool] = None
    estimation_basis: Optional[str] = None
    status: Optional[Literal["active", "stopped"]] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class InvoiceCreate(HistoricalInvoiceIn):
    issue_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    due_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    payment_status: Literal["unpaid", "paid"] = "unpaid"
    paid_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str = Field(default="", max_length=500)


def _next_due(row: dict[str, Any], today: date) -> Optional[date]:
    start = _parse_day(row.get("start_date"))
    if not start:
        return None
    target = max(today, start)
    if row.get("auto_renew") is False:
        first = cycle_bounds(
            start,
            _text(row.get("cycle")),
            start,
            custom_end=_parse_day(row.get("custom_end_date")),
        )
        if not first or today > first[1]:
            return None
    bounds = cycle_bounds(
        start,
        _text(row.get("cycle")),
        target,
        custom_end=_parse_day(row.get("custom_end_date")),
    )
    if not bounds:
        return None
    return bounds[1] + timedelta(days=1)


async def _entity_exists(
    db: Any, user_id: str, entity_type: str, entity_id: Optional[str]
) -> bool:
    if entity_type in {"business", "vehicle", "other"}:
        return True
    if not entity_id:
        return False
    if entity_type in {"branch", "warehouse"}:
        return bool(await db[WAREHOUSES].find_one(
            {"user_id": user_id, "id": entity_id, "status": "active"}, {"_id": 0, "id": 1}
        ))
    if entity_type == "employee":
        return bool(await db[EMPLOYEES].find_one(
            {"user_id": user_id, "id": entity_id}, {"_id": 0, "id": 1}
        ))
    return False


async def _enrich_rows(
    db: Any, user_id: str, rows: list[dict[str, Any]], today: date
) -> list[dict[str, Any]]:
    ids = [_text(row.get("id")) for row in rows]
    invoices = await db[INVOICES].find(
        {"user_id": user_id, "obligation_id": {"$in": ids}}, {"_id": 0}
    ).to_list(20000) if ids else []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for invoice in invoices:
        grouped[_text(invoice.get("obligation_id"))].append(invoice)
    output = []
    for row in rows:
        item = dict(row)
        related = grouped.get(_text(item.get("id")), [])
        daily = obligation_daily_amount(item, related, today)
        due = _next_due(item, today)
        state = _text(item.get("status") or "active")
        unpaid_due_dates = [
            _parse_day(invoice.get("due_date"))
            for invoice in related
            if invoice.get("payment_status") != "paid" and invoice.get("due_date")
        ]
        if state == "active" and any(day and day < today for day in unpaid_due_dates):
            display_status = "overdue"
        elif state == "active" and due and 0 <= (due - today).days <= 30:
            display_status = "due_soon"
        else:
            display_status = state
        accrual_start = _parse_day(item.get("start_date"))
        if not accrual_start or accrual_start > today:
            accrued_to_today = 0.0
        else:
            accrued_to_today = (await compute_recurring_obligations_for_range(
                _SingleObligationDb(item, related), user_id,
                accrual_start, today,
            ))["total"]
        item.update({
            "daily_amount": round(daily, 2),
            "next_due_date": due.isoformat() if due else None,
            "display_status": display_status,
            "invoice_count": len(related),
            "accrued_to_today": round(accrued_to_today, 2),
        })
        output.append(item)
    return output


class _ListCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    async def to_list(self, _limit: int) -> list[dict[str, Any]]:
        return list(self.rows)


class _ListCollection:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def find(self, _query: dict[str, Any], _projection: dict[str, Any]) -> _ListCursor:
        return _ListCursor(self.rows)


class _SingleObligationDb:
    """Small adapter used only to reuse the authoritative range calculator."""
    def __init__(self, row: dict[str, Any], invoices: list[dict[str, Any]]):
        self.collections = {
            OBLIGATIONS: _ListCollection([row]),
            INVOICES: _ListCollection(invoices),
        }

    def __getitem__(self, name: str) -> _ListCollection:
        return self.collections[name]


async def ensure_recurring_obligation_indexes(db: Any) -> None:
    try:
        await db[OBLIGATIONS].create_index(
            [("user_id", 1), ("status", 1), ("expense_type", 1)],
            name="ix_recurring_obligations_v2",
        )
        await db[INVOICES].create_index(
            [("user_id", 1), ("obligation_id", 1), ("period_start", 1), ("period_end", 1)],
            name="ix_recurring_invoices_v2",
        )
    except Exception:
        pass


def make_recurring_obligations_router(
    db: Any, current_user: Callable[..., Any]
) -> APIRouter:
    router = APIRouter(prefix="/recurring-obligations", tags=["Recurring Obligations V2"])

    @router.get("/options")
    async def options(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = _require_owner(user)
        user_id = _text(owner.get("id"))
        warehouses = await db[WAREHOUSES].find(
            {"user_id": user_id, "status": "active"}, {"_id": 0, "id": 1, "name": 1, "code": 1}
        ).sort("created_at", 1).to_list(500)
        employees = await db[EMPLOYEES].find(
            {"user_id": user_id}, {"_id": 0, "id": 1, "display_name": 1, "status": 1}
        ).sort("display_name", 1).to_list(1000)
        return {"locations": warehouses, "employees": employees}

    @router.get("")
    async def list_obligations(
        expense_type: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        user_id = _text(owner.get("id"))
        query: dict[str, Any] = {"user_id": user_id}
        if expense_type:
            query["expense_type"] = expense_type
        rows = await db[OBLIGATIONS].find(query, {"_id": 0}).sort(
            [("status", 1), ("created_at", -1)]
        ).to_list(5000)
        today = riyadh_today()
        items = await _enrich_rows(db, user_id, rows, today)
        range_today = await compute_recurring_obligations_for_range(
            db, user_id, today, today
        )
        due_soon = sum(1 for item in items if item.get("display_status") == "due_soon")
        overdue = sum(1 for item in items if item.get("display_status") == "overdue")
        due_amount = sum(
            float(item.get("period_amount") or 0)
            for item in items
            if item.get("display_status") == "due_soon"
        )
        return {
            "items": items,
            "summary": {
                "daily_total": range_today["total"],
                "due_next_30_days": round(due_amount, 2),
                "active_count": sum(1 for item in items if item.get("status") == "active"),
                "due_soon_count": due_soon,
                "overdue_count": overdue,
            },
            "source_contract": OBLIGATIONS,
        }

    @router.post("", status_code=201)
    async def create_obligation(
        payload: ObligationCreate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        user_id = _text(owner.get("id"))
        if not await _entity_exists(
            db, user_id, payload.entity_type, payload.entity_id
        ):
            raise HTTPException(status_code=422, detail={"code": "linked_entity_not_found"})
        now = _now()
        obligation_id = str(uuid.uuid4())
        doc = {
            "id": obligation_id,
            "user_id": user_id,
            "title": payload.title.strip(),
            "expense_type": payload.expense_type,
            "entity_type": payload.entity_type,
            "entity_id": payload.entity_id,
            "entity_name": payload.entity_name.strip(),
            "cycle": payload.cycle,
            "period_amount": round(float(payload.period_amount or 0), 2),
            "start_date": payload.start_date,
            "custom_end_date": payload.custom_end_date,
            "auto_renew": payload.auto_renew,
            "estimation_basis": payload.estimation_basis,
            "status": "active",
            "stopped_at": None,
            "notes": payload.notes.strip(),
            "created_at": now,
            "updated_at": now,
            "created_by": user_id,
        }
        await db[OBLIGATIONS].insert_one(doc)
        for historical in payload.historical_invoices:
            invoice = historical.dict()
            invoice.update({
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "obligation_id": obligation_id,
                "issue_date": None,
                "due_date": None,
                "payment_status": "paid",
                "paid_date": None,
                "notes": "فاتورة سابقة لمعيار التقدير",
                "created_at": now,
                "updated_at": now,
            })
            invoice["amount"] = round(float(invoice["amount"]), 2)
            await db[INVOICES].insert_one(invoice)
        doc.pop("_id", None)
        return doc

    @router.put("/{obligation_id}")
    async def update_obligation(
        obligation_id: str,
        payload: ObligationUpdate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        user_id = _text(owner.get("id"))
        existing = await db[OBLIGATIONS].find_one(
            {"user_id": user_id, "id": obligation_id}, {"_id": 0}
        )
        if not existing:
            raise HTTPException(status_code=404, detail={"code": "obligation_not_found"})
        updates = {key: value for key, value in payload.dict(exclude_unset=True).items()}
        merged = {**existing, **updates}
        try:
            ObligationCreate(
                **{
                    "title": merged["title"],
                    "expense_type": merged["expense_type"],
                    "entity_type": merged["entity_type"],
                    "entity_id": merged.get("entity_id"),
                    "entity_name": merged["entity_name"],
                    "cycle": merged["cycle"],
                    "period_amount": merged.get("period_amount"),
                    "start_date": merged["start_date"],
                    "custom_end_date": merged.get("custom_end_date"),
                    "auto_renew": merged.get("auto_renew", True),
                    "estimation_basis": merged.get("estimation_basis"),
                    "notes": merged.get("notes", ""),
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "obligation_invalid"}) from exc
        if any(key in updates for key in {"entity_type", "entity_id"}) and not await _entity_exists(
            db, user_id, merged["entity_type"], merged.get("entity_id")
        ):
            raise HTTPException(status_code=422, detail={"code": "linked_entity_not_found"})
        if "period_amount" in updates:
            updates["period_amount"] = round(float(updates["period_amount"] or 0), 2)
        if updates.get("status") == "stopped" and existing.get("status") != "stopped":
            updates["stopped_at"] = riyadh_today().isoformat()
        elif updates.get("status") == "active":
            updates["stopped_at"] = None
        updates["updated_at"] = _now()
        await db[OBLIGATIONS].update_one(
            {"user_id": user_id, "id": obligation_id}, {"$set": updates}
        )
        return await db[OBLIGATIONS].find_one(
            {"user_id": user_id, "id": obligation_id}, {"_id": 0}
        )

    @router.get("/{obligation_id}/invoices")
    async def list_invoices(
        obligation_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        user_id = _text(owner.get("id"))
        if not await db[OBLIGATIONS].find_one(
            {"user_id": user_id, "id": obligation_id}, {"_id": 0, "id": 1}
        ):
            raise HTTPException(status_code=404, detail={"code": "obligation_not_found"})
        rows = await db[INVOICES].find(
            {"user_id": user_id, "obligation_id": obligation_id}, {"_id": 0}
        ).sort("period_end", -1).to_list(5000)
        return {"items": rows}

    @router.post("/{obligation_id}/invoices", status_code=201)
    async def create_invoice(
        obligation_id: str,
        payload: InvoiceCreate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        user_id = _text(owner.get("id"))
        obligation = await db[OBLIGATIONS].find_one(
            {"user_id": user_id, "id": obligation_id}, {"_id": 0}
        )
        if not obligation:
            raise HTTPException(status_code=404, detail={"code": "obligation_not_found"})
        overlap = await db[INVOICES].find_one({
            "user_id": user_id,
            "obligation_id": obligation_id,
            "period_start": {"$lte": payload.period_end},
            "period_end": {"$gte": payload.period_start},
        }, {"_id": 0, "id": 1})
        if overlap:
            raise HTTPException(status_code=409, detail={"code": "invoice_period_overlap"})
        now = _now()
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "obligation_id": obligation_id,
            "amount": round(float(payload.amount), 2),
            "period_start": payload.period_start,
            "period_end": payload.period_end,
            "issue_date": payload.issue_date,
            "due_date": payload.due_date,
            "payment_status": payload.payment_status,
            "paid_date": payload.paid_date,
            "notes": payload.notes.strip(),
            "created_at": now,
            "updated_at": now,
        }
        await db[INVOICES].insert_one(doc)
        doc.pop("_id", None)
        return doc

    return router
