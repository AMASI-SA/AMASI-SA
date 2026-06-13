"""Universal Accounting Routes — Iter-161 (Phase 2)

The ONE unified entry point for ALL financial movements across:
  • Employees (advances, custody, salary)
  • Suppliers (invoices, payments)
  • External persons (loans/grants and collections)
  • Bank transfers and general expenses
  • Expense categories management

Every endpoint here:
  1. Validates the business rule
  2. Composes a balanced txn_group (Σ debits == Σ credits)
  3. Calls post_txn_group → atomically appends entries to general_ledger
  4. Writes audit rows
  5. Returns the txn group + computed balances

NO direct UPDATE of stored balances. NO DELETE. Corrections via /reverse
or /adjustment on the resulting txn_group_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db
from ledger_core import (
    compute_balance,
    post_txn_group,
    write_audit,
    REASON_CODES,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Default expense categories (seeded on first read per user) ──────
DEFAULT_EXPENSE_CATEGORIES: list[dict] = [
    {"code": "salary",            "name": "رواتب"},
    {"code": "shipping",          "name": "شحن"},
    {"code": "advertising",       "name": "إعلانات"},
    {"code": "inventory",         "name": "مشتريات"},
    {"code": "subscriptions",     "name": "اشتراكات"},
    {"code": "fuel",              "name": "وقود"},
    {"code": "office",            "name": "مكتبية"},
    {"code": "rent",              "name": "إيجارات"},
    {"code": "telecom",           "name": "اتصالات وإنترنت"},
    {"code": "bank_fees",         "name": "رسوم بنكية"},
    {"code": "tamara_fees",       "name": "رسوم تمارا"},
    {"code": "tabby_fees",        "name": "رسوم تابي"},
    {"code": "gateway_fees",      "name": "رسوم بوابات الدفع"},
    {"code": "insurance",         "name": "تأمينات"},
    {"code": "maintenance",       "name": "صيانة"},
    {"code": "other",             "name": "أخرى"},
]


# ── Pydantic schemas ────────────────────────────────────────────────
class AdvanceGrantIn(BaseModel):
    amount: float = Field(..., gt=0)
    paid_from_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class CustodyGrantIn(BaseModel):
    amount: float = Field(..., gt=0)
    paid_from_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class CustodyReturnIn(BaseModel):
    amount: float = Field(..., gt=0)
    deposited_to_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class CustodyReceiptItem(BaseModel):
    expense_category: str   # code from expense_categories
    amount: float = Field(..., gt=0)
    notes: Optional[str] = ""


class CustodySettleReceiptsIn(BaseModel):
    items: list[CustodyReceiptItem]
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class EmployeeSettleIn(BaseModel):
    """Pay salary (+ optionally consume advances + post excess as new advance)"""
    amount: float = Field(..., gt=0)
    paid_from_account_id: str
    payment_date: Optional[str] = None
    apply_open_advances: bool = True
    notes: Optional[str] = ""


class SalaryAccrualIn(BaseModel):
    """Monthly salary accrual (typically posted once per month/employee)"""
    amount: float = Field(..., gt=0)
    period: str  # YYYY-MM
    notes: Optional[str] = ""


class SupplierInvoiceIn(BaseModel):
    amount: float = Field(..., gt=0)
    expense_category: str = "inventory"
    invoice_no: Optional[str] = None
    invoice_date: Optional[str] = None
    notes: Optional[str] = ""


class SupplierPaymentIn(BaseModel):
    amount: float = Field(..., gt=0)
    paid_from_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class ExternalGrantIn(BaseModel):
    amount: float = Field(..., gt=0)
    paid_from_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class ExternalCollectIn(BaseModel):
    amount: float = Field(..., gt=0)
    deposited_to_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class BankTransferIn(BaseModel):
    amount: float = Field(..., gt=0)
    from_account_id: str
    to_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""


class ExpenseRecordIn(BaseModel):
    amount: float = Field(..., gt=0)
    expense_category: str
    paid_from_account_id: str
    payment_date: Optional[str] = None
    notes: Optional[str] = ""
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[str] = None


class ExpenseCategoryIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1)


# ── Helper: ensure default expense categories exist for this user ──
async def _ensure_default_expense_categories(db, user_id: str) -> None:
    count = await db.expense_categories.count_documents({"user_id": user_id})
    if count > 0:
        return
    now = _now()
    docs = [
        {"id": str(uuid.uuid4()), "user_id": user_id,
         "code": c["code"], "name": c["name"], "system": True,
         "created_at": now, "updated_at": now}
        for c in DEFAULT_EXPENSE_CATEGORIES
    ]
    await db.expense_categories.insert_many(docs)


async def _resolve_actor(user: dict) -> tuple[str, str]:
    return user["id"], (user.get("name") or user.get("email") or "")


# ── Router builder ──────────────────────────────────────────────────
def make_universal_router(db) -> APIRouter:
    router = APIRouter(prefix="/accounting", tags=["accounting"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    # ═══════════════════════════════════════════════════════════════
    # Expense Categories CRUD
    # ═══════════════════════════════════════════════════════════════
    @router.get("/expense-categories")
    async def list_expense_categories(user: dict = Depends(current_user)):
        await _ensure_default_expense_categories(db, user["id"])
        cats = await db.expense_categories.find(
            {"user_id": user["id"]}, {"_id": 0},
        ).sort("name", 1).to_list(200)
        return cats

    @router.post("/expense-categories")
    async def create_expense_category(
        payload: ExpenseCategoryIn,
        user: dict = Depends(current_user),
    ):
        await _ensure_default_expense_categories(db, user["id"])
        code = payload.code.strip().lower().replace(" ", "_")
        exists = await db.expense_categories.find_one(
            {"user_id": user["id"], "code": code}, {"_id": 0, "id": 1},
        )
        if exists:
            raise HTTPException(400, "هذا الكود مستخدم بالفعل")
        now = _now()
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "code": code,
            "name": payload.name.strip(),
            "system": False,
            "created_at": now,
            "updated_at": now,
        }
        await db.expense_categories.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.patch("/expense-categories/{cat_id}")
    async def update_expense_category(
        cat_id: str, payload: ExpenseCategoryIn,
        user: dict = Depends(current_user),
    ):
        cat = await db.expense_categories.find_one(
            {"id": cat_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not cat:
            raise HTTPException(404, "الفئة غير موجودة")
        new_code = payload.code.strip().lower().replace(" ", "_")
        if new_code != cat["code"]:
            dup = await db.expense_categories.find_one(
                {"user_id": user["id"], "code": new_code},
                {"_id": 0, "id": 1},
            )
            if dup:
                raise HTTPException(400, "هذا الكود مستخدم بالفعل")
        await db.expense_categories.update_one(
            {"id": cat_id, "user_id": user["id"]},
            {"$set": {"code": new_code, "name": payload.name.strip(),
                      "updated_at": _now()}},
        )
        return {"ok": True}

    @router.delete("/expense-categories/{cat_id}")
    async def delete_expense_category(
        cat_id: str,
        user: dict = Depends(current_user),
    ):
        cat = await db.expense_categories.find_one(
            {"id": cat_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not cat:
            raise HTTPException(404, "الفئة غير موجودة")
        if cat.get("system"):
            raise HTTPException(400, "لا يمكن حذف فئة افتراضية")
        # Check it's not used in the ledger
        used = await db.general_ledger.find_one(
            {"user_id": user["id"], "entity_type": "expense",
             "entity_id": cat["code"]},
            {"_id": 0, "id": 1},
        )
        if used:
            raise HTTPException(
                400, "هذه الفئة مستخدمة في قيود — لا يمكن حذفها",
            )
        await db.expense_categories.delete_one(
            {"id": cat_id, "user_id": user["id"]},
        )
        return {"ok": True}

    # ═══════════════════════════════════════════════════════════════
    # Employee: Advances
    # ═══════════════════════════════════════════════════════════════
    @router.post("/employees/{emp_id}/advances")
    async def grant_advance(
        emp_id: str, payload: AdvanceGrantIn,
        user: dict = Depends(current_user),
    ):
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")

        actor_id, actor_name = await _resolve_actor(user)
        notes = payload.notes or f"منح سلفة — {emp.get('name')}"
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="advance_grant", notes=notes,
            metadata={
                "employee_name": emp.get("name"),
                "bank_name": acc.get("name"),
                "payment_date": payload.payment_date,
            },
            entries=[
                {"entity_type": "employee", "entity_id": emp_id,
                 "sub_account": "advance", "side": "debit",
                 "amount": payload.amount, "entry_type": "advance_grant"},
                {"entity_type": "bank", "entity_id": payload.paid_from_account_id,
                 "sub_account": "main", "side": "credit",
                 "amount": payload.amount, "entry_type": "advance_grant"},
            ],
        )
        return {
            "ok": True, **result,
            "balance": await compute_balance(
                db, user_id=user["id"], entity_type="employee",
                entity_id=emp_id, sub_account="advance"),
        }

    # ═══════════════════════════════════════════════════════════════
    # Employee: Custody
    # ═══════════════════════════════════════════════════════════════
    @router.post("/employees/{emp_id}/custody")
    async def grant_custody(
        emp_id: str, payload: CustodyGrantIn,
        user: dict = Depends(current_user),
    ):
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        notes = payload.notes or f"تسليم عهدة — {emp.get('name')}"
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="custody_grant", notes=notes,
            metadata={"employee_name": emp.get("name"),
                       "payment_date": payload.payment_date},
            entries=[
                {"entity_type": "employee", "entity_id": emp_id,
                 "sub_account": "custody", "side": "debit",
                 "amount": payload.amount, "entry_type": "custody_grant"},
                {"entity_type": "bank", "entity_id": payload.paid_from_account_id,
                 "sub_account": "main", "side": "credit",
                 "amount": payload.amount, "entry_type": "custody_grant"},
            ],
        )
        return {"ok": True, **result,
                 "balance": await compute_balance(
                     db, user_id=user["id"], entity_type="employee",
                     entity_id=emp_id, sub_account="custody")}

    @router.post("/employees/{emp_id}/custody/return")
    async def return_custody(
        emp_id: str, payload: CustodyReturnIn,
        user: dict = Depends(current_user),
    ):
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        # Check there's enough custody open
        bal = await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=emp_id, sub_account="custody")
        if bal["net_balance"] < payload.amount - 0.01:
            raise HTTPException(
                400,
                f"رصيد العهدة المفتوح ({bal['net_balance']:.2f}) أقل من "
                f"المبلغ المُسترَد ({payload.amount:.2f})",
            )
        acc = await db.accounts.find_one(
            {"id": payload.deposited_to_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="custody_return",
            notes=payload.notes or f"إرجاع عهدة — {emp.get('name')}",
            metadata={"employee_name": emp.get("name"),
                       "payment_date": payload.payment_date},
            entries=[
                {"entity_type": "bank",
                 "entity_id": payload.deposited_to_account_id,
                 "sub_account": "main", "side": "debit",
                 "amount": payload.amount, "entry_type": "custody_return"},
                {"entity_type": "employee", "entity_id": emp_id,
                 "sub_account": "custody", "side": "credit",
                 "amount": payload.amount, "entry_type": "custody_return"},
            ],
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="employee",
                    entity_id=emp_id, sub_account="custody")}

    @router.post("/employees/{emp_id}/custody/settle-with-receipts")
    async def settle_custody_with_receipts(
        emp_id: str, payload: CustodySettleReceiptsIn,
        user: dict = Depends(current_user),
    ):
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        await _ensure_default_expense_categories(db, user["id"])
        valid_codes = {c["code"] for c in
                        await db.expense_categories.find(
                            {"user_id": user["id"]},
                            {"_id": 0, "code": 1}).to_list(500)}
        total = round(sum(it.amount for it in payload.items), 2)
        if total <= 0:
            raise HTTPException(400, "إجمالي الفواتير صفر")
        # Validate expense codes
        for it in payload.items:
            if it.expense_category not in valid_codes:
                raise HTTPException(
                    400, f"فئة المصاريف غير معتمدة: {it.expense_category}",
                )
        bal = await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=emp_id, sub_account="custody")
        if bal["net_balance"] < total - 0.01:
            raise HTTPException(
                400,
                f"رصيد العهدة المفتوح ({bal['net_balance']:.2f}) أقل من "
                f"إجمالي الفواتير ({total:.2f})",
            )
        actor_id, actor_name = await _resolve_actor(user)
        entries: list[dict] = []
        for it in payload.items:
            entries.append({
                "entity_type": "expense", "entity_id": it.expense_category,
                "side": "debit", "amount": it.amount,
                "entry_type": "custody_expense",
                "notes": it.notes or "",
                "metadata": {"employee_id": emp_id,
                              "employee_name": emp.get("name")},
            })
        entries.append({
            "entity_type": "employee", "entity_id": emp_id,
            "sub_account": "custody", "side": "credit",
            "amount": total, "entry_type": "custody_expense",
        })
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="custody_settle_receipts",
            notes=payload.notes or f"إقفال عهدة بفواتير — {emp.get('name')}",
            metadata={"employee_name": emp.get("name"),
                       "payment_date": payload.payment_date,
                       "receipts_count": len(payload.items)},
            entries=entries,
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="employee",
                    entity_id=emp_id, sub_account="custody")}

    # ═══════════════════════════════════════════════════════════════
    # Employee: Salary Settlement
    # ═══════════════════════════════════════════════════════════════
    @router.post("/employees/{emp_id}/salary-accrual")
    async def post_salary_accrual(
        emp_id: str, payload: SalaryAccrualIn,
        user: dict = Depends(current_user),
    ):
        """Post a monthly salary accrual entry. Typically run by a
        scheduled task at end of month, but can be invoked manually."""
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        # Idempotency: check no accrual already posted for this period.
        existing = await db.general_ledger.find_one(
            {"user_id": user["id"], "entity_type": "employee",
             "entity_id": emp_id, "sub_account": "salary_payable",
             "entry_type": "salary_accrual",
             "metadata.period": payload.period,
             "status": {"$ne": "reversed"}},
            {"_id": 0, "id": 1},
        )
        if existing:
            raise HTTPException(
                400,
                f"يوجد قيد استحقاق لهذا الموظف للفترة {payload.period} مسبقاً",
            )
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="salary_accrual",
            notes=payload.notes or (
                f"استحقاق راتب {payload.period} — {emp.get('name')}"
            ),
            metadata={"employee_name": emp.get("name"),
                       "period": payload.period},
            entries=[
                {"entity_type": "expense", "entity_id": "salary",
                 "side": "debit", "amount": payload.amount,
                 "entry_type": "salary_accrual"},
                {"entity_type": "employee", "entity_id": emp_id,
                 "sub_account": "salary_payable", "side": "credit",
                 "amount": payload.amount, "entry_type": "salary_accrual"},
            ],
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="employee",
                    entity_id=emp_id, sub_account="salary_payable")}

    @router.post("/employees/{emp_id}/settle")
    async def settle_employee(
        emp_id: str, payload: EmployeeSettleIn,
        user: dict = Depends(current_user),
    ):
        """Unified employee cash-out: pays accrued salary AND optionally
        consumes any open advance against it.

        Logic:
          payable_bal = current accrued salary (employee owed)
          advance_bal = current open advance (employee owes)
          gross_payment = payload.amount

          1. If apply_open_advances: pay payable up to (gross + advance_bal),
             but cap at payable_bal. The advance offset reduces the cash
             leaving the bank.
          2. Cash leaving the bank = gross_payment.
          3. salary_payable reduced by gross_payment + advance_offset.
          4. advance reduced by advance_offset (sub_account=advance,
             entry_type=advance_settle).

        For simplicity in this MVP we assume the merchant inputs the NET
        cash they intend to pay. If they want to apply more advance, they
        increase the gross.
        """
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")

        payable_bal = (await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=emp_id, sub_account="salary_payable",
        ))["outstanding_debt"]  # positive when we owe employee

        advance_bal = (await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=emp_id, sub_account="advance",
        ))["net_balance"]  # positive when employee owes us

        gross = round(payload.amount, 2)
        # Don't pay more than what's accrued; any excess becomes a new advance
        salary_part = round(min(gross, payable_bal), 2) if payable_bal > 0 else 0.0
        advance_offset = 0.0
        if payload.apply_open_advances and advance_bal > 0 and salary_part > 0:
            # Apply advance against the remaining payable beyond the cash
            # payment, up to the smaller of (payable_left, advance_bal)
            payable_left = round(payable_bal - salary_part, 2)
            advance_offset = round(min(payable_left, advance_bal), 2)
            if advance_offset < 0:
                advance_offset = 0.0
        new_advance_excess = round(gross - salary_part, 2) if gross > salary_part else 0.0

        actor_id, actor_name = await _resolve_actor(user)
        entries: list[dict] = []

        # Part 1: cash payment of salary
        if salary_part > 0:
            entries.append({
                "entity_type": "employee", "entity_id": emp_id,
                "sub_account": "salary_payable", "side": "debit",
                "amount": salary_part, "entry_type": "salary_payment",
            })
            entries.append({
                "entity_type": "bank",
                "entity_id": payload.paid_from_account_id,
                "sub_account": "main", "side": "credit",
                "amount": salary_part, "entry_type": "salary_payment",
            })

        # Part 2: advance offset against remaining accrual
        if advance_offset > 0:
            entries.append({
                "entity_type": "employee", "entity_id": emp_id,
                "sub_account": "salary_payable", "side": "debit",
                "amount": advance_offset, "entry_type": "advance_settle",
            })
            entries.append({
                "entity_type": "employee", "entity_id": emp_id,
                "sub_account": "advance", "side": "credit",
                "amount": advance_offset, "entry_type": "advance_settle",
            })

        # Part 3: any excess cash becomes a new advance
        if new_advance_excess > 0:
            entries.append({
                "entity_type": "employee", "entity_id": emp_id,
                "sub_account": "advance", "side": "debit",
                "amount": new_advance_excess, "entry_type": "advance_grant",
            })
            entries.append({
                "entity_type": "bank",
                "entity_id": payload.paid_from_account_id,
                "sub_account": "main", "side": "credit",
                "amount": new_advance_excess, "entry_type": "advance_grant",
            })

        if not entries:
            raise HTTPException(
                400,
                "لا يوجد راتب مستحق ولا مبلغ صالح للصرف",
            )

        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="employee_settle",
            notes=payload.notes or f"صرف راتب — {emp.get('name')}",
            metadata={
                "employee_name": emp.get("name"),
                "salary_part": salary_part,
                "advance_offset": advance_offset,
                "new_advance_excess": new_advance_excess,
                "payment_date": payload.payment_date,
            },
            entries=entries,
        )
        return {"ok": True, **result,
                "salary_part": salary_part,
                "advance_offset": advance_offset,
                "new_advance_excess": new_advance_excess,
                "salary_payable_after": (await compute_balance(
                    db, user_id=user["id"], entity_type="employee",
                    entity_id=emp_id, sub_account="salary_payable"))["outstanding_debt"],
                "advance_after": (await compute_balance(
                    db, user_id=user["id"], entity_type="employee",
                    entity_id=emp_id, sub_account="advance"))["net_balance"]}

    # ═══════════════════════════════════════════════════════════════
    # Suppliers
    # ═══════════════════════════════════════════════════════════════
    @router.post("/suppliers/{supplier_id}/invoice")
    async def supplier_invoice(
        supplier_id: str, payload: SupplierInvoiceIn,
        user: dict = Depends(current_user),
    ):
        cp = await db.counterparties.find_one(
            {"id": supplier_id, "user_id": user["id"], "kind": "supplier"},
            {"_id": 0, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "المورد غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="supplier_invoice",
            notes=payload.notes or f"فاتورة مورد — {cp.get('name')}",
            metadata={"supplier_name": cp.get("name"),
                       "invoice_no": payload.invoice_no,
                       "invoice_date": payload.invoice_date},
            entries=[
                {"entity_type": "expense",
                 "entity_id": payload.expense_category,
                 "side": "debit", "amount": payload.amount,
                 "entry_type": "supplier_invoice"},
                {"entity_type": "supplier", "entity_id": supplier_id,
                 "sub_account": "payable", "side": "credit",
                 "amount": payload.amount,
                 "entry_type": "supplier_invoice"},
            ],
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="supplier",
                    entity_id=supplier_id, sub_account="payable")}

    @router.post("/suppliers/{supplier_id}/pay")
    async def supplier_pay(
        supplier_id: str, payload: SupplierPaymentIn,
        user: dict = Depends(current_user),
    ):
        cp = await db.counterparties.find_one(
            {"id": supplier_id, "user_id": user["id"], "kind": "supplier"},
            {"_id": 0, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "المورد غير موجود")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="supplier_payment",
            notes=payload.notes or f"سداد مورد — {cp.get('name')}",
            metadata={"supplier_name": cp.get("name"),
                       "payment_date": payload.payment_date},
            entries=[
                {"entity_type": "supplier", "entity_id": supplier_id,
                 "sub_account": "payable", "side": "debit",
                 "amount": payload.amount,
                 "entry_type": "supplier_payment"},
                {"entity_type": "bank",
                 "entity_id": payload.paid_from_account_id,
                 "sub_account": "main", "side": "credit",
                 "amount": payload.amount,
                 "entry_type": "supplier_payment"},
            ],
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="supplier",
                    entity_id=supplier_id, sub_account="payable")}

    # ═══════════════════════════════════════════════════════════════
    # External persons (Receivables / Payables)
    # ═══════════════════════════════════════════════════════════════
    @router.post("/external-persons/{cp_id}/grant")
    async def external_grant(
        cp_id: str, payload: ExternalGrantIn,
        user: dict = Depends(current_user),
    ):
        cp = await db.counterparties.find_one(
            {"id": cp_id, "user_id": user["id"]},
            {"_id": 0, "name": 1, "kind": 1},
        )
        if not cp:
            raise HTTPException(404, "الشخص غير موجود")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="receivable_grant",
            notes=payload.notes or f"قرض/سلفة — {cp.get('name')}",
            metadata={"person_name": cp.get("name"),
                       "payment_date": payload.payment_date},
            entries=[
                {"entity_type": "external_person", "entity_id": cp_id,
                 "sub_account": "receivable", "side": "debit",
                 "amount": payload.amount,
                 "entry_type": "receivable_grant"},
                {"entity_type": "bank",
                 "entity_id": payload.paid_from_account_id,
                 "sub_account": "main", "side": "credit",
                 "amount": payload.amount,
                 "entry_type": "receivable_grant"},
            ],
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="external_person",
                    entity_id=cp_id, sub_account="receivable")}

    @router.post("/external-persons/{cp_id}/collect")
    async def external_collect(
        cp_id: str, payload: ExternalCollectIn,
        user: dict = Depends(current_user),
    ):
        cp = await db.counterparties.find_one(
            {"id": cp_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "الشخص غير موجود")
        acc = await db.accounts.find_one(
            {"id": payload.deposited_to_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="receivable_collection",
            notes=payload.notes or f"تحصيل من — {cp.get('name')}",
            metadata={"person_name": cp.get("name"),
                       "payment_date": payload.payment_date},
            entries=[
                {"entity_type": "bank",
                 "entity_id": payload.deposited_to_account_id,
                 "sub_account": "main", "side": "debit",
                 "amount": payload.amount,
                 "entry_type": "receivable_collection"},
                {"entity_type": "external_person", "entity_id": cp_id,
                 "sub_account": "receivable", "side": "credit",
                 "amount": payload.amount,
                 "entry_type": "receivable_collection"},
            ],
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="external_person",
                    entity_id=cp_id, sub_account="receivable")}

    # ═══════════════════════════════════════════════════════════════
    # Bank transfer & General expense
    # ═══════════════════════════════════════════════════════════════
    @router.post("/bank-transfer")
    async def bank_transfer(
        payload: BankTransferIn,
        user: dict = Depends(current_user),
    ):
        if payload.from_account_id == payload.to_account_id:
            raise HTTPException(400, "حساب المصدر والوجهة لا يمكن أن يتطابقا")
        accs = await db.accounts.find(
            {"id": {"$in": [payload.from_account_id,
                              payload.to_account_id]},
             "user_id": user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(2)
        if len(accs) != 2:
            raise HTTPException(404, "أحد الحسابات البنكية غير موجود")
        names = {a["id"]: a["name"] for a in accs}
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="bank_transfer",
            notes=payload.notes or (
                f"تحويل من {names.get(payload.from_account_id)} "
                f"إلى {names.get(payload.to_account_id)}"
            ),
            metadata={"payment_date": payload.payment_date,
                       "from_name": names.get(payload.from_account_id),
                       "to_name": names.get(payload.to_account_id)},
            entries=[
                {"entity_type": "bank",
                 "entity_id": payload.to_account_id,
                 "sub_account": "main", "side": "debit",
                 "amount": payload.amount, "entry_type": "bank_transfer"},
                {"entity_type": "bank",
                 "entity_id": payload.from_account_id,
                 "sub_account": "main", "side": "credit",
                 "amount": payload.amount, "entry_type": "bank_transfer"},
            ],
        )
        return {"ok": True, **result}

    @router.post("/expenses")
    async def record_expense(
        payload: ExpenseRecordIn,
        user: dict = Depends(current_user),
    ):
        await _ensure_default_expense_categories(db, user["id"])
        cat = await db.expense_categories.find_one(
            {"user_id": user["id"], "code": payload.expense_category},
            {"_id": 0, "name": 1},
        )
        if not cat:
            raise HTTPException(400, "فئة المصاريف غير معتمدة")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="expense_record",
            notes=payload.notes or f"مصروف — {cat.get('name')}",
            metadata={"category_name": cat.get("name"),
                       "payment_date": payload.payment_date,
                       "related_entity_type": payload.related_entity_type,
                       "related_entity_id": payload.related_entity_id},
            entries=[
                {"entity_type": "expense",
                 "entity_id": payload.expense_category,
                 "side": "debit", "amount": payload.amount,
                 "entry_type": "expense_record"},
                {"entity_type": "bank",
                 "entity_id": payload.paid_from_account_id,
                 "sub_account": "main", "side": "credit",
                 "amount": payload.amount, "entry_type": "expense_record"},
            ],
        )
        return {"ok": True, **result}

    # ═══════════════════════════════════════════════════════════════
    # Unified statements (single endpoint to view everything)
    # ═══════════════════════════════════════════════════════════════
    @router.get("/statement")
    async def entity_statement(
        entity_type: str,
        entity_id: str,
        sub_account: Optional[str] = None,
        limit: int = 200,
        user: dict = Depends(current_user),
    ):
        """Full statement of an entity: all ledger entries + computed
        balance + audit log."""
        q: dict = {"user_id": user["id"],
                    "entity_type": entity_type, "entity_id": entity_id}
        if sub_account:
            q["sub_account"] = sub_account
        entries = await db.general_ledger.find(
            q, {"_id": 0}).sort("entry_no", -1).limit(int(limit)).to_list(int(limit))
        balance = await compute_balance(
            db, user_id=user["id"], entity_type=entity_type,
            entity_id=entity_id, sub_account=sub_account)
        audit = await db.accounting_audit_log.find(
            q, {"_id": 0}).sort("timestamp", -1).limit(50).to_list(50)
        return {"entries": entries, "balance": balance,
                 "audit_log": audit}

    @router.get("/employees/{emp_id}/financial-summary")
    async def employee_financial_summary(
        emp_id: str,
        user: dict = Depends(current_user),
    ):
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        salary_payable = await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=emp_id, sub_account="salary_payable")
        advance = await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=emp_id, sub_account="advance")
        custody = await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=emp_id, sub_account="custody")
        # Net position: we owe him (payable) minus what he owes us (advance+custody)
        owed_to_emp = salary_payable["outstanding_debt"]
        owed_by_emp = advance["net_balance"] + custody["net_balance"]
        net = round(owed_to_emp - owed_by_emp, 2)
        return {
            "employee_id": emp_id,
            "name": emp.get("name"),
            "salary_payable": salary_payable,
            "advance": advance,
            "custody": custody,
            "net_position": net,
            "owed_to_employee": round(owed_to_emp, 2),
            "owed_by_employee": round(owed_by_emp, 2),
        }

    # ═══════════════════════════════════════════════════════════════
    # Trial Balance — unified accounting report across all entities
    # ═══════════════════════════════════════════════════════════════
    @router.get("/trial-balance")
    async def trial_balance(user: dict = Depends(current_user)):
        """One report showing the balance of EVERY entity that has
        any general_ledger activity. Groups by entity_type + sub_account."""
        uid = user["id"]
        pipeline = [
            {"$match": {"user_id": uid, "status": "posted"}},
            {"$group": {
                "_id": {
                    "entity_type": "$entity_type",
                    "entity_id": "$entity_id",
                    "sub_account": "$sub_account",
                },
                "debits": {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "credits": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}},
                "entry_count": {"$sum": 1},
            }},
        ]
        rows: list[dict] = []
        async for r in db.general_ledger.aggregate(pipeline):
            d = float(r["debits"]); c = float(r["credits"])
            net = round(d - c, 2)
            rows.append({
                "entity_type": r["_id"]["entity_type"],
                "entity_id": r["_id"]["entity_id"],
                "sub_account": r["_id"].get("sub_account"),
                "debits": round(d, 2),
                "credits": round(c, 2),
                "net": net,
                "entry_count": r["entry_count"],
            })
        # Enrich with names
        emp_ids = {r["entity_id"] for r in rows if r["entity_type"] == "employee"}
        sup_ids = {r["entity_id"] for r in rows
                    if r["entity_type"] in ("supplier", "external_person",
                                              "ad_account", "courier")}
        bank_ids = {r["entity_id"] for r in rows if r["entity_type"] == "bank"}
        emp_map = {e["id"]: e.get("name") for e in await db.operating_salaries.find(
            {"user_id": uid, "id": {"$in": list(emp_ids)}},
            {"_id": 0, "id": 1, "name": 1}).to_list(500)}
        cp_map = {c["id"]: c.get("name") for c in await db.counterparties.find(
            {"user_id": uid, "id": {"$in": list(sup_ids)}},
            {"_id": 0, "id": 1, "name": 1}).to_list(500)}
        bank_map = {a["id"]: a.get("name") for a in await db.accounts.find(
            {"user_id": uid, "id": {"$in": list(bank_ids)}},
            {"_id": 0, "id": 1, "name": 1}).to_list(500)}
        cat_map = {c["code"]: c.get("name") for c in await db.expense_categories.find(
            {"user_id": uid}, {"_id": 0, "code": 1, "name": 1}).to_list(500)}
        for r in rows:
            name = None
            if r["entity_type"] == "employee":
                name = emp_map.get(r["entity_id"])
            elif r["entity_type"] == "bank":
                name = bank_map.get(r["entity_id"])
            elif r["entity_type"] == "expense":
                name = cat_map.get(r["entity_id"]) or r["entity_id"]
            else:
                name = cp_map.get(r["entity_id"])
            r["name"] = name or r["entity_id"]

        total_debits = round(sum(r["debits"] for r in rows), 2)
        total_credits = round(sum(r["credits"] for r in rows), 2)
        return {
            "rows": sorted(rows, key=lambda x: (
                x["entity_type"], x.get("sub_account") or "",
                x.get("name") or "")),
            "total_debits": total_debits,
            "total_credits": total_credits,
            "is_balanced": abs(total_debits - total_credits) < 0.01,
        }

    return router
