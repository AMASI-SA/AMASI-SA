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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from auth import get_current_user_from_db
from ledger_core import (
    compute_balance,
    compute_balances_bulk,
    post_txn_group,
    write_audit,
    REASON_CODES,
)
from liabilities_routes import _compute_employee_accrual
from tz_utils import riyadh_today


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Iter-203: Post-cutoff Dynamic Salary Accrual ─────────────────────
#
# After Phase-4 migration (Iter-161), salary_payable in `general_ledger`
# is FROZEN at the cutoff snapshot. New days passing don't generate
# ledger entries until someone manually posts /salary-accrual.
#
# This helper computes the "missing daily accrual" since the cutoff and
# returns the un-posted delta that should be ADDED to the displayed
# salary_payable so the Employees screen reflects today's reality.
#
# We do NOT write to the ledger here. The ledger remains the SSOT for
# what has been EXPLICITLY posted; this is a transparent display layer.
async def _post_cutoff_accrual_delta(
    db, user_id: str, emp: dict, cutoff_date_str: Optional[str],
) -> float:
    """Return the un-posted daily accrual amount for `emp` since the
    migration cutoff (or since their start_date if no cutoff exists).
    Subtracts any salary_accrual ledger credits posted after cutoff so
    we don't double-count when admins explicitly run salary-accrual.
    Always ≥ 0.
    """
    import datetime as _dt
    today = riyadh_today()
    # Total accrued from start → today (live)
    accrued_today = float(_compute_employee_accrual(
        emp, today=today)["accrued"])
    # Accrued at cutoff date (frozen snapshot)
    accrued_at_cutoff = 0.0
    if cutoff_date_str:
        try:
            cutoff_d = _dt.date.fromisoformat(cutoff_date_str)
            accrued_at_cutoff = float(_compute_employee_accrual(
                emp, today=cutoff_d)["accrued"])
        except (ValueError, TypeError):
            accrued_at_cutoff = 0.0
    raw_delta = accrued_today - accrued_at_cutoff
    if raw_delta <= 0:
        return 0.0
    # Subtract any salary_accrual credit entries posted in the ledger
    # AFTER the cutoff to avoid double-counting.
    match = {
        "user_id": user_id,
        "entity_type": "employee",
        "entity_id": emp["id"],
        "sub_account": "salary_payable",
        "entry_type": "salary_accrual",
        "side": "credit",
        "status": "posted",
    }
    if cutoff_date_str:
        # We use metadata.accrual_through_date if present, else created_at
        match["created_at"] = {"$gt": cutoff_date_str}
    agg = await db.general_ledger.aggregate([
        {"$match": match},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(1)
    already_posted = float(agg[0]["total"]) if agg else 0.0
    delta = round(raw_delta - already_posted, 2)
    return max(0.0, delta)


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
    {"code": "cod_fees",          "name": "رسوم الدفع عند الاستلام"},
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
    # Iter-188 — "Golden Rule" guard. If the employee has an open
    # salary_payable balance, granting a NEW advance is almost always
    # an accounting mistake — the merchant probably wants to settle
    # the salary, not bloat the advance account. The backend rejects
    # with 409 unless this flag is true.
    acknowledge_pending_salary: bool = False


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


class CustodyTransferIn(BaseModel):
    """Transfer open custody from one employee to another.

    Pure inter-employee movement: no bank/cash account is involved.
    Posts a balanced 2-entry txn:
        debit:  to_employee.custody     +amount
        credit: from_employee.custody   -amount
    """
    from_employee_id: str = Field(..., min_length=1)
    to_employee_id: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
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
    # Iter-250b · P1.5.y — Pay source is polymorphic. EXACTLY ONE must
    # be provided. `paid_from_account_id` = bank/cash account;
    # `custody_employee_id` = employee custody wallet.
    paid_from_account_id: Optional[str] = None
    custody_employee_id: Optional[str] = None
    payment_date: Optional[str] = None
    notes: Optional[str] = ""
    # Iter-250b · P1.5.y — Allow over-payment. When True, any amount
    # in excess of `outstanding_debt` is booked to the supplier's
    # `advance` sub-account (دفعة مقدمة للمورد). Backend SAFE-defaults
    # to True so the new behaviour ships even if the form forgets to
    # send the flag.
    allow_advance: bool = True


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


# Iter-190 — Multi-leg COD settlement with a shipping company.
# Models the four real-world scenarios captured in the merchant's
# courier statements:
#   (1) full transfer to bank                        → bank_amount only
#   (2) partial transfer + shipping cost withheld    → bank_amount + shipping_cost
#   (3) partial transfer + COD fee                   → bank_amount + cod_fee
#   (4) partial transfer + shipping + COD fee + ...  → all legs
#   (5) no bank transfer; courier keeps COD against shipping/fees
# Anything left unsettled stays on the courier's `cod_receivable`.
class CodSettleIn(BaseModel):
    bank_amount: float = Field(0, ge=0)
    bank_account_id: Optional[str] = None
    shipping_cost: float = Field(0, ge=0)
    # COD commission and its recoverable input VAT are separate legs.  The
    # provider tax invoice/statement supplies the authoritative split.
    cod_fee: float = Field(0, ge=0)
    cod_fee_vat: float = Field(0, ge=0)
    other_fees: float = Field(0, ge=0)
    other_fees_category: Optional[str] = Field(
        None, description="Required when other_fees > 0. Expense "
                          "category code (e.g. 'gateway_fees').")
    payment_date: Optional[str] = None
    notes: Optional[str] = ""

    @model_validator(mode="after")
    def _validate_cod_fee_vat(self):
        if self.cod_fee_vat > 0 and self.cod_fee <= 0:
            raise ValueError("cod_fee_vat requires a positive cod_fee")
        return self


class ExpenseRecordIn(BaseModel):
    amount: float = Field(..., gt=0)
    expense_category: str
    # Iter-250b · P1.5.q — Pay source is now polymorphic:
    #   • `paid_from_account_id`  → traditional bank / cash / payment_platform
    #   • `custody_employee_id`   → the employee's open custody (BNPL-style
    #                                wallet inside the GL itself, no bank
    #                                touched).
    # EXACTLY ONE of the two must be provided. Validation lives in the
    # route to keep error messages localised in Arabic.
    paid_from_account_id: Optional[str] = None
    custody_employee_id: Optional[str] = None
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


# Iter-250b · P1.5.q — Resolve a user's effective permission set.
# Mirrors `server._effective_perms` but lives here to avoid an import
# cycle. Owner ALWAYS has every permission. We re-import the catalogue
# lazily so a server.py reload reflects without restart.
#
# Note: `role="user"` is the role assigned to every NEW account at
# registration time (server.py:488). In practice, such a user is the
# SOLE owner of their tenant data (there is no separate "owner above
# them" in the data model). We therefore grant `role="user"` the same
# `accounting.custody.spend_any` privilege as `role="owner"`, while
# still respecting an explicit `denied_permissions` override.
def _effective_perms_for(user: dict) -> set[str]:
    from server import (
        PERMISSIONS_CATALOGUE, ROLE_DEFAULT_PERMS,
    )
    role = (user.get("role") or "viewer").lower()
    if role == "owner":
        return set(PERMISSIONS_CATALOGUE.keys())
    if role == "user":
        # Primary tenant user — treat as full-perm unless explicitly
        # denied.
        base = set(PERMISSIONS_CATALOGUE.keys())
    else:
        base = set(ROLE_DEFAULT_PERMS.get(
            role, ROLE_DEFAULT_PERMS["viewer"]))
    base |= set(user.get("extra_permissions") or [])
    base -= set(user.get("denied_permissions") or [])
    return base


# ── Iter-184 — Operation→Accounts binding enforcement ───────────────
# All operation types whose UI exposes an account picker. The keys
# MUST mirror the OP_TYPES values used by the frontend
# UnifiedEntryScreen so the same string powers both UX filtering and
# backend validation.
# Iter-246c — Added the three Iter-245 ops so they obey the same
# allow-list mechanism as legacy ops.
ACCOUNT_BOUND_OPS = (
    "advance_grant",
    "salary_settle",
    "custody_grant",
    "custody_return",
    "supplier_pay",
    "external_grant",
    "external_collect",
    "expense_record",
    "bank_transfer",
    "supplier_invoice",
    "general_expense",
    "fixed_asset",
)

# Iter-246c — Allowed withdrawal methods per op (per merchant
# settings doc).  Empty / missing list = «السماح للكل».
ALLOWED_WITHDRAWAL_METHODS = ("cash", "transfer", "pos")
WITHDRAWAL_BOUND_OPS = (
    "advance_grant",
    "salary_settle",
    "custody_grant",
    "supplier_pay",
    "external_grant",
    "expense_record",
    "supplier_invoice",
    "general_expense",
    "fixed_asset",
)


async def _enforce_withdrawal_method(
    db, *, user_id: str, op_type: str, method: str,
) -> None:
    """Raise 400 if `method` is not in the merchant's allow-list for
    `op_type`.  Mirrors `_enforce_account_binding` semantics."""
    if not op_type or not method:
        return
    s = await db.settings.find_one(
        {"user_id": user_id},
        {"_id": 0, "operation_withdrawal_methods": 1},
    )
    bindings = (s or {}).get("operation_withdrawal_methods") or {}
    allowed = bindings.get(op_type)
    if not allowed:           # empty / missing → allow all
        return
    if method not in allowed:
        raise HTTPException(
            400,
            "طريقة الدفع هذه غير مسموحة لهذه العملية وفقاً لإعدادات "
            "«ربط العمليات بالحسابات». فعّلها من «الإعدادات → "
            "إعدادات ربط العمليات بالحسابات المالية» ثم أعد المحاولة.",
        )


async def _enforce_account_binding(
    db, *, user_id: str, op_type: str, account_id: str,
) -> None:
    """Raise 400 if `account_id` is not in the merchant's allow-list for
    `op_type`. An empty / missing list is treated as «السماح للكل» —
    backwards-compatible default so legacy users see no change until
    they opt in from Settings → «ربط العمليات بالحسابات».
    """
    if not op_type or not account_id:
        return
    s = await db.settings.find_one(
        {"user_id": user_id},
        {"_id": 0, "operation_account_bindings": 1},
    )
    bindings = (s or {}).get("operation_account_bindings") or {}
    allowed = bindings.get(op_type)
    if not allowed:           # empty list / missing → allow all
        return
    if account_id not in allowed:
        raise HTTPException(
            400,
            "هذا الحساب غير مسموح لهذه العملية وفقاً لإعدادات "
            "«ربط العمليات بالحسابات». فعّله من «الإعدادات → "
            "إعدادات ربط العمليات بالحسابات المالية» ثم أعد المحاولة.",
        )


# ── Iter-185 — Account live-balance helper & insufficient-funds guard
# Iter-192 — DOUBLE-COUNTING BUG FIX. Phase 4 migration writes
# `opening_balance` ledger entries that mirror `current_balance`. The
# previous "current_balance + ledger.net_balance" calculation thus
# doubled migrated accounts. New logic:
#   • If the account has at least one `opening_balance` ledger entry
#     ⇒ ledger is the single source of truth. live = ledger.net_balance.
#   • Otherwise (legacy / pre-migration / freshly-created account)
#     ⇒ live = current_balance (= legacy account_transactions sum).
# Either way: live is computed from EXACTLY ONE source, never both.
async def _account_live_balance(
    db, *, user_id: str, account_id: str,
) -> tuple[float, dict]:
    # Iter-195 — Route through centralized resolver so that
    # BNPL SSOT (Tabby/Tamara) takes priority over the stale
    # `current_balance` field. Without this, cash-out validation
    # for BNPL wallets compared against a drifted value.
    from balance_resolver import resolve_live_balance_by_id
    res = await resolve_live_balance_by_id(
        db, user_id=user_id, account_id=account_id,
    )
    if not res:
        return 0.0, {}
    return float(res["balance"]), res.get("account") or {}


async def _ensure_opening_balance_seeded(
    db, *, user_id: str, account_id: str,
) -> None:
    """Iter-192 — lazy backfill so the ledger becomes the single source
    of truth on the first universal-accounting touch of a non-migrated
    account.

    If the account has any ledger entry already, we assume the ledger
    is authoritative (no action). Otherwise we copy its stored
    `current_balance` into a synthetic `opening_balance` debit so the
    subsequent universal op produces a consistent net balance.
    """
    has_any = await db.general_ledger.find_one(
        {"user_id": user_id, "entity_type": "bank",
         "entity_id": account_id, "status": "posted"},
        {"_id": 1},
    )
    if has_any:
        return
    acc = await db.accounts.find_one(
        {"id": account_id, "user_id": user_id},
        {"_id": 0, "current_balance": 1, "name": 1},
    )
    if not acc:
        return
    cur = float(acc.get("current_balance") or 0)
    if abs(cur) < 0.005:
        return
    from ledger_core import post_txn_group as _ptg
    await _ptg(
        db, user_id=user_id, actor_id=user_id, actor_name="auto-seed",
        txn_type="adjustment",
        notes=f"رصيد افتتاحي تلقائي عند أول قيد على «{acc.get('name')}»",
        metadata={"source": "iter192_auto_seed"},
        entries=[
            {"entity_type": "bank", "entity_id": account_id,
             "sub_account": "main", "side": "debit",
             "amount": round(cur, 2),
             "entry_type": "opening_balance"},
            {"entity_type": "equity", "entity_id": "opening_balance",
             "side": "credit", "amount": round(cur, 2),
             "entry_type": "opening_balance"},
        ],
    )


async def _enforce_sufficient_funds(
    db, *, user_id: str, account_id: str, amount: float,
) -> None:
    """Block cash-out transactions whose amount exceeds the source
    account's live balance. Raises 400 with the merchant-facing error
    message specified in the iter-185 requirement.
    """
    # Iter-192 — seed opening_balance if needed so the post-op live
    # balance reads correctly from the ledger alone.
    await _ensure_opening_balance_seeded(
        db, user_id=user_id, account_id=account_id,
    )
    live, _acc = await _account_live_balance(
        db, user_id=user_id, account_id=account_id,
    )
    if live < amount - 0.005:
        raise HTTPException(
            400,
            "لا يمكن تنفيذ العملية، رصيد الحساب المختار غير كافٍ.",
        )


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

        await _enforce_account_binding(
            db, user_id=user["id"], op_type="advance_grant",
            account_id=payload.paid_from_account_id,
        )
        await _enforce_sufficient_funds(
            db, user_id=user["id"],
            account_id=payload.paid_from_account_id,
            amount=payload.amount,
        )

        # Iter-188 — "Golden Rule": paying an employee who already has
        # an open salary_payable should be recorded as salary_settle,
        # not a new advance. We block with 409 + a structured payload
        # the UI can use to offer an in-place conversion.
        if not payload.acknowledge_pending_salary:
            pending = (await compute_balance(
                db, user_id=user["id"], entity_type="employee",
                entity_id=emp_id, sub_account="salary_payable",
            ))["outstanding_debt"]
            if pending > 0.005:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PENDING_SALARY_BLOCK",
                        "salary_payable": round(pending, 2),
                        "employee_id": emp_id,
                        "employee_name": emp.get("name"),
                        "message": (
                            f"يوجد للموظف رصيد راتب مستحق قدره "
                            f"{pending:,.2f} ر.س. يفضّل تسجيل العملية "
                            "كـ «صرف راتب» بدلاً من «سلفة موظف» — لأن "
                            "السداد مقابل راتب مستحق ليس سلفة محاسبياً. "
                            "إذا كنت متأكداً أنها سلفة حقيقية، أعد الإرسال "
                            "مع acknowledge_pending_salary=true."
                        ),
                    },
                )

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
        await _enforce_account_binding(
            db, user_id=user["id"], op_type="custody_grant",
            account_id=payload.paid_from_account_id,
        )
        await _enforce_sufficient_funds(
            db, user_id=user["id"],
            account_id=payload.paid_from_account_id,
            amount=payload.amount,
        )
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
        await _enforce_account_binding(
            db, user_id=user["id"], op_type="custody_return",
            account_id=payload.deposited_to_account_id,
        )
        # Iter-192 — seed opening_balance on inflow target so the live
        # balance keeps a single source of truth (the ledger).
        await _ensure_opening_balance_seeded(
            db, user_id=user["id"],
            account_id=payload.deposited_to_account_id,
        )
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

    # ───────────────────────────────────────────────────────────────
    # Custody transfer between two employees (pure inter-employee
    # movement; no bank/cash touched).
    # ───────────────────────────────────────────────────────────────
    @router.post("/employees/custody/transfer")
    async def transfer_custody_between_employees(
        payload: CustodyTransferIn,
        user: dict = Depends(current_user),
    ):
        if payload.from_employee_id == payload.to_employee_id:
            raise HTTPException(
                400, "لا يمكن النقل لنفس الموظف",
            )

        from_emp = await db.operating_salaries.find_one(
            {"id": payload.from_employee_id, "user_id": user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not from_emp:
            raise HTTPException(404, "الموظف المحوِّل غير موجود")
        to_emp = await db.operating_salaries.find_one(
            {"id": payload.to_employee_id, "user_id": user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not to_emp:
            raise HTTPException(404, "الموظف المستلم غير موجود")

        # Guard: cannot transfer more than the open custody balance.
        bal_from = await compute_balance(
            db, user_id=user["id"], entity_type="employee",
            entity_id=payload.from_employee_id, sub_account="custody",
        )
        if bal_from["net_balance"] < payload.amount - 0.01:
            raise HTTPException(
                400,
                f"رصيد عهدة الموظف المحوِّل ({bal_from['net_balance']:.2f}) "
                f"أقل من المبلغ المراد نقله ({payload.amount:.2f})",
            )

        actor_id, actor_name = await _resolve_actor(user)
        notes = (
            payload.notes
            or f"نقل عهدة — من {from_emp.get('name')} إلى {to_emp.get('name')}"
        )
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="custody_transfer", notes=notes,
            metadata={
                "from_employee_id": payload.from_employee_id,
                "from_employee_name": from_emp.get("name"),
                "to_employee_id": payload.to_employee_id,
                "to_employee_name": to_emp.get("name"),
                "payment_date": payload.payment_date,
            },
            entries=[
                {"entity_type": "employee",
                 "entity_id": payload.to_employee_id,
                 "sub_account": "custody", "side": "debit",
                 "amount": payload.amount, "entry_type": "custody_transfer",
                 "metadata": {"counterpart_employee_id": payload.from_employee_id,
                              "counterpart_employee_name": from_emp.get("name"),
                              "direction": "in"}},
                {"entity_type": "employee",
                 "entity_id": payload.from_employee_id,
                 "sub_account": "custody", "side": "credit",
                 "amount": payload.amount, "entry_type": "custody_transfer",
                 "metadata": {"counterpart_employee_id": payload.to_employee_id,
                              "counterpart_employee_name": to_emp.get("name"),
                              "direction": "out"}},
            ],
        )
        return {
            "ok": True, **result,
            "from_balance": await compute_balance(
                db, user_id=user["id"], entity_type="employee",
                entity_id=payload.from_employee_id, sub_account="custody"),
            "to_balance": await compute_balance(
                db, user_id=user["id"], entity_type="employee",
                entity_id=payload.to_employee_id, sub_account="custody"),
        }

    # ───────────────────────────────────────────────────────────────
    # Custody open balances report — one row per employee with
    # breakdown by entry_type. Powers the "تقرير أرصدة العهد
    # المفتوحة" page.
    # ───────────────────────────────────────────────────────────────
    @router.get("/employees/custody/open-balances")
    async def custody_open_balances(
        debug_employee_id: str | None = None,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # Aggregate sub_account=custody entries grouped by
        # (employee_id, entry_type, side). One DB roundtrip.
        # P1.5.s.fix.custody-align — Apply the SAME filters as
        # `/employees/list` so the custody column matches across
        # pages. Without these filters reversed entries +
        # legacy_orphan rows leaked into the open balance and made
        # this page report up to 2× the Employees-Ledger value.
        match_filter = {
            "user_id": uid,
            "entity_type": "employee",
            "sub_account": "custody",
            "status": "posted",
            "entry_type": {"$ne": "reversal"},
            "metadata.legacy_orphan": {"$ne": True},
        }
        pipeline = [
            {"$match": match_filter},
            {"$group": {
                "_id": {
                    "emp": "$entity_id",
                    "etype": "$entry_type",
                    "side": "$side",
                },
                "total": {"$sum": "$amount"},
            }},
        ]
        # employee_id -> bucket dict
        buckets: dict[str, dict] = {}
        async for row in db.general_ledger.aggregate(pipeline):
            emp_id = row["_id"]["emp"]
            etype = row["_id"]["etype"]
            side = row["_id"]["side"]
            amt = round(float(row["total"]), 2)
            b = buckets.setdefault(emp_id, {
                "granted": 0.0,
                "settled_receipts": 0.0,
                "returned_cash": 0.0,
                "transferred_out": 0.0,
                "transferred_in": 0.0,
                "opening": 0.0,
                "other_debit": 0.0,
                "other_credit": 0.0,
                "debits": 0.0,
                "credits": 0.0,
            })
            if side == "debit":
                b["debits"] += amt
            else:
                b["credits"] += amt
            if etype == "custody_grant":
                b["granted"] += amt
            elif etype == "custody_expense":
                b["settled_receipts"] += amt
            elif etype == "custody_return":
                b["returned_cash"] += amt
            elif etype == "custody_transfer":
                if side == "debit":
                    b["transferred_in"] += amt
                else:
                    b["transferred_out"] += amt
            elif etype == "opening_balance":
                b["opening"] += amt if side == "debit" else -amt
            else:
                if side == "debit":
                    b["other_debit"] += amt
                else:
                    b["other_credit"] += amt

        if not buckets:
            return {"rows": [], "total_open_balance": 0.0}

        emp_ids = list(buckets.keys())
        emp_docs = await db.operating_salaries.find(
            {"user_id": uid, "id": {"$in": emp_ids}},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(2000)
        name_map = {e["id"]: e.get("name") for e in emp_docs}

        # P1.5.s.fix.custody — Bulk salary_payable + advance balances
        # per employee so the Custody Open Balances page can render the
        # FULL employee picture (custody is independent, but salary
        # and advance still belong on the same screen). ONE Mongo
        # aggregation, grouped by (entity_id, sub_account, side).
        salary_net: dict[str, float] = {sid: 0.0 for sid in emp_ids}
        advance_bal: dict[str, float] = {sid: 0.0 for sid in emp_ids}
        sub_pipe = [
            {"$match": {
                "user_id": uid,
                "entity_type": "employee",
                "entity_id": {"$in": emp_ids},
                "sub_account": {"$in": ["salary_payable", "advance"]},
                "status": "posted",
                # P1.5.s.fix.custody-align — same filter as
                # /employees/list to keep both screens in sync.
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
            }},
            {"$group": {
                "_id": {
                    "emp":  "$entity_id",
                    "sub":  "$sub_account",
                    "side": "$side",
                },
                "total": {"$sum": "$amount"},
            }},
        ]
        async for row in db.general_ledger.aggregate(sub_pipe):
            emp = row["_id"]["emp"]
            sub = row["_id"]["sub"]
            side = row["_id"]["side"]
            amt = float(row["total"] or 0)
            if sub == "salary_payable":
                # Liability: credit increases (owed to employee),
                # debit decreases (settled by salary_payment).
                if side == "credit":
                    salary_net[emp] = salary_net.get(emp, 0.0) + amt
                else:
                    salary_net[emp] = salary_net.get(emp, 0.0) - amt
            else:  # advance
                # Asset/Receivable: debit increases (we paid forward),
                # credit decreases (employee settled).
                if side == "debit":
                    advance_bal[emp] = advance_bal.get(emp, 0.0) + amt
                else:
                    advance_bal[emp] = advance_bal.get(emp, 0.0) - amt

        # P1.5.s.fix.activity — Last activity date per employee
        # (across ANY sub_account so we know when we last touched
        # them). One aggregation. Same filters as the balance pipes.
        last_activity: dict[str, str] = {}
        async for row in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid,
                "entity_type": "employee",
                "entity_id": {"$in": emp_ids},
                "status": "posted",
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
            }},
            {"$group": {
                "_id": "$entity_id",
                "last_dt": {"$max": {
                    "$ifNull": ["$txn_date", "$created_at"]}},
            }},
        ]):
            last_activity[row["_id"]] = row.get("last_dt")

        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)

        def _days_since(iso) -> int | None:
            if not iso:
                return None
            try:
                s = str(iso)
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
                return (now_utc - dt).days
            except Exception:
                return None

        rows = []
        for emp_id, b in buckets.items():
            open_balance = round(b["debits"] - b["credits"], 2)
            sp = round(max(0.0, salary_net.get(emp_id, 0.0)), 2)
            adv = round(max(0.0, advance_bal.get(emp_id, 0.0)), 2)
            # Net to employee (custody EXCLUDED per business rule).
            net = round(sp - adv, 2)
            if   net > 0.01:
                net_status = "owed_to_employee"   # له علينا
            elif net < -0.01:
                net_status = "owed_by_employee"   # عليه للنظام
            else:
                net_status = "balanced"
            last_dt = last_activity.get(emp_id)
            days = _days_since(last_dt)
            is_active = (days is not None and days <= 90)
            rows.append({
                "employee_id": emp_id,
                "name": name_map.get(emp_id) or "(موظف محذوف)",
                "granted":            round(b["granted"], 2),
                "settled_receipts":   round(b["settled_receipts"], 2),
                "returned_cash":      round(b["returned_cash"], 2),
                "transferred_out":    round(b["transferred_out"], 2),
                "transferred_in":     round(b["transferred_in"], 2),
                "opening":            round(b["opening"], 2),
                "other_debit":        round(b["other_debit"], 2),
                "other_credit":       round(b["other_credit"], 2),
                "open_balance":       open_balance,
                # P1.5.s.fix.custody — Salary/advance/net side panel.
                "salary_owed":        sp,
                "advance_open":       adv,
                "net_to_employee":    net,
                "net_status":         net_status,
                # P1.5.s.fix.activity — last activity meta.
                "last_activity":      last_dt,
                "days_since_last_activity": days,
                "is_active":          is_active,
            })
        rows.sort(key=lambda r: (-r["open_balance"], r["name"] or ""))
        total_open = round(sum(r["open_balance"] for r in rows), 2)

        # P1.5.s.fix.custody-align — Optional per-employee debug.
        # READ-ONLY: dumps every custody entry for `debug_employee_id`
        # with its `entry_type`, `side`, `amount`, `txn_date`,
        # `txn_group_id`, and the filters that include/exclude it.
        debug_block = None
        if debug_employee_id:
            emp_entries = []
            included_dr = 0.0
            included_cr = 0.0
            excluded_dr = 0.0
            excluded_cr = 0.0
            async for e in db.general_ledger.find(
                {"user_id": uid,
                 "entity_type": "employee",
                 "sub_account": "custody",
                 "entity_id": debug_employee_id},
                {"_id": 0, "id": 1, "side": 1, "amount": 1,
                 "entry_type": 1, "status": 1, "txn_date": 1,
                 "created_at": 1, "txn_group_id": 1, "metadata": 1,
                 "notes": 1},
            ).sort([("txn_date", 1), ("created_at", 1)]):
                meta = e.get("metadata") or {}
                is_legacy_orphan = bool(meta.get("legacy_orphan"))
                is_reversal = (e.get("entry_type") == "reversal")
                is_unposted = (e.get("status") != "posted")
                excluded_reasons = []
                if is_unposted:
                    excluded_reasons.append("not_posted")
                if is_reversal:
                    excluded_reasons.append("reversal")
                if is_legacy_orphan:
                    excluded_reasons.append("legacy_orphan")
                included = not excluded_reasons
                amt = round(float(e.get("amount") or 0), 2)
                if included:
                    if e.get("side") == "debit":
                        included_dr += amt
                    else:
                        included_cr += amt
                else:
                    if e.get("side") == "debit":
                        excluded_dr += amt
                    else:
                        excluded_cr += amt
                emp_entries.append({
                    "gl_id":         e.get("id"),
                    "txn_date":      (e.get("txn_date")
                                       or e.get("created_at")),
                    "entry_type":    e.get("entry_type"),
                    "side":          e.get("side"),
                    "amount":        amt,
                    "status":        e.get("status"),
                    "txn_group_id":  e.get("txn_group_id"),
                    "notes":         e.get("notes"),
                    "legacy_orphan": is_legacy_orphan,
                    "included_in_balance": included,
                    "excluded_reasons":    excluded_reasons,
                })
            included_balance = round(included_dr - included_cr, 2)
            excluded_balance = round(excluded_dr - excluded_cr, 2)
            debug_block = {
                "employee_id": debug_employee_id,
                "total_entries": len(emp_entries),
                "included_debits":  round(included_dr, 2),
                "included_credits": round(included_cr, 2),
                "included_balance_open": included_balance,
                "excluded_debits":  round(excluded_dr, 2),
                "excluded_credits": round(excluded_cr, 2),
                "excluded_balance": excluded_balance,
                "raw_unfiltered_balance": round(
                    included_balance + excluded_balance, 2),
                "filter_applied": (
                    "status==posted AND entry_type!=reversal "
                    "AND metadata.legacy_orphan!=true"),
                "matches_employees_list_endpoint": True,
                "entries": emp_entries,
            }

        return {
            "rows": rows,
            "total_open_balance": total_open,
            # Roll-ups for the new salary side cards.
            "total_salary_owed":     round(
                sum(r["salary_owed"]     for r in rows), 2),
            "total_advance_open":    round(
                sum(r["advance_open"]    for r in rows), 2),
            "total_net_to_employee": round(
                sum(r["net_to_employee"] for r in rows), 2),
            "_debug": debug_block,
        }

    # ───────────────────────────────────────────────────────────────
    # Iter-250b · P1.5.q — Spendable custody sources.
    #
    # Used by the new "Pay from custody" toggle in the Operating
    # Expenses entry form. Returns ONLY employees the caller may
    # legitimately spend from, with their CURRENT open custody
    # balance from `general_ledger` (SSOT).
    #
    # Authorisation:
    #   • Caller with `accounting.custody.spend_any` perm  → ALL
    #     employees with custody > 0.
    #   • Otherwise  → only `user.linked_employee_id` (if set AND
    #     has custody > 0).
    # ───────────────────────────────────────────────────────────────
    @router.get("/custody/spendable-sources")
    async def custody_spendable_sources(
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        perms = _effective_perms_for(user)
        can_any = "accounting.custody.spend_any" in perms
        linked_emp = (user.get("linked_employee_id") or "")

        # Aggregate the SSOT custody balance per employee in one
        # roundtrip.
        pipeline = [
            {"$match": {
                "user_id": uid,
                "entity_type": "employee",
                "sub_account": "custody",
                "status": "posted",
            }},
            {"$group": {
                "_id": "$entity_id",
                "debits":  {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "credits": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}},
            }},
        ]
        balances: dict[str, float] = {}
        async for row in db.general_ledger.aggregate(pipeline):
            balances[row["_id"]] = round(
                float(row["debits"]) - float(row["credits"]), 2)

        positive = {
            eid: bal for eid, bal in balances.items() if bal > 0.001
        }
        if not positive:
            return {
                "can_spend_any": can_any,
                "linked_employee_id": linked_emp or None,
                "sources": [],
            }

        # Resolve names from operating_salaries (modern) → employees
        # (legacy). Skip archived/deleted.
        emp_ids = list(positive.keys())
        name_map: dict[str, str] = {}
        async for e in db.operating_salaries.find(
            {"user_id": uid, "id": {"$in": emp_ids},
             "archived": {"$ne": True}, "is_archived": {"$ne": True},
             "deleted":  {"$ne": True}, "is_deleted":  {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1},
        ):
            name_map[e["id"]] = e.get("name") or "(بدون اسم)"
        missing = [e for e in emp_ids if e not in name_map]
        if missing:
            async for e in db.employees.find(
                {"user_id": uid, "id": {"$in": missing},
                 "archived": {"$ne": True}, "is_archived": {"$ne": True},
                 "deleted":  {"$ne": True}, "is_deleted":  {"$ne": True}},
                {"_id": 0, "id": 1, "name": 1},
            ):
                name_map[e["id"]] = e.get("name") or "(بدون اسم)"

        # Apply the per-user filter.
        rows: list[dict] = []
        for eid, bal in positive.items():
            if eid not in name_map:
                # archived/deleted/legacy-orphan — never spendable
                continue
            if not can_any and eid != linked_emp:
                continue
            rows.append({
                "employee_id": eid,
                "name": name_map[eid],
                "available_balance": bal,
            })
        rows.sort(key=lambda r: (-r["available_balance"], r["name"]))
        return {
            "can_spend_any": can_any,
            "linked_employee_id": linked_emp or None,
            "sources": rows,
        }

    # ───────────────────────────────────────────────────────────────
    # Iter-185 — Cash accounts with LIVE balance for UI freeze logic.
    # Returns every account the merchant can fund operations from
    # (bank / cash / payment_platform / courier) along with its true
    # live balance computed from BOTH the legacy account_transactions
    # ledger AND the universal general_ledger. Used by
    # UnifiedEntryScreen to freeze options whose balance < amount.
    # ───────────────────────────────────────────────────────────────
    @router.get("/cash-accounts-with-balances")
    async def cash_accounts_with_balances(
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        docs = await db.accounts.find(
            {"user_id": uid,
             "account_type": {"$in": [
                 "bank", "cash", "payment_platform", "courier",
             ]},
             "status": {"$ne": "hidden"}},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1,
             "current_balance": 1, "opening_balance": 1,
             "provider_name": 1,
             "normalized_payment_method": 1},
        ).sort("name", 1).to_list(2000)

        # Iter-250b · P1.5.h — Unify the live-balance source with the
        # `/accounts` page so dropdowns in `/new-transaction` show the
        # SAME number the merchant sees on the assets-and-accounts
        # page. Previously this endpoint had its own inline logic
        # (`migrated_ids` check → ledger_net vs current_balance) which
        # diverged from `_account_with_meta` once `account_balance_ssot`
        # gained the Iter-240 hybrid (`ledger_net + current_balance -
        # dw_net`). The hybrid is required for accounts whose history
        # lives in BOTH `account_transactions` (legacy) AND
        # `general_ledger` (new) — like Salla after a manual transfer:
        # ledger reflects the transfer but current_balance is not
        # auto-recomputed by `/accounting/bank-transfer`, so the old
        # logic returned the pre-transfer stored value.
        from financial_position_ssot import account_balance_ssot
        out = []
        for d in docs:
            base = float(d.get("current_balance") or 0)
            try:
                live = float(
                    await account_balance_ssot(
                        db, user_id=uid, account=d,
                    )
                )
                source = "ssot"
            except Exception:  # noqa: BLE001
                # Defensive: if the SSOT helper throws, fall back to
                # the stored value so the page doesn't break.
                live = base
                source = "current_balance_fallback"
            out.append({
                "id": d["id"],
                "name": d.get("name"),
                "account_type": d.get("account_type"),
                "provider_name": d.get("provider_name"),
                "current_balance": round(base, 2),
                "live_balance": round(live, 2),
                "balance_source": source,
            })
        return {"accounts": out}

    # ───────────────────────────────────────────────────────────────
    # Iter-185 — Single-employee net summary card. Returns ONE number
    # that the UI shows next to the picked employee:
    #   net_due_to_employee = salary_payable_outstanding
    #                         − advance_net
    #                         − custody_net
    # Positive ⇒ company OWES the employee (شركة عليها → عرض أخضر).
    # Negative ⇒ employee owes the company (موظف عليه → عرض أحمر).
    # ───────────────────────────────────────────────────────────────
    @router.get("/employees/{emp_id}/summary-balance")
    async def employee_summary_balance(
        emp_id: str,
        user: dict = Depends(current_user),
    ):
        emp = await db.operating_salaries.find_one(
            {"id": emp_id, "user_id": user["id"]},
            {"_id": 0},
        )
        if not emp:
            raise HTTPException(404, "الموظف غير موجود")
        uid = user["id"]
        payable = await compute_balance(
            db, user_id=uid, entity_type="employee",
            entity_id=emp_id, sub_account="salary_payable")
        advance = await compute_balance(
            db, user_id=uid, entity_type="employee",
            entity_id=emp_id, sub_account="advance")
        custody = await compute_balance(
            db, user_id=uid, entity_type="employee",
            entity_id=emp_id, sub_account="custody")
        # Iter-203 — apply the same un-posted daily accrual delta the
        # Employees list & financial-summary endpoints add, so the
        # "Add Transaction" screen shows TODAY's reality (not the
        # frozen migration snapshot).
        cutoff_doc = await db.migration_cutoffs.find_one(
            {"user_id": uid}, {"_id": 0, "cutoff_date": 1},
        )
        cutoff_date_str = (cutoff_doc or {}).get("cutoff_date")
        pending_accrual = await _post_cutoff_accrual_delta(
            db, uid, emp, cutoff_date_str)
        ledger_outstanding = float(payable["outstanding_debt"])
        outstanding_with_pending = round(
            ledger_outstanding + pending_accrual, 2)
        # Iter-250b · P1.5.u — Custody is NO LONGER auto-deducted
        # from the employee's net. By policy, custody is an
        # independent open-balance card; offsetting it against
        # salary requires an explicit accounting operation
        # ("custody → salary deduction" or "custody settlement").
        # Only ADVANCES still net against salary automatically.
        net_due = round(
            outstanding_with_pending
            - advance["net_balance"],
            2,
        )
        return {
            "employee_id": emp_id,
            "name": emp.get("name"),
            "net_due_to_employee": net_due,
            "salary_payable": outstanding_with_pending,
            "salary_payable_ledger": round(ledger_outstanding, 2),
            "pending_accrual": round(pending_accrual, 2),
            "advance_open": advance["net_balance"],
            "custody_open": custody["net_balance"],
        }

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

        await _enforce_account_binding(
            db, user_id=user["id"], op_type="salary_settle",
            account_id=payload.paid_from_account_id,
        )
        await _enforce_sufficient_funds(
            db, user_id=user["id"],
            account_id=payload.paid_from_account_id,
            amount=payload.amount,
        )

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
        uid = user["id"]
        cp = await db.counterparties.find_one(
            {"id": supplier_id, "user_id": uid, "kind": "supplier"},
            {"_id": 0, "name": 1},
        )
        # Iter-246g — Fallback to Iter-244 db.suppliers for entries
        # that pre-date the counterparties-bridge.  We also lazily
        # bridge them so subsequent reads find them directly.
        if not cp:
            s244 = await db.suppliers.find_one(
                {"id": supplier_id, "user_id": uid},
                {"_id": 0, "company_name": 1, "contact_person": 1,
                 "phone": 1, "email": 1, "status": 1},
            )
            if s244:
                name = s244.get("company_name")
                await db.counterparties.update_one(
                    {"id": supplier_id, "user_id": uid},
                    {"$setOnInsert": {
                        "id": supplier_id, "user_id": uid,
                        "kind": "supplier",
                        "created_at": _now(),
                    },
                     "$set": {
                        "name": name,
                        "name_lower": (name or "").strip().lower(),
                        "contact_person": s244.get("contact_person"),
                        "phone": s244.get("phone"),
                        "email": s244.get("email"),
                        "status": s244.get("status", "active"),
                        "updated_at": _now(),
                    }},
                    upsert=True,
                )
                cp = {"name": name}
        if not cp:
            raise HTTPException(404, "المورد غير موجود")

        # Iter-250b · P1.5.y — Reworked payment flow.
        #   • Allow over-payment: excess routes to `supplier.advance`
        #     (دفعة مقدمة) instead of being rejected.
        #   • Allow zero outstanding debt: full payment becomes a
        #     pre-advance against future invoices.
        #   • Allow paying from an employee CUSTODY wallet instead of
        #     a bank/cash account.
        sup_bal = await compute_balance(
            db, user_id=uid, entity_type="supplier",
            entity_id=supplier_id, sub_account="payable")
        outstanding = float(sup_bal.get("outstanding_debt") or 0.0)
        if outstanding < 0:
            outstanding = 0.0  # never go below zero in the split
        amount = round(float(payload.amount), 2)
        # Split: how much settles existing debt vs how much pre-pays.
        pay_to_debt   = round(min(amount, outstanding), 2)
        pay_to_advance = round(amount - pay_to_debt, 2)
        if pay_to_advance > 0 and not payload.allow_advance:
            raise HTTPException(
                400,
                f"مبلغ السداد ({amount:.2f}) أكبر من الرصيد المستحق "
                f"({outstanding:.2f} ر.س) وتم رفض الدفعة المقدمة.",
            )

        # ── Resolve the funding source ────────────────────────────
        has_bank    = bool(payload.paid_from_account_id)
        has_custody = bool(payload.custody_employee_id)
        if has_bank == has_custody:
            raise HTTPException(
                400,
                "يجب اختيار مصدر سداد واحد فقط: حساب بنكي/صندوق أو "
                "عهدة موظف.",
            )

        actor_id, actor_name = await _resolve_actor(user)
        credit_leg = None
        custody_emp_doc = None
        custody_avail = 0.0

        if has_bank:
            acc = await db.accounts.find_one(
                {"id": payload.paid_from_account_id,
                 "user_id": user["id"]},
                {"_id": 0, "name": 1},
            )
            if not acc:
                raise HTTPException(404, "الحساب البنكي غير موجود")
            await _enforce_account_binding(
                db, user_id=user["id"], op_type="supplier_pay",
                account_id=payload.paid_from_account_id,
            )
            await _enforce_sufficient_funds(
                db, user_id=user["id"],
                account_id=payload.paid_from_account_id,
                amount=amount,
            )
            credit_leg = {
                "entity_type": "bank",
                "entity_id":   payload.paid_from_account_id,
                "sub_account": "main",
                "side":        "credit",
                "amount":      amount,
                "entry_type":  "supplier_payment",
            }
        else:
            # Pay from employee custody — replicate the P1.5.q rules.
            emp_id = payload.custody_employee_id
            emp_query = {
                "user_id": uid,
                "$or": [
                    {"id": emp_id}, {"employee_id": emp_id},
                    {"external_id": emp_id}, {"legacy_id": emp_id},
                ],
                "archived":    {"$ne": True},
                "is_archived": {"$ne": True},
                "deleted":     {"$ne": True},
                "is_deleted":  {"$ne": True},
            }
            custody_emp_doc = (
                await db.operating_salaries.find_one(
                    emp_query, {"_id": 0, "id": 1, "name": 1})
                or await db.employees.find_one(
                    emp_query, {"_id": 0, "id": 1, "name": 1})
            )
            if not custody_emp_doc:
                raise HTTPException(
                    404, "الموظف غير موجود أو غير نشط.")
            perms = _effective_perms_for(user)
            if "accounting.custody.spend_any" not in perms:
                linked_emp = (user.get("linked_employee_id") or "")
                if not linked_emp or linked_emp != emp_id:
                    raise HTTPException(
                        403, "لا يمكنك الصرف من عهدة موظف آخر.")
            cust = await compute_balance(
                db, user_id=uid, entity_type="employee",
                entity_id=emp_id, sub_account="custody",
            )
            custody_avail = float(cust.get("net_balance") or 0)
            if amount > custody_avail + 0.001:
                raise HTTPException(
                    400,
                    f"رصيد العهدة غير كافٍ. "
                    f"المتاح: {custody_avail:.2f} ر.س، "
                    f"المطلوب: {amount:.2f} ر.س.",
                )
            credit_leg = {
                "entity_type": "employee",
                "entity_id":   emp_id,
                "sub_account": "custody",
                "side":        "credit",
                "amount":      amount,
                "entry_type":  "supplier_payment",
            }

        # ── Build the debit legs ──────────────────────────────────
        debit_legs = []
        if pay_to_debt > 0:
            debit_legs.append({
                "entity_type": "supplier", "entity_id": supplier_id,
                "sub_account": "payable", "side": "debit",
                "amount":      pay_to_debt,
                "entry_type":  "supplier_payment",
            })
        if pay_to_advance > 0:
            debit_legs.append({
                "entity_type": "supplier", "entity_id": supplier_id,
                "sub_account": "advance", "side": "debit",
                "amount":      pay_to_advance,
                # Re-use the standard `supplier_payment` entry_type so
                # the GL validator (Iter-226) accepts the row; the
                # `sub_account=advance` is the discriminator that
                # downstream reports / detail-page filters use.
                "entry_type":  "supplier_payment",
            })

        # ── Metadata for the timeline + employee ledger replays ───
        extra_meta = {
            "supplier_name": cp.get("name"),
            "payment_date":  payload.payment_date,
            "pay_to_debt":     pay_to_debt,
            "pay_to_advance":  pay_to_advance,
            "outstanding_before": round(outstanding, 2),
            "pay_source": "custody" if has_custody else "bank",
        }
        if has_custody:
            extra_meta.update({
                "custody_employee_id":   payload.custody_employee_id,
                "custody_employee_name": custody_emp_doc.get("name"),
                "custody_balance_before": round(custody_avail, 2),
                "custody_balance_after":  round(
                    custody_avail - amount, 2),
            })

        notes = payload.notes or (
            f"سداد مورد — {cp.get('name')}"
            + (f" (دفعة مقدمة {pay_to_advance:.2f})"
                if pay_to_advance > 0 else "")
        )
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id,
            actor_name=actor_name,
            txn_type="supplier_payment",
            notes=notes, metadata=extra_meta,
            entries=debit_legs + [credit_leg],
        )
        return {
            "ok": True,
            **result,
            "split": {
                "paid_to_debt":     pay_to_debt,
                "paid_to_advance":  pay_to_advance,
                "outstanding_before": round(outstanding, 2),
            },
            "balance": await compute_balance(
                db, user_id=user["id"], entity_type="supplier",
                entity_id=supplier_id, sub_account="payable"),
            "advance_balance": await compute_balance(
                db, user_id=user["id"], entity_type="supplier",
                entity_id=supplier_id, sub_account="advance"),
        }

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
        await _enforce_account_binding(
            db, user_id=user["id"], op_type="external_grant",
            account_id=payload.paid_from_account_id,
        )
        await _enforce_sufficient_funds(
            db, user_id=user["id"],
            account_id=payload.paid_from_account_id,
            amount=payload.amount,
        )
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
        await _enforce_account_binding(
            db, user_id=user["id"], op_type="external_collect",
            account_id=payload.deposited_to_account_id,
        )
        await _ensure_opening_balance_seeded(
            db, user_id=user["id"],
            account_id=payload.deposited_to_account_id,
        )
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

    # Iter-250b · P1.5.j — Helper: is this account eligible for an
    # internal transfer between accounts?
    #
    # ALLOWED: bank · cash · payment_platform EXCEPT:
    #   - COD (الدفع عند الاستلام)        → settled by courier-side flow
    #   - bank_transfer (تحويل بنكي)       → not a real wallet
    # REJECTED everywhere else (courier, ads, suppliers, employees, …)
    def _account_blocks_internal_transfer(acc: dict):
        """Return (blocked: bool, reason: str)."""
        atype = (acc.get("account_type") or "").strip()
        if atype not in ("bank", "cash", "payment_platform"):
            return True, (
                "هذا الحساب ليس حساباً قابلاً للتحويل الداخلي. "
                "استخدم المسار المحاسبي المخصص له."
            )
        # Block COD / bank_transfer payment_platforms by canonical key
        # or by Arabic provider name (data may have either).
        npm = (acc.get("normalized_payment_method") or "").lower()
        pname_raw = acc.get("provider_name") or acc.get("name") or ""
        pname = pname_raw.lower()
        cod_patterns = [
            "cod", "cash_on_delivery", "الدفع عند الاستلام",
        ]
        bank_transfer_patterns = [
            "bank_transfer", "تحويل بنكي",
        ]
        for p in cod_patterns:
            if p in npm or p in pname_raw:
                return True, (
                    "هذا الحساب ليس حساباً قابلاً للتحويل الداخلي. "
                    "استخدم المسار المحاسبي المخصص له."
                )
        for p in bank_transfer_patterns:
            if p in npm or p in pname_raw:
                return True, (
                    "هذا الحساب ليس حساباً قابلاً للتحويل الداخلي. "
                    "استخدم المسار المحاسبي المخصص له."
                )
        # Iter-250b · P1.5.L — Block Tamara/Tabby BNPL wallets from
        # internal transfers. Direct bank/cash → BNPL transfers
        # corrupt the canonical BNPL settlement bridge. The official
        # BNPL Settlements screen is the only valid path for these
        # wallets. Salla is NOT blocked — only Tamara/Tabby/bnpl.
        bnpl_patterns = ["tamara", "tabby", "bnpl", "تمارا", "تابي"]
        for p in bnpl_patterns:
            if p in npm or p in pname:
                return True, (
                    "لا يمكن تسجيل تحويل مباشر على حسابات Tamara/Tabby. "
                    "استخدم شاشة تسويات BNPL الرسمية."
                )
        return False, ""

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
            {"_id": 0, "id": 1, "name": 1, "account_type": 1,
             "provider_name": 1, "normalized_payment_method": 1},
        ).to_list(2)
        if len(accs) != 2:
            raise HTTPException(404, "أحد الحسابات البنكية غير موجود")
        # Iter-250b · P1.5.j — Validate both accounts are
        # internal-transfer-eligible BEFORE binding/sufficient-funds.
        for a in accs:
            blocked, reason = _account_blocks_internal_transfer(a)
            if blocked:
                raise HTTPException(400, reason)
        await _enforce_account_binding(
            db, user_id=user["id"], op_type="bank_transfer",
            account_id=payload.from_account_id,
        )
        await _enforce_account_binding(
            db, user_id=user["id"], op_type="bank_transfer",
            account_id=payload.to_account_id,
        )
        await _enforce_sufficient_funds(
            db, user_id=user["id"],
            account_id=payload.from_account_id,
            amount=payload.amount,
        )
        # Iter-192 — seed destination account too (inflow side).
        await _ensure_opening_balance_seeded(
            db, user_id=user["id"],
            account_id=payload.to_account_id,
        )
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

        # Iter-250b · P1.5.q — Resolve the payment SOURCE.
        # EXACTLY ONE of {paid_from_account_id, custody_employee_id} is
        # required.
        has_bank = bool(payload.paid_from_account_id)
        has_custody = bool(payload.custody_employee_id)
        if has_bank == has_custody:   # both or neither
            raise HTTPException(
                400,
                "يجب اختيار مصدر سداد واحد فقط: حساب بنكي/صندوق أو "
                "عهدة موظف.",
            )

        actor_id, actor_name = await _resolve_actor(user)

        # ── Branch A: traditional bank / cash payment ──────────────
        if has_bank:
            acc = await db.accounts.find_one(
                {"id": payload.paid_from_account_id,
                 "user_id": user["id"]},
                {"_id": 0, "name": 1},
            )
            if not acc:
                raise HTTPException(404, "الحساب البنكي غير موجود")
            await _enforce_account_binding(
                db, user_id=user["id"], op_type="expense_record",
                account_id=payload.paid_from_account_id,
            )
            await _enforce_sufficient_funds(
                db, user_id=user["id"],
                account_id=payload.paid_from_account_id,
                amount=payload.amount,
            )
            credit_leg = {
                "entity_type": "bank",
                "entity_id": payload.paid_from_account_id,
                "sub_account": "main", "side": "credit",
                "amount": payload.amount,
                "entry_type": "expense_record",
            }
            extra_meta = {}

        # ── Branch B: pay from employee custody ────────────────────
        else:
            emp_id = payload.custody_employee_id
            # (1) Employee must exist & not be archived/deleted, in
            #     either modern or legacy collection.
            emp_query = {
                "user_id": user["id"],
                "$or": [
                    {"id": emp_id}, {"employee_id": emp_id},
                    {"external_id": emp_id}, {"legacy_id": emp_id},
                ],
                "archived":    {"$ne": True},
                "is_archived": {"$ne": True},
                "deleted":     {"$ne": True},
                "is_deleted":  {"$ne": True},
            }
            emp_doc = (
                await db.operating_salaries.find_one(
                    emp_query, {"_id": 0, "id": 1, "name": 1, "status": 1})
                or await db.employees.find_one(
                    emp_query, {"_id": 0, "id": 1, "name": 1, "status": 1})
            )
            if not emp_doc:
                raise HTTPException(
                    404,
                    "الموظف غير موجود أو غير نشط.",
                )

            # (2) Authorisation:
            #     • Users with `accounting.custody.spend_any` (owner /
            #       admin / accountant by default) may spend from ANY
            #       employee.
            #     • Otherwise, the employee MUST equal the caller's own
            #       `users.linked_employee_id`.
            perms = _effective_perms_for(user)
            if "accounting.custody.spend_any" not in perms:
                linked_emp = (user.get("linked_employee_id") or "")
                if not linked_emp or linked_emp != emp_id:
                    raise HTTPException(
                        403,
                        "لا يمكنك الصرف من عهدة موظف آخر.",
                    )

            # (3) Custody balance must cover the amount.
            cust = await compute_balance(
                db, user_id=user["id"],
                entity_type="employee",
                entity_id=emp_id,
                sub_account="custody",
            )
            available = float(cust.get("net_balance") or 0)
            if payload.amount > available + 0.001:
                raise HTTPException(
                    400,
                    f"رصيد العهدة غير كافٍ. "
                    f"المتاح: {available:.2f} ر.س، "
                    f"المطلوب: {payload.amount:.2f} ر.س.",
                )

            credit_leg = {
                "entity_type": "employee",
                "entity_id": emp_id,
                "sub_account": "custody",
                "side": "credit",
                "amount": payload.amount,
                "entry_type": "expense_record",
            }
            extra_meta = {
                "pay_source": "custody",
                "custody_employee_id": emp_id,
                "custody_employee_name": emp_doc.get("name"),
                "custody_balance_before": round(available, 2),
                "custody_balance_after": round(
                    available - payload.amount, 2),
            }

        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id,
            actor_name=actor_name,
            txn_type="expense_record",
            notes=payload.notes or f"مصروف — {cat.get('name')}",
            metadata={"category_name": cat.get("name"),
                       "payment_date": payload.payment_date,
                       "related_entity_type": payload.related_entity_type,
                       "related_entity_id": payload.related_entity_id,
                       **extra_meta},
            entries=[
                {"entity_type": "expense",
                 "entity_id": payload.expense_category,
                 "side": "debit", "amount": payload.amount,
                 "entry_type": "expense_record"},
                credit_leg,
            ],
        )
        return {"ok": True, **result}

    # ═══════════════════════════════════════════════════════════════
    # Couriers / Shipping companies (same shape as suppliers)
    # ═══════════════════════════════════════════════════════════════
    @router.post("/couriers/{courier_id}/charge")
    async def courier_charge(
        courier_id: str, payload: SupplierInvoiceIn,
        user: dict = Depends(current_user),
    ):
        """Post a shipping-charge owed to a courier (e.g. monthly invoice
        from SMSA / iMile). Sub_account=payable (we owe them)."""
        cp = await db.counterparties.find_one(
            {"id": courier_id, "user_id": user["id"], "kind": "courier"},
            {"_id": 0, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "شركة الشحن غير موجودة")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="courier_charge",
            notes=payload.notes or f"رسوم شحن — {cp.get('name')}",
            metadata={"courier_name": cp.get("name"),
                       "invoice_no": payload.invoice_no,
                       "invoice_date": payload.invoice_date},
            entries=[
                {"entity_type": "expense",
                 "entity_id": payload.expense_category or "shipping",
                 "side": "debit", "amount": payload.amount,
                 "entry_type": "supplier_invoice"},
                {"entity_type": "courier", "entity_id": courier_id,
                 "sub_account": "payable", "side": "credit",
                 "amount": payload.amount,
                 "entry_type": "supplier_invoice"},
            ],
        )
        return {"ok": True, **result,
                "balance": await compute_balance(
                    db, user_id=user["id"], entity_type="courier",
                    entity_id=courier_id, sub_account="payable")}

    @router.post("/couriers/{courier_id}/pay")
    async def courier_pay(
        courier_id: str, payload: SupplierPaymentIn,
        user: dict = Depends(current_user),
    ):
        cp = await db.counterparties.find_one(
            {"id": courier_id, "user_id": user["id"], "kind": "courier"},
            {"_id": 0, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "شركة الشحن غير موجودة")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="courier_payment",
            notes=payload.notes or f"سداد شركة شحن — {cp.get('name')}",
            metadata={"courier_name": cp.get("name"),
                       "payment_date": payload.payment_date},
            entries=[
                {"entity_type": "courier", "entity_id": courier_id,
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
                    db, user_id=user["id"], entity_type="courier",
                    entity_id=courier_id, sub_account="payable")}

    @router.post("/couriers/{courier_id}/cod-deposit")
    async def courier_cod_deposit(
        courier_id: str, payload: SupplierPaymentIn,
        user: dict = Depends(current_user),
    ):
        """The courier has collected COD from buyers and deposits to
        our bank. This INCREASES our bank and DECREASES what the courier
        owes us (i.e. raises sub_account=receivable balance toward 0)."""
        cp = await db.counterparties.find_one(
            {"id": courier_id, "user_id": user["id"], "kind": "courier"},
            {"_id": 0, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "شركة الشحن غير موجودة")
        acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1},
        )
        if not acc:
            raise HTTPException(404, "الحساب البنكي غير موجود")
        # Iter-192 — seed opening_balance on first ledger touch.
        await _ensure_opening_balance_seeded(
            db, user_id=user["id"],
            account_id=payload.paid_from_account_id,
        )
        actor_id, actor_name = await _resolve_actor(user)
        result = await post_txn_group(
            db, user_id=user["id"], actor_id=actor_id, actor_name=actor_name,
            txn_type="courier_cod_deposit",
            notes=payload.notes or f"إيداع COD — {cp.get('name')}",
            metadata={"courier_name": cp.get("name"),
                       "payment_date": payload.payment_date},
            entries=[
                {"entity_type": "bank",
                 "entity_id": payload.paid_from_account_id,
                 "sub_account": "main", "side": "debit",
                 "amount": payload.amount,
                 "entry_type": "receivable_collection"},
                {"entity_type": "courier", "entity_id": courier_id,
                 "sub_account": "cod_receivable", "side": "credit",
                 "amount": payload.amount,
                 "entry_type": "receivable_collection"},
            ],
        )
        return {"ok": True, **result,
                "cod_balance": await compute_balance(
                    db, user_id=user["id"], entity_type="courier",
                    entity_id=courier_id, sub_account="cod_receivable")}

    # ───────────────────────────────────────────────────────────────
    # Iter-190 / MZ2-FIN-CUTOVER-001 — Multi-leg COD settlement with a
    # shipping company. The bank leg may be zero when the courier nets the
    # whole collection against shipping/fees.
    # Replaces the simple `cod-deposit` for non-trivial real-world
    # settlements (partial transfer + withheld shipping + COD fee +
    # other fees). Posts ONE balanced txn_group:
    #   debit:  bank/cash  (if any)
    #   debit:  expense:shipping   (if any)
    #   debit:  expense:cod_fees   (if any)
    #   debit:  expense:<chosen>   (if any)
    #   credit: courier.cod_receivable  (total = sum of all debits)
    # ───────────────────────────────────────────────────────────────
    @router.post("/couriers/{courier_id}/cod-settle")
    async def courier_cod_settle(
        courier_id: str, payload: CodSettleIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        cp = await db.counterparties.find_one(
            {"id": courier_id, "user_id": uid, "kind": "courier"},
            {"_id": 0, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "شركة الشحن غير موجودة")

        # Round to 2dp once; sum of all legs.
        bank_amt = round(float(payload.bank_amount or 0), 2)
        ship_amt = round(float(payload.shipping_cost or 0), 2)
        cod_fee  = round(float(payload.cod_fee or 0), 2)
        cod_fee_vat = round(float(payload.cod_fee_vat or 0), 2)
        other    = round(float(payload.other_fees or 0), 2)
        total    = round(bank_amt + ship_amt + cod_fee + cod_fee_vat + other, 2)

        if total <= 0:
            raise HTTPException(
                400,
                "أدخل قيمة واحدة على الأقل (التحويل / تكلفة الشحن / "
                "رسوم COD / رسوم أخرى).",
            )

        # ── Cap at the courier's open COD receivable. ─────────────
        cod_bal = (await compute_balance(
            db, user_id=uid, entity_type="courier",
            entity_id=courier_id, sub_account="cod_receivable"))["net_balance"]
        if total > cod_bal + 0.01:
            raise HTTPException(
                400,
                f"إجمالي التسوية ({total:,.2f} ر.س) أكبر من رصيد COD "
                f"المستحق على «{cp.get('name')}» "
                f"({cod_bal:,.2f} ر.س). راجع المبلغ.",
            )

        # ── Validate the bank/cash leg. ───────────────────────────
        if bank_amt > 0:
            if not payload.bank_account_id:
                raise HTTPException(
                    400,
                    "اختر حساب الإيداع (بنك أو صندوق) عند تعبئة "
                    "«المبلغ المحول للبنك».",
                )
            acc = await db.accounts.find_one(
                {"id": payload.bank_account_id, "user_id": uid},
                {"_id": 0, "id": 1, "name": 1, "account_type": 1},
            )
            if not acc:
                raise HTTPException(404, "الحساب البنكي غير موجود")
            if acc.get("account_type") not in ("bank", "cash"):
                raise HTTPException(
                    400,
                    "حساب الإيداع يجب أن يكون من نوع «بنك» أو "
                    "«صندوق نقدي» فقط.",
                )
            # Iter-192 — seed opening_balance on first ledger touch.
            await _ensure_opening_balance_seeded(
                db, user_id=uid, account_id=payload.bank_account_id,
            )

        # ── Validate the other_fees category. ─────────────────────
        if other > 0:
            if not payload.other_fees_category:
                raise HTTPException(
                    400,
                    "حدد فئة المصاريف للرسوم الأخرى (مثل: gateway_fees).",
                )
            valid = {c["code"] async for c in db.expense_categories.find(
                {"user_id": uid}, {"_id": 0, "code": 1})}
            if payload.other_fees_category not in valid:
                raise HTTPException(
                    400,
                    f"فئة المصاريف غير معتمدة: {payload.other_fees_category}",
                )

        # ── Build the balanced txn_group. ─────────────────────────
        entries: list[dict] = []
        if bank_amt > 0:
            entries.append({
                "entity_type": "bank",
                "entity_id": payload.bank_account_id,
                "sub_account": "main",
                "side": "debit", "amount": bank_amt,
                "entry_type": "courier_cod_settle",
                "metadata": {"leg": "bank_transfer"},
            })
        if ship_amt > 0:
            entries.append({
                "entity_type": "expense", "entity_id": "shipping",
                "side": "debit", "amount": ship_amt,
                "entry_type": "courier_cod_settle",
                "metadata": {"leg": "shipping_cost",
                              "courier_id": courier_id},
            })
        if cod_fee > 0:
            entries.append({
                "entity_type": "expense", "entity_id": "cod_fees",
                "side": "debit", "amount": cod_fee,
                "entry_type": "courier_cod_settle",
                "metadata": {"leg": "cod_fee",
                              "courier_id": courier_id},
            })
        if cod_fee_vat > 0:
            entries.append({
                "entity_type": "tax", "entity_id": "input_vat",
                "sub_account": "recoverable",
                "side": "debit", "amount": cod_fee_vat,
                "entry_type": "courier_cod_settle",
                "metadata": {"leg": "cod_fee_input_vat",
                              "courier_id": courier_id},
            })
        if other > 0:
            entries.append({
                "entity_type": "expense",
                "entity_id": payload.other_fees_category,
                "side": "debit", "amount": other,
                "entry_type": "courier_cod_settle",
                "metadata": {"leg": "other_fees",
                              "courier_id": courier_id},
            })
        # Single credit on the courier closing the COD receivable.
        entries.append({
            "entity_type": "courier", "entity_id": courier_id,
            "sub_account": "cod_receivable",
            "side": "credit", "amount": total,
            "entry_type": "courier_cod_settle",
        })

        actor_id, actor_name = await _resolve_actor(user)
        notes = (
            payload.notes
            or f"تسوية COD — {cp.get('name')} ({total:,.2f} ر.س)"
        )
        result = await post_txn_group(
            db, user_id=uid, actor_id=actor_id, actor_name=actor_name,
            txn_type="courier_cod_settlement", notes=notes,
            metadata={
                "courier_id": courier_id,
                "courier_name": cp.get("name"),
                "payment_date": payload.payment_date,
                "bank_amount": bank_amt,
                "shipping_cost": ship_amt,
                "cod_fee": cod_fee,
                "cod_fee_vat": cod_fee_vat,
                "other_fees": other,
                "other_fees_category": payload.other_fees_category,
            },
            entries=entries,
        )
        new_cod_balance = (await compute_balance(
            db, user_id=uid, entity_type="courier",
            entity_id=courier_id, sub_account="cod_receivable"))["net_balance"]
        return {
            "ok": True, **result,
            "settlement_total": total,
            "previous_cod_balance": round(cod_bal, 2),
            "remaining_cod_balance": round(new_cod_balance, 2),
        }

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
        # Iter-203 — add un-posted daily accrual delta to the displayed
        # outstanding debt (ledger is frozen at migration cutoff)
        cutoff_doc = await db.migration_cutoffs.find_one(
            {"user_id": user["id"]}, {"_id": 0, "cutoff_date": 1},
        )
        cutoff_date_str = (cutoff_doc or {}).get("cutoff_date")
        pending_accrual = await _post_cutoff_accrual_delta(
            db, user["id"], emp, cutoff_date_str)
        ledger_outstanding = float(salary_payable.get("outstanding_debt") or 0)
        salary_payable["outstanding_debt_ledger"] = round(ledger_outstanding, 2)
        salary_payable["pending_accrual"] = round(pending_accrual, 2)
        salary_payable["outstanding_debt"] = round(
            ledger_outstanding + pending_accrual, 2)
        # Iter-250b · P1.5.u — Custody is NOT a debt against the
        # employee's salary; surfaced separately as `custody_open`.
        # Only ADVANCES net against salary automatically.
        owed_to_emp = salary_payable["outstanding_debt"]
        owed_by_emp = advance["net_balance"]   # custody intentionally excluded
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
            "custody_open": round(custody["net_balance"], 2),
        }

    # ═══════════════════════════════════════════════════════════════
    # Phase 4 — Universal list endpoints (Ledger-only)
    # ═══════════════════════════════════════════════════════════════
    @router.get("/employees/list")
    async def employees_with_balances(user: dict = Depends(current_user)):
        """All employees + their LIVE ledger balances (3 sub_accounts).
        This is the Phase-4 replacement for the legacy
        `/api/liabilities/salary-accrual-summary` view.

        Iter-203 — Adds an on-the-fly "post-cutoff daily accrual"
        delta to `salary_payable` so the screen reflects each new day
        as it passes (the ledger snapshot is frozen at cutoff)."""
        uid = user["id"]
        emps = await db.operating_salaries.find(
            {"user_id": uid, "category": "employee"}, {"_id": 0},
        ).to_list(500)
        emp_ids = [e["id"] for e in emps]
        # Iter-203: Migration cutoff (None for un-migrated users)
        cutoff_doc = await db.migration_cutoffs.find_one(
            {"user_id": uid}, {"_id": 0, "cutoff_date": 1},
        )
        cutoff_date_str = (cutoff_doc or {}).get("cutoff_date")
        # Bulk-aggregate the 3 sub-accounts in one pipeline
        # Iter-250b · P1.5.i — Apply the SAME filters as `compute_balance`
        # (used by /employees/{id}/summary-balance + /financial-summary)
        # so the table totals match the details/new-transaction screen.
        # Without these filters, reversed entries and legacy_orphan
        # rows leak into the table's payable column, inflating the
        # «المستحق» figure that drives «صرف راتب» picker.
        agg = await db.general_ledger.aggregate([
            {"$match": {"user_id": uid, "entity_type": "employee",
                          "entity_id": {"$in": emp_ids},
                          "status": "posted",
                          "entry_type": {"$ne": "reversal"},
                          "metadata.legacy_orphan": {"$ne": True}}},
            {"$group": {
                "_id": {"emp": "$entity_id", "sub": "$sub_account",
                          "side": "$side"},
                "total": {"$sum": "$amount"},
            }},
        ]).to_list(5000)
        # Build {emp_id: {sub_account: {debit, credit}}}
        by_emp: dict = {}
        for r in agg:
            eid = r["_id"]["emp"]; sub = r["_id"]["sub"] or "_"; side = r["_id"]["side"]
            by_emp.setdefault(eid, {}).setdefault(sub, {}).setdefault(side, 0.0)
            by_emp[eid][sub][side] = float(r["total"])

        def _net(emp_id, sub):
            x = (by_emp.get(emp_id) or {}).get(sub) or {}
            return round(float(x.get("debit", 0)) - float(x.get("credit", 0)), 2)

        rows = []
        total_payable = 0.0
        total_advance = 0.0
        total_custody = 0.0
        total_pending_accrual = 0.0
        for e in emps:
            ledger_payable = max(-_net(e["id"], "salary_payable"), 0.0)
            advance = max(_net(e["id"], "advance"), 0.0)
            custody = max(_net(e["id"], "custody"), 0.0)
            # Iter-203 — un-posted daily accrual delta
            pending_accrual = await _post_cutoff_accrual_delta(
                db, uid, e, cutoff_date_str)
            payable = round(ledger_payable + pending_accrual, 2)
            # Iter-250b · P1.5.u — Salary net position no longer
            # auto-deducts custody. Custody is a separate open balance
            # that requires an explicit accounting operation to offset
            # against salary. Only advances net automatically.
            net_pos = round(payable - advance, 2)
            rows.append({
                "id": e["id"], "name": e.get("name"),
                "monthly_amount": round(float(e.get("monthly_amount") or 0), 2),
                "status": e.get("status") or "active",
                "salary_payable": payable,
                "salary_payable_ledger": round(ledger_payable, 2),
                "pending_accrual": round(pending_accrual, 2),
                "advance": advance,
                "custody": custody,
                "net_position": net_pos,
            })
            total_payable += payable
            total_advance += advance
            total_custody += custody
            total_pending_accrual += pending_accrual
        return {
            "employees": rows,
            "totals": {
                "salary_payable": round(total_payable, 2),
                "pending_accrual": round(total_pending_accrual, 2),
                "advance": round(total_advance, 2),
                "custody": round(total_custody, 2),
                # Iter-250b · P1.5.u — Top-level net position excludes
                # custody. Custody is shown side-by-side as its own
                # independent metric.
                "net_position": round(total_payable - total_advance, 2),
            },
            "cutoff_date": cutoff_date_str,
        }

    @router.get("/suppliers/list")
    async def suppliers_with_balances(
        user: dict = Depends(current_user),
        with_debt_only: bool = Query(False),
    ):
        """Iter-246f — Unified supplier list for «سداد مورد».

        Sources merged:
          * `db.counterparties` (kind=supplier) — legacy + bridged rows
          * `db.suppliers`       (Iter-244)    — new entries that may
            pre-date the counterparties-bridge

        When `with_debt_only=true` we hide entries whose
        `outstanding_debt <= 0` so the supplier-pay dropdown only lists
        suppliers that actually owe money.  Profile/user counterparties
        without a debt entry never show up under this filter.
        """
        uid = user["id"]

        # 1) Legacy counterparties marked as supplier
        cps = await db.counterparties.find(
            {"user_id": uid, "kind": "supplier"}, {"_id": 0},
        ).to_list(1000)
        seen = {c["id"] for c in cps}

        # 2) Iter-244 suppliers that aren't yet bridged
        async for s in db.suppliers.find(
            {"user_id": uid}, {"_id": 0, "id": 1, "company_name": 1,
                               "status": 1},
        ):
            if s["id"] in seen:
                continue
            cps.append({
                "id": s["id"],
                "name": s.get("company_name"),
                "kind": "supplier",
                "status": s.get("status", "active"),
            })
            seen.add(s["id"])

        ids = [c["id"] for c in cps]
        balances = await __import__("ledger_core").compute_balances_bulk(
            db, user_id=uid, entity_type="supplier", entity_ids=ids,
        ) if ids else {}

        # Iter-250b · P1.5.x — Per-supplier DRIFT counts (movements
        # in `financial_movements` without a valid GL post). We
        # aggregate in ONE round-trip to avoid an N+1 hit.
        # READ-ONLY · no writes.
        drift_by_supplier: dict = {}
        if ids:
            # Step 1: pull all supplier_invoice movements for these
            # suppliers and their ledger_txn_group_id.
            mvs_cursor = db.financial_movements.find(
                {"user_id": uid,
                 "supplier_id": {"$in": ids},
                 "movement_type": "supplier_invoice"},
                {"_id": 0, "supplier_id": 1,
                 "ledger_txn_group_id": 1,
                 "total_amount": 1, "status": 1},
            )
            mv_buckets: dict = {sid: [] for sid in ids}
            referenced_groups: set = set()
            async for m in mvs_cursor:
                sid = m.get("supplier_id")
                if sid in mv_buckets:
                    mv_buckets[sid].append(m)
                tg = m.get("ledger_txn_group_id")
                if tg:
                    referenced_groups.add(tg)
            # Step 2: in one shot, find which of those group_ids
            # actually have a SUPPLIER-PAYABLE leg posted in GL.
            # This is the SAME criterion used by the supplier detail
            # page (`supplier_ledger_detail_routes.py`) — without it,
            # the two pages disagree on the drift count.
            present_groups: set = set()
            if referenced_groups:
                async for g in db.general_ledger.find(
                    {"user_id": uid,
                     "entity_type": "supplier",
                     "sub_account": "payable",
                     "txn_group_id": {"$in": list(referenced_groups)},
                     "status": "posted"},
                    {"_id": 0, "txn_group_id": 1},
                ):
                    present_groups.add(g["txn_group_id"])
            # Step 3: classify each movement.
            for sid, mlist in mv_buckets.items():
                cnt = 0; tot = 0.0
                for m in mlist:
                    tg = m.get("ledger_txn_group_id")
                    if (not tg) or (tg not in present_groups):
                        cnt += 1
                        tot += float(m.get("total_amount") or 0)
                if cnt > 0:
                    drift_by_supplier[sid] = {
                        "drifted_count":  cnt,
                        "drifted_total":  round(tot, 2),
                    }

        rows = []
        total_owed = 0.0
        total_drifted_count = 0
        total_drifted_amount = 0.0
        for c in cps:
            b = balances.get(c["id"], {})
            owed = b.get("outstanding_debt", 0.0)
            drift = drift_by_supplier.get(c["id"], {})
            d_cnt = drift.get("drifted_count", 0)
            d_amt = drift.get("drifted_total", 0.0)
            # Iter-250b · P1.5.x — `with_debt_only` filter must NOT
            # hide a supplier that only has drifted movements; the
            # merchant still needs to see them.
            if with_debt_only and owed <= 0 and d_cnt == 0:
                continue
            total_owed += owed
            total_drifted_count += d_cnt
            total_drifted_amount += d_amt
            rows.append({
                "id": c["id"], "name": c.get("name"),
                "outstanding_debt": owed,
                "debits": b.get("debits", 0.0),
                "credits": b.get("credits", 0.0),
                # New drift fields (always present, zero by default).
                "drifted_count":  d_cnt,
                "drifted_total":  d_amt,
            })
        # Sort by debt desc so the «top owed» supplier surfaces first
        # in the merchant's pay flow.
        rows.sort(key=lambda r: r["outstanding_debt"], reverse=True)
        return {"suppliers": rows,
                 "totals": {
                    "outstanding_debt": round(total_owed, 2),
                    "drifted_count":  total_drifted_count,
                    "drifted_total":  round(total_drifted_amount, 2),
                 }}

    @router.get("/externals/list")
    async def externals_with_balances(user: dict = Depends(current_user)):
        uid = user["id"]
        cps = await db.counterparties.find(
            {"user_id": uid, "kind": {"$nin": [
                "ad_account", "supplier", "courier"]}},
            {"_id": 0},
        ).to_list(500)
        ids = [c["id"] for c in cps]
        balances = await __import__("ledger_core").compute_balances_bulk(
            db, user_id=uid, entity_type="external_person", entity_ids=ids,
        ) if ids else {}
        rows = []
        total_recv = 0.0
        for c in cps:
            b = balances.get(c["id"], {})
            recv = b.get("net_balance", 0.0)
            total_recv += recv
            rows.append({
                "id": c["id"], "name": c.get("name"),
                "kind": c.get("kind"),
                "receivable": recv,
                "debits": b.get("debits", 0.0),
                "credits": b.get("credits", 0.0),
            })
        return {"externals": rows,
                 "totals": {"receivable": round(total_recv, 2)}}

    @router.get("/couriers/list")
    async def couriers_with_balances(user: dict = Depends(current_user)):
        uid = user["id"]
        cps = await db.counterparties.find(
            {"user_id": uid, "kind": "courier"}, {"_id": 0},
        ).to_list(500)
        rows = []
        total_payable = 0.0
        total_cod = 0.0
        for c in cps:
            pay = await compute_balance(
                db, user_id=uid, entity_type="courier",
                entity_id=c["id"], sub_account="payable")
            cod = await compute_balance(
                db, user_id=uid, entity_type="courier",
                entity_id=c["id"], sub_account="cod_receivable")
            owed = pay["outstanding_debt"]
            cod_open = cod["net_balance"]
            total_payable += owed
            total_cod += cod_open
            rows.append({
                "id": c["id"], "name": c.get("name"),
                "payable": owed,
                "cod_receivable": cod_open,
            })
        return {"couriers": rows,
                 "totals": {"payable": round(total_payable, 2),
                              "cod_receivable": round(total_cod, 2)}}

    # ═══════════════════════════════════════════════════════════════
    # Phase 4 — Financial Position (ALL from Ledger)
    # ═══════════════════════════════════════════════════════════════
    @router.get("/financial-position")
    async def financial_position(user: dict = Depends(current_user)):
        """Iter-217 — SSOT financial position computed strictly from
        `general_ledger` (with the documented Iter-192/217 fallback
        for accounts that have ZERO ledger activity). Returns a
        backward-compatible superset of the Phase-4 shape so both
        `/financial-position` (legacy page) and
        `/financial-position-ledger` (new page) can consume it.
        """
        from financial_position_ssot import compute_financial_position
        return await compute_financial_position(db, user["id"])

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

    # ═══════════════════════════════════════════════════════════════
    # Iter-206 — Advertising Expenses Report (SSOT-backed)
    # ═══════════════════════════════════════════════════════════════
    @router.get("/reports/advertising-expenses")
    async def advertising_expenses_report(
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        """Read `expense.advertising` entries from `general_ledger`
        (written by Iter-205) and return three grouped views:

          • by_platform  — Snapchat / Meta / TikTok / other
          • by_ad_account — per counterparty card
          • by_month     — chronological monthly series
          • daily        — full daily series for charting

        Optional date filter on `metadata.spend_date` (YYYY-MM-DD).
        """
        uid = user["id"]
        match: dict = {
            "user_id": uid,
            "entity_type": "expense",
            "entity_id": "advertising",
            "side": "debit",
            "status": "posted",
        }
        if from_date or to_date:
            sd: dict = {}
            if from_date:
                sd["$gte"] = from_date
            if to_date:
                sd["$lte"] = to_date
            match["metadata.spend_date"] = sd

        rows = await db.general_ledger.find(
            match,
            {"_id": 0, "amount": 1, "metadata": 1, "posted_at": 1},
        ).sort("posted_at", 1).to_list(50000)

        total = 0.0
        by_platform: dict = {}
        by_account: dict = {}
        by_month: dict = {}
        by_day: dict = {}
        for r in rows:
            md = r.get("metadata") or {}
            amt = float(r.get("amount") or 0)
            total += amt
            prov = (md.get("ad_provider") or "unknown")
            acc_id = md.get("ad_account_id") or "—"
            acc_name = md.get("ad_account_name") or "—"
            sd = (md.get("spend_date")
                  or (r.get("posted_at") or "")[:10])
            month = sd[:7] if sd else "—"

            pg = by_platform.setdefault(prov, {
                "key": prov, "total": 0.0, "count": 0,
            })
            pg["total"] += amt
            pg["count"] += 1

            ag = by_account.setdefault(acc_id, {
                "ad_account_id": acc_id, "name": acc_name,
                "ad_provider": prov, "total": 0.0, "count": 0,
            })
            ag["total"] += amt
            ag["count"] += 1

            mg = by_month.setdefault(month, {
                "month": month, "total": 0.0, "count": 0,
            })
            mg["total"] += amt
            mg["count"] += 1

            dg = by_day.setdefault(sd, {
                "date": sd, "total": 0.0, "count": 0,
            })
            dg["total"] += amt
            dg["count"] += 1

        def _round_group(d):
            d["total"] = round(d["total"], 2)
            return d

        return {
            "from_date": from_date,
            "to_date": to_date,
            "total": round(total, 2),
            "by_platform": sorted(
                (_round_group(p) for p in by_platform.values()),
                key=lambda x: -x["total"],
            ),
            "by_ad_account": sorted(
                (_round_group(a) for a in by_account.values()),
                key=lambda x: -x["total"],
            ),
            "by_month": sorted(
                (_round_group(m) for m in by_month.values()),
                key=lambda x: x["month"],
            ),
            "daily": sorted(
                (_round_group(d) for d in by_day.values()),
                key=lambda x: x["date"],
            ),
        }

    return router
