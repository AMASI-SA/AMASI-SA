"""Fail-closed MZ2 P03 inventory/purchase phase contract.

P03 is deliberately separate from P01/P02.  Operational supplier receiving and
Inventory V2 may continue to collect evidence, but no P03 accounting writer is
allowed merely because P01 is active.  A merchant owner must explicitly open
P03 with an evidence reference after P02 has been activated.

This module starts with the phase gate and a read-only work queue.  Financial
posting is added behind this gate; the legacy purchase/liability writers are
never an accounting authority for MZ2.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounting_atomic import atomic_owner
from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
    require_owner,
)
from accounting_module_status_routes import fresh_accounting_user


SOURCE = "accounting_inventory_p03"
P03_GATE_FIELD = "p03_inventory_purchases_enabled"
P03_GATE_REF_FIELD = "p03_inventory_purchases_activation_ref"
MEZAN_SUPPLIERS_V2 = "mezan_suppliers_v2"
MEZAN_SUPPLIER_INVOICES_V2 = "mezan_supplier_invoices_v2"
INVENTORY_RECEIPTS_V2 = "mezan_inventory_receipts_v2"
P03_AUDIT = "mz2_inventory_p03_audit"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _aware(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


async def read_p03_phase(db: Any, owner: str) -> dict[str, Any]:
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
    )
    state = dict((row or {}).get("mezan2_financial_cutover") or {})
    return {
        "operation_id": state.get("operation_id"),
        "p01_status": state.get("status"),
        "cutover_at": state.get("cutover_at"),
        "opening_balance_txn_group_id": state.get("opening_balance_txn_group_id"),
        "p02_shipping_cod_enabled": state.get("p02_shipping_cod_enabled") is True,
        "p02_shipping_cod_activation_ref": _text(
            state.get("p02_shipping_cod_activation_ref")
        ) or None,
        "p03_inventory_purchases_enabled": state.get(P03_GATE_FIELD) is True,
        "p03_inventory_purchases_activation_ref": _text(
            state.get(P03_GATE_REF_FIELD)
        ) or None,
        "p03_inventory_purchases_activated_at": state.get(
            "p03_inventory_purchases_activated_at"
        ),
        "p03_inventory_purchases_activated_by": state.get(
            "p03_inventory_purchases_activated_by"
        ),
    }


def p03_phase_ready(phase: dict[str, Any], *, event_at: Any = None) -> bool:
    cutover = _aware(phase.get("cutover_at"))
    event = _aware(event_at) if event_at is not None else datetime.now(timezone.utc)
    return bool(
        phase.get("operation_id") == OPERATION_ID
        and phase.get("p01_status") == "active"
        and phase.get("opening_balance_txn_group_id")
        and cutover
        and event
        and event >= cutover
        and phase.get("p02_shipping_cod_enabled") is True
        and phase.get("p02_shipping_cod_activation_ref")
        and phase.get("p03_inventory_purchases_enabled") is True
        and phase.get("p03_inventory_purchases_activation_ref")
    )


async def require_p03_inventory_financial_writes(
    db: Any,
    *,
    owner: str,
    event_at: Any = None,
) -> None:
    phase = await read_p03_phase(db, owner)
    if not p03_phase_ready(phase, event_at=event_at):
        raise HTTPException(
            423,
            detail={
                "code": "p03_inventory_purchases_locked",
                "message": "P03 inventory/purchase financial writes are locked",
                "phase": phase,
            },
        )


class P03ActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activation_ref: str = Field(min_length=3, max_length=500)
    confirmation: Literal["ACTIVATE_MZ2_P03"]

    @field_validator("activation_ref")
    @classmethod
    def clean_ref(cls, value: str) -> str:
        return value.strip()


async def activate_p03(
    db: Any,
    *,
    owner: str,
    actor: dict[str, Any],
    payload: P03ActivateIn,
) -> dict[str, Any]:
    require_owner(actor)
    require_accounting_permission(actor, "accounting.purchases.post")
    phase = await read_p03_phase(db, owner)

    if (
        phase.get("p03_inventory_purchases_enabled")
        and phase.get("p03_inventory_purchases_activation_ref")
    ):
        return {**phase, "state": "already_active"}

    missing = []
    if phase.get("operation_id") != OPERATION_ID:
        missing.append("operation_id")
    if phase.get("p01_status") != "active":
        missing.append("p01_active")
    if not phase.get("opening_balance_txn_group_id"):
        missing.append("opening_balance")
    if not phase.get("p02_shipping_cod_enabled"):
        missing.append("p02_shipping_cod_enabled")
    if not phase.get("p02_shipping_cod_activation_ref"):
        missing.append("p02_shipping_cod_activation_ref")
    if missing:
        raise HTTPException(
            409,
            detail={"code": "p03_activation_not_ready", "missing": missing},
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.settings.update_one(
        {"user_id": owner},
        {"$set": {
            f"mezan2_financial_cutover.{P03_GATE_FIELD}": True,
            f"mezan2_financial_cutover.{P03_GATE_REF_FIELD}": payload.activation_ref,
            "mezan2_financial_cutover.p03_inventory_purchases_activated_at": now,
            "mezan2_financial_cutover.p03_inventory_purchases_activated_by": actor["id"],
        }},
    )
    await db[P03_AUDIT].insert_one({
        "user_id": owner,
        "action": "p03_activated",
        "actor_id": actor["id"],
        "at": now,
        "activation_ref": payload.activation_ref,
        "p01_opening_balance_txn_group_id": phase.get(
            "opening_balance_txn_group_id"
        ),
        "p02_activation_ref": phase.get("p02_shipping_cod_activation_ref"),
    })
    return {
        **phase,
        "state": "active",
        "p03_inventory_purchases_enabled": True,
        "p03_inventory_purchases_activation_ref": payload.activation_ref,
        "p03_inventory_purchases_activated_at": now,
        "p03_inventory_purchases_activated_by": actor["id"],
    }


async def inventory_p03_workspace(db: Any, *, owner: str) -> dict[str, Any]:
    """Read-only queue; safe while P03 is locked."""
    phase = await read_p03_phase(db, owner)
    suppliers = await db[MEZAN_SUPPLIERS_V2].find(
        {
            "user_id": owner,
            "status": {"$ne": "inactive"},
        },
        {
            "_id": 0,
            "id": 1,
            "company_name": 1,
            "contact_person": 1,
            "status": 1,
        },
    ).sort("company_name", 1).to_list(2000)

    supplier_invoices = await db[MEZAN_SUPPLIER_INVOICES_V2].find(
        {
            "user_id": owner,
            "experiment_mode": {"$ne": True},
        },
        {
            "_id": 0,
            "id": 1,
            "invoice_number": 1,
            "supplier_id": 1,
            "supplier_snapshot": 1,
            "status": 1,
            "payment_status": 1,
            "total_halalas": 1,
            "outstanding_halalas": 1,
            "approved_at": 1,
            "ledger_txn_group_id": 1,
            "ledger_entry_ids": 1,
            "financial_invoice_created": 1,
            "liability_created": 1,
            "session_id": 1,
        },
    ).sort("approved_at", -1).limit(300).to_list(300)

    inventory_receipts = await db[INVENTORY_RECEIPTS_V2].find(
        {
            "user_id": owner,
            "status": "posted",
        },
        {
            "_id": 0,
            "id": 1,
            "purchase_invoice_id": 1,
            "purchase_invoice_line_id": 1,
            "invoice_number": 1,
            "supplier_name": 1,
            "mezan_product_id": 1,
            "product_name": 1,
            "sku": 1,
            "quantity": 1,
            "warehouse_id": 1,
            "location_id": 1,
            "posted_at": 1,
            "accounting_event_id": 1,
            "accounting_txn_group_id": 1,
        },
    ).sort("posted_at", -1).limit(300).to_list(300)

    return {
        "phase": phase,
        "rules": {
            "financial_writes_fail_closed": True,
            "supplier_identity_source": MEZAN_SUPPLIERS_V2,
            "supplier_invoice_source": MEZAN_SUPPLIER_INVOICES_V2,
            "inventory_receipt_source": INVENTORY_RECEIPTS_V2,
            "legacy_liabilities_are_accounting_authority": False,
            "inventory_v2_remains_operational_stock_authority": True,
        },
        "suppliers": suppliers,
        "supplier_invoices": supplier_invoices,
        "inventory_receipts": inventory_receipts,
        "summary": {
            "active_suppliers": len(suppliers),
            "supplier_invoices": len(supplier_invoices),
            "posted_inventory_receipts": len(inventory_receipts),
            "supplier_invoices_with_existing_ledger": sum(
                1 for row in supplier_invoices if row.get("ledger_txn_group_id")
            ),
            "inventory_receipts_without_mz2_accounting": sum(
                1
                for row in inventory_receipts
                if not row.get("accounting_event_id")
            ),
        },
    }


def install_inventory_p03_routes(
    router: Any,
    db: Any,
    current_user: Callable[..., Any],
) -> None:
    base = "/accounting-module/inventory-p03"

    async def actor_scope(user: dict[str, Any], permission: str):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, permission)
        owner = accounting_owner_id(actor)
        if not owner:
            raise HTTPException(403, "accounting_owner_scope_missing")
        return actor, owner

    @router.get(base + "/workspace")
    async def workspace(user: dict = Depends(current_user)):
        _, owner = await actor_scope(user, "accounting.inventory.view")
        return await inventory_p03_workspace(db, owner=owner)

    @router.post(base + "/activate")
    async def activate(
        payload: P03ActivateIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await actor_scope(user, "accounting.purchases.post")

        async def commit(scoped):
            return await activate_p03(
                scoped,
                owner=owner,
                actor=actor,
                payload=payload,
            )

        return await atomic_owner(db, owner, commit)
