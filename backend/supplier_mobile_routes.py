"""Mobile supplier management API.

Provides the AMASI mobile app with the same supplier records used by Mezan.
Suppliers are stored in ``db.suppliers`` and supplier invoices are read from
``financial_movements`` (movement_type=supplier_invoice).

Business rules:
- supplier company name is unique per tenant (case/whitespace insensitive)
- supplier phone is unique per tenant (digits only, Saudi 05/+966 normalized)
- one supplier may have multiple bank accounts
- bank accounts are never deleted from this API; they are activated/deactivated
- at most one active bank account is the default
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator


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


def _norm_iban(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _public_bank_account(row: dict) -> dict:
    return {
        "id": str(row.get("id") or ""),
        "bank_name": str(row.get("bank_name") or ""),
        "account_name": str(row.get("account_name") or ""),
        "account_number": str(row.get("account_number") or ""),
        "iban": str(row.get("iban") or ""),
        "status": row.get("status") or "active",
        "is_default": bool(row.get("is_default")),
        "has_image": bool(row.get("image_data_url")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


class MobileSupplierCreateIn(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=160)
    contact_person: Optional[str] = Field(None, max_length=160)
    phone: str = Field(..., min_length=7, max_length=32)
    email: Optional[str] = Field(None, max_length=200)
    category_ids: list[str] = Field(default_factory=list)


class SupplierBankAccountIn(BaseModel):
    bank_name: str = Field(..., min_length=2, max_length=120)
    account_name: str = Field(..., min_length=2, max_length=160)
    account_number: str = Field(default="", max_length=80)
    iban: str = Field(default="", max_length=64)
    image_data_url: Optional[str] = Field(default=None, max_length=1_800_000)
    status: Literal["active", "inactive"] = "active"
    is_default: bool = False

    @field_validator("bank_name", "account_name", "account_number")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    @field_validator("iban")
    @classmethod
    def validate_iban(cls, value: str) -> str:
        normalized = _norm_iban(value)
        if normalized and (len(normalized) < 8 or len(normalized) > 34 or not normalized.isalnum()):
            raise ValueError("supplier_bank_iban_invalid")
        return normalized

    @field_validator("image_data_url")
    @classmethod
    def validate_image(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if not re.match(r"^data:image/(jpeg|jpg|png|webp);base64,", value, flags=re.I):
            raise ValueError("supplier_bank_image_invalid")
        return value


def _normalize_bank_accounts(rows: list[dict]) -> list[dict]:
    result = []
    default_seen = False
    for raw in rows or []:
        row = dict(raw)
        row["status"] = "inactive" if row.get("status") == "inactive" else "active"
        if row["status"] != "active":
            row["is_default"] = False
        elif row.get("is_default") and not default_seen:
            default_seen = True
            row["is_default"] = True
        else:
            row["is_default"] = False
        result.append(row)
    if not default_seen:
        for row in result:
            if row.get("status") == "active":
                row["is_default"] = True
                break
    return result


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
            bank_accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
            rows.append({
                "id": sid,
                "company_name": supplier.get("company_name") or "مورد",
                "contact_person": supplier.get("contact_person") or "",
                "phone": supplier.get("phone") or "",
                "email": supplier.get("email") or "",
                "status": supplier.get("status") or "active",
                "category_ids": supplier.get("category_ids") or [],
                "bank_accounts": [_public_bank_account(account) for account in bank_accounts],
                "active_bank_accounts_count": sum(1 for account in bank_accounts if account.get("status") == "active"),
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
            "bank_accounts": [],
            "created_at": now,
            "updated_at": now,
            "created_source": "amasi_mobile",
        }
        await db.suppliers.insert_one(doc)
        doc.pop("_id", None)
        return {"ok": True, "supplier": doc}

    async def get_supplier(uid: str, supplier_id: str) -> dict:
        supplier = await db.suppliers.find_one({"user_id": uid, "id": supplier_id}, {"_id": 0})
        if not supplier:
            raise HTTPException(status_code=404, detail={"code": "supplier_not_found", "message": "المورد غير موجود."})
        return supplier

    def assert_unique_account(accounts: list[dict], payload: SupplierBankAccountIn, current_id: str = "") -> None:
        for row in accounts:
            if str(row.get("id") or "") == current_id:
                continue
            if payload.iban and _norm_iban(row.get("iban") or "") == payload.iban:
                raise HTTPException(status_code=409, detail={"code": "supplier_bank_iban_duplicate", "message": "رقم الآيبان مسجل من قبل لهذا المورد."})
            same_number = payload.account_number and str(row.get("account_number") or "").strip() == payload.account_number
            same_bank = _norm_name(row.get("bank_name") or "") == _norm_name(payload.bank_name)
            if same_number and same_bank:
                raise HTTPException(status_code=409, detail={"code": "supplier_bank_account_duplicate", "message": "رقم الحساب مسجل من قبل لهذا البنك."})

    @router.post("/{supplier_id}/bank-accounts")
    async def add_bank_account(supplier_id: str, payload: SupplierBankAccountIn, user: dict = Depends(current_user)):
        uid = user["id"]
        supplier = await get_supplier(uid, supplier_id)
        accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
        assert_unique_account(accounts, payload)
        now = _now()
        make_default = payload.status == "active" and (payload.is_default or not any(a.get("status") == "active" and a.get("is_default") for a in accounts))
        if make_default:
            for account in accounts:
                account["is_default"] = False
        row = {
            "id": str(uuid.uuid4()),
            "bank_name": payload.bank_name,
            "account_name": payload.account_name,
            "account_number": payload.account_number,
            "iban": payload.iban,
            "image_data_url": payload.image_data_url,
            "status": payload.status,
            "is_default": make_default,
            "created_at": now,
            "updated_at": now,
        }
        accounts.append(row)
        accounts = _normalize_bank_accounts(accounts)
        await db.suppliers.update_one(
            {"user_id": uid, "id": supplier_id},
            {"$set": {"bank_accounts": accounts, "updated_at": now}},
        )
        return {"ok": True, "bank_account": _public_bank_account(row)}

    @router.put("/{supplier_id}/bank-accounts/{account_id}")
    async def update_bank_account(supplier_id: str, account_id: str, payload: SupplierBankAccountIn, user: dict = Depends(current_user)):
        uid = user["id"]
        supplier = await get_supplier(uid, supplier_id)
        accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
        target = next((row for row in accounts if str(row.get("id") or "") == account_id), None)
        if not target:
            raise HTTPException(status_code=404, detail={"code": "supplier_bank_account_not_found", "message": "الحساب البنكي غير موجود."})
        assert_unique_account(accounts, payload, account_id)
        now = _now()
        previous_image = target.get("image_data_url")
        if payload.is_default and payload.status == "active":
            for account in accounts:
                account["is_default"] = False
        target.update({
            "bank_name": payload.bank_name,
            "account_name": payload.account_name,
            "account_number": payload.account_number,
            "iban": payload.iban,
            "image_data_url": payload.image_data_url if payload.image_data_url is not None else previous_image,
            "status": payload.status,
            "is_default": payload.is_default and payload.status == "active",
            "updated_at": now,
        })
        accounts = _normalize_bank_accounts(accounts)
        await db.suppliers.update_one(
            {"user_id": uid, "id": supplier_id},
            {"$set": {"bank_accounts": accounts, "updated_at": now}},
        )
        saved = next(row for row in accounts if str(row.get("id") or "") == account_id)
        return {"ok": True, "bank_account": _public_bank_account(saved)}

    return router
