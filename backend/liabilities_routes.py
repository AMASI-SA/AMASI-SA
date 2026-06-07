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


# ── Catalogue ──────────────────────────────────────────────────────────────
LIABILITY_KINDS = ("salary", "ad_account", "salary_advance")
LIABILITY_STATUSES = ("unpaid", "partial", "paid")
AD_PROVIDERS = ("snapchat", "tiktok", "meta")


# ── Helpers ────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    return date.today().isoformat()


def _strip(doc: dict) -> dict:
    return {k: v for k, v in (doc or {}).items() if not k.startswith("_")}


def _round(v) -> float:
    return round(float(v or 0), 2)


def _compute_status(expected: float, paid: float) -> str:
    """Recompute the status field from the amounts only."""
    expected = _round(expected)
    paid = _round(paid)
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
    """Manual creation — primarily for `ad_account` bills or
    `salary_advance` entries. Salaries are created via
    /generate-salaries to enforce idempotency per period.

    For salary_advance: pass employee_salary_id and amount. The advance
    will be recorded with paid_amount = expected_amount = amount so the
    bank movement reflects the cash leaving immediately. paid_from_account_id
    is required for advances to update the bank balance.
    """
    kind: Literal["ad_account", "salary_advance"]
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
        today = date.today()
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

    # ── POST / (manual create) ────────────────────────────────────────
    @router.post("")
    async def create_liability(
        payload: LiabilityCreate, user: dict = Depends(current_user)
    ):
        kind = payload.kind
        now = _now()
        liab_id = str(uuid.uuid4())

        if kind == "ad_account":
            row = {
                "id": liab_id,
                "user_id": user["id"],
                "kind": "ad_account",
                "employee_salary_id": None,
                "ad_provider": payload.ad_provider,
                "ad_account_label": payload.ad_account_label or "",
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
    @router.get("/summary")
    async def summary(user: dict = Depends(current_user)):
        """Assets − Liabilities = Net financial position.

        Assets reused from existing `accounts` collection:
          - banks:     SUM(current_balance) where account_type=bank
          - platforms: SUM(expected_orders_balance) where
                       account_type=payment_platform AND status != hidden

        Liabilities = SUM(remaining_amount) over open `liabilities` rows.
        salary_advance rows are EXCLUDED from the liability total because
        the cash already left the bank (they're modelled as paid).
        """
        uid = user["id"]

        # Assets ───────────────────────────────────────────────────
        banks_total = 0.0
        platforms_total = 0.0
        async for a in db.accounts.find(
            {"user_id": uid},
            {"_id": 0, "account_type": 1, "status": 1,
             "current_balance": 1, "expected_orders_balance": 1},
        ):
            if (a.get("status") or "active") == "hidden":
                continue
            t = a.get("account_type")
            if t == "bank":
                banks_total += float(a.get("current_balance") or 0)
            elif t == "payment_platform":
                platforms_total += float(a.get("expected_orders_balance") or 0)
        assets_total = _round(banks_total + platforms_total)

        # Liabilities ──────────────────────────────────────────────
        today = _today_str()
        agg = {
            "salaries_unpaid": 0.0,
            "ad_accounts_unpaid": 0.0,
            "overdue_total": 0.0,
        }
        by_provider = {p: 0.0 for p in AD_PROVIDERS}
        async for r in db.liabilities.find(
            {
                "user_id": uid,
                "kind": {"$in": ["salary", "ad_account"]},
                "status": {"$ne": "paid"},
            },
            {"_id": 0, "kind": 1, "expected_amount": 1, "paid_amount": 1,
             "due_date": 1, "ad_provider": 1, "status": 1},
        ):
            remaining = _round(
                _round(r.get("expected_amount")) - _round(r.get("paid_amount"))
            )
            if remaining <= 0:
                continue
            if r["kind"] == "salary":
                agg["salaries_unpaid"] += remaining
            elif r["kind"] == "ad_account":
                agg["ad_accounts_unpaid"] += remaining
                prov = r.get("ad_provider")
                if prov in by_provider:
                    by_provider[prov] += remaining
            if r.get("due_date") and r["due_date"] < today:
                agg["overdue_total"] += remaining

        liabilities_total = _round(
            agg["salaries_unpaid"] + agg["ad_accounts_unpaid"]
        )

        return {
            "assets": {
                "banks": _round(banks_total),
                "payment_platforms_expected": _round(platforms_total),
                "total": assets_total,
            },
            "liabilities": {
                "salaries_unpaid": _round(agg["salaries_unpaid"]),
                "ad_accounts_unpaid": _round(agg["ad_accounts_unpaid"]),
                "overdue_total": _round(agg["overdue_total"]),
                "total": liabilities_total,
                "by_ad_provider": {k: _round(v) for k, v in by_provider.items()},
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
