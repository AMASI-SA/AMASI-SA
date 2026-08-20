"""Mobile supplier management API.

Provides the AMASI mobile app with the same supplier records used by Mezan.
Suppliers are stored in ``db.suppliers`` and supplier invoices are read from
``financial_movements`` (movement_type=supplier_invoice).

Business rules:
- supplier company name is unique per tenant (case/whitespace insensitive)
- supplier phone is unique per tenant (digits only, Saudi 05/+966 normalized)
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower()).replace("ـ", "")


def _norm_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00966"):
        digits = digits[2:]
    if digits.startswith("9660"):
        digits = "966" + digits[4:]
    if re.fullmatch(r"05\d{8}", digits):
        digits = "966" + digits[1:]
    elif re.fullmatch(r"5\d{8}", digits):
        digits = "966" + digits
    return digits


class MobileSupplierCreateIn(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=160)
    contact_person: Optional[str] = Field(None, max_length=160)
    phone: str = Field(..., min_length=7, max_length=32)
    email: Optional[str] = Field(None, max_length=200)
    category_ids: list[str] = Field(default_factory=list)


def make_supplier_mobile_router(db, current_user):
    router = APIRouter(prefix="/mobile", tags=["mobile", "suppliers"])

    @router.get("")
    async def list_mobile_suppliers(
        user: dict = Depends(current_user),
        q: Optional[str] = Query(None, max_length=100),
    ):
        uid = user["id"]
        suppliers = await db.suppliers.find(
            {"user_id": uid}, {"_id": 0}
        ).sort([("created_at", -1)]).to_list(5000)

        needle = _norm_name(q or "")
        if needle:
            suppliers = [s for s in suppliers if needle in _norm_name(s.get("company_name") or "")]

        ids = [str(s.get("id") or "") for s in suppliers if s.get("id")]
        invoice_map: dict[str, list[dict]] = {sid: [] for sid in ids}
        totals_map: dict[str, dict] = {
            sid: {"invoices_count": 0, "invoices_total": 0.0, "paid_total": 0.0}
            for sid in ids
        }
        if ids:
            cursor = db.financial_movements.find(
                {
                    "user_id": uid,
                    "movement_type": "supplier_invoice",
                    "supplier_id": {"$in": ids},
                },
                {
                    "_id": 0,
                    "id": 1,
                    "supplier_id": 1,
                    "doc_number": 1,
                    "invoice_no": 1,
                    "doc_date": 1,
                    "invoice_date": 1,
                    "total_amount": 1,
                    "paid_amount": 1,
                    "notes": 1,
                    "created_at": 1,
                },
            ).sort([("doc_date", -1), ("created_at", -1)])
            async for inv in cursor:
                sid = str(inv.get("supplier_id") or "")
                if sid not in invoice_map:
                    continue
                amount = round(float(inv.get("total_amount") or 0), 2)
                paid = round(float(inv.get("paid_amount") or 0), 2)
                invoice_map[sid].append({
                    "id": str(inv.get("id") or ""),
                    "number": str(inv.get("doc_number") or inv.get("invoice_no") or ""),
                    "date": inv.get("doc_date") or inv.get("invoice_date") or inv.get("created_at"),
                    "amount": amount,
                    "paid_amount": paid,
                    "remaining_amount": round(max(amount - paid, 0), 2),
                    "notes": str(inv.get("notes") or ""),
                })
                totals_map[sid]["invoices_count"] += 1
                totals_map[sid]["invoices_total"] += amount
                totals_map[sid]["paid_total"] += paid

        rows = []
        for supplier in suppliers:
            sid = str(supplier.get("id") or "")
            t = totals_map.get(sid, {"invoices_count": 0, "invoices_total": 0.0, "paid_total": 0.0})
            rows.append({
                "id": sid,
                "company_name": supplier.get("company_name") or "مورد",
                "contact_person": supplier.get("contact_person") or "",
                "phone": supplier.get("phone") or "",
                "email": supplier.get("email") or "",
                "status": supplier.get("status") or "active",
                "category_ids": supplier.get("category_ids") or [],
                "invoices_count": int(t["invoices_count"]),
                "invoices_total": round(float(t["invoices_total"]), 2),
                "paid_total": round(float(t["paid_total"]), 2),
                "remaining_total": round(max(float(t["invoices_total"]) - float(t["paid_total"]), 0), 2),
                "invoices": invoice_map.get(sid, []),
            })

        return {
            "items": rows,
            "totals": {
                "suppliers_count": len(rows),
                "invoices_count": sum(r["invoices_count"] for r in rows),
                "invoices_total": round(sum(r["invoices_total"] for r in rows), 2),
                "paid_total": round(sum(r["paid_total"] for r in rows), 2),
                "remaining_total": round(sum(r["remaining_total"] for r in rows), 2),
            },
        }

    @router.post("")
    async def create_mobile_supplier(payload: MobileSupplierCreateIn, user: dict = Depends(current_user)):
        uid = user["id"]
        company_name = re.sub(r"\s+", " ", payload.company_name.strip())
        phone = _norm_phone(payload.phone)
        if len(phone) < 7:
            raise HTTPException(status_code=422, detail={"code": "supplier_phone_invalid", "message": "رقم جوال المورد غير صالح."})

        existing = await db.suppliers.find(
            {"user_id": uid},
            {"_id": 0, "id": 1, "company_name": 1, "phone": 1},
        ).to_list(5000)
        for row in existing:
            if _norm_name(row.get("company_name") or "") == _norm_name(company_name):
                raise HTTPException(status_code=409, detail={"code": "supplier_name_duplicate", "message": "يوجد مورد مسجل بنفس الاسم. اسم المورد يجب أن يكون فريدًا."})
            if _norm_phone(row.get("phone") or "") == phone:
                raise HTTPException(status_code=409, detail={"code": "supplier_phone_duplicate", "message": "يوجد مورد مسجل بنفس رقم الجوال. رقم الجوال يجب أن يكون فريدًا."})

        now = _now()
        sid = str(uuid.uuid4())
        doc = {
            "id": sid,
            "user_id": uid,
            "company_name": company_name,
            "contact_person": (payload.contact_person or "").strip(),
            "phone": phone,
            "email": (payload.email or "").strip().lower(),
            "status": "active",
            "category_ids": [str(x).strip() for x in payload.category_ids if str(x).strip()],
            "created_at": now,
            "updated_at": now,
            "created_source": "amasi_mobile",
        }
        await db.suppliers.insert_one(doc)
        doc.pop("_id", None)
        return {"ok": True, "supplier": doc}

    return router
