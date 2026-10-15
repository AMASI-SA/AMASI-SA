"""AMASI mobile adapter for the Mezan 2 supplier SSOT.

Mobile supplier identity is category-first: the merchant assigns one or more
canonical product/resource categories (for example clothes or coatings).
Services remain owned by those categories and are derived automatically; the
mobile UI never asks the merchant to bind services directly.

This router reads and writes ONLY ``mezan_suppliers_v2``. Legacy ``suppliers``
and ``counterparties`` records are never consulted.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

from component_workspace_cost_compat_routes import COMPONENT_CATEGORIES
from fulfillment_v2_routes import _actor_context, _require_permission
from mezan_supplier_management_routes import (
    MEZAN_SUPPLIERS_V2,
    MEZAN_SUPPLIER_INVOICES_V2,
    SUPPLIERS_MANAGE_PERMISSION,
    SUPPLIERS_READ_PERMISSION,
    _audit,
    ensure_mezan_supplier_indexes,
)
from product_option_cost_routes import RESOURCES


def _now():
    return datetime.now(timezone.utc)


def _text(value) -> str:
    return str(value or "").strip()


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold()).replace("ـ", "")


def _norm_iban(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _unique_ids(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _riyals(halalas) -> float:
    return round(int(halalas or 0) / 100, 2)


def _public_bank_account(row: dict) -> dict:
    return {
        "id": _text(row.get("id")),
        "bank_name": _text(row.get("bank_name")),
        "account_name": _text(row.get("account_name")),
        "account_number": _text(row.get("account_number")),
        "iban": _text(row.get("iban")),
        "status": row.get("status") or "active",
        "is_default": bool(row.get("is_default")),
        "has_image": bool(row.get("image_data_url")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


class MobileSupplierCreateIn(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=120)
    contact_person: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=40)
    email: Optional[str] = Field(default=None, max_length=254)
    notes: Optional[str] = Field(default=None, max_length=1000)
    category_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("company_name")
    @classmethod
    def normalize_company(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("supplier_company_name_required")
        return value

    @field_validator("contact_person", "phone", "email", "notes")
    @classmethod
    def normalize_optional(cls, value: Optional[str]) -> Optional[str]:
        value = _text(value)
        return value or None

    @field_validator("category_ids")
    @classmethod
    def normalize_categories(cls, values: list[str]) -> list[str]:
        result = _unique_ids(values)
        if not result:
            raise ValueError("supplier_category_required")
        return result


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


async def _category_selection(db, merchant_id: str, category_ids: list[str]):
    category_ids = _unique_ids(category_ids)
    categories = await db[COMPONENT_CATEGORIES].find(
        {
            "user_id": merchant_id,
            "id": {"$in": category_ids},
            "status": {"$ne": "inactive"},
        },
        {"_id": 0, "id": 1, "name": 1},
    ).to_list(100)
    by_id = {_text(row.get("id")): row for row in categories}
    missing = [category_id for category_id in category_ids if category_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "supplier_category_not_found",
                "message": "أحد تصنيفات المورد لم يعد موجودًا في ميزان.",
                "missing_category_ids": missing,
            },
        )

    service_rows = await db[RESOURCES].find(
        {
            "user_id": merchant_id,
            "kind": "service",
            "track_inventory": {"$ne": True},
            "status": {"$ne": "inactive"},
            "category_ids": {"$in": category_ids},
        },
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "code": 1,
            "unit": 1,
            "unit_cost": 1,
            "requires_preparation": 1,
            "category_ids": 1,
        },
    ).sort("name", 1).to_list(2000)

    category_links = [
        {"category_id": category_id, "category_name": _text(by_id[category_id].get("name")) or category_id}
        for category_id in category_ids
    ]
    service_links = [
        {
            "service_id": _text(row.get("id")),
            "service_name": _text(row.get("name")) or "خدمة",
            "service_code": _text(row.get("code")) or None,
            "unit": _text(row.get("unit")) or "job",
            "unit_cost": row.get("unit_cost"),
            "requires_preparation": bool(row.get("requires_preparation")),
            "category_ids": _unique_ids(row.get("category_ids")),
        }
        for row in service_rows
        if _text(row.get("id"))
    ]
    return category_ids, category_links, service_links


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

        all_service_ids = {
            service_id
            for supplier in suppliers
            for service_id in _unique_ids(supplier.get("service_ids"))
        }
        service_categories: dict[str, list[str]] = {}
        if all_service_ids:
            async for resource in db[RESOURCES].find(
                {"user_id": merchant_id, "id": {"$in": list(all_service_ids)}},
                {"_id": 0, "id": 1, "category_ids": 1},
            ):
                service_categories[_text(resource.get("id"))] = _unique_ids(resource.get("category_ids"))

        supplier_category_ids: dict[str, list[str]] = {}
        all_category_ids: set[str] = set()
        for supplier in suppliers:
            sid = _text(supplier.get("id"))
            category_ids = _unique_ids(supplier.get("category_ids"))
            if not category_ids:
                category_ids = _unique_ids([
                    category_id
                    for service_id in _unique_ids(supplier.get("service_ids"))
                    for category_id in service_categories.get(service_id, [])
                ])
            supplier_category_ids[sid] = category_ids
            all_category_ids.update(category_ids)

        category_names: dict[str, str] = {}
        if all_category_ids:
            async for category in db[COMPONENT_CATEGORIES].find(
                {"user_id": merchant_id, "id": {"$in": list(all_category_ids)}},
                {"_id": 0, "id": 1, "name": 1},
            ):
                category_names[_text(category.get("id"))] = _text(category.get("name"))

        needle = _norm_name(q or "")
        if needle:
            suppliers = [
                row for row in suppliers
                if needle in _norm_name(row.get("company_name") or "")
                or needle in _norm_name(row.get("contact_person") or "")
                or any(
                    needle in _norm_name(category_names.get(category_id, ""))
                    for category_id in supplier_category_ids.get(_text(row.get("id")), [])
                )
            ]

        ids = [_text(row.get("id")) for row in suppliers if row.get("id")]
        invoice_map: dict[str, list[dict]] = {sid: [] for sid in ids}
        totals_map: dict[str, dict] = {
            sid: {"invoices_count": 0, "invoices_total": 0.0, "paid_total": 0.0, "remaining_total": 0.0}
            for sid in ids
        }
        if ids:
            cursor = db[MEZAN_SUPPLIER_INVOICES_V2].find(
                {"user_id": merchant_id, "supplier_id": {"$in": ids}, "experiment_mode": {"$ne": True}},
                {
                    "_id": 0, "id": 1, "supplier_id": 1, "invoice_number": 1,
                    "approved_at": 1, "total_halalas": 1, "paid_halalas": 1,
                    "outstanding_halalas": 1, "notes": 1,
                },
            ).sort([("approved_at", -1)])
            async for inv in cursor:
                sid = _text(inv.get("supplier_id"))
                if sid not in invoice_map:
                    continue
                amount = _riyals(inv.get("total_halalas"))
                paid = _riyals(inv.get("paid_halalas"))
                remaining = _riyals(inv.get("outstanding_halalas"))
                invoice_map[sid].append({
                    "id": _text(inv.get("id")),
                    "number": _text(inv.get("invoice_number")),
                    "date": inv.get("approved_at"),
                    "amount": amount,
                    "paid_amount": paid,
                    "remaining_amount": remaining,
                    "notes": _text(inv.get("notes")),
                })
                totals_map[sid]["invoices_count"] += 1
                totals_map[sid]["invoices_total"] += amount
                totals_map[sid]["paid_total"] += paid
                totals_map[sid]["remaining_total"] += remaining

        rows = []
        for supplier in suppliers:
            sid = _text(supplier.get("id"))
            t = totals_map.get(sid, {"invoices_count": 0, "invoices_total": 0.0, "paid_total": 0.0, "remaining_total": 0.0})
            bank_accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
            category_ids = supplier_category_ids.get(sid, [])
            rows.append({
                "id": sid,
                "company_name": supplier.get("company_name") or "مورد",
                "contact_person": supplier.get("contact_person") or "",
                "phone": supplier.get("phone") or "",
                "email": supplier.get("email") or "",
                "status": supplier.get("status") or "active",
                "category_ids": category_ids,
                "category_links": [
                    {"category_id": category_id, "category_name": category_names.get(category_id, category_id)}
                    for category_id in category_ids
                ],
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
            "supplier_binding": "categories",
            "legacy_supplier_data_used": False,
        }

    @router.post("", status_code=201)
    async def create_mobile_supplier(
        payload: MobileSupplierCreateIn,
        user: dict = Depends(current_user),
    ):
        context = await context_for(user, SUPPLIERS_MANAGE_PERMISSION)
        merchant_id = context["merchant_id"]
        await ensure_mezan_supplier_indexes(db)
        category_ids, category_links, service_links = await _category_selection(
            db, merchant_id, payload.category_ids
        )
        now = _now()
        row = {
            "id": f"msv2_{uuid.uuid4().hex}",
            "user_id": merchant_id,
            "company_name": payload.company_name,
            "_lc_company_name": _norm_name(payload.company_name),
            "contact_person": payload.contact_person,
            "phone": payload.phone,
            "email": payload.email,
            "notes": payload.notes,
            "status": "active",
            "category_ids": category_ids,
            "category_links": category_links,
            "service_ids": [link["service_id"] for link in service_links],
            "service_links": service_links,
            "bank_accounts": [],
            "created_by": context["actor_id"],
            "updated_by": context["actor_id"],
            "created_at": now,
            "updated_at": now,
            "legacy_dependency": False,
            "accounting_linked": True,
        }
        try:
            await db[MEZAN_SUPPLIERS_V2].insert_one(dict(row))
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_company_name_exists", "message": "يوجد مورد في ميزان 2 بنفس الاسم."},
            ) from exc
        await _audit(
            db,
            user_id=merchant_id,
            supplier_id=row["id"],
            actor_id=context["actor_id"],
            event_type="mezan_supplier_created_from_mobile_category",
            before=None,
            after=row,
        )
        return {"ok": True, "supplier": row, "source": "mezan_suppliers_v2"}

    def assert_unique_account(accounts: list[dict], payload: SupplierBankAccountIn, current_id: str = "") -> None:
        for row in accounts:
            if _text(row.get("id")) == current_id:
                continue
            if payload.iban and _norm_iban(row.get("iban") or "") == payload.iban:
                raise HTTPException(status_code=409, detail={"code": "supplier_bank_iban_duplicate", "message": "رقم الآيبان مسجل من قبل لهذا المورد."})
            same_number = payload.account_number and _text(row.get("account_number")) == payload.account_number
            same_bank = _norm_name(row.get("bank_name") or "") == _norm_name(payload.bank_name)
            if same_number and same_bank:
                raise HTTPException(status_code=409, detail={"code": "supplier_bank_account_duplicate", "message": "رقم الحساب مسجل من قبل لهذا البنك."})

    @router.post("/{supplier_id}/bank-accounts")
    async def add_bank_account(supplier_id: str, payload: SupplierBankAccountIn, user: dict = Depends(current_user)):
        context = await context_for(user, SUPPLIERS_MANAGE_PERMISSION)
        merchant_id = context["merchant_id"]
        supplier = await get_supplier(merchant_id, supplier_id)
        accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
        assert_unique_account(accounts, payload)
        now = _now()
        make_default = payload.status == "active" and (
            payload.is_default or not any(a.get("status") == "active" and a.get("is_default") for a in accounts)
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
    async def update_bank_account(supplier_id: str, account_id: str, payload: SupplierBankAccountIn, user: dict = Depends(current_user)):
        context = await context_for(user, SUPPLIERS_MANAGE_PERMISSION)
        merchant_id = context["merchant_id"]
        supplier = await get_supplier(merchant_id, supplier_id)
        accounts = _normalize_bank_accounts(supplier.get("bank_accounts") or [])
        target = next((row for row in accounts if _text(row.get("id")) == account_id), None)
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
        await db[MEZAN_SUPPLIERS_V2].update_one(
            {"user_id": merchant_id, "id": supplier_id},
            {"$set": {"bank_accounts": accounts, "updated_at": now}},
        )
        updated = next(row for row in accounts if _text(row.get("id")) == account_id)
        return {"ok": True, "bank_account": _public_bank_account(updated)}

    return router
