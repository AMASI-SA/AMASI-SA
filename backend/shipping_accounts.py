"""Accounts payable for deferred shipping companies.

A deferred shipping company is one whose cost is NOT deducted from Salla's
transfer to the bank. We accrue what the merchant owes them based on each
analysis (orders_count × cost_per_order × (1 + VAT)) and let the merchant
record payments against that liability over time.

Each company's account shows:
- total_owed   = sum of shipping_breakdown.total_cost across analyses (deferred rows only)
- total_paid   = sum of shipping_payments.amount
- remaining    = total_owed − total_paid
- payments[]   = ledger entries (date, amount, invoice_number, note)

Endpoints (all under /api/shipping-accounts):
- GET    /                          → list every deferred company w/ totals + payments
- GET    /{company}/payments        → payments for one company
- POST   /{company}/payments        → record a payment
- DELETE /payments/{payment_id}     → delete a payment
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db, ensure_user_settings, DEFAULT_SHIPPING_COMPANIES


class PaymentIn(BaseModel):
    amount: float = Field(gt=0)
    payment_date: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD
    invoice_number: Optional[str] = ""
    note: Optional[str] = ""


def _build_router(db) -> APIRouter:
    router = APIRouter(prefix="/shipping-accounts", tags=["shipping-accounts"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _deferred_company_names(user_id: str) -> list[str]:
        settings = await ensure_user_settings(db, user_id)
        return [
            (s.get("name") or "").strip()
            for s in settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES)
            if s.get("is_deferred") and (s.get("name") or "").strip()
        ]

    async def _owed_per_company(user_id: str) -> dict[str, dict]:
        """Aggregate {company_name: {owed, orders_count, cost_per_order}} from analyses.

        Only rows flagged is_deferred=True in shipping_breakdown are counted.
        We sum across ALL analyses (no date filter — this is total accrued).
        """
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$unwind": "$report.shipping_breakdown"},
            {"$match": {"report.shipping_breakdown.is_deferred": True}},
            {"$group": {
                "_id": "$report.shipping_breakdown.name",
                "owed": {"$sum": "$report.shipping_breakdown.total_cost"},
                "orders_count": {"$sum": "$report.shipping_breakdown.orders_count"},
                "cost_per_order": {"$last": "$report.shipping_breakdown.cost_per_order"},
            }},
        ]
        out: dict[str, dict] = {}
        async for doc in db.analyses.aggregate(pipeline):
            name = (doc.get("_id") or "").strip()
            if not name:
                continue
            out[name] = {
                "owed": round(float(doc.get("owed", 0) or 0), 2),
                "orders_count": int(doc.get("orders_count", 0) or 0),
                "cost_per_order": float(doc.get("cost_per_order", 0) or 0),
            }
        return out

    async def _paid_per_company(user_id: str) -> dict[str, float]:
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$group": {"_id": "$company_name", "paid": {"$sum": "$amount"}}},
        ]
        out: dict[str, float] = {}
        async for doc in db.shipping_payments.aggregate(pipeline):
            out[(doc.get("_id") or "").strip()] = round(float(doc.get("paid", 0) or 0), 2)
        return out

    @router.get("")
    async def list_accounts(user: dict = Depends(current_user)):
        configured = await _deferred_company_names(user["id"])
        owed_map = await _owed_per_company(user["id"])
        paid_map = await _paid_per_company(user["id"])

        # Union: configured ∪ any company with accrued/paid balance in DB
        all_names = list({*configured, *owed_map.keys(), *paid_map.keys()})
        accounts = []
        for name in sorted(all_names):
            owed_info = owed_map.get(name, {"owed": 0.0, "orders_count": 0, "cost_per_order": 0.0})
            owed = owed_info["owed"]
            paid = paid_map.get(name, 0.0)
            accounts.append({
                "name": name,
                "is_configured": name in configured,
                "orders_count": owed_info["orders_count"],
                "cost_per_order": owed_info["cost_per_order"],
                "total_owed": round(owed, 2),
                "total_paid": round(paid, 2),
                "remaining": round(owed - paid, 2),
            })
        return {
            "accounts": accounts,
            "totals": {
                "total_owed": round(sum(a["total_owed"] for a in accounts), 2),
                "total_paid": round(sum(a["total_paid"] for a in accounts), 2),
                "remaining": round(sum(a["remaining"] for a in accounts), 2),
            },
        }

    @router.get("/{company}/payments")
    async def list_payments(company: str, user: dict = Depends(current_user)):
        items = await db.shipping_payments.find(
            {"user_id": user["id"], "company_name": company.strip()},
            {"_id": 0},
        ).sort("payment_date", -1).to_list(500)
        return {"payments": items}

    @router.post("/{company}/payments")
    async def add_payment(company: str, payload: PaymentIn, user: dict = Depends(current_user)):
        try:
            datetime.strptime(payload.payment_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
        if not company.strip():
            raise HTTPException(status_code=400, detail="اسم الشركة مطلوب")
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "company_name": company.strip(),
            "amount": round(float(payload.amount), 2),
            "payment_date": payload.payment_date,
            "invoice_number": (payload.invoice_number or "").strip(),
            "note": (payload.note or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.shipping_payments.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.delete("/payments/{payment_id}")
    async def delete_payment(payment_id: str, user: dict = Depends(current_user)):
        res = await db.shipping_payments.delete_one({"id": payment_id, "user_id": user["id"]})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="الدفعة غير موجودة")
        return {"ok": True}

    return router


def attach_shipping_accounts_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
