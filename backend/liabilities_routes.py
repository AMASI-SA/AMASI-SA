"""Liabilities Center — Iter-92 Phase 1
========================================

Single new collection `liabilities` that models every monetary obligation
the merchant carries:

  • salary           — generated monthly from active `operating_salaries`
                       (idempotent: one row per (employee × month)).
  • ad_account       — manually entered when Snap/TikTok/Meta send a bill.
  • salary_advance   — money advanced to an employee out of the bank
                       BEFORE the salary month closes. Auto-deducted from
                       the matching salary obligation when generated.

Reused (no new collections):
  • operating_salaries     — employee definitions (read-only here).
  • accounts               — source of payment (bank / wallet).
  • account_transactions   — every payment writes a row here so the bank
                             ledger stays consistent.

Endpoints (all under /api/liabilities):
  POST   /generate-salaries        # create this-month rows for active staff
  POST   /                         # create an ad_account bill (or manual)
  GET    /                         # list with filters
  GET    /summary                  # Assets − Liabilities = Net position
  PUT    /{id}                     # edit expected/due_date/notes
  POST   /{id}/pay                 # record a payment from a bank account
  DELETE /{id}                     # delete (only if paid_amount == 0)
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, validator

from auth import get_current_user_from_db
from tz_utils import riyadh_today, riyadh_today_iso


# ── Catalogue ──────────────────────────────────────────────────────────────
# Iter-97 — added "supplier" (generic third-party invoices like landlords,
# contractors, packaging vendors) and "receivable" (money owed TO the
# merchant by customers, employees, third parties — a current asset).
LIABILITY_KINDS = (
    "salary", "ad_account", "salary_advance",
    "supplier", "receivable",
)
LIABILITY_STATUSES = ("unpaid", "partial", "paid")
AD_PROVIDERS = ("snapchat", "tiktok", "meta", "google", "twitter", "other")
RECEIVABLE_PARTY_TYPES = ("customer", "employee", "person", "company")


# ── Helpers ────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    # Iter-140 — Asia/Riyadh calendar date (server runs in UTC).
    return riyadh_today_iso()


def _strip(doc: dict) -> dict:
    return {k: v for k, v in (doc or {}).items() if not k.startswith("_")}


def _round(v) -> float:
    return round(float(v or 0), 2)


def _compute_status(expected: float, paid: float) -> str:
    """Recompute the status field from the amounts only."""
    expected = _round(expected)
    paid = _round(paid)
    if expected <= 0:                # nothing owed → row is settled
        return "paid"
    if paid <= 0:
        return "unpaid"
    if paid + 0.01 >= expected:
        return "paid"
    return "partial"


def _enrich(row: dict) -> dict:
    """Add runtime fields (overdue flag) without persisting them."""
    out = _strip(row)
    out["remaining_amount"] = _round(
        _round(out.get("expected_amount")) - _round(out.get("paid_amount"))
    )
    due = out.get("due_date") or ""
    out["is_overdue"] = bool(
        out.get("status") != "paid" and due and due < _today_str()
    )
    return out


async def ensure_liabilities_indexes(db) -> None:
    """Idempotent salary uniqueness: one row per (user × employee × month)."""
    try:
        await db.liabilities.create_index(
            [("user_id", 1), ("kind", 1),
             ("employee_salary_id", 1), ("period_key", 1)],
            unique=True,
            partialFilterExpression={"kind": "salary"},
            name="liab_salary_unique",
        )
    except Exception:
        pass
    try:
        await db.liabilities.create_index(
            [("user_id", 1), ("id", 1)], unique=True, name="liab_pk"
        )
    except Exception:
        pass
    try:
        await db.liabilities.create_index(
            [("user_id", 1), ("kind", 1), ("status", 1), ("due_date", 1)],
            name="liab_query",
        )
    except Exception:
        pass


# ── Pydantic ───────────────────────────────────────────────────────────────
class LiabilityCreate(BaseModel):
    """Manual creation. Salaries are still created via /generate-salaries
    for idempotency; everything else (ad/supplier/advance/receivable)
    goes through here."""
    kind: Literal["ad_account", "salary_advance", "supplier", "receivable"]
    expected_amount: float = Field(..., gt=0)
    due_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    description: str = Field("", max_length=300)
    notes: Optional[str] = Field("", max_length=500)
    # ad_account
    ad_provider: Optional[str] = None
    ad_account_label: Optional[str] = Field(None, max_length=160)
    # salary_advance
    employee_salary_id: Optional[str] = None
    paid_from_account_id: Optional[str] = None  # required for advances
    # Iter-99 — preferred linkage: counterparty_id from counterparties table.
    # When provided for supplier/ad_account, the row name is sourced from
    # the counterparty record (single source of truth, no dupes).
    # Declared BEFORE supplier_name so its value is available to the
    # supplier_name validator below (pydantic v1 validates in declaration order).
    counterparty_id: Optional[str] = None
    # supplier
    supplier_name: Optional[str] = Field(None, max_length=160)
    # receivable
    counterparty_name: Optional[str] = Field(None, max_length=160)
    counterparty_type: Optional[str] = None  # customer / employee / person / company

    @validator("ad_provider")
    def _v_prov(cls, v, values):
        if values.get("kind") == "ad_account":
            if not v or v not in AD_PROVIDERS:
                raise ValueError(f"ad_provider must be one of {AD_PROVIDERS}")
        return v

    @validator("employee_salary_id")
    def _v_emp(cls, v, values):
        if values.get("kind") == "salary_advance" and not v:
            raise ValueError("employee_salary_id required for salary_advance")
        return v

    @validator("paid_from_account_id")
    def _v_pacc(cls, v, values):
        if values.get("kind") == "salary_advance" and not v:
            raise ValueError(
                "paid_from_account_id required for salary_advance"
            )
        return v

    @validator("supplier_name")
    def _v_supp(cls, v, values):
        # Iter-99 — supplier_name is required only if no counterparty_id
        # was provided (counterparty supplies the name in that case).
        if (values.get("kind") == "supplier"
                and not (v and v.strip())
                and not values.get("counterparty_id")):
            raise ValueError("supplier_name or counterparty_id required for supplier")
        return v

    @validator("counterparty_name")
    def _v_cp(cls, v, values):
        if values.get("kind") == "receivable" and not (v and v.strip()):
            raise ValueError("counterparty_name required for receivable")
        return v


class LiabilityUpdate(BaseModel):
    expected_amount: Optional[float] = Field(None, gt=0)
    due_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    description: Optional[str] = Field(None, max_length=300)
    notes: Optional[str] = Field(None, max_length=500)
    ad_account_label: Optional[str] = Field(None, max_length=160)


class PaymentIn(BaseModel):
    amount: float = Field(..., gt=0)
    paid_from_account_id: str
    payment_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: Optional[str] = Field("", max_length=500)


# ── Core logic ─────────────────────────────────────────────────────────────
async def _recompute_account_balance(db, user_id: str, account_id: str) -> float:
    """Light-weight account balance recompute (mirrors accounts_routes)."""
    acc = await db.accounts.find_one(
        {"id": account_id, "user_id": user_id},
        {"_id": 0, "expected_orders_balance": 1},
    ) or {}
    running = float(acc.get("expected_orders_balance") or 0)
    docs = await db.account_transactions.find(
        {"user_id": user_id, "account_id": account_id},
        {"_id": 0, "id": 1, "amount": 1, "direction": 1, "balance_after": 1},
    ).sort([("transaction_date", 1), ("created_at", 1)]).to_list(50000)
    for d in docs:
        amt = float(d.get("amount", 0) or 0)
        running += amt if d.get("direction") == "in" else -amt
        new_balance = round(running, 2)
        if d.get("balance_after") != new_balance:
            await db.account_transactions.update_one(
                {"id": d["id"], "user_id": user_id},
                {"$set": {"balance_after": new_balance, "updated_at": _now()}},
            )
    final = round(running, 2)
    await db.accounts.update_one(
        {"id": account_id, "user_id": user_id},
        {"$set": {"current_balance": final, "updated_at": _now()}},
    )
    return final


async def _post_bank_tx(
    db, user_id: str, *,
    account_id: str, amount: float, direction: str,
    transaction_date: str, description: str,
    peer_liability_id: Optional[str] = None,
    transaction_type: str = "debt_payment",
) -> dict:
    """Insert an account_transactions row for a liability payment / advance."""
    tx = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "account_id": account_id,
        "transaction_type": transaction_type,
        "amount": _round(amount),
        "direction": direction,
        "description": description,
        "transaction_date": transaction_date,
        "balance_after": 0.0,  # filled by recompute
        "status": "posted",
        "peer_liability_id": peer_liability_id,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.account_transactions.insert_one(tx)
    await _recompute_account_balance(db, user_id, account_id)
    tx.pop("_id", None)
    return tx


async def _apply_open_advances_to_salary(
    db, user_id: str, employee_salary_id: str, salary_liability: dict
) -> dict:
    """When a salary obligation is generated, consume any open advances
    (kind=salary_advance, remaining < 0 meaning the employee owes) by
    deducting them from the salary expected_amount.

    Advances are recorded with paid_amount == expected_amount (already
    out of the bank). They have a field `consumed_amount` that grows as
    each new salary deducts from them.
    """
    open_adv = await db.liabilities.find(
        {
            "user_id": user_id,
            "kind": "salary_advance",
            "employee_salary_id": employee_salary_id,
            "advance_status": "open",
        },
        {"_id": 0},
    ).sort([("created_at", 1)]).to_list(1000)

    remaining_salary = _round(salary_liability["expected_amount"])
    total_deducted = 0.0

    for adv in open_adv:
        if remaining_salary <= 0:
            break
        adv_remaining = _round(
            adv["expected_amount"] - (adv.get("consumed_amount") or 0)
        )
        if adv_remaining <= 0:
            continue
        take = min(adv_remaining, remaining_salary)
        new_consumed = _round((adv.get("consumed_amount") or 0) + take)
        new_status = (
            "fully_consumed"
            if new_consumed + 0.01 >= adv["expected_amount"]
            else "open"
        )
        await db.liabilities.update_one(
            {"id": adv["id"], "user_id": user_id},
            {"$set": {
                "consumed_amount": new_consumed,
                "advance_status": new_status,
                "updated_at": _now(),
            }},
        )
        total_deducted = _round(total_deducted + take)
        remaining_salary = _round(remaining_salary - take)

    if total_deducted > 0:
        # Treat the consumed advance as a pre-paid amount on the salary
        # (the employee already received this money).
        new_paid = _round(
            (salary_liability.get("paid_amount") or 0) + total_deducted
        )
        new_status = _compute_status(
            salary_liability["expected_amount"], new_paid
        )
        await db.liabilities.update_one(
            {"id": salary_liability["id"], "user_id": user_id},
            {"$set": {
                "paid_amount": new_paid,
                "status": new_status,
                "advance_deducted": total_deducted,
                "updated_at": _now(),
            }},
        )
        salary_liability["paid_amount"] = new_paid
        salary_liability["status"] = new_status
        salary_liability["advance_deducted"] = total_deducted

    return salary_liability


def _last_day_of_month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


# ── Iter-115 — Cross-month daily salary accrual ────────────────────────────
# Per user requirement: an employee's accrued salary equals the sum of
# their daily-rate over every actually-worked calendar day.  Each month
# uses ITS OWN days_in_month (June=30, July=31, ...). Suspended employees
# stop accruing at their stop date (operating_salaries.stopped_at if set,
# else updated_at as a fallback).
def _parse_iso_safe(s: Optional[str]) -> Optional[date]:
    if not s or len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def _add_one_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _compute_employee_accrual(
    emp: dict, today: Optional[date] = None,
) -> dict:
    """Compute days-worked and accrued amount for a single employee row.

    Rules (Iter-115):
      • Daily rate is recomputed per month using THAT month's day count.
      • Active employees accrue from start_date → today (inclusive).
      • Stopped employees accrue from start_date → stop_date (inclusive).
        stop_date = operating_salaries.stopped_at  ─ if explicitly stored
                  ↪ else operating_salaries.updated_at  (when status was
                    flipped to 'stopped').
        If neither exists, we DON'T accrue past today (defensive default).
      • If the employee has not started yet (start_date > end), accrued=0.
    """
    today = today or riyadh_today()
    start = _parse_iso_safe(emp.get("start_date"))
    if start is None:
        return {
            "days_worked": 0,
            "accrued": 0.0,
            "start_date": None,
            "end_date": None,
            "is_active": (emp.get("status") or "active") == "active",
        }

    is_active = (emp.get("status") or "active") == "active"
    if is_active:
        end = today
    else:
        stop_str = (
            emp.get("stopped_at")
            or emp.get("updated_at")
            or ""
        )
        end = _parse_iso_safe(stop_str) or today
        # Clamp: a stop date in the future is meaningless.
        if end > today:
            end = today

    if start > end:
        return {
            "days_worked": 0,
            "accrued": 0.0,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "is_active": is_active,
        }

    monthly = float(emp.get("monthly_amount") or 0)
    total = 0.0
    days = 0
    cursor = start
    while cursor <= end:
        dim = calendar.monthrange(cursor.year, cursor.month)[1]
        month_last = date(cursor.year, cursor.month, dim)
        eff_end = min(end, month_last)
        seg_days = (eff_end - cursor).days + 1
        daily_rate = (monthly / dim) if dim > 0 else 0.0
        total += daily_rate * seg_days
        days += seg_days
        cursor = _add_one_month(cursor)

    return {
        "days_worked": days,
        "accrued": round(total, 2),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "is_active": is_active,
    }


async def _aggregate_salary_accrual(db, user_id: str) -> dict:
    """Aggregate salary-accrual metrics across all employees for the
    `المركز المالي` view.  Only category=employee rows participate.

    Advances are reported SEPARATELY — they DO NOT cancel the raw
    accrued liability (user requirement, Iter-115).

    Paid total = actual bank-cash payments made on salary obligations,
    i.e. liabilities.kind=salary  (paid_amount − advance_deducted).
    Advances that got consumed are NOT counted here; they live in the
    advances bucket.
    """
    today = riyadh_today()
    employees: List[dict] = []
    accrued_total = 0.0
    active_count = 0
    suspended_count = 0

    cursor = db.operating_salaries.find(
        {"user_id": user_id, "category": "employee"}, {"_id": 0},
    )
    async for emp in cursor:
        calc = _compute_employee_accrual(emp, today=today)
        accrued_total += calc["accrued"]
        if calc["is_active"]:
            active_count += 1
        else:
            suspended_count += 1
        employees.append({
            "id": emp.get("id"),
            "name": emp.get("name"),
            "status": emp.get("status") or "active",
            "monthly_amount": round(float(emp.get("monthly_amount") or 0), 2),
            "start_date": calc["start_date"],
            "end_date": calc["end_date"],
            "days_worked": calc["days_worked"],
            "accrued": calc["accrued"],
        })

    # ── Outstanding advances (employee debt to company) ──
    advances_total = 0.0
    open_advances: List[dict] = []
    async for adv in db.liabilities.find(
        {
            "user_id": user_id,
            "kind": "salary_advance",
            "advance_status": "open",
        },
        {"_id": 0, "id": 1, "employee_salary_id": 1,
         "expected_amount": 1, "consumed_amount": 1,
         "description": 1, "due_date": 1, "created_at": 1},
    ):
        remaining = _round(
            _round(adv.get("expected_amount"))
            - _round(adv.get("consumed_amount"))
        )
        if remaining <= 0:
            continue
        advances_total += remaining
        open_advances.append({
            "id": adv.get("id"),
            "employee_salary_id": adv.get("employee_salary_id"),
            "remaining": remaining,
            "description": adv.get("description") or "",
            "due_date": adv.get("due_date") or "",
        })

    # ── Paid total = real cash sent for salaries (excl. advance offsets) ──
    paid_total = 0.0
    async for r in db.liabilities.find(
        {"user_id": user_id, "kind": "salary"},
        {"_id": 0, "paid_amount": 1, "advance_deducted": 1,
         "employee_salary_id": 1},
    ):
        cash_paid = _round(
            _round(r.get("paid_amount"))
            - _round(r.get("advance_deducted"))
        )
        if cash_paid > 0:
            paid_total += cash_paid

    accrued_total = _round(accrued_total)
    advances_total = _round(advances_total)
    paid_total = _round(paid_total)
    net_due = _round(max(0.0, accrued_total - paid_total))

    # Attach per-employee outstanding advance breakdown for the UI.
    adv_by_emp: dict = {}
    for adv in open_advances:
        eid = adv.get("employee_salary_id")
        adv_by_emp[eid] = _round(
            (adv_by_emp.get(eid) or 0) + adv["remaining"]
        )
    paid_by_emp: dict = {}
    async for r in db.liabilities.find(
        {"user_id": user_id, "kind": "salary"},
        {"_id": 0, "paid_amount": 1, "advance_deducted": 1,
         "employee_salary_id": 1},
    ):
        cash_paid = _round(
            _round(r.get("paid_amount"))
            - _round(r.get("advance_deducted"))
        )
        if cash_paid > 0:
            eid = r.get("employee_salary_id")
            paid_by_emp[eid] = _round((paid_by_emp.get(eid) or 0) + cash_paid)

    for e in employees:
        e["outstanding_advance"] = adv_by_emp.get(e["id"]) or 0.0
        e["paid"] = paid_by_emp.get(e["id"]) or 0.0
        e["net_due"] = _round(max(0.0, e["accrued"] - e["paid"]))

    # Sort: active first, then by net_due desc.
    employees.sort(
        key=lambda x: (0 if x["status"] == "active" else 1, -x["net_due"])
    )

    return {
        "accrued_total": accrued_total,
        "advances_total": advances_total,
        "paid_total": paid_total,
        "net_due": net_due,
        "active_count": active_count,
        "suspended_count": suspended_count,
        "employees": employees,
        "advances": open_advances,
        "generated_at": _now(),
    }


# ── Router ────────────────────────────────────────────────────────────────
def attach_liabilities_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)
    router = APIRouter(prefix="/liabilities", tags=["liabilities"])

    # ── POST /generate-salaries ────────────────────────────────────────
    @router.post("/generate-salaries")
    async def generate_salaries(
        period: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}$",
            description="Target month YYYY-MM (defaults to current month)",
        ),
        user: dict = Depends(current_user),
    ):
        """Idempotent salary-obligation generation.

        For each active row in `operating_salaries`, ensure exactly one
        `liabilities` row exists with
            kind='salary', period_key='YYYY-MM',
            expected_amount=monthly_amount,
            due_date=last day of the month.

        Any open advances for that employee are auto-deducted (advances
        already left the bank, so they count as pre-paid on the salary).
        """
        today = riyadh_today()
        if period:
            y, m = int(period[:4]), int(period[5:7])
        else:
            y, m = today.year, today.month
        period_key = f"{y:04d}-{m:02d}"
        due_date = _last_day_of_month(y, m)

        salaries = await db.operating_salaries.find(
            {"user_id": user["id"], "status": "active"}, {"_id": 0},
        ).to_list(2000)

        created = 0
        skipped = 0
        for s in salaries:
            start_date = s.get("start_date") or "1900-01-01"
            if start_date[:7] > period_key:
                skipped += 1
                continue
            existing = await db.liabilities.find_one(
                {
                    "user_id": user["id"],
                    "kind": "salary",
                    "employee_salary_id": s["id"],
                    "period_key": period_key,
                },
                {"_id": 0, "id": 1},
            )
            if existing:
                skipped += 1
                continue
            row = {
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "kind": "salary",
                "employee_salary_id": s["id"],
                "ad_provider": None,
                "ad_account_label": None,
                "period_key": period_key,
                # Iter-102 — pro-rata salary support.
                # Initial row assumes the employee worked the full month.
                # The user can lower `days_worked` via PUT .../days-worked
                # which recomputes expected_amount = base × worked / total.
                "monthly_amount_base": _round(s.get("monthly_amount")),
                "days_in_month": calendar.monthrange(y, m)[1],
                "days_worked": calendar.monthrange(y, m)[1],
                # Iter-113 — daily-accrual mode. When `accrual_mode='daily'`
                # the salary-status endpoint returns the time-accrued amount
                # only (monthly × days_passed_in_month / days_in_month).
                # The stored `expected_amount` STILL equals the full
                # monthly figure so legacy code continues to work; daily
                # views computed live via _compute_accrued().
                "accrual_mode": s.get("accrual_mode") or "monthly",
                "accrual_start_date": s.get("accrual_start_date")
                    or f"{y:04d}-{m:02d}-01",
                "expected_amount": _round(s.get("monthly_amount")),
                "paid_amount": 0.0,
                "advance_deducted": 0.0,
                "due_date": due_date,
                "status": "unpaid",
                "description": f"راتب {period_key} — {s.get('name', '')}",
                "notes": "",
                "auto_generated": True,
                "created_at": _now(),
                "updated_at": _now(),
            }
            await db.liabilities.insert_one(row)
            # Consume any open advances
            await _apply_open_advances_to_salary(
                db, user["id"], s["id"], row
            )
            created += 1

        return {
            "ok": True, "period": period_key,
            "created": created, "skipped": skipped,
            "total_active_salaries": len(salaries),
        }

    # ── POST /salary-topup ─────────────────────────────────────────────
    # Iter-151 — Ad-hoc salary liability for an employee whose monthly
    # salary row is already FULLY PAID but who has accrued additional
    # net_due (e.g. mid-month or accrual_mode=daily and the merchant
    # paid before all days were counted).  The standard generate-
    # salaries endpoint is idempotent per (user × employee × period),
    # so it skips this case.  This endpoint creates a NEW open salary
    # liability with a unique `period_key` (`YYYY-MM-topup-<uuid>`) so
    # the unique index isn't violated.  The amount defaults to the
    # employee's current `net_due` from the accrual aggregator, but the
    # caller can override (e.g. UI form).
    @router.post("/salary-topup")
    async def create_salary_topup(
        employee_salary_id: str = Query(...,
            description="The operating_salaries row id"),
        amount: Optional[float] = Query(
            None, gt=0.0,
            description="Override expected_amount (defaults to net_due)",
        ),
        notes: Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        emp = await db.operating_salaries.find_one(
            {"id": employee_salary_id, "user_id": user["id"]},
            {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        if emp.get("category") != "employee":
            raise HTTPException(400, "هذا السجل ليس موظفاً عاملاً")

        # Compute remaining net_due via the existing aggregator. This
        # honours daily accrual, start/end dates, and any open
        # advances/paid offsets.
        summary = await _aggregate_salary_accrual(db, user["id"])
        emp_row = next(
            (e for e in summary.get("employees", [])
             if e["id"] == employee_salary_id),
            None,
        )
        net_due = float(emp_row.get("net_due") or 0) if emp_row else 0.0

        ask = _round(amount) if amount is not None else _round(net_due)
        if ask <= 0:
            raise HTTPException(
                400,
                "لا يوجد رصيد راتب مستحق لهذا الموظف حالياً",
            )
        if amount is not None and ask > net_due + 0.01:
            # Hard cap at net_due to prevent accidental over-creation.
            raise HTTPException(
                400,
                f"المبلغ المطلوب ({ask:.2f}) أكبر من الرصيد المستحق ({net_due:.2f} ر.س)",
            )

        today_rd = riyadh_today()
        today_str = today_rd.isoformat() if hasattr(today_rd, "isoformat") else str(today_rd)
        period_key = f"{today_str[:7]}-topup-{uuid.uuid4().hex[:8]}"
        liab_id = str(uuid.uuid4())
        row = {
            "id": liab_id,
            "user_id": user["id"],
            "kind": "salary",
            "employee_salary_id": employee_salary_id,
            "ad_provider": None,
            "ad_account_label": None,
            "period_key": period_key,
            "monthly_amount_base": _round(emp.get("monthly_amount")),
            "days_in_month": None,
            "days_worked": None,
            "accrual_mode": emp.get("accrual_mode") or "monthly",
            "accrual_start_date": emp.get("accrual_start_date") or today_str,
            "expected_amount": ask,
            "paid_amount": 0.0,
            "advance_deducted": 0.0,
            "due_date": today_str,
            "status": "unpaid",
            "description": f"راتب مستحق إضافي — {emp.get('name', '')}",
            "notes": notes or "",
            "auto_generated": False,
            "is_topup": True,
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.liabilities.insert_one(row)
        # Consume any open advances against this top-up (same rules
        # as monthly salary rows).
        await _apply_open_advances_to_salary(
            db, user["id"], employee_salary_id, row,
        )
        return _enrich(row)

    # ── POST / (manual create) ────────────────────────────────────────
    @router.post("")
    async def create_liability(
        payload: LiabilityCreate, user: dict = Depends(current_user)
    ):
        kind = payload.kind
        now = _now()
        liab_id = str(uuid.uuid4())

        if kind == "ad_account":
            # Iter-99 — if counterparty_id provided, source the label from
            # the registered ad account (supports "Snapchat Account 1/2/3").
            ad_label = payload.ad_account_label or ""
            ad_provider = payload.ad_provider
            if payload.counterparty_id:
                cp = await db.counterparties.find_one(
                    {"id": payload.counterparty_id, "user_id": user["id"],
                     "kind": "ad_account"}, {"_id": 0},
                )
                if not cp:
                    raise HTTPException(404, "الحساب الإعلاني غير موجود في قائمة الأطراف")
                ad_label = cp["name"]
                ad_provider = cp.get("ad_provider") or ad_provider
            row = {
                "id": liab_id,
                "user_id": user["id"],
                "kind": "ad_account",
                "employee_salary_id": None,
                "ad_provider": ad_provider,
                "ad_account_label": ad_label,
                "counterparty_id": payload.counterparty_id,
                "period_key": None,
                "expected_amount": _round(payload.expected_amount),
                "paid_amount": 0.0,
                "advance_deducted": 0.0,
                "due_date": payload.due_date,
                "status": "unpaid",
                "description": payload.description or "",
                "notes": payload.notes or "",
                "auto_generated": False,
                "created_at": now,
                "updated_at": now,
            }
            await db.liabilities.insert_one(row)
            return _enrich(row)

        if kind == "supplier":
            # Iter-99 — supplier name may be sourced from a counterparty.
            supplier_name = (payload.supplier_name or "").strip()
            if payload.counterparty_id:
                cp = await db.counterparties.find_one(
                    {"id": payload.counterparty_id, "user_id": user["id"],
                     "kind": {"$in": ["supplier", "general"]}}, {"_id": 0},
                )
                if not cp:
                    raise HTTPException(404, "المورد/الجهة غير موجود في قائمة الأطراف")
                supplier_name = cp["name"]
            if not supplier_name:
                raise HTTPException(400, "اسم المورد/الجهة مطلوب")
            row = {
                "id": liab_id,
                "user_id": user["id"],
                "kind": "supplier",
                "supplier_name": supplier_name,
                "counterparty_id": payload.counterparty_id,
                "expected_amount": _round(payload.expected_amount),
                "paid_amount": 0.0,
                "advance_deducted": 0.0,
                "due_date": payload.due_date,
                "status": "unpaid",
                "description": payload.description or "",
                "notes": payload.notes or "",
                "auto_generated": False,
                "created_at": now,
                "updated_at": now,
            }
            await db.liabilities.insert_one(row)
            return _enrich(row)

        if kind == "receivable":
            if not (payload.counterparty_name and payload.counterparty_name.strip()):
                raise HTTPException(400, "اسم الجهة المدينة مطلوب")
            # Iter-97 — money OWED TO the merchant. Same row schema; lives
            # on the assets side of the financial-position summary.
            row = {
                "id": liab_id,
                "user_id": user["id"],
                "kind": "receivable",
                "counterparty_name": payload.counterparty_name.strip(),
                "counterparty_type": payload.counterparty_type
                    if payload.counterparty_type in RECEIVABLE_PARTY_TYPES
                    else "person",
                "expected_amount": _round(payload.expected_amount),
                "paid_amount": 0.0,
                "advance_deducted": 0.0,
                "due_date": payload.due_date,
                "status": "unpaid",   # "unpaid" = uncollected
                "description": payload.description or "",
                "notes": payload.notes or "",
                "auto_generated": False,
                "created_at": now,
                "updated_at": now,
            }
            await db.liabilities.insert_one(row)
            return _enrich(row)

        # salary_advance — money OUT of bank to employee, no salary yet
        # to deduct from. Recorded as expected=paid (it's already paid)
        # with advance_status=open for later consumption.
        emp = await db.operating_salaries.find_one(
            {"id": payload.employee_salary_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not emp:
            raise HTTPException(404, "Employee salary record not found")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "Source account not found")

        amount = _round(payload.expected_amount)
        row = {
            "id": liab_id,
            "user_id": user["id"],
            "kind": "salary_advance",
            "employee_salary_id": payload.employee_salary_id,
            "ad_provider": None,
            "ad_account_label": None,
            "period_key": None,
            "expected_amount": amount,
            "paid_amount": amount,           # cash already left the bank
            "advance_deducted": 0.0,
            "consumed_amount": 0.0,
            "advance_status": "open",
            "due_date": payload.due_date,
            "status": "paid",                # the advance itself is settled
            "description": (
                payload.description
                or f"سلفة — {emp.get('name', '')}"
            ),
            "notes": payload.notes or "",
            "auto_generated": False,
            "created_at": now,
            "updated_at": now,
        }
        await db.liabilities.insert_one(row)
        # Bank movement
        await _post_bank_tx(
            db, user["id"],
            account_id=payload.paid_from_account_id,
            amount=amount, direction="out",
            transaction_date=payload.due_date,
            description=row["description"],
            peer_liability_id=liab_id,
            transaction_type="expense",
        )
        return _enrich(row)

    # ── GET / ─────────────────────────────────────────────────────────
    @router.get("")
    async def list_liabilities(
        kind: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        ad_provider: Optional[str] = Query(None),
        period_key: Optional[str] = Query(None),
        employee_salary_id: Optional[str] = Query(None),
        from_due: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_due: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        page: int = Query(1, ge=1),
        limit: int = Query(200, ge=1, le=2000),
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if kind:
            if kind not in LIABILITY_KINDS:
                raise HTTPException(400, f"kind must be one of {LIABILITY_KINDS}")
            q["kind"] = kind
        if status:
            if status not in LIABILITY_STATUSES:
                raise HTTPException(400, f"status must be one of {LIABILITY_STATUSES}")
            q["status"] = status
        if ad_provider:
            q["ad_provider"] = ad_provider
        if period_key:
            q["period_key"] = period_key
        if employee_salary_id:
            q["employee_salary_id"] = employee_salary_id
        if from_due or to_due:
            d: dict = {}
            if from_due:
                d["$gte"] = from_due
            if to_due:
                d["$lte"] = to_due
            q["due_date"] = d

        total = await db.liabilities.count_documents(q)
        skip = (page - 1) * limit
        raw = await (
            db.liabilities.find(q, {"_id": 0})
            .sort([("due_date", 1), ("created_at", 1)])
            .skip(skip).limit(limit).to_list(limit)
        )
        return {
            "items": [_enrich(r) for r in raw],
            "total": total, "page": page, "limit": limit,
            "pages": max(1, (total + limit - 1) // limit),
        }

    # ── GET /summary ─────────────────────────────────────────────────
    @router.get("/salary-accrual-summary")
    async def salary_accrual_summary(user: dict = Depends(current_user)):
        """Iter-115 — Days-worked based salary accrual snapshot.

        Returns the dynamic per-employee accrued amounts (computed live
        from calendar days using each month's real days-in-month), the
        outstanding employee advances, real cash paid against salaries
        and the resulting net due-to-pay.  Used by the `المركز المالي`
        screen to display real-time salary liabilities instead of the
        legacy full-month block.
        """
        return await _aggregate_salary_accrual(db, user["id"])

    @router.get("/summary")
    async def summary(user: dict = Depends(current_user)):
        """Assets − Liabilities = Net financial position.

        Assets reused from existing `accounts` collection:
          - banks:                       SUM(current_balance) where account_type=bank
          - payment_platforms_remaining: SUM(current_balance) where
                                         account_type=payment_platform AND status != hidden
                                         (Iter-100: was `expected_orders_balance` which
                                         caused double-counting because that field stores
                                         the GROSS historical order amount and is never
                                         decremented when funds move to the bank. Switched
                                         to `current_balance` which is the running balance
                                         after all transfers, refunds and settlements via
                                         `account_transactions`.)

        Liabilities = SUM(remaining_amount) over open `liabilities` rows.
        salary_advance rows are EXCLUDED from the liability total because
        the cash already left the bank (they're modelled as paid).
        """
        uid = user["id"]

        # Iter-115 — salary liabilities are now computed from the
        # days-worked accrual aggregator (real-time, calendar-based)
        # instead of the legacy per-month block. Advances are EXCLUDED
        # from the headline "salaries_unpaid" and reported separately,
        # so they do NOT cancel the raw obligation.
        salary_accrual = await _aggregate_salary_accrual(db, uid)

        # Assets ───────────────────────────────────────────────────
        # Iter-119 — apply BNPL SSOT when summing payment_platform totals
        # so the Financial Position page (المركز المالي) matches the
        # per-row balances in /accounts and /bnpl-settlements.
        from bnpl.balance_service import is_bnpl_account, get_bnpl_provider_balance
        # Iter-149 v2 — pull the bank-transfer cutoff once.  Bank balances
        # in the financial position will be ADJUSTED so any account_
        # transactions dated BEFORE the cutoff are subtracted out — i.e.,
        # the displayed bank balance reflects only post-cutoff activity.
        try:
            from accounting_cutoffs import get_cutoff
            bank_cutoff = await get_cutoff(db, uid, "bank_transfer")
        except Exception:
            bank_cutoff = None

        banks_total = 0.0
        platforms_total = 0.0
        async for a in db.accounts.find(
            {"user_id": uid},
            {"_id": 0, "id": 1, "account_type": 1, "status": 1,
             "current_balance": 1, "expected_orders_balance": 1,
             "provider_name": 1, "name": 1,
             "normalized_payment_method": 1},
        ):
            if (a.get("status") or "active") == "hidden":
                continue
            t = a.get("account_type")
            if t == "bank":
                bal = float(a.get("current_balance") or 0)
                # Iter-149 v2 — subtract net effect of pre-cutoff txns.
                if bank_cutoff:
                    pre_net = 0.0
                    async for tx in db.account_transactions.find(
                        {"user_id": uid, "account_id": a.get("id"),
                         "transaction_date": {"$lt": bank_cutoff}},
                        {"_id": 0, "amount": 1, "direction": 1},
                    ):
                        amt = float(tx.get("amount") or 0)
                        d = (tx.get("direction") or "").lower()
                        pre_net += amt if d == "in" else -amt
                    bal -= pre_net
                banks_total += bal
            elif t == "payment_platform":
                # Iter-100 — REMAINING (un-transferred) balance only.
                bal = float(a.get("current_balance") or 0)
                try:
                    bnpl_provider = is_bnpl_account(a)
                    if bnpl_provider:
                        canon = await get_bnpl_provider_balance(
                            db, uid, bnpl_provider,
                        )
                        bal = float(canon["balance"])
                except Exception:  # noqa: BLE001
                    pass
                platforms_total += bal
        assets_total = _round(banks_total + platforms_total)

        # Liabilities ──────────────────────────────────────────────
        today = _today_str()
        agg = {
            # Iter-115 — `salaries_unpaid` is now the days-worked NET DUE
            # from the accrual aggregator (real-time), NOT the legacy
            # sum of liabilities.kind=salary rows.
            "salaries_unpaid": salary_accrual["net_due"],
            "ad_accounts_unpaid": 0.0,
            "suppliers_unpaid": 0.0,
            "overdue_total": 0.0,
        }
        by_provider = {p: 0.0 for p in AD_PROVIDERS}
        async for r in db.liabilities.find(
            {
                "user_id": uid,
                "kind": {"$in": ["ad_account", "supplier"]},
                "status": {"$ne": "paid"},
                # Iter-149 — skip pre-accounting liabilities.
                "is_pre_accounting": {"$ne": True},
            },
            {"_id": 0, "kind": 1, "expected_amount": 1, "paid_amount": 1,
             "due_date": 1, "ad_provider": 1, "status": 1},
        ):
            remaining = _round(
                _round(r.get("expected_amount")) - _round(r.get("paid_amount"))
            )
            if remaining <= 0:
                continue
            if r["kind"] == "ad_account":
                agg["ad_accounts_unpaid"] += remaining
                prov = r.get("ad_provider")
                if prov in by_provider:
                    by_provider[prov] += remaining
            elif r["kind"] == "supplier":
                agg["suppliers_unpaid"] += remaining
            if r.get("due_date") and r["due_date"] < today:
                agg["overdue_total"] += remaining

        liabilities_total = _round(
            agg["salaries_unpaid"] + agg["ad_accounts_unpaid"] + agg["suppliers_unpaid"]
        )

        # Iter-101 — shipping liabilities (deferred couriers).
        # Owed is computed ONLY from orders with delivered/completed
        # statuses; cancelled / in-transit / refunded orders create no
        # courier obligation. Paid amounts come from `shipping_payments`
        # (both manual payments and COD-net fee deductions land there),
        # so the liability decreases automatically.
        from shipping_accounts import compute_owed_per_company, compute_paid_per_company
        owed_ship = await compute_owed_per_company(db, uid)
        paid_ship = await compute_paid_per_company(db, uid)
        by_shipping_company: dict[str, dict] = {}
        shipping_total = 0.0
        for name, data in owed_ship.items():
            paid_amt = float(paid_ship.get(name, 0.0))
            remaining = max(0.0, _round(data["owed"] - paid_amt))
            by_shipping_company[name] = {
                "owed": _round(data["owed"]),
                "paid": _round(paid_amt),
                "remaining": remaining,
                "orders_count": int(data.get("orders_count", 0)),
            }
            shipping_total += remaining
        # Also surface any company that ONLY appears in paid_ship (overpayment / legacy) as info.
        for name, paid_amt in paid_ship.items():
            if name not in by_shipping_company:
                by_shipping_company[name] = {
                    "owed": 0.0,
                    "paid": _round(paid_amt),
                    "remaining": 0.0,
                    "orders_count": 0,
                }
        shipping_total = _round(shipping_total)
        liabilities_total = _round(liabilities_total + shipping_total)

        # Iter-97 — receivables = money owed TO the merchant; count as a
        # current asset (المديونيات على الغير).
        receivables_total = 0.0
        async for r in db.liabilities.find(
            {"user_id": uid, "kind": "receivable", "status": {"$ne": "paid"}},
            {"_id": 0, "expected_amount": 1, "paid_amount": 1},
        ):
            receivables_total += max(
                0.0,
                _round(r.get("expected_amount")) - _round(r.get("paid_amount")),
            )
        receivables_total = _round(receivables_total)
        assets_total = _round(assets_total + receivables_total)

        return {
            "assets": {
                "banks": _round(banks_total),
                # Iter-100 — `payment_platforms_remaining` is the new,
                # clearer name (only un-transferred balance). The old
                # `payment_platforms_expected` key is kept with the SAME
                # value for backward compatibility with the existing
                # /financial-position UI; it no longer reflects the gross
                # historical figure.
                "payment_platforms_remaining": _round(platforms_total),
                "payment_platforms_expected": _round(platforms_total),
                "receivables": receivables_total,
                "total": assets_total,
            },
            "liabilities": {
                "salaries_unpaid": _round(agg["salaries_unpaid"]),
                "ad_accounts_unpaid": _round(agg["ad_accounts_unpaid"]),
                "suppliers_unpaid": _round(agg["suppliers_unpaid"]),
                # Iter-101 — shipping accrued from delivered orders only.
                "shipping_unpaid": shipping_total,
                "by_shipping_company": by_shipping_company,
                "overdue_total": _round(agg["overdue_total"]),
                "total": liabilities_total,
                "by_ad_provider": {k: _round(v) for k, v in by_provider.items()},
            },
            # Iter-115 — full salary breakdown so the UI can render
            # the four headline numbers requested by the user
            # (accrued / advances / paid / net_due) plus active &
            # suspended counts and per-employee rows.
            "salary_breakdown": {
                "accrued_total": salary_accrual["accrued_total"],
                "advances_total": salary_accrual["advances_total"],
                "paid_total": salary_accrual["paid_total"],
                "net_due": salary_accrual["net_due"],
                "active_count": salary_accrual["active_count"],
                "suspended_count": salary_accrual["suspended_count"],
                "employees": salary_accrual["employees"],
            },
            "net_position": _round(assets_total - liabilities_total),
            "generated_at": _now(),
        }

    # ── PUT /{id} ─────────────────────────────────────────────────────
    @router.put("/{liab_id}")
    async def update_liability(
        liab_id: str, payload: LiabilityUpdate,
        user: dict = Depends(current_user),
    ):
        existing = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not existing:
            raise HTTPException(404, "Liability not found")
        if existing.get("kind") == "salary_advance":
            raise HTTPException(
                400, "Advances cannot be edited; delete and recreate."
            )

        upd = {"updated_at": _now()}
        if payload.expected_amount is not None:
            new_expected = _round(payload.expected_amount)
            if new_expected < _round(existing.get("paid_amount")):
                raise HTTPException(
                    400, "expected_amount cannot be less than already-paid",
                )
            upd["expected_amount"] = new_expected
            upd["status"] = _compute_status(
                new_expected, existing.get("paid_amount")
            )
        if payload.due_date:
            upd["due_date"] = payload.due_date
        if payload.description is not None:
            upd["description"] = payload.description
        if payload.notes is not None:
            upd["notes"] = payload.notes
        if payload.ad_account_label is not None:
            upd["ad_account_label"] = payload.ad_account_label

        await db.liabilities.update_one(
            {"id": liab_id, "user_id": user["id"]}, {"$set": upd},
        )
        fresh = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        return _enrich(fresh)

    # ── PUT /{id}/days-worked (Iter-102) ──────────────────────────────
    # Pro-rata salary: when the merchant edits the days an employee
    # actually worked in a given month, we recompute the row's
    # `expected_amount = monthly_amount_base × days_worked / days_in_month`.
    # Constraints:
    #   • Only `kind=salary` rows.
    #   • Only when the linked operating_salaries row is `category=employee`
    #     (household / charity payments are not day-based).
    #   • `days_worked` must be in [0, days_in_month].
    #   • The new expected_amount must not be < already-paid amount.
    # ── Iter-113 — Daily-accrual mode helpers ─────────────────────────
    @router.get("/{liab_id}/salary-status")
    async def salary_status(
        liab_id: str, user: dict = Depends(current_user),
    ):
        """Live view of a salary liability with daily accrual computed
        as_of today. Works for both `monthly` and `daily` modes.
        """
        liab = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not liab:
            raise HTTPException(404, "Liability not found")
        if liab.get("kind") != "salary":
            raise HTTPException(400, "هذا الـ endpoint للرواتب فقط")

        monthly = float(liab.get("monthly_amount_base") or 0)
        period_key = liab.get("period_key") or _now()[:7]
        y, m = int(period_key[:4]), int(period_key[5:7])
        dim = calendar.monthrange(y, m)[1] or 30
        daily = round(monthly / dim, 4) if dim > 0 else 0.0

        start = liab.get("accrual_start_date") or f"{y:04d}-{m:02d}-01"
        start_d = date.fromisoformat(start)
        today_d = riyadh_today()
        period_first = date(y, m, 1)
        period_last  = date(y, m, dim)
        eff_start = max(start_d, period_first)
        eff_end   = min(today_d, period_last)
        days_accrued = max(0, (eff_end - eff_start).days + 1) if eff_end >= eff_start else 0
        days_accrued = min(days_accrued, dim)

        accrual_mode = liab.get("accrual_mode") or "monthly"
        if accrual_mode == "daily":
            accrued = round(daily * days_accrued, 2)
        else:
            dw = liab.get("days_worked")
            if dw is not None and dim > 0:
                accrued = round(monthly * float(dw) / dim, 2)
            else:
                accrued = round(float(liab.get("expected_amount") or 0), 2)

        paid = round(float(liab.get("paid_amount") or 0), 2)
        remaining = round(max(0.0, accrued - paid), 2)
        advance = round(max(0.0, paid - accrued), 2)

        return {
            "id": liab["id"],
            "period_key": period_key,
            "accrual_mode": accrual_mode,
            "accrual_start_date": start,
            "monthly_salary": round(monthly, 2),
            "days_in_month": dim,
            "daily_salary": round(daily, 2),
            "days_accrued": days_accrued,
            "accrued_total": accrued,
            "paid_amount": paid,
            "remaining": remaining,
            "advance": advance,
            "today": today_d.isoformat(),
        }

    @router.put("/{liab_id}/accrual-mode")
    async def set_accrual_mode(
        liab_id: str,
        payload: dict,
        user: dict = Depends(current_user),
    ):
        """Toggle a salary liability between monthly and daily accrual."""
        liab = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not liab:
            raise HTTPException(404, "Liability not found")
        if liab.get("kind") != "salary":
            raise HTTPException(400, "هذا الـ endpoint للرواتب فقط")
        mode = payload.get("accrual_mode")
        if mode not in ("monthly", "daily"):
            raise HTTPException(400, "accrual_mode يجب أن يكون 'monthly' أو 'daily'")
        upd: dict = {"accrual_mode": mode, "updated_at": _now()}
        if payload.get("accrual_start_date"):
            try:
                date.fromisoformat(payload["accrual_start_date"])
            except (TypeError, ValueError):
                raise HTTPException(400, "accrual_start_date يجب أن يكون YYYY-MM-DD")
            upd["accrual_start_date"] = payload["accrual_start_date"]

        monthly = float(liab.get("monthly_amount_base") or 0)
        period_key = liab.get("period_key") or _now()[:7]
        y, m = int(period_key[:4]), int(period_key[5:7])
        dim = calendar.monthrange(y, m)[1] or 30
        if mode == "daily":
            start = upd.get("accrual_start_date") or liab.get("accrual_start_date") or f"{y:04d}-{m:02d}-01"
            start_d = date.fromisoformat(start)
            eff_start = max(start_d, date(y, m, 1))
            eff_end   = min(riyadh_today(), date(y, m, dim))
            days_accrued = max(0, (eff_end - eff_start).days + 1) if eff_end >= eff_start else 0
            days_accrued = min(days_accrued, dim)
            upd["days_worked"] = days_accrued
            upd["expected_amount"] = round(monthly * days_accrued / dim, 2)
        else:
            dw = int(liab.get("days_worked") or dim)
            upd["expected_amount"] = round(monthly * dw / dim, 2)

        paid = float(liab.get("paid_amount") or 0)
        if upd["expected_amount"] <= paid + 0.01 and upd["expected_amount"] > 0:
            upd["status"] = "paid"
        elif paid > 0:
            upd["status"] = "partial"
        else:
            upd["status"] = "unpaid"

        await db.liabilities.update_one(
            {"id": liab_id, "user_id": user["id"]}, {"$set": upd},
        )
        return await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )


    @router.put("/{liab_id}/days-worked")
    async def set_days_worked(
        liab_id: str,
        payload: dict,                                     # {days_worked: int}
        user: dict = Depends(current_user),
    ):
        liab = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not liab:
            raise HTTPException(404, "Liability not found")
        if liab.get("kind") != "salary":
            raise HTTPException(400, "أيام العمل تنطبق فقط على التزامات الرواتب")
        if liab.get("status") == "paid":
            raise HTTPException(400, "لا يمكن تعديل الأيام بعد سداد الراتب")

        # Verify the underlying employee is in category=employee.
        emp = await db.operating_salaries.find_one(
            {"id": liab.get("employee_salary_id"), "user_id": user["id"]},
            {"_id": 0, "category": 1, "monthly_amount": 1},
        )
        if not emp:
            raise HTTPException(404, "Employee record not found")
        if emp.get("category") != "employee":
            raise HTTPException(
                400, "أيام العمل لا تنطبق على فئة (مصاريف منزل / صدقات)",
            )

        try:
            days_worked = int(payload.get("days_worked"))
        except (TypeError, ValueError):
            raise HTTPException(400, "days_worked يجب أن يكون رقماً صحيحاً")

        # Backfill base/period info for rows created before Iter-102.
        base = _round(liab.get("monthly_amount_base") or emp.get("monthly_amount"))
        pk = liab.get("period_key") or _today_str()[:7]
        try:
            y, m = int(pk[:4]), int(pk[5:7])
            days_in_month = calendar.monthrange(y, m)[1]
        except Exception:
            days_in_month = 30
        days_in_month = int(liab.get("days_in_month") or days_in_month)

        if days_worked < 0 or days_worked > days_in_month:
            raise HTTPException(
                400, f"أيام العمل يجب أن تكون بين 0 و {days_in_month}",
            )

        new_expected = _round(base * days_worked / days_in_month)
        paid_amount = _round(liab.get("paid_amount"))
        if new_expected + 0.01 < paid_amount:
            raise HTTPException(
                400,
                f"المبلغ الجديد ({new_expected}) أقل من المسدَّد ({paid_amount})",
            )

        upd = {
            "monthly_amount_base": base,
            "days_in_month": days_in_month,
            "days_worked": days_worked,
            "expected_amount": new_expected,
            "status": _compute_status(new_expected, paid_amount),
            "updated_at": _now(),
        }
        await db.liabilities.update_one(
            {"id": liab_id, "user_id": user["id"]}, {"$set": upd},
        )
        fresh = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        return _enrich(fresh)

    # ── POST /{id}/pay ────────────────────────────────────────────────
    @router.post("/{liab_id}/pay")
    async def pay_liability(
        liab_id: str, payload: PaymentIn,
        user: dict = Depends(current_user),
    ):
        liab = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not liab:
            raise HTTPException(404, "Liability not found")
        if liab.get("kind") == "salary_advance":
            raise HTTPException(
                400,
                "Advances are recorded as already-paid; create a salary "
                "and pay against it instead.",
            )

        amount = _round(payload.amount)
        expected = _round(liab.get("expected_amount"))
        already_paid = _round(liab.get("paid_amount"))
        remaining = _round(expected - already_paid)

        if amount > remaining + 0.01:
            raise HTTPException(
                400,
                f"Payment ({amount}) exceeds remaining ({remaining})",
            )

        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1},
        )
        if not acc:
            raise HTTPException(404, "Source account not found")

        # 1) Post the bank movement
        tx = await _post_bank_tx(
            db, user["id"],
            account_id=payload.paid_from_account_id,
            amount=amount, direction="out",
            transaction_date=payload.payment_date,
            description=(
                f"سداد التزام — {liab.get('description', '')}"
            ),
            peer_liability_id=liab_id,
            transaction_type="debt_payment",
        )

        # 2) Update liability
        new_paid = _round(already_paid + amount)
        new_status = _compute_status(expected, new_paid)
        await db.liabilities.update_one(
            {"id": liab_id, "user_id": user["id"]},
            {"$set": {
                "paid_amount": new_paid,
                "status": new_status,
                "updated_at": _now(),
            }},
        )
        fresh = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        return {
            "ok": True,
            "liability": _enrich(fresh),
            "transaction_id": tx["id"],
        }

    # ── POST /{id}/collect ───────────────────────────────────────────
    # Iter-104 — opposite of /pay. For `kind=receivable` only.
    # When a debtor pays us, our bank goes UP and the receivable goes
    # DOWN. Mirrors /pay but with direction="in" on the bank tx.
    @router.post("/{liab_id}/collect")
    async def collect_receivable(
        liab_id: str, payload: PaymentIn,
        user: dict = Depends(current_user),
    ):
        liab = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not liab:
            raise HTTPException(404, "Liability not found")
        if liab.get("kind") != "receivable":
            raise HTTPException(
                400, "التحصيل ينطبق فقط على الذمم المدينة (kind=receivable)",
            )

        amount = _round(payload.amount)
        expected = _round(liab.get("expected_amount"))
        already_collected = _round(liab.get("paid_amount"))
        remaining = _round(expected - already_collected)
        if amount > remaining + 0.01:
            raise HTTPException(
                400,
                f"المبلغ المحصَّل ({amount}) أكبر من المتبقي ({remaining})",
            )

        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1},
        )
        if not acc:
            raise HTTPException(404, "Destination account not found")

        # 1) Add to bank
        tx = await _post_bank_tx(
            db, user["id"],
            account_id=payload.paid_from_account_id,
            amount=amount, direction="in",
            transaction_date=payload.payment_date,
            description=f"تحصيل من — {liab.get('counterparty_name') or liab.get('description', '')}",
            peer_liability_id=liab_id,
            transaction_type="receivable_collection",
        )

        # 2) Reduce receivable
        new_collected = _round(already_collected + amount)
        new_status = _compute_status(expected, new_collected)
        await db.liabilities.update_one(
            {"id": liab_id, "user_id": user["id"]},
            {"$set": {
                "paid_amount": new_collected,
                "status": new_status,
                "updated_at": _now(),
            }},
        )
        fresh = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]}, {"_id": 0},
        )
        return {
            "ok": True,
            "liability": _enrich(fresh),
            "transaction_id": tx["id"],
        }

    # ── DELETE /{id} ──────────────────────────────────────────────────
    @router.delete("/{liab_id}")
    async def delete_liability(
        liab_id: str, user: dict = Depends(current_user)
    ):
        liab = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user["id"]},
            {"_id": 0, "paid_amount": 1, "kind": 1},
        )
        if not liab:
            raise HTTPException(404, "Liability not found")
        if _round(liab.get("paid_amount")) > 0 and liab.get("kind") != "salary_advance":
            raise HTTPException(
                400,
                "Cannot delete a liability with payments — reverse "
                "payments first.",
            )
        # For an advance we also need to roll back the bank movement.
        if liab.get("kind") == "salary_advance":
            txs = await db.account_transactions.find(
                {"user_id": user["id"], "peer_liability_id": liab_id},
                {"_id": 0, "id": 1, "account_id": 1},
            ).to_list(100)
            for t in txs:
                await db.account_transactions.delete_one(
                    {"id": t["id"], "user_id": user["id"]}
                )
                await _recompute_account_balance(
                    db, user["id"], t["account_id"]
                )
        await db.liabilities.delete_one(
            {"id": liab_id, "user_id": user["id"]}
        )
        return {"ok": True}

    parent_router.include_router(router)
