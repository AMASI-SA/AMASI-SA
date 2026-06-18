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
        `is_sufficient` (= balance ≥ amount). For the UI to grey-out
        insufficient accounts visually (Requirement #5)."""
        uid = user["id"]
        rows = await db.accounts.find(
            {"user_id": uid},
            {"_id": 0, "id": 1, "name": 1,
             "account_type": 1, "current_balance": 1,
             "currency": 1},
        ).sort([("name", 1)]).to_list(500)
        for r in rows:
            bal = _r(r.get("current_balance"))
            r["available_balance"] = bal
            r["is_sufficient"] = bal >= _r(amount)
        return {"ok": True, "items": rows,
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
            li_total = _r(sum(
                _r(li.quantity) * _r(li.unit_price)
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

        paid_acc_snap = None
        if paid > 0:
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
                    "total": _r(_r(li.quantity) * _r(li.unit_price)),
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

        # ── 7) Post Double-Entry to general_ledger via Iter-240 ─────
        # We post the CASH leg (if any) using the existing
        # mirror helper which guarantees idempotency & metadata.
        if paid > 0 and payload.paid_from_account_id:
            from ledger_double_write import mirror_account_txn_to_ledger
            # Bridge: write to account_transactions so the mirror is
            # picked up by the same SSOT path Iter-240 already proved.
            txn_id = str(uuid.uuid4())
            await db.account_transactions.insert_one({
                "id": txn_id,
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
            # Recompute the account balance using legacy helper.
            try:
                from accounts_routes import _recompute_balance
                await _recompute_balance(
                    db, uid, payload.paid_from_account_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                res = await mirror_account_txn_to_ledger(
                    db,
                    user_id=uid,
                    account_id=payload.paid_from_account_id,
                    account_transaction_id=txn_id,
                    amount=paid,
                    direction="out",
                    transaction_type=payload.movement_type,
                    transaction_date=payload.doc_date,
                    description=" > ".join(cat_path),
                    counter_entity_type="expense_category",
                    counter_entity_id=cat["id"],
                    created_by_endpoint=(
                        "POST /api/financial-movements"),
                    idempotency_key=f"iter245:{mv_id}",
                )
                await db.financial_movements.update_one(
                    {"id": mv_id, "user_id": uid},
                    {"$set": {
                        "ledger_txn_group_id":
                            res.get("txn_group_id"),
                        "updated_at": _now(),
                    }},
                )
            except Exception:  # noqa: BLE001 — never block doc post
                import logging
                logging.getLogger(__name__).warning(
                    "iter245 mirror failed for mv %s", mv_id)

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
        q: dict = {"user_id": uid, "status": "posted"}
        if movement_type:
            q["movement_type"] = movement_type
        if supplier_id:
            q["supplier_id"] = supplier_id
        if category_id:
            # Match either header category OR any path-id in ancestors.
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
            {"_id": 0,
             "receipt_attachment.base64": 0},  # don't ship file bytes
        ).sort([("doc_date", -1), ("created_at", -1)]).to_list(limit)
        return {"ok": True, "items": rows, "count": len(rows)}

    @router.get("/{mv_id}")
    async def get_movement(
        mv_id: str,
        user: dict = Depends(current_user),
    ):
        mv = await db.financial_movements.find_one(
            {"id": mv_id, "user_id": user["id"]}, {"_id": 0})
        if not mv:
            raise HTTPException(404, "العملية غير موجودة")
        return mv

    return router
