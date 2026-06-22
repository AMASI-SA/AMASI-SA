"""Iter-245 — Unified Financial Movements (Phase 1: 3 types).

Supports `supplier_invoice`, `general_expense`, `fixed_asset` only.
Every posted movement creates a balanced Double-Entry pair in the
SSOT `general_ledger` via the Iter-240 helper.

Forward-only — no touch to legacy `liabilities`, `daily_expenses`,
or `counterparties` rows.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator


SUPPORTED_TYPES = {"supplier_invoice", "general_expense", "fixed_asset"}
# Iter-246c — Supplier invoices ALWAYS require line items, regardless
# of the chosen root.  This rule used to be gated by `PURCHASE_ROOTS`
# (a single hard-coded root) but the merchant ran into edge cases where
# new purchase roots wouldn't trigger the table.  Keeping
# `PURCHASE_ROOTS` only for documentation / legacy references.
PURCHASE_ROOTS = {"تكاليف المنتجات"}
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5 MB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _r(x) -> float:
    return round(float(x or 0), 2)


# ── Models ──────────────────────────────────────────────────────────

class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    # Optional category override per line (defaults to header category).
    category_id: Optional[str] = None
    # Future-ready (Phase 5 hooks)
    product_id: Optional[str] = None
    product_sku: Optional[str] = None
    # Iter-250b · Phase 3.7 — per-line discount/tax/notes.
    # Both monetary amounts are FIXED VALUES in SAR (not percentages).
    # Server validates non-negativity AND that discount never exceeds
    # the pre-tax line total. `notes` is a free-form per-line memo.
    discount_amount: float = 0.0
    tax_amount: float = 0.0
    notes: Optional[str] = None


class Attachment(BaseModel):
    filename: str
    content_type: str
    base64: str


class MovementCreate(BaseModel):
    movement_type: str
    doc_date: str
    doc_number: Optional[str] = None
    notes: Optional[str] = None

    supplier_id: Optional[str] = None
    category_id: str

    payment_terms: str  # "credit" | "cash" | "partial"
    total_amount: float  # invoice total (or = paid_amount for cash)
    paid_amount: float = 0.0
    paid_from_account_id: Optional[str] = None
    # Iter-250b · P1.5.q — Alternative pay source: employee custody
    # wallet. When set, the credit leg debits the employee's open
    # custody balance instead of a bank/cash account. Only valid for
    # `general_expense` in this Phase. Mutually exclusive with
    # `paid_from_account_id`.
    custody_employee_id: Optional[str] = None
    withdrawal_method: Optional[str] = None  # cash/transfer/pos
    reference_number: Optional[str] = None
    attachment: Optional[Attachment] = None

    line_items: list[LineItem] = Field(default_factory=list)

    @field_validator("movement_type")
    @classmethod
    def _type_ok(cls, v: str) -> str:
        if v not in SUPPORTED_TYPES:
            raise ValueError(
                f"movement_type must be one of {sorted(SUPPORTED_TYPES)}")
        return v

    @field_validator("payment_terms")
    @classmethod
    def _terms_ok(cls, v: str) -> str:
        if v not in ("credit", "cash", "partial"):
            raise ValueError(
                "payment_terms must be credit/cash/partial")
        return v


# ── Helpers ─────────────────────────────────────────────────────────

async def _resolve_category_path(db, uid: str, cat_id: str) -> dict:
    cat = await db.expense_category_tree.find_one(
        {"id": cat_id, "user_id": uid},
        {"_id": 0, "id": 1, "name": 1, "path": 1,
         "path_ids": 1, "status": 1, "parent_id": 1,
         "movement_types": 1},
    )
    if not cat:
        raise HTTPException(404, "التصنيف غير موجود")
    if cat.get("status") == "inactive":
        raise HTTPException(400, "التصنيف موقوف")
    return cat


async def _resolve_root_movement_types(db, uid: str,
                                       cat: dict) -> list[str]:
    """Walk up to the root and return its `movement_types`."""
    node = cat
    guard = 0
    while node.get("parent_id") and guard < 64:
        parent = await db.expense_category_tree.find_one(
            {"id": node["parent_id"], "user_id": uid},
            {"_id": 0, "id": 1, "parent_id": 1,
             "movement_types": 1},
        )
        if not parent:
            break
        node = parent
        guard += 1
    return node.get("movement_types") or []


async def _resolve_account(db, uid: str, acc_id: str) -> dict:
    acc = await db.accounts.find_one(
        {"id": acc_id, "user_id": uid},
        {"_id": 0, "id": 1, "name": 1, "account_type": 1,
         "current_balance": 1, "currency": 1},
    )
    if not acc:
        raise HTTPException(404, "الحساب غير موجود")
    return acc


async def _resolve_supplier(db, uid: str, sup_id: str) -> dict:
    s = await db.suppliers.find_one(
        {"id": sup_id, "user_id": uid},
        {"_id": 0, "id": 1, "company_name": 1,
         "contact_person": 1, "phone": 1, "status": 1},
    )
    if not s:
        raise HTTPException(404, "المورد غير موجود")
    if s.get("status") == "inactive":
        raise HTTPException(400, "المورد موقوف")
    return s


def _decode_attachment_or_400(att: Optional[Attachment]) -> Optional[dict]:
    if not att:
        return None
    try:
        raw = base64.b64decode(att.base64, validate=True)
    except Exception:
        raise HTTPException(400, "ملف الإيصال غير صالح")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(400, "حجم الإيصال يتجاوز 5MB")
    return {
        "filename": att.filename,
        "content_type": att.content_type,
        "base64": att.base64,
        "size_bytes": len(raw),
    }


def _requires_line_items(cat_path: list[str]) -> bool:
    return bool(cat_path) and cat_path[0] in PURCHASE_ROOTS


# ── Iter-250b · Phase 4 — Product cost auto-update on supplier-invoice
#
# When a `supplier_invoice` movement is successfully posted to the GL,
# walk every line item that carries a `product_id` and append a new
# cost-history record on the corresponding `products` document.
#
# Iter-250b · Phase 4.5 — Reversal-aware cost engine.
# Every cost_history entry now carries a `status` field:
#   • "active"   → counts towards cost_current / cost_avg
#   • "reversed" → kept for audit trail, IGNORED in calculations
# Entries with no `status` field are legacy and treated as active.
#
# The single source of truth for recomputing a product's cost numbers
# lives in `recalculate_product_cost(db, uid, product_id, dry_run)` —
# every place that mutates cost_history (new invoice, reversal, edit)
# MUST call it afterwards.

def _is_active_entry(h: dict) -> bool:
    if not isinstance(h, dict):
        return False
    return (h.get("status") or "").lower() != "reversed"


def _entry_sort_key(h: dict) -> str:
    return str(h.get("at") or h.get("invoice_date") or "")


async def recalculate_product_cost(
    db, uid: str, product_id: str, dry_run: bool = False,
) -> dict:
    """Recompute cost_current / cost_avg / needs_cost for a product.
    Only `status != "reversed"` entries are considered.
    """
    prod = await db.products.find_one(
        {"user_id": uid, "$or": [{"id": product_id},
                                  {"product_id": product_id}]},
        {"_id": 0, "id": 1, "product_id": 1, "name": 1,
         "cost_current": 1, "cost_avg": 1,
         "needs_cost": 1, "cost_history": 1},
    )
    if not prod:
        return {"ok": False, "error": "product_not_found"}

    history = prod.get("cost_history") or []
    active  = [h for h in history if _is_active_entry(h)]

    if not active:
        new_current, new_avg, new_needs = 0.0, 0.0, True
    else:
        latest = sorted(active, key=_entry_sort_key, reverse=True)[0]
        new_current = _r(latest.get("unit_cost") or latest.get("amount") or 0)
        qty_entries = [
            h for h in active
            if h.get("quantity") is not None
            and h.get("unit_cost") is not None
            and _r(h.get("quantity")) > 0
        ]
        if qty_entries:
            tot_qty = sum(_r(h["quantity"]) for h in qty_entries)
            tot_val = sum(_r(h["quantity"]) * _r(h["unit_cost"])
                          for h in qty_entries)
            new_avg = round(tot_val / tot_qty, 2) if tot_qty else 0.0
        else:
            amts = [_r(h.get("amount") or h.get("unit_cost") or 0)
                    for h in active
                    if (h.get("amount") or h.get("unit_cost"))]
            new_avg = round(sum(amts) / len(amts), 2) if amts else 0.0
        new_needs = new_current <= 0 and new_avg <= 0

    before = {
        "cost_current": prod.get("cost_current"),
        "cost_avg":     prod.get("cost_avg"),
        "needs_cost":   prod.get("needs_cost"),
    }
    after = {
        "cost_current": new_current,
        "cost_avg":     new_avg,
        "needs_cost":   bool(new_needs),
    }
    changed = (
        _r(before.get("cost_current") or 0) != _r(after["cost_current"])
        or _r(before.get("cost_avg") or 0)  != _r(after["cost_avg"])
        or bool(before.get("needs_cost"))   != after["needs_cost"]
    )
    if changed and not dry_run:
        await db.products.update_one(
            {"id": prod["id"], "user_id": uid},
            {"$set": {
                "cost_current": after["cost_current"],
                "cost_avg":     after["cost_avg"],
                "needs_cost":   after["needs_cost"],
                "updated_at":   _now(),
            }},
        )
    return {
        "ok": True,
        "product_id":      prod["id"],
        "product_code":    prod.get("product_id"),
        "name":            prod.get("name"),
        "before":          before,
        "after":           after,
        "changed":         changed,
        "active_entries":  len(active),
        "total_entries":   len(history),
        "applied":         changed and not dry_run,
    }


async def _apply_product_cost_updates(
    db,
    uid: str,
    mv: dict,
) -> dict:
    if mv.get("movement_type") != "supplier_invoice":
        return {"updated": 0, "skipped": 0, "errors": []}

    line_items = mv.get("line_items") or []
    supplier_id = mv.get("supplier_id")
    mv_id = mv.get("id")
    doc_date = mv.get("doc_date")
    now = _now()

    updated = 0
    skipped = 0
    errors: list[str] = []

    for li in line_items:
        pid = (li.get("product_id") or "").strip()
        if not pid:
            skipped += 1
            continue
        qty = _r(li.get("quantity"))
        unit_cost = _r(li.get("unit_price"))
        total_cost = _r(qty * unit_cost)
        if qty <= 0 or unit_cost <= 0:
            skipped += 1
            continue

        # Resolve product by internal `id` first then by `product_id`
        # (both are valid identifiers in the catalogue).
        prod = await db.products.find_one(
            {"user_id": uid,
             "$or": [{"id": pid}, {"product_id": pid}]},
            {"_id": 0, "id": 1},
        )
        if not prod:
            errors.append(f"product {pid} not found")
            continue

        # Build the new (append-only) history record with explicit
        # lifecycle status so the reversal hook can flip it cleanly.
        new_entry = {
            "amount":              unit_cost,
            "source":              "supplier-invoice",
            "at":                  now,
            "supplier_id":         supplier_id,
            "supplier_invoice_id": mv_id,
            "invoice_date":        doc_date,
            "quantity":            qty,
            "unit_cost":           unit_cost,
            "total_cost":          total_cost,
            "status":              "active",
        }

        try:
            await db.products.update_one(
                {"id": prod["id"], "user_id": uid},
                {"$push": {"cost_history": new_entry}},
            )
            await recalculate_product_cost(db, uid, prod["id"])
            updated += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pid}: {e}")

    return {"updated": updated, "skipped": skipped, "errors": errors}


# ── Iter-250b · Phase 4.5 — Mark cost-history entries as reversed
# when the supplier-invoice movement is reversed via
# /api/ledger/groups/{group_id}/reverse. Append-only: flips `status`
# and stamps reversal metadata, then recomputes affected products.
async def mark_supplier_invoice_cost_reversed(
    db, uid: str, txn_group_id: str,
) -> dict:
    if not txn_group_id:
        return {"reversed_lines": 0, "products_recomputed": 0,
                "affected_products": [], "errors": ["empty_group_id"]}

    mv = await db.financial_movements.find_one(
        {"user_id": uid,
         "ledger_txn_group_id": txn_group_id,
         "movement_type": "supplier_invoice"},
        {"_id": 0, "id": 1, "line_items": 1},
    )
    if not mv:
        return {"reversed_lines": 0, "products_recomputed": 0,
                "affected_products": [], "errors": []}

    mv_id = mv["id"]
    affected_products: set[str] = set()
    reversed_count = 0
    now = _now()

    for li in (mv.get("line_items") or []):
        pid = (li.get("product_id") or "").strip()
        if not pid:
            continue
        prod = await db.products.find_one(
            {"user_id": uid,
             "$or": [{"id": pid}, {"product_id": pid}]},
            {"_id": 0, "id": 1},
        )
        if not prod:
            continue
        res = await db.products.update_one(
            {"id": prod["id"], "user_id": uid},
            {"$set": {
                "cost_history.$[elem].status":                "reversed",
                "cost_history.$[elem].reversed_at":           now,
                "cost_history.$[elem].reversal_txn_group_id": txn_group_id,
            }},
            array_filters=[{
                "elem.supplier_invoice_id": mv_id,
                "$or": [
                    {"elem.status": {"$exists": False}},
                    {"elem.status": "active"},
                ],
            }],
        )
        if res.modified_count > 0:
            reversed_count += res.modified_count
        affected_products.add(prod["id"])

    recomputed = 0
    errors: list[str] = []
    for pid in affected_products:
        try:
            r = await recalculate_product_cost(db, uid, pid)
            if r.get("applied"):
                recomputed += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pid}: {e}")

    return {
        "reversed_lines":      reversed_count,
        "products_recomputed": recomputed,
        "affected_products":   list(affected_products),
        "errors":              errors,
    }


# ── Router ──────────────────────────────────────────────────────────

def make_financial_movements_router(db, current_user):
    router = APIRouter(prefix="/financial-movements",
                       tags=["financial-movements"])

    @router.get("/accounts-with-availability")
    async def accounts_with_availability(
        user: dict = Depends(current_user),
        amount: float = Query(0.0, ge=0.0),
    ):
        """Return all accounts annotated with `available_balance` and
        `is_sufficient` (= balance ≥ amount).  Iter-246i — Balance is
        now computed via the shared `account_balance_ssot` so it
        MATCHES every other screen (`/accounts`, `/accounts/summary`,
        `/accounting/financial-position`).  Previously this endpoint
        read the legacy `current_balance` field directly, which could
        be hours-to-days stale on accounts whose `_recompute_balance`
        had failed silently."""
        uid = user["id"]
        rows = await db.accounts.find(
            {"user_id": uid},
            {"_id": 0, "id": 1, "name": 1,
             "account_type": 1, "current_balance": 1,
             "currency": 1, "opening_balance": 1,
             "expected_orders_balance": 1,
             "normalized_payment_method": 1},
        ).sort([("name", 1)]).to_list(500)

        # Pull SSOT balance per row.  Falls back to the stored value
        # only if SSOT raises (never silently — we surface the source).
        try:
            from financial_position_ssot import account_balance_ssot
        except Exception:  # noqa: BLE001
            account_balance_ssot = None

        out = []
        for r in rows:
            stored = _r(r.get("current_balance"))
            ssot_bal = stored
            source = "stored"
            if (account_balance_ssot
                    and r.get("account_type") in (
                        "bank", "cash", "payment_platform")):
                try:
                    ssot_bal = _r(await account_balance_ssot(
                        db, user_id=uid, account=r))
                    source = "ssot"
                except Exception:  # noqa: BLE001
                    source = "stored_fallback"

            # Iter-246j — ledger_net for the debug field the merchant
            # requested.  Cheap because Mongo will use the
            # (user_id, entity_type, entity_id) index already in place
            # for `general_ledger`.
            ledger_bal = None
            try:
                from ledger_core import compute_balance
                lb = await compute_balance(
                    db, user_id=uid, entity_type="bank",
                    entity_id=r["id"], sub_account="main")
                ledger_bal = _r(lb.get("net_balance") or 0)
            except Exception:  # noqa: BLE001
                pass

            out.append({
                "id": r["id"],
                "name": r.get("name"),
                "account_type": r.get("account_type"),
                "currency": r.get("currency"),
                "available_balance": ssot_bal,
                "is_sufficient": ssot_bal >= _r(amount),
                "balance_source": source,
                "stored_balance": stored,
                "ledger_balance": ledger_bal,
                "ssot_balance": ssot_bal,
                "last_calculated_at": _now(),
            })
        return {"ok": True, "items": out,
                "requested_amount": _r(amount)}

    @router.post("")
    async def create_movement(
        payload: MovementCreate,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # ── 1) Category (full path persisted — Requirement #2) ──────
        cat = await _resolve_category_path(db, uid, payload.category_id)

        # Iter-246 — enforce movement_type ↔ category mapping.
        applicable = await _resolve_root_movement_types(db, uid, cat)
        if applicable and payload.movement_type not in applicable:
            raise HTTPException(
                400,
                f"التصنيف المختار غير متاح لعملية "
                f"«{payload.movement_type}». التصنيف متاح فقط لـ: "
                f"{applicable}",
            )

        # ── 2) Supplier (required only for supplier_invoice) ────────
        sup_snap = None
        if payload.movement_type == "supplier_invoice":
            if not payload.supplier_id:
                raise HTTPException(
                    400, "المورد مطلوب لفاتورة المورد")
            sup = await _resolve_supplier(db, uid, payload.supplier_id)
            sup_snap = {
                "company_name": sup["company_name"],
                "contact_person": sup["contact_person"],
                "phone": sup["phone"],
            }

        # ── 3) Line-items rule (Iter-246c) ──────────────────────────
        # Supplier invoices ALWAYS require detailed line_items so the
        # merchant can never type a header-only total.  Each line must
        # carry a non-empty description and strictly-positive quantity
        # AND unit_price.
        cat_path = cat.get("path") or [cat["name"]]
        if payload.movement_type == "supplier_invoice":
            if not payload.line_items:
                raise HTTPException(
                    400,
                    "فواتير المورد تتطلب جدول أصناف. أضف صفاً "
                    "واحداً على الأقل (الصنف، الكمية، سعر الوحدة).",
                )
            for idx, li in enumerate(payload.line_items, start=1):
                if not (li.description or "").strip():
                    raise HTTPException(
                        400,
                        f"الصف #{idx}: اسم الصنف مطلوب")
                if _r(li.quantity) <= 0:
                    raise HTTPException(
                        400,
                        f"الصف #{idx}: الكمية يجب أن تكون > 0")
                if _r(li.unit_price) <= 0:
                    raise HTTPException(
                        400,
                        f"الصف #{idx}: سعر الوحدة يجب أن يكون > 0")
                # Iter-250b · Phase 3.7 — Validate discount/tax.
                if _r(li.discount_amount) < 0:
                    raise HTTPException(
                        400,
                        f"الصف #{idx}: الخصم لا يقبل قيمة سالبة")
                if _r(li.tax_amount) < 0:
                    raise HTTPException(
                        400,
                        f"الصف #{idx}: الضريبة لا تقبل قيمة سالبة")
                pre_tax = _r(_r(li.quantity) * _r(li.unit_price))
                if _r(li.discount_amount) > pre_tax + 0.001:
                    raise HTTPException(
                        400,
                        f"الصف #{idx}: الخصم ({_r(li.discount_amount)}) "
                        f"يتجاوز إجمالي السطر قبل الضريبة ({pre_tax})")
            # Iter-250b · Phase 3.7 — server-side total =
            #   Σ (qty × unit_price − discount + tax)
            li_total = _r(sum(
                _r(_r(li.quantity) * _r(li.unit_price)
                    - _r(li.discount_amount) + _r(li.tax_amount))
                for li in payload.line_items
            ))
            # The frontend computes `total_amount` from line_items, but
            # we re-compute server-side and overwrite to keep the SSOT
            # in the line table even if a client tampered with the
            # header total.
            payload.total_amount = li_total

        # ── 4) Payment terms + balance check (Requirement #5) ───────
        total = _r(payload.total_amount)
        if total <= 0:
            raise HTTPException(400, "الإجمالي يجب أن يكون أكبر من صفر")
        paid = _r(payload.paid_amount)
        if payload.payment_terms == "credit":
            paid = 0.0
        elif payload.payment_terms == "cash":
            paid = total
        else:  # partial
            if paid <= 0 or paid >= total:
                raise HTTPException(
                    400,
                    "السداد الجزئي يتطلب مبلغاً بين 0 والإجمالي")
        remaining = _r(total - paid)

        # Iter-250b · P1.5.q — Custody pay-source guard rails.
        # When the merchant pays from an employee custody wallet
        # instead of a bank/cash account, several constraints apply.
        is_custody_pay = bool(payload.custody_employee_id)
        custody_emp_doc = None
        custody_avail = 0.0
        if is_custody_pay:
            if payload.paid_from_account_id:
                raise HTTPException(
                    400,
                    "اختر مصدر سداد واحد فقط: حساب بنكي/صندوق أو "
                    "عهدة موظف.",
                )
            if payload.movement_type != "general_expense":
                raise HTTPException(
                    400,
                    "الدفع من عهدة الموظف متاح حالياً فقط لعملية "
                    "«مصروف عام».",
                )
            if payload.payment_terms != "cash":
                raise HTTPException(
                    400,
                    "الدفع من العهدة يكون نقدياً فقط — لا يقبل آجل أو "
                    "سداد جزئي.",
                )
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
            # Authorisation — spend_any perm OR linked employee.
            from universal_accounting_routes import _effective_perms_for
            perms = _effective_perms_for(user)
            if "accounting.custody.spend_any" not in perms:
                linked_emp = (user.get("linked_employee_id") or "")
                if not linked_emp or linked_emp != emp_id:
                    raise HTTPException(
                        403, "لا يمكنك الصرف من عهدة موظف آخر.")
            # Balance check.
            from ledger_core import compute_balance as _cb
            cust = await _cb(
                db, user_id=uid, entity_type="employee",
                entity_id=emp_id, sub_account="custody",
            )
            custody_avail = float(cust.get("net_balance") or 0)
            if total > custody_avail + 0.001:
                raise HTTPException(
                    400,
                    f"رصيد العهدة غير كافٍ. "
                    f"المتاح: {custody_avail:.2f} ر.س، "
                    f"المطلوب: {total:.2f} ر.س.",
                )

        paid_acc_snap = None
        if paid > 0 and not is_custody_pay:
            if not payload.paid_from_account_id:
                raise HTTPException(
                    400, "الحساب الدافع مطلوب")
            # Iter-246c — Apply merchant's Operation↔Accounts allow-list.
            from universal_accounting_routes import (
                _enforce_account_binding,
                _enforce_withdrawal_method,
            )
            await _enforce_account_binding(
                db, user_id=uid, op_type=payload.movement_type,
                account_id=payload.paid_from_account_id,
            )
            acc = await _resolve_account(
                db, uid, payload.paid_from_account_id)
            avail = _r(acc.get("current_balance"))
            if avail < paid:
                raise HTTPException(
                    400,
                    f"رصيد الحساب ({avail}) غير كافٍ لإتمام العملية "
                    f"بمبلغ {paid}. اختر حساباً آخر أو قلّل المبلغ.",
                )
            if (acc.get("account_type") or "").lower() == "bank":
                if payload.withdrawal_method not in (
                        "cash", "transfer", "pos"):
                    raise HTTPException(
                        400,
                        "طريقة السحب مطلوبة عند الدفع من حساب بنكي")
                # Iter-246c — Apply withdrawal-method allow-list.
                await _enforce_withdrawal_method(
                    db, user_id=uid, op_type=payload.movement_type,
                    method=payload.withdrawal_method,
                )
            paid_acc_snap = {
                "name": acc.get("name"),
                "type": acc.get("account_type"),
            }

        # ── 5) Attachment (Requirement #4) ──────────────────────────
        attach = _decode_attachment_or_400(payload.attachment)
        if attach and payload.withdrawal_method != "transfer":
            # Allowed only on bank transfer per spec.
            attach = None

        # ── 6) Persist movement ─────────────────────────────────────
        mv_id = str(uuid.uuid4())
        mv: dict[str, Any] = {
            "id": mv_id,
            "user_id": uid,
            "movement_type": payload.movement_type,
            "doc_date": payload.doc_date,
            "doc_number": payload.doc_number or None,
            "notes": payload.notes or None,
            "supplier_id": payload.supplier_id,
            "supplier_snapshot": sup_snap,
            "category_id": cat["id"],
            "category_path": cat_path,
            "category_path_ids": (cat.get("path_ids") or []) + [cat["id"]],
            "payment_terms": payload.payment_terms,
            "total_amount": total,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "paid_from_account_id": payload.paid_from_account_id,
            "paid_from_account_snapshot": paid_acc_snap,
            # Iter-250b · P1.5.q — Record custody pay source for
            # reporting / employee-ledger replays.
            "custody_employee_id": payload.custody_employee_id,
            "custody_employee_snapshot": (
                {"id": custody_emp_doc["id"],
                 "name": custody_emp_doc.get("name")}
                if is_custody_pay and custody_emp_doc else None
            ),
            "pay_source": "custody" if is_custody_pay else "bank",
            "withdrawal_method": payload.withdrawal_method,
            "reference_number": payload.reference_number,
            "receipt_attachment": attach,
            "has_line_items": bool(payload.line_items),
            "line_items": [
                {
                    "line_id": str(uuid.uuid4()),
                    "description": li.description,
                    "quantity": _r(li.quantity),
                    "unit_price": _r(li.unit_price),
                    # Iter-250b · Phase 3.7 — per-line discount/tax/notes
                    # plus the computed `line_total` so downstream views
                    # don't have to recompute.
                    "discount_amount": _r(li.discount_amount),
                    "tax_amount":      _r(li.tax_amount),
                    "notes":           (li.notes or "").strip() or None,
                    # `total` is kept as the pre-tax line subtotal for
                    # backwards-compatibility with old views that read it.
                    "total": _r(_r(li.quantity) * _r(li.unit_price)),
                    # `line_total` is the post-discount + tax figure
                    # that feeds invoice header `total_amount`.
                    "line_total": _r(
                        _r(li.quantity) * _r(li.unit_price)
                        - _r(li.discount_amount) + _r(li.tax_amount)
                    ),
                    "category_id": li.category_id or cat["id"],
                    "category_path": cat_path,
                    "product_id": li.product_id,
                    "product_sku": li.product_sku,
                    "cost_after_allocation": None,  # Phase 5 hook
                }
                for li in payload.line_items
            ],
            "status": "posted",
            "ledger_txn_group_id": None,
            "ledger_idempotency_key": f"iter245:{mv_id}",
            "iter": "iter245",
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.financial_movements.insert_one(mv)

        # ── 7) Post a proper multi-leg Double-Entry journal (Iter-246d)
        # The old single-leg «mirror cash» logic only recognised the
        # paid portion, which under-stated both the expense and the
        # supplier liability on partial / credit invoices.  We now
        # always post the FULL invoice value:
        #
        #   Dr expense_category   = total
        #     Cr bank             = paid          (only if paid > 0)
        #     Cr supplier         = remaining     (only if remaining > 0)
        #
        # For invoices with both legs (partial) all three rows share
        # the same `txn_group_id`, and `post_txn_group` enforces the
        # `Σ debit == Σ credit` invariant atomically.
        #
        # An `account_transactions` row is still created for the cash
        # leg so the existing balance-recompute / SSOT view stays
        # consistent.
        actor_name = (
            user.get("name") or user.get("email") or "user")
        entries: list[dict] = []
        meta_common = {
            "iter": "iter246d",
            "movement_id": mv_id,
            "movement_type": payload.movement_type,
            "category_id": cat["id"],
            "category_path": cat_path,
            "doc_number": payload.doc_number,
            "supplier_id": payload.supplier_id,
        }
        # 7a) Expense / inventory recognition leg — Debit
        # entity_type=expense_category, entity_id=leaf category_id.
        # entry_type maps cleanly to the merchant-facing semantics:
        #   • supplier_invoice → "supplier_invoice"
        #   • general_expense  → "expense_record"
        #   • fixed_asset      → "fixed_asset_purchase"
        gl_entry_type = {
            "supplier_invoice": "supplier_invoice",
            "general_expense":  "expense_record",
            "fixed_asset":      "fixed_asset_purchase",
        }[payload.movement_type]
        entries.append({
            "entity_type": "expense_category",
            "entity_id":   cat["id"],
            "side":        "debit",
            "amount":      total,
            "entry_type":  gl_entry_type,
            "notes":       " > ".join(cat_path)[:280],
            "metadata":    {**meta_common, "leg": "expense"},
        })
        # 7b) Bank / cash / CUSTODY leg — Credit (only when something
        # was paid). Iter-250b · P1.5.q — when `is_custody_pay`, the
        # credit lands on `employee.custody` instead of a bank/cash
        # account; no `account_transactions` entry, no balance
        # recompute (the employee custody balance is purely SSOT'd
        # from `general_ledger`).
        remaining = _r(total - paid)
        if paid > 0:
            if is_custody_pay:
                entries.append({
                    "entity_type": "employee",
                    "entity_id":   payload.custody_employee_id,
                    "sub_account": "custody",
                    "side":        "credit",
                    "amount":      paid,
                    "entry_type":  gl_entry_type,
                    "notes": (
                        f"دفعة من عهدة الموظف — "
                        f"{(custody_emp_doc or {}).get('name') or ''}"
                    )[:280],
                    "metadata": {**meta_common, "leg": "custody",
                                  "pay_source": "custody",
                                  "custody_employee_id":
                                      payload.custody_employee_id,
                                  "custody_employee_name":
                                      (custody_emp_doc or {}).get('name'),
                                  "custody_balance_before":
                                      round(custody_avail, 2),
                                  "custody_balance_after":
                                      round(custody_avail - paid, 2)},
                })
            else:
                entries.append({
                    "entity_type": "bank",
                    "entity_id":   payload.paid_from_account_id,
                    # Iter-246j — bank legs go to sub_account="main" to
                    # match the SSOT's `compute_balance(sub_account="main")`
                    # query, AND carry the `account_transaction_double_write`
                    # source tag so SSOT correctly nets out the double-
                    # counting with `accounts.current_balance` (which
                    # already reflects the cash deduction via
                    # `_recompute_balance`).
                    "sub_account": "main",
                    "side":        "credit",
                    "amount":      paid,
                    "entry_type":  gl_entry_type,
                    "notes": (
                        f"دفعة من حساب {paid_acc_snap.get('name')}"
                        if paid_acc_snap else "دفعة نقدية"
                    )[:280],
                    "metadata": {**meta_common, "leg": "cash",
                                  "source": "account_transaction_double_write",
                                  "withdrawal_method":
                                      payload.withdrawal_method},
                })
        # 7c) Supplier / payable leg — Credit (only when something
        # remains unpaid).  Supplier_id is REQUIRED here; the route
        # already validates it for `supplier_invoice`, and for the
        # other two ops we fall back to a generic «other_payable»
        # entity so the books still balance — this keeps the merchant
        # free to record «I paid 50, owe 94» on a general_expense even
        # without binding a supplier.
        if remaining > 0:
            if payload.supplier_id:
                payable_type, payable_id = "supplier", payload.supplier_id
                payable_sub = "payable"
            else:
                payable_type, payable_id = (
                    "other_payable", "uncategorised_payable")
                payable_sub = None
            entries.append({
                "entity_type": payable_type,
                "entity_id":   payable_id,
                # Iter-246h — sub_account=payable so the credit lands
                # on the same «bucket» that supplier_pay's debit reads
                # and writes.  Without this the books are split into
                # two parallel pools and never net out.
                "sub_account": payable_sub,
                "side":        "credit",
                "amount":      remaining,
                "entry_type":  gl_entry_type,
                "notes": (
                    f"رصيد مستحق على المورد — فاتورة "
                    f"{payload.doc_number or mv_id[:8]}"
                )[:280],
                "metadata": {**meta_common, "leg": "payable"},
            })

        # Write the cash leg into account_transactions FIRST so the
        # existing balance-recompute pipeline reflects the deduction.
        # Iter-250b · P1.5.q — skip entirely for custody-funded movements
        # (no bank touched, custody is SSOT'd from general_ledger only).
        if paid > 0 and payload.paid_from_account_id and not is_custody_pay:
            await db.account_transactions.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "account_id": payload.paid_from_account_id,
                "amount": paid,
                "direction": "out",
                "transaction_type": payload.movement_type,
                "transaction_date": payload.doc_date,
                "description": (
                    f"{payload.movement_type} — "
                    + " > ".join(cat_path)
                )[:280],
                "peer_movement_id": mv_id,
                "created_at": _now(),
                "updated_at": _now(),
            })
            try:
                from accounts_routes import _recompute_balance
                await _recompute_balance(
                    db, uid, payload.paid_from_account_id)
            except Exception:  # noqa: BLE001
                pass

        # Now post the balanced journal atomically.
        try:
            from ledger_core import post_txn_group
            group = await post_txn_group(
                db,
                user_id=uid,
                actor_id=uid,
                actor_name=actor_name,
                entries=entries,
                txn_type=payload.movement_type,
                notes=" > ".join(cat_path),
                metadata={**meta_common,
                          "idempotency_key": f"iter245:{mv_id}"},
            )
            await db.financial_movements.update_one(
                {"id": mv_id, "user_id": uid},
                {"$set": {
                    "ledger_txn_group_id": group["txn_group_id"],
                    "updated_at": _now(),
                }},
            )
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "iter246d journal failed for mv %s: %s", mv_id, e)
            # Roll the movement back to a failed status so the
            # merchant doesn't see ghost docs without a ledger.
            await db.financial_movements.update_one(
                {"id": mv_id, "user_id": uid},
                {"$set": {"status": "ledger_failed",
                          "updated_at": _now()}},
            )
            raise HTTPException(
                500,
                f"فشل ترحيل القيد المحاسبي: {e}",
            )

        # Iter-250b · Phase 4 — Auto-update product catalogue costs
        # after the GL has been posted successfully. Append-only; any
        # failures are logged but never break the invoice creation.
        try:
            mv_fresh = await db.financial_movements.find_one(
                {"id": mv_id, "user_id": uid}, {"_id": 0})
            if mv_fresh:
                cost_result = await _apply_product_cost_updates(
                    db, uid, mv_fresh)
                if cost_result.get("errors"):
                    import logging
                    logging.getLogger(__name__).warning(
                        "Phase4 product-cost partial errors for mv %s: %s",
                        mv_id, cost_result["errors"])
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "Phase4 product-cost update failed for mv %s: %s",
                mv_id, e)

        out = await db.financial_movements.find_one(
            {"id": mv_id, "user_id": uid}, {"_id": 0})
        return out

    @router.get("")
    async def list_movements(
        user: dict = Depends(current_user),
        movement_type: Optional[str] = None,
        supplier_id: Optional[str] = None,
        category_id: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = Query(200, ge=1, le=1000),
    ):
        uid = user["id"]
        # Iter-250b · P1.5.aa — Show ALL movements (including
        # `ledger_failed` ones) so the merchant can never have a
        # "ghost" row that exists in financial_movements but is
        # invisible everywhere. We classify each row's posting
        # status against general_ledger and surface it in the
        # response.
        q: dict = {"user_id": uid,
                    "status": {"$in": ["posted", "ledger_failed"]}}
        if movement_type:
            q["movement_type"] = movement_type
        if supplier_id:
            q["supplier_id"] = supplier_id
        if category_id:
            q["$or"] = [
                {"category_id": category_id},
                {"category_path_ids": category_id},
            ]
        if from_date or to_date:
            q["doc_date"] = {}
            if from_date:
                q["doc_date"]["$gte"] = from_date
            if to_date:
                q["doc_date"]["$lte"] = to_date
        rows = await db.financial_movements.find(
            q,
            {"_id": 0, "receipt_attachment.base64": 0},
        ).sort([("doc_date", -1), ("created_at", -1)]).to_list(limit)

        # ── Iter-250b · P1.5.aa — Enrich each row with posting status.
        # We collect all referenced txn_group_ids and check their
        # existence in `general_ledger` in ONE roundtrip.
        group_ids = list({r.get("ledger_txn_group_id")
                          for r in rows if r.get("ledger_txn_group_id")})
        gl_present: set = set()
        gl_counts: dict = {}
        if group_ids:
            pipeline = [
                {"$match": {"user_id": uid,
                            "txn_group_id": {"$in": group_ids},
                            "status": "posted"}},
                {"$group": {"_id": "$txn_group_id",
                            "n": {"$sum": 1}}},
            ]
            async for g in db.general_ledger.aggregate(pipeline):
                gl_present.add(g["_id"])
                gl_counts[g["_id"]] = g["n"]

        for r in rows:
            tg = r.get("ledger_txn_group_id")
            mv_status = r.get("status") or "posted"
            if mv_status == "ledger_failed":
                ps = "posted_failed"
                reason = "فشل ترحيل القيد إلى GL"
            elif tg and tg in gl_present:
                ps = "posted_to_gl"
                reason = ""
            elif tg and tg not in gl_present:
                ps = "not_posted"
                reason = ("تحمل ledger_txn_group_id لكن لا يوجد قيد "
                          "GL — فشل كتابة جزئي")
            else:
                ps = "not_posted"
                reason = ("بدون ledger_txn_group_id — Legacy أو مسار "
                          "كود قديم لا يكتب GL")
            r["posting_status"] = ps
            r["posting_status_reason"] = reason
            r["gl_entries_count"] = gl_counts.get(tg, 0)

        return {"ok": True, "items": rows, "count": len(rows)}

    @router.get("/{mv_id}")
    async def get_movement(
        mv_id: str,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        mv = await db.financial_movements.find_one(
            {"id": mv_id, "user_id": uid}, {"_id": 0})
        if not mv:
            raise HTTPException(404, "العملية غير موجودة")
        # Iter-250b · P1.5.aa — Add forensic block: GL link, entry
        # count, sample entries (for diagnosis).
        tg = mv.get("ledger_txn_group_id")
        gl_entries = []
        if tg:
            async for g in db.general_ledger.find(
                {"user_id": uid, "txn_group_id": tg},
                {"_id": 0, "id": 1, "side": 1, "amount": 1,
                 "entity_type": 1, "entity_id": 1, "sub_account": 1,
                 "status": 1, "entry_type": 1, "created_at": 1,
                 "notes": 1},
            ).limit(20):
                gl_entries.append(g)
        mv_status = mv.get("status") or "posted"
        if mv_status == "ledger_failed":
            ps = "posted_failed"; reason = "فشل ترحيل القيد إلى GL"
        elif tg and gl_entries:
            ps = "posted_to_gl"; reason = ""
        elif tg and not gl_entries:
            ps = "not_posted"
            reason = "تحمل ledger_txn_group_id لكن لا يوجد قيد GL"
        else:
            ps = "not_posted"
            reason = "بدون ledger_txn_group_id — Legacy أو مسار قديم"
        mv["posting_diagnostic"] = {
            "movement_id":          mv.get("id"),
            "txn_group_id":         tg,
            "has_gl_entries":       bool(gl_entries),
            "gl_entries_count":     len(gl_entries),
            "gl_entries_sample":    gl_entries,
            "movement_status":      mv_status,
            "posting_status":       ps,
            "reason":               reason,
        }
        return mv

    return router
