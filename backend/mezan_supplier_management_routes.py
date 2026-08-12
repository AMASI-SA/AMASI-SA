"""Independent Mezan 2 supplier directory and service bindings.

This module deliberately does not read, migrate, merge, or write the legacy
``suppliers``/``counterparties`` accounting records.  Mezan 2 suppliers are an
operational catalog whose services come from the existing Product V2 resource
catalog. Approved receiving sessions use the same Mezan 2 supplier identity
for accounting invoices and payables; legacy supplier records stay excluded.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from fulfillment_v2_routes import _actor_context, _require_permission
from product_option_cost_routes import RESOURCES


MEZAN_SUPPLIERS_V2 = "mezan_suppliers_v2"
MEZAN_SUPPLIER_AUDIT_V2 = "mezan_supplier_audit_v2"
MEZAN_SUPPLIER_INVOICES_V2 = "mezan_supplier_invoices_v2"
SUPPLIERS_READ_PERMISSION = "suppliers.read"
SUPPLIERS_MANAGE_PERMISSION = "suppliers.manage"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).casefold()


def _halalas_from_riyals(value: Any) -> int:
    return int(round(float(value or 0) * 100))


def _public_financial_invoice(row: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for line in row.get("lines") or []:
        services = [
            {
                "service_id": _text(service.get("service_id")) or None,
                "service_name": _text(service.get("service_name")) or "خدمة",
                "quantity": float(service.get("total_quantity") or 0),
                "unit_price_halalas": int(service.get("unit_price_halalas") or 0),
                "total_halalas": int(service.get("total_halalas") or 0),
            }
            for service in (line.get("services") or [])
        ]
        lines.append(
            {
                "line_number": int(line.get("line_number") or 0),
                "product_id": _text(line.get("product_id")) or None,
                "product_name": _text(line.get("product_name")) or "منتج",
                "sku": _text(line.get("sku")) or None,
                "quantity": int(line.get("quantity") or 0),
                "product_unit_price_halalas": int(
                    line.get("product_unit_price_halalas") or 0
                ),
                "product_total_halalas": int(line.get("product_total_halalas") or 0),
                "services_total_halalas": int(line.get("services_total_halalas") or 0),
                "total_halalas": int(line.get("total_halalas") or 0),
                "services": services,
            }
        )
    return {
        "id": _text(row.get("id")),
        "supplier_id": _text(row.get("supplier_id")),
        "invoice_number": _text(row.get("invoice_number")),
        "session_reference": _text(row.get("session_reference")) or None,
        "status": _text(row.get("status")),
        "payment_status": _text(row.get("payment_status")) or None,
        "currency": _text(row.get("currency")) or "SAR",
        "piece_count": int(row.get("piece_count") or 0),
        "line_count": int(row.get("line_count") or len(lines)),
        "total_halalas": int(row.get("total_halalas") or 0),
        "paid_halalas": int(row.get("paid_halalas") or 0),
        "outstanding_halalas": int(row.get("outstanding_halalas") or 0),
        "approved_at": row.get("approved_at"),
        "approved_by_name": _text(row.get("supplier_approved_by_name")) or None,
        "share_status": _text(row.get("share_status")) or None,
        "share_confirmed": bool(row.get("share_confirmed")),
        "experiment_mode": bool(row.get("experiment_mode")),
        "financial_invoice_created": bool(row.get("financial_invoice_created")),
        "liability_created": bool(row.get("liability_created")),
        "qoyod_updated": bool(row.get("qoyod_updated")),
        "salla_updated": bool(row.get("salla_updated")),
        "lines": lines,
    }


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
        "accounting_linked": True,
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
            "unit_cost": 1,
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
            "unit_cost": by_id[service_id].get("unit_cost"),
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
                "unit_cost": 1,
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
                "unit_cost": row.get("unit_cost"),
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
                "accounting_linked": True,
                "delete_supported": False,
            },
            "permissions": {
                "can_read": True,
                "can_manage": SUPPLIERS_MANAGE_PERMISSION in context["permissions"],
            },
        }

    @router.get("/financials")
    async def financials(
        supplier_id: str | None = Query(default=None),
        invoice_limit: int = Query(default=300, ge=1, le=2000),
        timeline_limit: int = Query(default=300, ge=1, le=2000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Read-only post-receiving invoices and supplier payable balances.

        Supplier balances are derived exclusively from ``general_ledger``.
        Receiving invoices enrich that balance with product and service detail;
        experiment invoices remain visible but never enter the payable math.
        """
        context = await _actor_context(db, user)
        _require_permission(context, SUPPLIERS_READ_PERMISSION)
        merchant_id = context["merchant_id"]
        supplier_query: dict[str, Any] = {"user_id": merchant_id}
        if supplier_id:
            supplier_query["id"] = _text(supplier_id)
        supplier_rows = (
            await db[MEZAN_SUPPLIERS_V2]
            .find(
                supplier_query,
                {
                    "_id": 0,
                    "id": 1,
                    "company_name": 1,
                    "contact_person": 1,
                    "phone": 1,
                    "status": 1,
                },
            )
            .sort("company_name", 1)
            .to_list(2000)
        )
        if supplier_id and not supplier_rows:
            raise HTTPException(
                status_code=404,
                detail={"code": "mezan_supplier_not_found"},
            )
        supplier_ids = [
            _text(row.get("id")) for row in supplier_rows if _text(row.get("id"))
        ]
        base = {
            supplier_key: {
                "supplier_id": supplier_key,
                "invoiced_halalas": 0,
                "paid_halalas": 0,
                "outstanding_halalas": 0,
                "credit_balance_halalas": 0,
                "real_invoice_count": 0,
                "experiment_invoice_count": 0,
                "experiment_total_halalas": 0,
                "last_invoice_at": None,
                "last_payment_at": None,
            }
            for supplier_key in supplier_ids
        }
        if not supplier_ids:
            return {
                "ok": True,
                "summary": {
                    "supplier_count": 0,
                    "real_invoice_count": 0,
                    "experiment_invoice_count": 0,
                    "invoiced_halalas": 0,
                    "paid_halalas": 0,
                    "outstanding_halalas": 0,
                    "credit_balance_halalas": 0,
                },
                "suppliers": [],
                "invoices": [],
                "timeline": [],
                "rules": {
                    "balance_source": "general_ledger",
                    "experiment_invoices_excluded_from_debt": True,
                    "legacy_supplier_data_used": False,
                    "read_only": True,
                },
            }

        ledger_match = {
            "user_id": merchant_id,
            "entity_type": "supplier",
            "entity_id": {"$in": supplier_ids},
            "sub_account": "payable",
            "status": "posted",
        }
        ledger_totals = await db.general_ledger.aggregate(
            [
                {"$match": ledger_match},
                {
                    "$group": {
                        "_id": "$entity_id",
                        "credits": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": ["$side", "credit"]},
                                    "$amount",
                                    0,
                                ]
                            }
                        },
                        "debits": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": ["$side", "debit"]},
                                    "$amount",
                                    0,
                                ]
                            }
                        },
                        "last_invoice_at": {
                            "$max": {
                                "$cond": [
                                    {"$eq": ["$side", "credit"]},
                                    "$created_at",
                                    None,
                                ]
                            }
                        },
                        "last_payment_at": {
                            "$max": {
                                "$cond": [
                                    {"$eq": ["$side", "debit"]},
                                    "$created_at",
                                    None,
                                ]
                            }
                        },
                    }
                },
            ]
        ).to_list(len(supplier_ids))
        for row in ledger_totals:
            supplier_key = _text(row.get("_id"))
            if supplier_key not in base:
                continue
            credits = _halalas_from_riyals(row.get("credits"))
            debits = _halalas_from_riyals(row.get("debits"))
            net = credits - debits
            base[supplier_key].update(
                {
                    "invoiced_halalas": credits,
                    "paid_halalas": debits,
                    "outstanding_halalas": max(0, net),
                    "credit_balance_halalas": max(0, -net),
                    "last_invoice_at": row.get("last_invoice_at"),
                    "last_payment_at": row.get("last_payment_at"),
                }
            )

        invoice_match = {
            "user_id": merchant_id,
            "supplier_id": {"$in": supplier_ids},
        }
        invoice_totals = (
            await db[MEZAN_SUPPLIER_INVOICES_V2]
            .aggregate(
                [
                    {"$match": invoice_match},
                    {
                        "$group": {
                            "_id": {
                                "supplier_id": "$supplier_id",
                                "experiment_mode": {"$eq": ["$experiment_mode", True]},
                            },
                            "count": {"$sum": 1},
                            "total_halalas": {"$sum": "$total_halalas"},
                        }
                    },
                ]
            )
            .to_list(max(1, len(supplier_ids) * 2))
        )
        for row in invoice_totals:
            key = row.get("_id") or {}
            supplier_key = _text(key.get("supplier_id"))
            if supplier_key not in base:
                continue
            count = int(row.get("count") or 0)
            total = int(row.get("total_halalas") or 0)
            if bool(key.get("experiment_mode")):
                base[supplier_key]["experiment_invoice_count"] = count
                base[supplier_key]["experiment_total_halalas"] = total
            else:
                base[supplier_key]["real_invoice_count"] = count

        invoice_rows = (
            await db[MEZAN_SUPPLIER_INVOICES_V2]
            .find(
                invoice_match,
                {"_id": 0, "user_id": 0, "ledger_entry_ids": 0},
            )
            .sort("approved_at", -1)
            .limit(invoice_limit)
            .to_list(invoice_limit)
        )
        invoices = [_public_financial_invoice(row) for row in invoice_rows]

        ledger_rows = (
            await db.general_ledger.find(
                ledger_match,
                {
                    "_id": 0,
                    "id": 1,
                    "entity_id": 1,
                    "side": 1,
                    "amount": 1,
                    "entry_type": 1,
                    "notes": 1,
                    "created_at": 1,
                    "metadata": 1,
                    "txn_group_id": 1,
                },
            )
            .sort("created_at", -1)
            .limit(timeline_limit)
            .to_list(timeline_limit)
        )
        timeline = [
            {
                "id": _text(row.get("id")),
                "supplier_id": _text(row.get("entity_id")),
                "kind": "invoice" if _text(row.get("side")) == "credit" else "payment",
                "amount_halalas": _halalas_from_riyals(row.get("amount")),
                "entry_type": _text(row.get("entry_type")),
                "notes": _text(row.get("notes")) or None,
                "created_at": row.get("created_at"),
                "supplier_invoice_id": _text(
                    (row.get("metadata") or {}).get("supplier_invoice_v2_id")
                )
                or None,
                "txn_group_id": _text(row.get("txn_group_id")) or None,
            }
            for row in ledger_rows
        ]

        suppliers = []
        for row in supplier_rows:
            supplier_key = _text(row.get("id"))
            suppliers.append(
                {
                    "id": supplier_key,
                    "company_name": _text(row.get("company_name")),
                    "contact_person": _text(row.get("contact_person")) or None,
                    "phone": _text(row.get("phone")) or None,
                    "status": _text(row.get("status")) or "active",
                    "financial": base[supplier_key],
                }
            )

        summary_fields = (
            "invoiced_halalas",
            "paid_halalas",
            "outstanding_halalas",
            "credit_balance_halalas",
            "real_invoice_count",
            "experiment_invoice_count",
        )
        summary = {
            field: sum(int(row[field] or 0) for row in base.values())
            for field in summary_fields
        }
        summary["supplier_count"] = len(supplier_rows)
        return {
            "ok": True,
            "summary": summary,
            "suppliers": suppliers,
            "invoices": invoices,
            "timeline": timeline,
            "rules": {
                "balance_source": "general_ledger",
                "experiment_invoices_excluded_from_debt": True,
                "legacy_supplier_data_used": False,
                "read_only": True,
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
            "accounting_linked": True,
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
            "accounting_linked": True,
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
    "MEZAN_SUPPLIER_INVOICES_V2",
    "MezanSupplierWriteRequest",
    "SUPPLIERS_MANAGE_PERMISSION",
    "SUPPLIERS_READ_PERMISSION",
    "ensure_mezan_supplier_indexes",
    "make_mezan_supplier_management_router",
]
