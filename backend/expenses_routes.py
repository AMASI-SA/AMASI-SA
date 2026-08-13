"""Operating Expenses (المصروفات التشغيلية اليومية) — formal source of all
fixed and variable operating costs used in P&L calculations.

This module supports three independent expense types whose daily costs are
combined into the dashboard's "إجمالي المصروفات التشغيلية اليومية" KPI:

1) Monthly Salaries (الرواتب الشهرية)
   - Categories:
     * employee    (موظفين/إداريين/محاسبين/مسوقين/عاملين)
     * household   (مصروف الأسرة/المنزل/الشخصي)
     * charity     (الصدقات/المساهمات/الكفالات/التبرعات)
   - Daily cost = monthly_amount / days_in_month
   - Active records only (status="active")

2) Annual Rentals (الإيجارات السنوية)
   - Types: office / warehouse / shop / employee_housing / other
   - Daily cost = annual_amount / 365
   - Active records only (status="active")

3) Daily Expenses (المصروفات اليومية الأخرى)
   - Variable per-day entries (fuel, maintenance, subscriptions, etc.)
   - Recorded with explicit date, type, description, amount, payment method

Endpoints (all under /api/operating-expenses):
- Salaries:   GET / POST / PUT / DELETE  /salaries[/{id}]
- Rentals:    GET / POST / PUT / DELETE  /rentals[/{id}]
- Daily:      GET / POST / PUT / DELETE  /daily[/{id}]
- Summary:    GET /summary               → KPI cards data
- Report:     GET /report                → aggregated daily/monthly/yearly totals
"""
import calendar
import uuid
from datetime import datetime, date, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db
from employee_payroll_status import (
    employee_salary_rows,
    find_employee_salary,
    salary_active_on as employee_salary_active_on,
)
from tz_utils import riyadh_today


SALARY_CATEGORIES = {"employee", "household", "charity"}
SALARY_COUNTRIES = {"saudi", "yemen", "other"}
RENTAL_PROPERTY_TYPES = {"office", "warehouse", "shop", "employee_housing", "other"}
PREPAID_TYPES = {
    "vehicle_insurance",     # تأمين السيارات
    "worker_insurance",      # تأمين الموظفين
    "iqama_visa",            # الإقامات والتأشيرات
    "government_license",    # الرخص والتصاريح الحكومية
    "annual_subscription",   # الاشتراكات السنوية
    "other",                 # أخرى
}


# ── Schemas ───────────────────────────────────────────────────────────────────
class SalaryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str  # employee | household | charity
    country: str = "saudi"  # saudi | yemen | other
    monthly_amount: float = Field(gt=0)
    start_date: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD
    status: str = "active"  # active | stopped
    notes: Optional[str] = ""


class SalaryUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    country: Optional[str] = None
    monthly_amount: Optional[float] = None
    start_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class RentalIn(BaseModel):
    property_name: str = Field(min_length=1, max_length=120)
    property_type: str  # office | warehouse | shop | employee_housing | other
    annual_amount: float = Field(gt=0)
    start_date: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD
    end_date: str = Field(min_length=10, max_length=10)    # YYYY-MM-DD
    status: str = "active"  # active | expired
    notes: Optional[str] = ""


class RentalUpdate(BaseModel):
    property_name: Optional[str] = None
    property_type: Optional[str] = None
    annual_amount: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class DailyExpenseIn(BaseModel):
    date: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD
    expense_type: str = Field(min_length=1, max_length=80)
    description: Optional[str] = ""
    amount: float = Field(gt=0)
    payment_method: Optional[str] = ""
    notes: Optional[str] = ""
    # Iter-94: optional link to a bank/cash account. When set, the daily
    # expense automatically posts an out-flowing account_transactions row
    # so the bank balance and the financial-position screen stay in sync.
    paid_from_account_id: Optional[str] = None


class DailyExpenseUpdate(BaseModel):
    date: Optional[str] = None
    expense_type: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    paid_from_account_id: Optional[str] = None  # null = unlink from account


class PrepaidExpenseIn(BaseModel):
    expense_type: str  # vehicle_insurance | worker_insurance | iqama_visa | government_license | annual_subscription | other
    beneficiary: str = Field(min_length=1, max_length=160)  # اسم المستفيد / الأصل (لوحة السيارة، اسم العامل…)
    amount: float = Field(gt=0)
    start_date: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD
    end_date: str = Field(min_length=10, max_length=10)    # YYYY-MM-DD
    status: str = "active"  # active | expired
    notes: Optional[str] = ""


class PrepaidExpenseUpdate(BaseModel):
    expense_type: Optional[str] = None
    beneficiary: Optional[str] = None
    amount: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _parse_iso(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def _daily_from_monthly(monthly_amount: float, ref_day: date) -> float:
    """Daily cost of a monthly salary for the month of ref_day."""
    if monthly_amount <= 0:
        return 0.0
    return round(monthly_amount / _days_in_month(ref_day), 4)


def _daily_from_annual(annual_amount: float) -> float:
    if annual_amount <= 0:
        return 0.0
    return round(annual_amount / 365.0, 4)


def _salary_active_on(s: dict, day: date) -> bool:
    """Apply Employee OS dates to employees and legacy dates to other costs."""
    if s.get("category") == "employee" or s.get("payroll_source"):
        return employee_salary_active_on(s, day)
    if (s.get("status") or "active") != "active":
        return False
    start = _parse_iso(s.get("start_date") or "")
    if start is None:
        return True  # treat missing start as always-active
    return start <= day


def _rental_active_on(r: dict, day: date) -> bool:
    """A rental is active on `day` iff status=active AND day is within
    [start_date, end_date]."""
    if (r.get("status") or "active") != "active":
        return False
    start = _parse_iso(r.get("start_date") or "")
    end = _parse_iso(r.get("end_date") or "")
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _prepaid_active_on(p: dict, day: date) -> bool:
    """A prepaid expense is active on `day` iff status=active AND day is within
    [start_date, end_date]. Same rule as rentals."""
    if (p.get("status") or "active") != "active":
        return False
    start = _parse_iso(p.get("start_date") or "")
    end = _parse_iso(p.get("end_date") or "")
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _prepaid_period_days(p: dict) -> int:
    """Inclusive day count between start_date and end_date.
    Returns 1 if either is missing or the range is degenerate."""
    start = _parse_iso(p.get("start_date") or "")
    end = _parse_iso(p.get("end_date") or "")
    if not start or not end:
        return 1
    days = (end - start).days + 1
    return max(days, 1)


def _daily_from_prepaid(p: dict) -> float:
    """Per-day amortized cost of a prepaid expense across its period."""
    amount = float(p.get("amount") or 0)
    if amount <= 0:
        return 0.0
    return round(amount / _prepaid_period_days(p), 4)


# Iter-94 — bank movement helpers for daily expenses paid from accounts.
async def _recompute_account_balance_for_expense(db, user_id: str, account_id: str) -> None:
    """Re-walk all transactions of `account_id` in chronological order so
    `balance_after` and `current_balance` stay correct after any insert/delete.
    Mirrors `accounts_routes._recompute_balance` but kept local to avoid a
    cross-module import cycle."""
    acc = await db.accounts.find_one(
        {"id": account_id, "user_id": user_id},
        {"_id": 0, "expected_orders_balance": 1},
    ) or {}
    running = float(acc.get("expected_orders_balance") or 0)
    docs = await db.account_transactions.find(
        {"user_id": user_id, "account_id": account_id},
        {"_id": 0, "id": 1, "amount": 1, "direction": 1, "balance_after": 1},
    ).sort([("transaction_date", 1), ("created_at", 1)]).to_list(50000)
    now = datetime.now(timezone.utc).isoformat()
    for d in docs:
        amt = float(d.get("amount", 0) or 0)
        running += amt if d.get("direction") == "in" else -amt
        new_balance = round(running, 2)
        if d.get("balance_after") != new_balance:
            await db.account_transactions.update_one(
                {"id": d["id"], "user_id": user_id},
                {"$set": {"balance_after": new_balance, "updated_at": now}},
            )
    final = round(running, 2)
    await db.accounts.update_one(
        {"id": account_id, "user_id": user_id},
        {"$set": {"current_balance": final, "updated_at": now}},
    )


async def _post_daily_expense_tx(
    db, user_id: str, expense_id: str, account_id: str,
    amount: float, transaction_date: str,
    expense_type: str, description: str,
) -> str:
    """Insert an out-flowing `account_transactions` row tied to a daily
    expense. Returns the new transaction id."""
    tx_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    desc = f"مصروف يومي: {expense_type}"
    if description:
        desc += f" — {description}"
    await db.account_transactions.insert_one({
        "id": tx_id,
        "user_id": user_id,
        "account_id": account_id,
        "transaction_type": "expense",
        "amount": round(float(amount), 2),
        "direction": "out",
        "description": desc[:280],
        "transaction_date": transaction_date,
        "balance_after": 0.0,    # set by recompute below
        "status": "posted",
        "peer_daily_expense_id": expense_id,
        "created_at": now,
        "updated_at": now,
    })
    await _recompute_account_balance_for_expense(db, user_id, account_id)

    # Iter-240 — mirror this expense payment into general_ledger (SSOT).
    try:
        from ledger_double_write import mirror_account_txn_to_ledger
        await mirror_account_txn_to_ledger(
            db,
            user_id=user_id,
            account_id=account_id,
            account_transaction_id=tx_id,
            amount=round(float(amount), 2),
            direction="out",
            transaction_type="expense",
            transaction_date=transaction_date,
            description=desc[:280],
            counter_entity_type="expense",
            counter_entity_id=expense_id or "expense_unknown",
            created_by_endpoint="expenses_routes._post_daily_expense_tx",
            idempotency_key=f"daily_expense:{expense_id}",
        )
    except Exception as _e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "iter240 mirror failed for expense tx %s: %s", tx_id, _e
        )
    return tx_id


async def _delete_daily_expense_tx(
    db, user_id: str, transaction_id: str, account_id: Optional[str],
) -> None:
    """Remove a daily-expense-linked transaction and refresh balances."""
    await db.account_transactions.delete_one(
        {"id": transaction_id, "user_id": user_id}
    )
    # Iter-240 — also purge the mirrored ledger pair.
    try:
        await db.general_ledger.delete_many({
            "user_id": user_id,
            "metadata.account_transaction_id": transaction_id,
            "metadata.source": "account_transaction_double_write",
        })
    except Exception:  # noqa: BLE001
        pass
    if account_id:
        await _recompute_account_balance_for_expense(db, user_id, account_id)


async def compute_operating_expenses_for_day(db, user_id: str, day: date) -> dict:
    """Return a breakdown of the daily operating cost for one day.

    Result fields (all floats, rounded to 2 decimals at the edge):
      - salaries_employee
      - salaries_household
      - salaries_charity
      - salaries_total_daily      ( = employee + household + charity )
      - rentals_daily
      - prepaid_daily             (total of all active prepaid expenses on this day)
      - prepaid_by_type           (dict of type → daily amortized cost)
      - daily_other_total         (sum of `daily_expenses` rows on that exact date)
      - operating_total           (grand total of all above)
    """
    non_employee_salaries = await db.operating_salaries.find(
        {"user_id": user_id, "category": {"$in": ["household", "charity"]}},
        {"_id": 0},
    ).to_list(5000)
    salaries = await employee_salary_rows(db, user_id) + non_employee_salaries
    rentals = await db.operating_rentals.find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(5000)
    prepaids = await db.operating_prepaid_expenses.find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(5000)

    emp = house = char = 0.0
    for s in salaries:
        if not _salary_active_on(s, day):
            continue
        d = _daily_from_monthly(float(s.get("monthly_amount") or 0), day)
        cat = (s.get("category") or "").strip()
        if cat == "employee":
            emp += d
        elif cat == "household":
            house += d
        elif cat == "charity":
            char += d
    rent = 0.0
    for r in rentals:
        if not _rental_active_on(r, day):
            continue
        rent += _daily_from_annual(float(r.get("annual_amount") or 0))

    prepaid_total = 0.0
    prepaid_by_type: dict = {}
    for p in prepaids:
        if not _prepaid_active_on(p, day):
            continue
        d = _daily_from_prepaid(p)
        prepaid_total += d
        t = (p.get("expense_type") or "other").strip() or "other"
        prepaid_by_type[t] = round(prepaid_by_type.get(t, 0.0) + d, 4)

    iso_day = day.isoformat()
    other_total = 0.0
    async for ex in db.operating_daily_expenses.find(
        {"user_id": user_id, "date": iso_day}, {"_id": 0, "amount": 1}
    ):
        other_total += float(ex.get("amount") or 0)

    salaries_total = emp + house + char
    operating_total = salaries_total + rent + prepaid_total + other_total

    return {
        "date": iso_day,
        "salaries_employee": round(emp, 2),
        "salaries_household": round(house, 2),
        "salaries_charity": round(char, 2),
        "salaries_total_daily": round(salaries_total, 2),
        "rentals_daily": round(rent, 2),
        "prepaid_daily": round(prepaid_total, 2),
        "prepaid_by_type": {k: round(v, 2) for k, v in prepaid_by_type.items()},
        "daily_other_total": round(other_total, 2),
        "operating_total": round(operating_total, 2),
    }


async def compute_operating_expenses_for_range(
    db, user_id: str, from_day: date, to_day: date
) -> dict:
    """Sum the per-day operating cost over [from_day, to_day] inclusive."""
    if to_day < from_day:
        from_day, to_day = to_day, from_day

    non_employee_salaries = await db.operating_salaries.find(
        {"user_id": user_id, "category": {"$in": ["household", "charity"]}},
        {"_id": 0},
    ).to_list(5000)
    salaries = await employee_salary_rows(db, user_id) + non_employee_salaries
    rentals = await db.operating_rentals.find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(5000)
    prepaids = await db.operating_prepaid_expenses.find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(5000)

    emp_sum = house_sum = char_sum = rent_sum = prepaid_sum = 0.0
    prepaid_by_type: dict = {}
    cur = from_day
    while cur <= to_day:
        for s in salaries:
            if not _salary_active_on(s, cur):
                continue
            d = _daily_from_monthly(float(s.get("monthly_amount") or 0), cur)
            cat = (s.get("category") or "").strip()
            if cat == "employee":
                emp_sum += d
            elif cat == "household":
                house_sum += d
            elif cat == "charity":
                char_sum += d
        for r in rentals:
            if _rental_active_on(r, cur):
                rent_sum += _daily_from_annual(float(r.get("annual_amount") or 0))
        for p in prepaids:
            if not _prepaid_active_on(p, cur):
                continue
            d = _daily_from_prepaid(p)
            prepaid_sum += d
            t = (p.get("expense_type") or "other").strip() or "other"
            prepaid_by_type[t] = prepaid_by_type.get(t, 0.0) + d
        cur = cur + timedelta(days=1)

    other_sum = 0.0
    async for ex in db.operating_daily_expenses.find(
        {
            "user_id": user_id,
            "date": {"$gte": from_day.isoformat(), "$lte": to_day.isoformat()},
        },
        {"_id": 0, "amount": 1},
    ):
        other_sum += float(ex.get("amount") or 0)

    salaries_total = emp_sum + house_sum + char_sum
    return {
        "from_date": from_day.isoformat(),
        "to_date": to_day.isoformat(),
        "salaries_employee": round(emp_sum, 2),
        "salaries_household": round(house_sum, 2),
        "salaries_charity": round(char_sum, 2),
        "salaries_total": round(salaries_total, 2),
        "rentals_total": round(rent_sum, 2),
        "prepaid_total": round(prepaid_sum, 2),
        "prepaid_by_type": {k: round(v, 2) for k, v in prepaid_by_type.items()},
        "daily_other_total": round(other_sum, 2),
        "operating_total": round(
            salaries_total + rent_sum + prepaid_sum + other_sum, 2
        ),
    }


# ── Router ────────────────────────────────────────────────────────────────────
def _build_router(db) -> APIRouter:
    router = APIRouter(prefix="/operating-expenses", tags=["operating-expenses"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    # ─── Salaries CRUD ────────────────────────────────────────────────────────
    @router.get("/salaries")
    async def list_salaries(user: dict = Depends(current_user)):
        non_employee_items = await db.operating_salaries.find(
            {
                "user_id": user["id"],
                "category": {"$in": ["household", "charity"]},
            },
            {"_id": 0},
        ).sort("created_at", -1).to_list(2000)
        items = await employee_salary_rows(db, user["id"])
        return {
            "items": items + non_employee_items,
            "employee_salary_source": "mezan_employee_salary_contracts_v2",
            "legacy_employee_salary_reads": 0,
        }

    @router.post("/salaries")
    async def create_salary(payload: SalaryIn, user: dict = Depends(current_user)):
        if payload.category not in SALARY_CATEGORIES:
            raise HTTPException(status_code=400, detail="نوع الراتب غير صحيح")
        if payload.category == "employee":
            raise HTTPException(
                status_code=409,
                detail="رواتب الموظفين تُدار من إدارة الموظفين في ميزان 2",
            )
        if payload.country not in SALARY_COUNTRIES:
            raise HTTPException(status_code=400, detail="الدولة غير صحيحة")
        if _parse_iso(payload.start_date) is None:
            raise HTTPException(status_code=400, detail="صيغة تاريخ البداية يجب أن تكون YYYY-MM-DD")
        if payload.status not in {"active", "stopped"}:
            raise HTTPException(status_code=400, detail="حالة السجل غير صحيحة")
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "name": payload.name.strip(),
            "category": payload.category,
            "country": payload.country,
            "monthly_amount": round(float(payload.monthly_amount), 2),
            "start_date": payload.start_date,
            "status": payload.status,
            "notes": (payload.notes or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.operating_salaries.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.put("/salaries/{salary_id}")
    async def update_salary(
        salary_id: str, payload: SalaryUpdate, user: dict = Depends(current_user)
    ):
        if await find_employee_salary(db, user["id"], salary_id):
            raise HTTPException(
                status_code=409,
                detail="حالة وراتب الموظف تُداران من إدارة الموظفين في ميزان 2",
            )
        existing = await db.operating_salaries.find_one(
            {
                "id": salary_id,
                "user_id": user["id"],
                "category": {"$in": ["household", "charity"]},
            },
            {"_id": 0},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="سجل الراتب غير موجود")
        update: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.name is not None:
            update["name"] = payload.name.strip()
        if payload.category is not None:
            if payload.category not in SALARY_CATEGORIES:
                raise HTTPException(status_code=400, detail="نوع الراتب غير صحيح")
            if payload.category == "employee":
                raise HTTPException(
                    status_code=409,
                    detail="رواتب الموظفين تُدار من إدارة الموظفين في ميزان 2",
                )
            update["category"] = payload.category
        if payload.country is not None:
            if payload.country not in SALARY_COUNTRIES:
                raise HTTPException(status_code=400, detail="الدولة غير صحيحة")
            update["country"] = payload.country
        if payload.monthly_amount is not None:
            if payload.monthly_amount <= 0:
                raise HTTPException(status_code=400, detail="المبلغ يجب أن يكون أكبر من صفر")
            update["monthly_amount"] = round(float(payload.monthly_amount), 2)
        if payload.start_date is not None:
            if _parse_iso(payload.start_date) is None:
                raise HTTPException(status_code=400, detail="صيغة تاريخ البداية يجب أن تكون YYYY-MM-DD")
            update["start_date"] = payload.start_date
        if payload.status is not None:
            if payload.status not in {"active", "stopped"}:
                raise HTTPException(status_code=400, detail="حالة السجل غير صحيحة")
            update["status"] = payload.status
            # Iter-115 — track stop_date explicitly when status flips to
            # "stopped" so the salary-accrual aggregator can cap accrual
            # at the actual suspension day instead of falling back to
            # `updated_at`.
            if payload.status == "stopped" and existing.get("status") != "stopped":
                update["stopped_at"] = datetime.now(timezone.utc).date().isoformat()
            elif payload.status == "active":
                update["stopped_at"] = None
        if payload.notes is not None:
            update["notes"] = payload.notes.strip()
        await db.operating_salaries.update_one(
            {"id": salary_id, "user_id": user["id"]}, {"$set": update}
        )
        return await db.operating_salaries.find_one(
            {"id": salary_id, "user_id": user["id"]}, {"_id": 0}
        )

    @router.delete("/salaries/{salary_id}")
    async def delete_salary(salary_id: str, user: dict = Depends(current_user)):
        if await find_employee_salary(db, user["id"], salary_id):
            raise HTTPException(
                status_code=409,
                detail="لا يمكن حذف عقد موظف من صفحة المصروفات القديمة",
            )
        res = await db.operating_salaries.delete_one(
            {
                "id": salary_id,
                "user_id": user["id"],
                "category": {"$in": ["household", "charity"]},
            }
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="سجل الراتب غير موجود")
        return {"ok": True}

    # ─── Rentals CRUD ─────────────────────────────────────────────────────────
    @router.get("/rentals")
    async def list_rentals(user: dict = Depends(current_user)):
        items = await db.operating_rentals.find(
            {"user_id": user["id"]}, {"_id": 0}
        ).sort("created_at", -1).to_list(2000)
        return {"items": items}

    @router.post("/rentals")
    async def create_rental(payload: RentalIn, user: dict = Depends(current_user)):
        if payload.property_type not in RENTAL_PROPERTY_TYPES:
            raise HTTPException(status_code=400, detail="نوع العقار غير صحيح")
        sd = _parse_iso(payload.start_date)
        ed = _parse_iso(payload.end_date)
        if sd is None or ed is None:
            raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
        if ed < sd:
            raise HTTPException(status_code=400, detail="تاريخ النهاية يجب أن يكون بعد تاريخ البداية")
        if payload.status not in {"active", "expired"}:
            raise HTTPException(status_code=400, detail="حالة العقد غير صحيحة")
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "property_name": payload.property_name.strip(),
            "property_type": payload.property_type,
            "annual_amount": round(float(payload.annual_amount), 2),
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "status": payload.status,
            "notes": (payload.notes or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.operating_rentals.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.put("/rentals/{rental_id}")
    async def update_rental(
        rental_id: str, payload: RentalUpdate, user: dict = Depends(current_user)
    ):
        existing = await db.operating_rentals.find_one(
            {"id": rental_id, "user_id": user["id"]}, {"_id": 0}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="سجل الإيجار غير موجود")
        update: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.property_name is not None:
            update["property_name"] = payload.property_name.strip()
        if payload.property_type is not None:
            if payload.property_type not in RENTAL_PROPERTY_TYPES:
                raise HTTPException(status_code=400, detail="نوع العقار غير صحيح")
            update["property_type"] = payload.property_type
        if payload.annual_amount is not None:
            if payload.annual_amount <= 0:
                raise HTTPException(status_code=400, detail="المبلغ يجب أن يكون أكبر من صفر")
            update["annual_amount"] = round(float(payload.annual_amount), 2)
        # Date validation (need a coherent pair after merge)
        sd_str = payload.start_date if payload.start_date is not None else existing.get("start_date")
        ed_str = payload.end_date if payload.end_date is not None else existing.get("end_date")
        if payload.start_date is not None or payload.end_date is not None:
            sd, ed = _parse_iso(sd_str or ""), _parse_iso(ed_str or "")
            if sd is None or ed is None:
                raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
            if ed < sd:
                raise HTTPException(status_code=400, detail="تاريخ النهاية يجب أن يكون بعد تاريخ البداية")
            if payload.start_date is not None:
                update["start_date"] = payload.start_date
            if payload.end_date is not None:
                update["end_date"] = payload.end_date
        if payload.status is not None:
            if payload.status not in {"active", "expired"}:
                raise HTTPException(status_code=400, detail="حالة العقد غير صحيحة")
            update["status"] = payload.status
        if payload.notes is not None:
            update["notes"] = payload.notes.strip()
        await db.operating_rentals.update_one(
            {"id": rental_id, "user_id": user["id"]}, {"$set": update}
        )
        return await db.operating_rentals.find_one(
            {"id": rental_id, "user_id": user["id"]}, {"_id": 0}
        )

    @router.delete("/rentals/{rental_id}")
    async def delete_rental(rental_id: str, user: dict = Depends(current_user)):
        res = await db.operating_rentals.delete_one(
            {"id": rental_id, "user_id": user["id"]}
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="سجل الإيجار غير موجود")
        return {"ok": True}

    # ─── Daily expenses CRUD ──────────────────────────────────────────────────
    @router.get("/daily")
    async def list_daily(
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        q = {"user_id": user["id"]}
        if from_date or to_date:
            df = {}
            if from_date:
                df["$gte"] = from_date
            if to_date:
                df["$lte"] = to_date
            q["date"] = df
        items = await db.operating_daily_expenses.find(
            q, {"_id": 0}
        ).sort([("date", -1), ("created_at", -1)]).to_list(5000)
        return {"items": items}

    @router.post("/daily")
    async def create_daily(payload: DailyExpenseIn, user: dict = Depends(current_user)):
        if _parse_iso(payload.date) is None:
            raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
        # Validate the account if linked (Iter-94)
        if payload.paid_from_account_id:
            acc = await db.accounts.find_one(
                {"id": payload.paid_from_account_id, "user_id": user["id"]},
                {"_id": 0, "id": 1, "name": 1},
            )
            if not acc:
                raise HTTPException(status_code=404, detail="الحساب المختار للدفع غير موجود")
        amount = round(float(payload.amount), 2)
        expense_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": expense_id,
            "user_id": user["id"],
            "date": payload.date,
            "expense_type": payload.expense_type.strip(),
            "description": (payload.description or "").strip(),
            "amount": amount,
            "payment_method": (payload.payment_method or "").strip(),
            "notes": (payload.notes or "").strip(),
            "paid_from_account_id": payload.paid_from_account_id,
            "linked_transaction_id": None,
            "created_at": now,
            "updated_at": now,
        }
        # Auto-post the bank movement if a source account is linked
        if payload.paid_from_account_id:
            tx_id = await _post_daily_expense_tx(
                db, user["id"], expense_id, payload.paid_from_account_id,
                amount, payload.date, doc["expense_type"], doc["description"],
            )
            doc["linked_transaction_id"] = tx_id
        await db.operating_daily_expenses.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.put("/daily/{expense_id}")
    async def update_daily(
        expense_id: str, payload: DailyExpenseUpdate, user: dict = Depends(current_user)
    ):
        existing = await db.operating_daily_expenses.find_one(
            {"id": expense_id, "user_id": user["id"]}, {"_id": 0}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="سجل المصروف غير موجود")
        update: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.date is not None:
            if _parse_iso(payload.date) is None:
                raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
            update["date"] = payload.date
        if payload.expense_type is not None:
            update["expense_type"] = payload.expense_type.strip()
        if payload.description is not None:
            update["description"] = payload.description.strip()
        if payload.amount is not None:
            if payload.amount <= 0:
                raise HTTPException(status_code=400, detail="المبلغ يجب أن يكون أكبر من صفر")
            update["amount"] = round(float(payload.amount), 2)
        if payload.payment_method is not None:
            update["payment_method"] = payload.payment_method.strip()
        if payload.notes is not None:
            update["notes"] = payload.notes.strip()

        # Iter-94 — handle the linked bank transaction in sync.
        sent_fields = payload.__fields_set__ if hasattr(payload, "__fields_set__") else set(payload.model_dump(exclude_unset=True).keys())
        explicit_account = "paid_from_account_id" in sent_fields
        new_account_id = payload.paid_from_account_id if explicit_account else existing.get("paid_from_account_id")
        new_amount = update.get("amount", existing.get("amount"))
        new_date = update.get("date", existing.get("date"))
        new_type = update.get("expense_type", existing.get("expense_type"))
        new_desc = update.get("description", existing.get("description"))

        old_account_id = existing.get("paid_from_account_id")
        old_tx_id = existing.get("linked_transaction_id")

        account_changed = new_account_id != old_account_id
        amount_changed = payload.amount is not None and update.get("amount") != existing.get("amount")
        date_changed = payload.date is not None and update.get("date") != existing.get("date")

        if old_tx_id and (account_changed or amount_changed or date_changed
                          or payload.expense_type is not None or payload.description is not None):
            # Drop the old tx (and recompute the old account's balance)
            await _delete_daily_expense_tx(db, user["id"], old_tx_id, old_account_id)
            update["linked_transaction_id"] = None

        if new_account_id:
            # Validate the (new or same) account before re-posting
            acc = await db.accounts.find_one(
                {"id": new_account_id, "user_id": user["id"]},
                {"_id": 0, "id": 1},
            )
            if not acc:
                raise HTTPException(status_code=404, detail="الحساب المختار للدفع غير موجود")
            if account_changed or amount_changed or date_changed \
                    or payload.expense_type is not None or payload.description is not None \
                    or (old_account_id and not old_tx_id):
                tx_id = await _post_daily_expense_tx(
                    db, user["id"], expense_id, new_account_id,
                    new_amount, new_date, new_type, new_desc,
                )
                update["linked_transaction_id"] = tx_id
            update["paid_from_account_id"] = new_account_id
        elif old_account_id and not new_account_id:
            # Explicitly unlinked
            update["paid_from_account_id"] = None
            update["linked_transaction_id"] = None

        await db.operating_daily_expenses.update_one(
            {"id": expense_id, "user_id": user["id"]}, {"$set": update}
        )
        return await db.operating_daily_expenses.find_one(
            {"id": expense_id, "user_id": user["id"]}, {"_id": 0}
        )

    @router.delete("/daily/{expense_id}")
    async def delete_daily(expense_id: str, user: dict = Depends(current_user)):
        # Iter-94 — roll back the linked bank movement if any.
        existing = await db.operating_daily_expenses.find_one(
            {"id": expense_id, "user_id": user["id"]},
            {"_id": 0, "linked_transaction_id": 1, "paid_from_account_id": 1},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="سجل المصروف غير موجود")
        if existing.get("linked_transaction_id") and existing.get("paid_from_account_id"):
            await _delete_daily_expense_tx(
                db, user["id"], existing["linked_transaction_id"],
                existing["paid_from_account_id"],
            )
        await db.operating_daily_expenses.delete_one(
            {"id": expense_id, "user_id": user["id"]}
        )
        return {"ok": True}

    # ─── Prepaid expenses CRUD (المصروفات المدفوعة مقدماً) ────────────────────
    @router.get("/prepaid")
    async def list_prepaid(user: dict = Depends(current_user)):
        items = await db.operating_prepaid_expenses.find(
            {"user_id": user["id"]}, {"_id": 0}
        ).sort("created_at", -1).to_list(2000)
        # Enrich with derived fields so the UI shows them without recomputing
        for it in items:
            it["period_days"] = _prepaid_period_days(it)
            it["daily_cost"] = round(_daily_from_prepaid(it), 2)
        return {"items": items}

    @router.post("/prepaid")
    async def create_prepaid(
        payload: PrepaidExpenseIn, user: dict = Depends(current_user)
    ):
        if payload.expense_type not in PREPAID_TYPES:
            raise HTTPException(status_code=400, detail="نوع المصروف غير صحيح")
        sd = _parse_iso(payload.start_date)
        ed = _parse_iso(payload.end_date)
        if sd is None or ed is None:
            raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
        if ed < sd:
            raise HTTPException(status_code=400, detail="تاريخ النهاية يجب أن يكون بعد تاريخ البداية")
        if payload.status not in {"active", "expired"}:
            raise HTTPException(status_code=400, detail="الحالة غير صحيحة")
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "expense_type": payload.expense_type,
            "beneficiary": payload.beneficiary.strip(),
            "amount": round(float(payload.amount), 2),
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "status": payload.status,
            "notes": (payload.notes or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.operating_prepaid_expenses.insert_one(doc)
        doc.pop("_id", None)
        doc["period_days"] = _prepaid_period_days(doc)
        doc["daily_cost"] = round(_daily_from_prepaid(doc), 2)
        return doc

    @router.put("/prepaid/{prepaid_id}")
    async def update_prepaid(
        prepaid_id: str,
        payload: PrepaidExpenseUpdate,
        user: dict = Depends(current_user),
    ):
        existing = await db.operating_prepaid_expenses.find_one(
            {"id": prepaid_id, "user_id": user["id"]}, {"_id": 0}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="السجل غير موجود")
        update: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.expense_type is not None:
            if payload.expense_type not in PREPAID_TYPES:
                raise HTTPException(status_code=400, detail="نوع المصروف غير صحيح")
            update["expense_type"] = payload.expense_type
        if payload.beneficiary is not None:
            update["beneficiary"] = payload.beneficiary.strip()
        if payload.amount is not None:
            if payload.amount <= 0:
                raise HTTPException(status_code=400, detail="المبلغ يجب أن يكون أكبر من صفر")
            update["amount"] = round(float(payload.amount), 2)
        sd_str = payload.start_date if payload.start_date is not None else existing.get("start_date")
        ed_str = payload.end_date if payload.end_date is not None else existing.get("end_date")
        if payload.start_date is not None or payload.end_date is not None:
            sd = _parse_iso(sd_str or "")
            ed = _parse_iso(ed_str or "")
            if sd is None or ed is None:
                raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
            if ed < sd:
                raise HTTPException(status_code=400, detail="تاريخ النهاية يجب أن يكون بعد تاريخ البداية")
            if payload.start_date is not None:
                update["start_date"] = payload.start_date
            if payload.end_date is not None:
                update["end_date"] = payload.end_date
        if payload.status is not None:
            if payload.status not in {"active", "expired"}:
                raise HTTPException(status_code=400, detail="الحالة غير صحيحة")
            update["status"] = payload.status
        if payload.notes is not None:
            update["notes"] = payload.notes.strip()
        await db.operating_prepaid_expenses.update_one(
            {"id": prepaid_id, "user_id": user["id"]}, {"$set": update}
        )
        out = await db.operating_prepaid_expenses.find_one(
            {"id": prepaid_id, "user_id": user["id"]}, {"_id": 0}
        )
        out["period_days"] = _prepaid_period_days(out)
        out["daily_cost"] = round(_daily_from_prepaid(out), 2)
        return out

    @router.delete("/prepaid/{prepaid_id}")
    async def delete_prepaid(prepaid_id: str, user: dict = Depends(current_user)):
        res = await db.operating_prepaid_expenses.delete_one(
            {"id": prepaid_id, "user_id": user["id"]}
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="السجل غير موجود")
        return {"ok": True}

    # ─── Summary (KPI cards data) ────────────────────────────────────────────
    @router.get("/summary")
    async def summary(user: dict = Depends(current_user)):
        """Return the rolled-up amounts displayed on the page's top cards.

        Computes:
          - Total active monthly salaries (per category + grand total)
          - Total active annual rentals + their derived daily cost
          - Today's other daily expenses (from `operating_daily_expenses`)
          - Today's total operating cost (the formal per-day operating cost
            used by the dashboard / reports)
        """
        uid = user["id"]
        today = riyadh_today()

        non_employee_salaries = await db.operating_salaries.find(
            {
                "user_id": uid,
                "status": "active",
                "category": {"$in": ["household", "charity"]},
            },
            {"_id": 0},
        ).to_list(5000)
        salaries = [
            row for row in await employee_salary_rows(db, uid)
            if _salary_active_on(row, today)
        ] + non_employee_salaries
        emp = house = char = 0.0
        country_breakdown: dict = {}
        for s in salaries:
            amt = float(s.get("monthly_amount") or 0)
            cat = (s.get("category") or "").strip()
            country = (s.get("country") or "saudi").strip() or "saudi"
            if cat == "employee":
                emp += amt
            elif cat == "household":
                house += amt
            elif cat == "charity":
                char += amt
            cb = country_breakdown.setdefault(country, {"monthly_total": 0.0, "count": 0})
            cb["monthly_total"] += amt
            cb["count"] += 1
        salaries_total_monthly = emp + house + char
        for v in country_breakdown.values():
            v["monthly_total"] = round(v["monthly_total"], 2)

        rentals = await db.operating_rentals.find(
            {"user_id": uid, "status": "active"}, {"_id": 0}
        ).to_list(5000)
        annual_total = 0.0
        for r in rentals:
            if _rental_active_on(r, today):
                annual_total += float(r.get("annual_amount") or 0)
        rentals_daily = round(annual_total / 365.0, 2) if annual_total else 0.0

        # Prepaid expenses (المصروفات المدفوعة مقدماً) — only currently-active.
        prepaids = await db.operating_prepaid_expenses.find(
            {"user_id": uid, "status": "active"}, {"_id": 0}
        ).to_list(5000)
        prepaid_total_paid = 0.0
        prepaid_active_count = 0
        prepaid_by_type_monthly: dict = {}
        for p in prepaids:
            if not _prepaid_active_on(p, today):
                continue
            prepaid_active_count += 1
            prepaid_total_paid += float(p.get("amount") or 0)
            t = (p.get("expense_type") or "other").strip() or "other"
            cb = prepaid_by_type_monthly.setdefault(
                t, {"total_paid": 0.0, "daily_cost": 0.0, "count": 0}
            )
            cb["total_paid"] += float(p.get("amount") or 0)
            cb["daily_cost"] += _daily_from_prepaid(p)
            cb["count"] += 1
        for v in prepaid_by_type_monthly.values():
            v["total_paid"] = round(v["total_paid"], 2)
            v["daily_cost"] = round(v["daily_cost"], 2)

        today_breakdown = await compute_operating_expenses_for_day(db, uid, today)
        return {
            "salaries": {
                "employee_monthly": round(emp, 2),
                "household_monthly": round(house, 2),
                "charity_monthly": round(char, 2),
                "total_monthly": round(salaries_total_monthly, 2),
                "active_count": len(salaries),
                "by_country": country_breakdown,
            },
            "rentals": {
                "annual_total": round(annual_total, 2),
                "daily_total": rentals_daily,
                "active_count": sum(1 for r in rentals if _rental_active_on(r, today)),
            },
            "prepaid": {
                "total_paid": round(prepaid_total_paid, 2),
                "daily_total": today_breakdown.get("prepaid_daily", 0.0),
                "active_count": prepaid_active_count,
                "by_type": prepaid_by_type_monthly,
            },
            "today": today_breakdown,
        }

    # ─── Report (daily / monthly / yearly aggregates) ────────────────────────
    @router.get("/report")
    async def report(
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        """Return daily / monthly / yearly operating-cost totals.

        - `daily`: today's breakdown
        - `monthly`: current month so far (or [from_date, to_date] if provided)
        - `yearly`: current year so far
        - `range`: same shape as monthly but for the explicit user-supplied range
        """
        uid = user["id"]
        today = datetime.now(timezone.utc).date()
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        daily = await compute_operating_expenses_for_day(db, uid, today)
        monthly = await compute_operating_expenses_for_range(db, uid, month_start, today)
        yearly = await compute_operating_expenses_for_range(db, uid, year_start, today)

        range_data = None
        if from_date or to_date:
            fd = _parse_iso(from_date or month_start.isoformat()) or month_start
            td = _parse_iso(to_date or today.isoformat()) or today
            range_data = await compute_operating_expenses_for_range(db, uid, fd, td)

        return {
            "daily": daily,
            "monthly": monthly,
            "yearly": yearly,
            "range": range_data,
        }

    return router


def attach_operating_expenses_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
