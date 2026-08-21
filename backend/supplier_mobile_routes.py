"""AMASI mobile adapter for the Mezan 2 supplier SSOT.

This compatibility router intentionally reads and writes ONLY
``mezan_suppliers_v2``. Legacy ``suppliers`` and ``counterparties`` records are
never consulted, so the mobile supplier list is identical to /suppliers-v2.

The adapter keeps the existing mobile response shape and bank-account URLs so
older installed clients can move to the correct SSOT without a parallel store.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from fulfillment_v2_routes import _actor_context, _require_permission
from mezan_supplier_management_routes import (
    MEZAN_SUPPLIERS_V2,
    MEZAN_SUPPLIER_INVOICES_V2,
    SUPPLIERS_MANAGE_PERMISSION,
    SUPPLIERS_READ_PERMISSION,
)


def _now():
    return datetime.now(timezone.utc)


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold()).replace("ـ", "")


def _norm_iban(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _riyals(halalas) -> float:
    return round(int(halalas or 0) / 100, 2)


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
    result: list[dict] = []
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
    router = APIRouter(prefix="/mobile", tags=["mobile", "suppliers-v2"])

    async def context_for(user: dict, permission: str):
        context = await _actor_context(db, user)
        _require_permission(context, permission)
        return context

    async def get_supplier(merchant_id: str, supplier_id: str) -> dict:
        supplier = await db[MEZAN_SUPPLIERS_V2].find_one(
            {"user_id": merchant_id, "id": supplier_id}, {"_id": 0}
        )
        if not supplier:
            raise HTTPException(
                status_code=404,
                detail={"code": "mezan_supplier_not_found", "message": "المورد غير موجود في ميزان 2."},
            )
        return supplier

    @router.get("")
    async def list_mobile_suppliers(
        user: dict = Depends(current_user),
        q: Optional[str] = Query(None, max_length=100),
    ):
        context = await context_for(user, SUPPLIERS_READ_PERMISSION)
        merchant_id = context["merchant_id"]
        suppliers = await db[MEZAN_SUPPLIERS_V2].find(
            {"user_id": merchant_id}, {"_id": 0, "user_id": 0, "_lc_company_name": 0}
        ).sort([("status", 1), ("company_name", 1)]).to_list(2000)

        needle = _norm_name(q or "")
        if needle:
            suppliers = [
                row for row in suppliers
                if needle in _norm_name(row.get("company_name") or "")
                or needle in _norm_name(row.get("contact_person") or "")
            ]

        ids = [str(row.get("id") or "") for row in suppliers if row.get("id")]
        invoice_map: dict[str, list[dict]] = {sid: [] for sid in ids}
        totals_map: dict[str, dict] = {
            sid: {"invoices_count": 0, "invoices_total": 0.0, "paid_total": 0.0, "remaining_total": 0.0}
            for sid in ids
        }
        if ids:
            cursor = db[MEZAN_SUPPLIER_INVOICES_V2].find(
                {
                    "user_id": merchant_id,
                    "supplier_id": {"$in": ids},
                    "experiment_mode": {"$ne": True},
                },
                {
                    "_id": 0,
                    "id": 1,
                    "supplier_id": 1,
                    "invoice_number": 1,
                    "approved_at": 1,
                    "total_halalas": 1,
                    "paid_halalas": 1,
                    "outstanding_halalas": 1,
                    "notes": 1,
                },
            ).sort([("approved_at", -1)])
            async for inv in cursor:
                sid = str(inv.get("supplier_id") or "")
                if sid not in invoice_map:
                    continue
                amount = _riyals(inv.get("total_halalas"))
                paid = _riyals(inv.get("paid_halalas"))
                remaining = _riyals(inv.get("outstanding_halalas"))
                invoice_map[sid].append({
                    "id": str(inv.get("id") or ""),
                    "number": str(inv.get("invoice_number") or ""),
                    "date": inv.get("approved_at"),
                    "amount": amount,
                    "paid_amount": paid,
                    "remaining_amount": remaining,
                    "notes": str(inv.get("notes") or ""),
                })
                totals_map[sid]["invoices_count"] += 1
                totals_map[sid]["invoices_total"] += amount
                totals_map[sid]["paid_total"] += paid
                totals_map[sid]["remaining_total"] += remaining

        rows = []
        for supplier in suppliers:
            sid = str(supplier.get("id") or "")
            t = totals_map.get(sid, {"invoices_count": 0, "invoices_total": 0.0, "paid_total": 0.0, "remaining_total": 0.0})
            bank_accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
            rows.append({
                "id": sid,
                "company_name": supplier.get("company_name") or "مورد",
                "contact_person": supplier.get("contact_person") or "",
                "phone": supplier.get("phone") or "",
                "email": supplier.get("email") or "",
                "status": supplier.get("status") or "active",
                "service_ids": supplier.get("service_ids") or [],
                "service_links": supplier.get("service_links") or [],
                "bank_accounts": [_public_bank_account(account) for account in bank_accounts],
                "active_bank_accounts_count": sum(1 for account in bank_accounts if account.get("status") == "active"),
                "invoices_count": int(t["invoices_count"]),
                "invoices_total": round(float(t["invoices_total"]), 2),
                "paid_total": round(float(t["paid_total"]), 2),
                "remaining_total": round(float(t["remaining_total"]), 2),
                "invoices": invoice_map.get(sid, []),
                "source": "mezan_suppliers_v2",
                "legacy_dependency": False,
            })

        return {
            "items": rows,
            "totals": {
                "suppliers_count": len(rows),
                "invoices_count": sum(row["invoices_count"] for row in rows),
                "invoices_total": round(sum(row["invoices_total"] for row in rows), 2),
                "paid_total": round(sum(row["paid_total"] for row in rows), 2),
                "remaining_total": round(sum(row["remaining_total"] for row in rows), 2),
            },
            "source": "mezan_suppliers_v2",
            "legacy_supplier_data_used": False,
        }

    def assert_unique_account(accounts: list[dict], payload: SupplierBankAccountIn, current_id: str = "") -> None:
        for row in accounts:
            if str(row.get("id") or "") == current_id:
                continue
            if payload.iban and _norm_iban(row.get("iban") or "") == payload.iban:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_bank_iban_duplicate", "message": "رقم الآيبان مسجل من قبل لهذا المورد."},
                )
            same_number = payload.account_number and str(row.get("account_number") or "").strip() == payload.account_number
            same_bank = _norm_name(row.get("bank_name") or "") == _norm_name(payload.bank_name)
            if same_number and same_bank:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_bank_account_duplicate", "message": "رقم الحساب مسجل من قبل لهذا البنك."},
                )

    @router.post("/{supplier_id}/bank-accounts")
    async def add_bank_account(
        supplier_id: str,
        payload: SupplierBankAccountIn,
        user: dict = Depends(current_user),
    ):
        context = await context_for(user, SUPPLIERS_MANAGE_PERMISSION)
        merchant_id = context["merchant_id"]
        supplier = await get_supplier(merchant_id, supplier_id)
        accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
        assert_unique_account(accounts, payload)
        now = _now()
        make_default = payload.status == "active" and (
            payload.is_default
            or not any(a.get("status") == "active" and a.get("is_default") for a in accounts)
        )
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
        await db[MEZAN_SUPPLIERS_V2].update_one(
            {"user_id": merchant_id, "id": supplier_id},
            {"$set": {"bank_accounts": accounts, "updated_at": now}},
        )
        return {"ok": True, "bank_account": _public_bank_account(row)}

    @router.put("/{supplier_id}/bank-accounts/{account_id}")
    async def update_bank_account(
        supplier_id: str,
        account_id: str,
        payload: SupplierBankAccountIn,
        user: dict = Depends(current_user),
    ):
        context = await context_for(user, SUPPLIERS_MANAGE_PERMISSION)
        merchant_id = context["merchant_id"]
        supplier = await get_supplier(merchant_id, supplier_id)
        accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
        target = next((row for row in accounts if str(row.get("id") or "") == account_id), None)
        if not target:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_bank_account_not_found", "message": "الحساب البنكي غير موجود."},
            )
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
        await db[MEZAN_SUPPLIERS_V2].update_one(
            {"user_id": merchant_id, "id": supplier_id},
            {"$set": {"bank_accounts": accounts, "updated_at": now}},
        )
        updated = next(row for row in accounts if str(row.get("id") or "") == account_id)
        return {"ok": True, "bank_account": _public_bank_account(updated)}

    return router
