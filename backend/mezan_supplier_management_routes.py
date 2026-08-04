"""Independent Mezan 2 supplier directory and service bindings.

This module deliberately does not read, migrate, merge, or write the legacy
``suppliers``/``counterparties`` accounting records.  Mezan 2 suppliers are an
operational catalog whose services come from the existing Product V2 resource
catalog.  Accounting invoices and liabilities remain a later, explicit step.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from fulfillment_v2_routes import _actor_context, _require_permission
from product_option_cost_routes import RESOURCES


MEZAN_SUPPLIERS_V2 = "mezan_suppliers_v2"
MEZAN_SUPPLIER_AUDIT_V2 = "mezan_supplier_audit_v2"
SUPPLIERS_READ_PERMISSION = "suppliers.read"
SUPPLIERS_MANAGE_PERMISSION = "suppliers.manage"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).casefold()


def _is_service(row: dict[str, Any]) -> bool:
    return _normalized(row.get("kind")) == "service" and not bool(
        row.get("track_inventory")
    )


def _public_supplier(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    service_links = [dict(link) for link in row.get("service_links") or []]
    return {
        "id": _text(row.get("id")),
        "company_name": _text(row.get("company_name")),
        "contact_person": _text(row.get("contact_person")) or None,
        "phone": _text(row.get("phone")) or None,
        "email": _text(row.get("email")) or None,
        "notes": _text(row.get("notes")) or None,
        "status": _text(row.get("status")) or "active",
        "service_ids": [
            _text(link.get("service_id")) for link in service_links
            if _text(link.get("service_id"))
        ],
        "service_links": service_links,
        "service_count": len(service_links),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "legacy_dependency": False,
        "accounting_linked": False,
    }


class MezanSupplierWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=120)
    contact_person: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)
    notes: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "inactive"] = "active"
    service_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("company_name")
    @classmethod
    def validate_company_name(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("supplier_company_name_required")
        return value

    @field_validator("contact_person", "phone", "email", "notes")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        value = _text(value)
        return value or None

    @field_validator("service_ids")
    @classmethod
    def normalize_service_ids(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            service_id = _text(value)
            if service_id and service_id not in seen:
                seen.add(service_id)
                result.append(service_id)
        if not result:
            raise ValueError("supplier_service_required")
        return result


async def ensure_mezan_supplier_indexes(db: Any) -> None:
    await db[MEZAN_SUPPLIERS_V2].create_index(
        [("user_id", ASCENDING), ("_lc_company_name", ASCENDING)],
        unique=True,
        name="uq_mezan_supplier_company_v2",
    )
    await db[MEZAN_SUPPLIERS_V2].create_index(
        [("user_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
        name="ix_mezan_supplier_status_v2",
    )
    await db[MEZAN_SUPPLIERS_V2].create_index(
        [("user_id", ASCENDING), ("service_ids", ASCENDING)],
        name="ix_mezan_supplier_services_v2",
    )
    await db[MEZAN_SUPPLIER_AUDIT_V2].create_index(
        [("user_id", ASCENDING), ("supplier_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_mezan_supplier_audit_v2",
    )


async def _service_links(
    db: Any,
    *,
    user_id: str,
    service_ids: list[str],
) -> list[dict[str, Any]]:
    rows = await db[RESOURCES].find(
        {
            "user_id": user_id,
            "id": {"$in": service_ids},
            "kind": "service",
            "track_inventory": {"$ne": True},
        },
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "code": 1,
            "unit": 1,
            "kind": 1,
            "track_inventory": 1,
            "requires_preparation": 1,
        },
    ).to_list(200)
    by_id = {_text(row.get("id")): row for row in rows if _is_service(row)}
    missing = [service_id for service_id in service_ids if service_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "supplier_service_not_found",
                "missing_service_ids": missing,
            },
        )
    return [
        {
            "service_id": service_id,
            "service_name": _text(by_id[service_id].get("name")) or service_id,
            "service_code": _text(by_id[service_id].get("code")) or None,
            "unit": _text(by_id[service_id].get("unit")) or "job",
            "requires_preparation": bool(
                by_id[service_id].get("requires_preparation")
            ),
        }
        for service_id in service_ids
    ]


async def _audit(
    db: Any,
    *,
    user_id: str,
    supplier_id: str,
    actor_id: str,
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    await db[MEZAN_SUPPLIER_AUDIT_V2].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "supplier_id": supplier_id,
        "actor_id": actor_id,
        "event_type": event_type,
        "before": _public_supplier(before),
        "after": _public_supplier(after),
        "occurred_at": _now(),
        "legacy_dependency": False,
        "accounting_write": False,
    })


def make_mezan_supplier_management_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/suppliers-v2", tags=["Mezan Suppliers V2"])

    @router.get("/workspace")
    async def workspace(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, SUPPLIERS_READ_PERMISSION)
        await ensure_mezan_supplier_indexes(db)
        merchant_id = context["merchant_id"]
        suppliers = await db[MEZAN_SUPPLIERS_V2].find(
            {"user_id": merchant_id},
            {"_id": 0, "user_id": 0, "_lc_company_name": 0},
        ).sort([("status", 1), ("company_name", 1)]).to_list(2000)
        raw_services = await db[RESOURCES].find(
            {
                "user_id": merchant_id,
                "kind": "service",
                "track_inventory": {"$ne": True},
            },
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "code": 1,
                "unit": 1,
                "kind": 1,
                "track_inventory": 1,
                "requires_preparation": 1,
            },
        ).sort("name", 1).to_list(2000)
        services = [
            {
                "id": _text(row.get("id")),
                "name": _text(row.get("name")),
                "code": _text(row.get("code")) or None,
                "unit": _text(row.get("unit")) or "job",
                "requires_preparation": bool(row.get("requires_preparation")),
            }
            for row in raw_services
            if _is_service(row)
        ]
        public_suppliers = [_public_supplier(row) for row in suppliers]
        return {
            "ok": True,
            "suppliers": public_suppliers,
            "services": services,
            "summary": {
                "total": len(public_suppliers),
                "active": sum(
                    1 for row in public_suppliers if row and row["status"] == "active"
                ),
                "inactive": sum(
                    1 for row in public_suppliers if row and row["status"] == "inactive"
                ),
                "services": len(services),
            },
            "rules": {
                "service_required": True,
                "legacy_supplier_data_used": False,
                "accounting_linked": False,
                "delete_supported": False,
            },
            "permissions": {
                "can_read": True,
                "can_manage": SUPPLIERS_MANAGE_PERMISSION in context["permissions"],
            },
        }

    @router.post("", status_code=201)
    async def create_supplier(
        payload: MezanSupplierWriteRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, SUPPLIERS_MANAGE_PERMISSION)
        await ensure_mezan_supplier_indexes(db)
        now = _now()
        links = await _service_links(
            db,
            user_id=context["merchant_id"],
            service_ids=payload.service_ids,
        )
        row = {
            "id": f"msv2_{uuid.uuid4().hex}",
            "user_id": context["merchant_id"],
            "company_name": payload.company_name,
            "_lc_company_name": _normalized(payload.company_name),
            "contact_person": payload.contact_person,
            "phone": payload.phone,
            "email": payload.email,
            "notes": payload.notes,
            "status": payload.status,
            "service_ids": [link["service_id"] for link in links],
            "service_links": links,
            "created_by": context["actor_id"],
            "updated_by": context["actor_id"],
            "created_at": now,
            "updated_at": now,
            "legacy_dependency": False,
            "accounting_linked": False,
        }
        try:
            await db[MEZAN_SUPPLIERS_V2].insert_one(dict(row))
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_company_name_exists"},
            ) from exc
        await _audit(
            db,
            user_id=context["merchant_id"],
            supplier_id=row["id"],
            actor_id=context["actor_id"],
            event_type="mezan_supplier_created",
            before=None,
            after=row,
        )
        return {"ok": True, "supplier": _public_supplier(row)}

    @router.put("/{supplier_id}")
    async def update_supplier(
        supplier_id: str,
        payload: MezanSupplierWriteRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, SUPPLIERS_MANAGE_PERMISSION)
        await ensure_mezan_supplier_indexes(db)
        selector = {
            "user_id": context["merchant_id"],
            "id": _text(supplier_id),
        }
        before = await db[MEZAN_SUPPLIERS_V2].find_one(selector, {"_id": 0})
        if not before:
            raise HTTPException(
                status_code=404,
                detail={"code": "mezan_supplier_not_found"},
            )
        links = await _service_links(
            db,
            user_id=context["merchant_id"],
            service_ids=payload.service_ids,
        )
        patch = {
            "company_name": payload.company_name,
            "_lc_company_name": _normalized(payload.company_name),
            "contact_person": payload.contact_person,
            "phone": payload.phone,
            "email": payload.email,
            "notes": payload.notes,
            "status": payload.status,
            "service_ids": [link["service_id"] for link in links],
            "service_links": links,
            "updated_by": context["actor_id"],
            "updated_at": _now(),
            "legacy_dependency": False,
            "accounting_linked": False,
        }
        try:
            await db[MEZAN_SUPPLIERS_V2].update_one(selector, {"$set": patch})
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_company_name_exists"},
            ) from exc
        after = {**before, **patch}
        await _audit(
            db,
            user_id=context["merchant_id"],
            supplier_id=_text(supplier_id),
            actor_id=context["actor_id"],
            event_type="mezan_supplier_updated",
            before=before,
            after=after,
        )
        return {"ok": True, "supplier": _public_supplier(after)}

    return router


__all__ = [
    "MEZAN_SUPPLIERS_V2",
    "MEZAN_SUPPLIER_AUDIT_V2",
    "MezanSupplierWriteRequest",
    "SUPPLIERS_MANAGE_PERMISSION",
    "SUPPLIERS_READ_PERMISSION",
    "ensure_mezan_supplier_indexes",
    "make_mezan_supplier_management_router",
]
