"""Purchase Invoices — Iter-103 Phase 1 (no inventory yet)
==========================================================

Lightweight supplier purchase-invoice register that REUSES the existing
`liabilities` row of `kind=supplier` for the money side. This module
ONLY adds the invoice header and line items; payments, status and the
financial-position impact continue to live on the linked liability row
(single source of truth, no duplicated balance math).

Collections
-----------
purchase_invoices  (NEW)
    id, user_id, supplier_counterparty_id, supplier_name,
    invoice_number?, invoice_date, due_date?,
    lines: [ { id, product_name, sku?, quantity, unit_price, line_total } ],
    subtotal, tax_amount, total,
    liability_id      — points to the auto-created supplier liability,
    notes, status (derived from liability),
    created_at, updated_at

Why we DON'T touch inventory here
---------------------------------
The merchant explicitly chose Option B: track purchases & supplier
balances without on-hand quantity or FIFO/AVG cost recalculation.
We therefore record `quantity` and `unit_price` per line for the paper
trail only, but never touch `product_costs` or any stock collection.

Endpoints (all under /api/purchase-invoices)
--------------------------------------------
POST   /                        create + auto-create supplier liability
GET    /                        list with filters (?supplier_id, ?status, ?from, ?to)
GET    /{id}                    single + enriched payment state
PUT    /{id}                    edit lines/notes (refuses if any payment recorded)
DELETE /{id}                    delete (refuses if any payment recorded)
GET    /supplier/{cp_id}/statement
                                aggregated supplier statement
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, validator

from auth import get_current_user_from_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(v) -> float:
    return round(float(v or 0), 2)


# ── Pydantic models ────────────────────────────────────────────────────
class InvoiceLine(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    sku: Optional[str] = Field(None, max_length=80)
    quantity: float = Field(..., gt=0)
    unit_price: float = Field(..., ge=0)


class PurchaseInvoiceCreate(BaseModel):
    supplier_counterparty_id: str = Field(..., min_length=1)
    invoice_number: Optional[str] = Field(None, max_length=80)
    invoice_date: str = Field(..., min_length=10, max_length=10)   # YYYY-MM-DD
    due_date: Optional[str] = Field(None, min_length=10, max_length=10)
    lines: List[InvoiceLine] = Field(..., min_items=1)
    tax_amount: float = Field(0.0, ge=0)
    notes: Optional[str] = Field("", max_length=2000)

    @validator("lines")
    def _at_least_one(cls, v):
        if not v:
            raise ValueError("الفاتورة يجب أن تحتوي على بند واحد على الأقل")
        return v


class PurchaseInvoiceUpdate(BaseModel):
    invoice_number: Optional[str] = Field(None, max_length=80)
    invoice_date: Optional[str] = Field(None, min_length=10, max_length=10)
    due_date: Optional[str] = Field(None, min_length=10, max_length=10)
    lines: Optional[List[InvoiceLine]] = None
    tax_amount: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = Field(None, max_length=2000)


# ── Helpers ────────────────────────────────────────────────────────────
def _compute_totals(lines: list[dict], tax_amount: float) -> tuple[float, float, float]:
    subtotal = sum(_round(l["quantity"]) * _round(l["unit_price"]) for l in lines)
    subtotal = _round(subtotal)
    tax = _round(tax_amount)
    total = _round(subtotal + tax)
    return subtotal, tax, total


def _enrich_lines(lines: list[InvoiceLine]) -> list[dict]:
    out: list[dict] = []
    for ln in lines:
        q = _round(ln.quantity)
        p = _round(ln.unit_price)
        out.append({
            "id": str(uuid.uuid4()),
            "product_name": ln.product_name.strip(),
            "sku": (ln.sku or "").strip() or None,
            "quantity": q,
            "unit_price": p,
            "line_total": _round(q * p),
        })
    return out


async def _enrich_with_liability(db, user_id: str, doc: dict) -> dict:
    """Pull live payment state from the linked liability row."""
    out = {k: v for k, v in (doc or {}).items() if not k.startswith("_")}
    liab_id = out.get("liability_id")
    if liab_id:
        liab = await db.liabilities.find_one(
            {"id": liab_id, "user_id": user_id},
            {"_id": 0, "paid_amount": 1, "expected_amount": 1, "status": 1},
        )
        if liab:
            paid = _round(liab.get("paid_amount"))
            expected = _round(liab.get("expected_amount"))
            out["paid_amount"] = paid
            out["remaining_amount"] = max(0.0, _round(expected - paid))
            out["status"] = liab.get("status") or "unpaid"
        else:
            out["paid_amount"] = 0.0
            out["remaining_amount"] = _round(out.get("total"))
            out["status"] = "unpaid"
    else:
        out["paid_amount"] = 0.0
        out["remaining_amount"] = _round(out.get("total"))
        out["status"] = "unpaid"
    return out


async def ensure_purchase_invoices_indexes(db) -> None:
    """Idempotent indexes for purchase_invoices."""
    try:
        await db.purchase_invoices.create_index(
            [("user_id", 1), ("id", 1)], unique=True,
            name="pinv_pk",
        )
    except Exception:
        pass
    try:
        await db.purchase_invoices.create_index(
            [("user_id", 1), ("supplier_counterparty_id", 1),
             ("invoice_date", -1)],
            name="pinv_supplier_date",
        )
    except Exception:
        pass


# ── Router ─────────────────────────────────────────────────────────────
def attach_purchase_invoice_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/purchase-invoices", tags=["purchase-invoices"])

    # ── POST / ────────────────────────────────────────────────────────
    @router.post("")
    async def create_invoice(
        payload: PurchaseInvoiceCreate,
        user: dict = Depends(current_user),
    ):
        # 1) Resolve supplier from counterparties
        cp = await db.counterparties.find_one(
            {"id": payload.supplier_counterparty_id, "user_id": user["id"],
             "kind": {"$in": ["supplier", "general"]}},
            {"_id": 0},
        )
        if not cp:
            raise HTTPException(404, "المورد غير موجود في قائمة الأطراف")

        # 2) Build invoice doc
        lines = _enrich_lines(payload.lines)
        subtotal, tax, total = _compute_totals(lines, payload.tax_amount)
        if total <= 0:
            raise HTTPException(400, "إجمالي الفاتورة يجب أن يكون أكبر من صفر")

        inv_id = str(uuid.uuid4())
        liab_id = str(uuid.uuid4())
        now = _now()

        invoice_doc = {
            "id": inv_id,
            "user_id": user["id"],
            "supplier_counterparty_id": cp["id"],
            "supplier_name": cp["name"],
            "invoice_number": (payload.invoice_number or "").strip() or None,
            "invoice_date": payload.invoice_date,
            "due_date": payload.due_date,
            "lines": lines,
            "subtotal": subtotal,
            "tax_amount": tax,
            "total": total,
            "liability_id": liab_id,
            "notes": payload.notes or "",
            "created_at": now,
            "updated_at": now,
        }

        # 3) Create the linked supplier liability — single source of
        #    truth for payment state. We never duplicate balances here.
        liability_doc = {
            "id": liab_id,
            "user_id": user["id"],
            "kind": "supplier",
            "supplier_name": cp["name"],
            "counterparty_id": cp["id"],
            "expected_amount": total,
            "paid_amount": 0.0,
            "advance_deducted": 0.0,
            "due_date": payload.due_date or payload.invoice_date,
            "status": "unpaid",
            "description": (
                f"فاتورة شراء "
                f"{payload.invoice_number or '—'} — {cp['name']}"
            ),
            "notes": payload.notes or "",
            "auto_generated": True,
            "source": "purchase_invoice",        # tag for traceability
            "purchase_invoice_id": inv_id,
            "created_at": now,
            "updated_at": now,
        }

        # 4) Insert both atomically (best-effort: if liab fails we don't
        #    leave a dangling invoice).
        await db.liabilities.insert_one(liability_doc)
        try:
            await db.purchase_invoices.insert_one(invoice_doc)
        except Exception:
            # rollback liability so we don't leak it
            await db.liabilities.delete_one({"id": liab_id})
            raise

        return await _enrich_with_liability(db, user["id"], invoice_doc)

    # ── GET / ─────────────────────────────────────────────────────────
    @router.get("")
    async def list_invoices(
        supplier_id: Optional[str] = Query(None),
        status: Optional[Literal["unpaid", "partial", "paid"]] = Query(None),
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to"),
        limit: int = Query(200, ge=1, le=2000),
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if supplier_id:
            q["supplier_counterparty_id"] = supplier_id
        if from_date:
            q["invoice_date"] = {"$gte": from_date}
        if to_date:
            q.setdefault("invoice_date", {})["$lte"] = to_date

        cur = db.purchase_invoices.find(q, {"_id": 0}).sort(
            [("invoice_date", -1), ("created_at", -1)],
        ).limit(limit)
        items = []
        async for d in cur:
            enriched = await _enrich_with_liability(db, user["id"], d)
            if status and enriched.get("status") != status:
                continue
            items.append(enriched)
        return {"items": items, "total": len(items)}

    # ── GET /{id} ─────────────────────────────────────────────────────
    @router.get("/{inv_id}")
    async def get_invoice(inv_id: str, user: dict = Depends(current_user)):
        doc = await db.purchase_invoices.find_one(
            {"id": inv_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(404, "الفاتورة غير موجودة")
        return await _enrich_with_liability(db, user["id"], doc)

    # ── PUT /{id} ─────────────────────────────────────────────────────
    @router.put("/{inv_id}")
    async def update_invoice(
        inv_id: str, payload: PurchaseInvoiceUpdate,
        user: dict = Depends(current_user),
    ):
        existing = await db.purchase_invoices.find_one(
            {"id": inv_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not existing:
            raise HTTPException(404, "الفاتورة غير موجودة")

        liab = await db.liabilities.find_one(
            {"id": existing["liability_id"], "user_id": user["id"]},
            {"_id": 0, "paid_amount": 1, "status": 1},
        )
        if liab and _round(liab.get("paid_amount")) > 0:
            raise HTTPException(
                400,
                "لا يمكن تعديل فاتورة سُدِّد منها مبلغ. احذف السدادات أولاً أو أنشئ فاتورة جديدة.",
            )

        upd: dict = {"updated_at": _now()}
        if payload.invoice_number is not None:
            upd["invoice_number"] = payload.invoice_number.strip() or None
        if payload.invoice_date:
            upd["invoice_date"] = payload.invoice_date
        if payload.due_date is not None:
            upd["due_date"] = payload.due_date or None
        if payload.notes is not None:
            upd["notes"] = payload.notes
        # If lines or tax changed, recompute totals + sync liability.
        recompute = payload.lines is not None or payload.tax_amount is not None
        if recompute:
            new_lines = (
                _enrich_lines(payload.lines)
                if payload.lines is not None
                else existing.get("lines", [])
            )
            new_tax = (
                _round(payload.tax_amount)
                if payload.tax_amount is not None
                else _round(existing.get("tax_amount"))
            )
            subtotal, tax, total = _compute_totals(new_lines, new_tax)
            if total <= 0:
                raise HTTPException(400, "إجمالي الفاتورة يجب أن يكون أكبر من صفر")
            upd["lines"] = new_lines
            upd["subtotal"] = subtotal
            upd["tax_amount"] = tax
            upd["total"] = total

        await db.purchase_invoices.update_one(
            {"id": inv_id, "user_id": user["id"]}, {"$set": upd},
        )

        if recompute:
            await db.liabilities.update_one(
                {"id": existing["liability_id"], "user_id": user["id"]},
                {"$set": {
                    "expected_amount": upd["total"],
                    "status": "unpaid",      # paid_amount is 0 here
                    "updated_at": _now(),
                }},
            )

        fresh = await db.purchase_invoices.find_one(
            {"id": inv_id, "user_id": user["id"]}, {"_id": 0},
        )
        return await _enrich_with_liability(db, user["id"], fresh)

    # ── DELETE /{id} ──────────────────────────────────────────────────
    @router.delete("/{inv_id}")
    async def delete_invoice(inv_id: str, user: dict = Depends(current_user)):
        doc = await db.purchase_invoices.find_one(
            {"id": inv_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(404, "الفاتورة غير موجودة")

        liab = await db.liabilities.find_one(
            {"id": doc["liability_id"], "user_id": user["id"]},
            {"_id": 0, "paid_amount": 1},
        )
        if liab and _round(liab.get("paid_amount")) > 0:
            raise HTTPException(
                400,
                "لا يمكن حذف فاتورة سُدِّد منها مبلغ. احذف السدادات أولاً.",
            )

        # Remove invoice + its (unpaid) liability together.
        await db.purchase_invoices.delete_one(
            {"id": inv_id, "user_id": user["id"]},
        )
        await db.liabilities.delete_one(
            {"id": doc["liability_id"], "user_id": user["id"]},
        )
        return {"ok": True}

    # ── GET /supplier/{cp_id}/statement ───────────────────────────────
    @router.get("/supplier/{cp_id}/statement")
    async def supplier_statement(
        cp_id: str, user: dict = Depends(current_user),
    ):
        """Aggregated statement for one supplier:
            total_invoiced   = SUM(invoice.total) of all invoices
            total_paid       = SUM(paid_amount) on linked liabilities
            balance_owed     = total_invoiced − total_paid (>=0)
            invoices         = each row with status + remaining
        """
        cp = await db.counterparties.find_one(
            {"id": cp_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not cp:
            raise HTTPException(404, "المورد غير موجود")

        rows = []
        total_invoiced = 0.0
        total_paid = 0.0
        async for d in db.purchase_invoices.find(
            {"user_id": user["id"], "supplier_counterparty_id": cp_id},
            {"_id": 0},
        ).sort([("invoice_date", -1)]):
            enriched = await _enrich_with_liability(db, user["id"], d)
            rows.append({
                "id": enriched["id"],
                "invoice_number": enriched.get("invoice_number"),
                "invoice_date": enriched["invoice_date"],
                "due_date": enriched.get("due_date"),
                "total": enriched["total"],
                "paid_amount": enriched["paid_amount"],
                "remaining_amount": enriched["remaining_amount"],
                "status": enriched["status"],
            })
            total_invoiced += enriched["total"]
            total_paid += enriched["paid_amount"]

        total_invoiced = _round(total_invoiced)
        total_paid = _round(total_paid)
        return {
            "supplier": {"id": cp["id"], "name": cp["name"], "kind": cp.get("kind")},
            "totals": {
                "total_invoiced": total_invoiced,
                "total_paid": total_paid,
                "balance_owed": max(0.0, _round(total_invoiced - total_paid)),
            },
            "invoices": rows,
            "generated_at": _now(),
        }

    parent_router.include_router(router)
