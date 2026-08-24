"""External-courier settlement-bank bindings for P01.

P01 establishes the current bank used by each external courier settlement.
Shipping cost, COD commission tiers, store-driver balances, and courier
settlement posting remain locked to P02. Internal store drivers and pickup
methods are deliberately excluded from this catalogue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_module_contract import (
    accounting_owner_id,
    require_accounting_permission,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_settlement_routes import ensure_accounting_settlement_indexes
from auth import DEFAULT_SHIPPING_COMPANIES, ensure_user_settings
from ledger_core import write_audit
from shipping_companies import normalize_shipping_company

_INTERNAL_COURIER_KEYS = frozenset({"mandoob", "mandoob_riyadh", "pickup"})
_INTERNAL_NAME_MARKERS = (
    "مندوب",
    "استلام من المتجر",
    "تسليم مباشر",
    "store driver",
    "store_driver",
    "pickup",
)
_SOURCE_KINDS = frozenset({
    "provider_statement",
    "provider_invoice",
    "owner_confirmed",
    "legacy_copy",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_external_courier(key: str, display: str, raw_name: str) -> bool:
    """Fail closed for store delivery and pickup identities."""
    if key == "unknown" or key in _INTERNAL_COURIER_KEYS:
        return False
    haystack = " ".join((display, raw_name)).strip().lower()
    return not any(marker in haystack for marker in _INTERNAL_NAME_MARKERS)


def external_courier_catalog_from_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize and deduplicate configured external courier identities."""
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        raw_name = _clean(row.get("name"))
        key, display = normalize_shipping_company(raw_name)
        if not _is_external_courier(key, display, raw_name) or key in seen:
            continue
        seen.add(key)
        catalog.append({
            "courier_key": key,
            "provider_id": f"shipping:{key}",
            "display_name": display,
            "configured_name": raw_name,
            "active": row.get("is_active") is not False and row.get("active") is not False,
            "payment_mode": (
                "deferred" if row.get("is_deferred") else "prepaid"
            ),
        })
    return catalog


class CourierBankBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: str = Field(min_length=1, max_length=120)
    source_kind: Literal[
        "provider_statement",
        "provider_invoice",
        "owner_confirmed",
        "legacy_copy",
    ] = "owner_confirmed"
    confirmed: bool = False
    evidence_ref: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=1000)


async def _scope(db, user: dict[str, Any], permission: str) -> tuple[dict[str, Any], str]:
    actor = await fresh_accounting_user(db, user)
    require_accounting_permission(actor, permission)
    owner_id = accounting_owner_id(actor)
    if not owner_id:
        raise HTTPException(403, "لا يوجد مالك بيانات محاسبية مرتبط بالمستخدم")
    return actor, owner_id


async def _find_bank(db, owner_id: str, bank_id: str) -> dict[str, Any] | None:
    if not _clean(bank_id):
        return None
    return await db.accounts.find_one(
        {
            "user_id": owner_id,
            "id": _clean(bank_id),
            "account_type": {"$in": ["bank", "cash"]},
        },
        {"_id": 0, "id": 1, "name": 1, "account_type": 1},
    )


async def _catalog(db, owner_id: str) -> list[dict[str, Any]]:
    settings = await ensure_user_settings(db, owner_id)
    rows = settings.get("shipping_companies") or DEFAULT_SHIPPING_COMPANIES
    return external_courier_catalog_from_rows(rows)


async def _courier_or_404(db, owner_id: str, courier_key: str) -> dict[str, Any]:
    requested = _clean(courier_key)
    for courier in await _catalog(db, owner_id):
        if courier["courier_key"] == requested:
            return courier
    raise HTTPException(
        404,
        {
            "code": "external_courier_not_configured",
            "message": "شركة الشحن الخارجية غير موجودة في إعدادات المتجر",
            "courier_key": requested,
        },
    )


async def _binding_view(db, owner_id: str, courier: dict[str, Any]) -> dict[str, Any]:
    doc = await db.accounting_provider_bank_bindings_v2.find_one(
        {
            "user_id": owner_id,
            "provider": courier["provider_id"],
        },
        {"_id": 0},
    )
    bank = await _find_bank(db, owner_id, (doc or {}).get("bank_account_id"))
    return {
        **courier,
        "bank_account_id": (bank or {}).get("id"),
        "bank_account_name": (bank or {}).get("name"),
        "bank_account_type": (bank or {}).get("account_type"),
        "source_kind": (doc or {}).get("source_kind"),
        "verification_status": (
            (doc or {}).get("verification_status")
            if bank else "missing"
        ),
        "evidence_ref": (doc or {}).get("evidence_ref"),
        "notes": (doc or {}).get("notes"),
        "approved_by": (doc or {}).get("approved_by"),
        "approved_at": (doc or {}).get("approved_at"),
        "configured": bool(bank),
        "needs_confirmation": (
            not bank or (doc or {}).get("verification_status") != "verified"
        ),
        "p02_financial_logic_locked": True,
    }


def install_accounting_courier_bank_routes(router, db, current_user):
    @router.get("/accounting-module/settlements/courier-bindings")
    async def list_courier_bindings(user: dict = Depends(current_user)):
        _actor, owner_id = await _scope(
            db,
            user,
            "accounting.settlements.view",
        )
        couriers = await _catalog(db, owner_id)
        items = [
            await _binding_view(db, owner_id, courier)
            for courier in couriers
        ]
        return {
            "items": items,
            "count": len(items),
            "store_drivers_excluded": True,
            "pickup_excluded": True,
            "p02_financial_logic_locked": True,
        }

    @router.put("/accounting-module/settlements/courier-bindings/{courier_key}")
    async def save_courier_binding(
        courier_key: str,
        payload: CourierBankBindingIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db,
            user,
            "accounting.rules.manage",
        )
        if payload.source_kind not in _SOURCE_KINDS:
            raise HTTPException(400, "مصدر ربط البنك غير مدعوم")
        if not payload.confirmed:
            raise HTTPException(
                400,
                "يجب تأكيد أن هذا هو البنك الحالي لتسويات شركة الشحن",
            )
        courier = await _courier_or_404(db, owner_id, courier_key)
        bank = await _find_bank(db, owner_id, payload.bank_account_id)
        if not bank:
            raise HTTPException(400, "الحساب البنكي غير موجود أو لا يتبع المتجر")

        await ensure_accounting_settlement_indexes(db)
        before = await db.accounting_provider_bank_bindings_v2.find_one(
            {
                "user_id": owner_id,
                "provider": courier["provider_id"],
            },
            {"_id": 0},
        )
        now = _now()
        doc = {
            "user_id": owner_id,
            "provider": courier["provider_id"],
            "provider_kind": "shipping_company",
            "provider_code": courier["courier_key"],
            "provider_label": courier["display_name"],
            "bank_account_id": bank["id"],
            "bank_account_name": bank.get("name") or "",
            "bank_account_type": bank.get("account_type"),
            "source_kind": payload.source_kind,
            "verification_status": "verified",
            "evidence_ref": _clean(payload.evidence_ref) or None,
            "notes": payload.notes or "",
            "approved_by": actor.get("id"),
            "approved_by_name": actor.get("name") or actor.get("email"),
            "approved_at": now,
            "updated_at": now,
            "p02_financial_logic_locked": True,
        }
        await db.accounting_provider_bank_bindings_v2.update_one(
            {
                "user_id": owner_id,
                "provider": courier["provider_id"],
            },
            {
                "$set": doc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        await write_audit(
            db,
            user_id=owner_id,
            actor_id=str(actor.get("id") or ""),
            actor_name=actor.get("name") or actor.get("email") or "",
            entity_type="shipping_company",
            entity_id=courier["courier_key"],
            action="update_courier_settlement_bank",
            reason_code="courier_settlement_bank_binding",
            notes=payload.notes or "",
            before_state=before,
            after_state=doc,
        )
        return await _binding_view(db, owner_id, courier)

    return router


__all__ = [
    "CourierBankBindingIn",
    "external_courier_catalog_from_rows",
    "install_accounting_courier_bank_routes",
]
