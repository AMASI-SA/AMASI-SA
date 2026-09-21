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
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
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
from accounting_mz2_balances import read_mz2_write_balances
from ledger_core import post_txn_group


SOURCE = "accounting_inventory_p03"
P03_GATE_FIELD = "p03_inventory_purchases_enabled"
P03_GATE_REF_FIELD = "p03_inventory_purchases_activation_ref"
MEZAN_SUPPLIERS_V2 = "mezan_suppliers_v2"
MEZAN_SUPPLIER_INVOICES_V2 = "mezan_supplier_invoices_v2"
INVENTORY_RECEIPTS_V2 = "mezan_inventory_receipts_v2"
P03_AUDIT = "mz2_inventory_p03_audit"
P03_EVENTS = "mz2_inventory_p03_events"
MONEY = Decimal("0.01")


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


def _money_from_halalas(value: Any) -> Decimal:
    try:
        halalas = int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "p03_invoice_total_invalid") from None
    if halalas <= 0:
        raise HTTPException(422, "p03_invoice_total_required")
    return (Decimal(halalas) / Decimal(100)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


async def p01_controls_purchase_accounting(
    db: Any,
    *,
    owner: str,
    mongo_session: Any = None,
) -> bool:
    """True once MZ2 P01 owns post-cutover financial journals.

    Operational receiving may still proceed, but it must stop using its
    historical direct general-ledger writer from this point forward.
    """
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
        session=mongo_session,
    )
    state = dict((row or {}).get("mezan2_financial_cutover") or {})
    return bool(
        state.get("operation_id") == OPERATION_ID
        and state.get("status") == "active"
        and state.get("opening_balance_txn_group_id")
        and _aware(state.get("cutover_at"))
    )


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


class P03PostReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip()


async def prepare_supplier_invoice_post(
    db: Any,
    *,
    owner: str,
    invoice_id: str,
) -> dict[str, Any]:
    invoice = await db[MEZAN_SUPPLIER_INVOICES_V2].find_one(
        {"user_id": owner, "id": invoice_id},
        {"_id": 0},
    )
    if not invoice:
        raise HTTPException(404, "p03_supplier_invoice_not_found")
    if invoice.get("experiment_mode") is True:
        raise HTTPException(409, "p03_experiment_invoice_not_postable")

    supplier_id = _text(invoice.get("supplier_id"))
    supplier = await db[MEZAN_SUPPLIERS_V2].find_one(
        {"user_id": owner, "id": supplier_id},
        {"_id": 0, "id": 1, "company_name": 1, "status": 1},
    )
    if not supplier:
        raise HTTPException(409, "p03_supplier_identity_missing")

    event_at = _aware(invoice.get("approved_at"))
    if not event_at:
        raise HTTPException(409, "p03_supplier_invoice_date_invalid")
    amount = _money_from_halalas(invoice.get("total_halalas"))
    event_id = _digest([owner, "supplier_invoice_v2", invoice_id])
    facts = {
        "invoice_id": invoice_id,
        "invoice_number": _text(invoice.get("invoice_number")),
        "supplier_id": supplier_id,
        "supplier_name": _text(supplier.get("company_name")) or supplier_id,
        "amount": format(amount, ".2f"),
        "accounting_at": event_at.isoformat(),
        "source_session_id": _text(invoice.get("session_id")) or None,
        "cost_treatment": "supplier_fulfillment_expense",
    }
    economic_hash = _digest(facts)

    prior = await db[P03_EVENTS].find_one(
        {"_id": event_id, "user_id": owner},
        {"_id": 0},
    )
    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise HTTPException(409, "p03_supplier_invoice_economic_conflict")
        if prior.get("status") == "posted":
            return {
                "state": "already_posted",
                "event_id": event_id,
                "facts": facts,
                "txn_group_id": prior.get("txn_group_id"),
            }
        raise HTTPException(409, "p03_supplier_invoice_requires_recovery")

    existing_group = _text(invoice.get("ledger_txn_group_id"))
    if existing_group:
        return {
            "state": "blocked",
            "event_id": event_id,
            "facts": facts,
            "reasons": ["supplier_invoice_existing_non_p03_ledger"],
            "existing_txn_group_id": existing_group,
        }

    return {
        "state": "eligible",
        "event_id": event_id,
        "facts": facts,
        "economic_hash": economic_hash,
    }


async def post_supplier_invoice(
    db: Any,
    *,
    owner: str,
    actor: dict[str, Any],
    invoice_id: str,
    reason: str,
) -> dict[str, Any]:
    proposal = await prepare_supplier_invoice_post(
        db,
        owner=owner,
        invoice_id=invoice_id,
    )
    if proposal["state"] == "already_posted":
        return proposal
    if proposal["state"] != "eligible":
        raise HTTPException(
            409,
            detail={
                "code": "p03_supplier_invoice_not_eligible",
                "reasons": proposal.get("reasons") or [],
            },
        )

    facts = proposal["facts"]
    await require_p03_inventory_financial_writes(
        db,
        owner=owner,
        event_at=facts["accounting_at"],
    )
    await read_mz2_write_balances(
        db,
        owner=owner,
        required_accounts=[
            ("supplier", facts["supplier_id"], "payable"),
        ],
    )

    now = datetime.now(timezone.utc).isoformat()
    event = {
        "_id": proposal["event_id"],
        "id": proposal["event_id"],
        "user_id": owner,
        "kind": "supplier_invoice",
        "status": "posting",
        "economic_hash": proposal["economic_hash"],
        "facts": facts,
        "reason": reason,
        "created_at": now,
        "created_by": actor["id"],
    }
    await db[P03_EVENTS].insert_one(event)

    result = await post_txn_group(
        db,
        user_id=owner,
        actor_id=actor["id"],
        actor_name=actor.get("name") or actor.get("email") or actor["id"],
        txn_type="mz2_supplier_invoice",
        notes=f"فاتورة مورد MZ2 — {facts['invoice_number']} — {facts['supplier_name']}",
        metadata={
            "operation_id": OPERATION_ID,
            "source": SOURCE,
            "p03_event_id": proposal["event_id"],
            "p03_kind": "supplier_invoice",
            "supplier_invoice_v2_id": facts["invoice_id"],
            "supplier_id": facts["supplier_id"],
            "supplier_receiving_session_id": facts["source_session_id"],
            "accounting_at": facts["accounting_at"],
            "cost_treatment": facts["cost_treatment"],
            "reason": reason,
        },
        entries=[
            {
                "entity_type": "expense",
                "entity_id": "supplier_fulfillment",
                "side": "debit",
                "amount": facts["amount"],
                "entry_type": "supplier_invoice",
            },
            {
                "entity_type": "supplier",
                "entity_id": facts["supplier_id"],
                "sub_account": "payable",
                "side": "credit",
                "amount": facts["amount"],
                "entry_type": "supplier_invoice",
            },
        ],
    )

    changed = await db[MEZAN_SUPPLIER_INVOICES_V2].update_one(
        {
            "user_id": owner,
            "id": facts["invoice_id"],
            "$or": [
                {"ledger_txn_group_id": {"$exists": False}},
                {"ledger_txn_group_id": None},
                {"ledger_txn_group_id": ""},
            ],
        },
        {"$set": {
            "status": "payable_posted",
            "payment_status": "unpaid",
            "outstanding_halalas": int(
                (Decimal(facts["amount"]) * Decimal(100)).to_integral_value()
            ),
            "financial_invoice_created": True,
            "liability_created": True,
            "payable_posted_at": now,
            "ledger_txn_group_id": result["txn_group_id"],
            "ledger_entry_ids": result.get("entry_ids") or [],
            "p03_accounting_event_id": proposal["event_id"],
            "p03_accounting_source": SOURCE,
            "updated_at": now,
        }},
    )
    if changed.modified_count != 1:
        raise HTTPException(409, "p03_supplier_invoice_concurrent_post")

    await db[P03_EVENTS].update_one(
        {
            "_id": proposal["event_id"],
            "user_id": owner,
            "status": "posting",
        },
        {"$set": {
            "status": "posted",
            "txn_group_id": result["txn_group_id"],
            "posted_at": now,
            "posted_by": actor["id"],
        }},
    )
    await db[P03_AUDIT].insert_one({
        "user_id": owner,
        "action": "supplier_invoice_posted",
        "actor_id": actor["id"],
        "at": now,
        "invoice_id": facts["invoice_id"],
        "supplier_id": facts["supplier_id"],
        "event_id": proposal["event_id"],
        "txn_group_id": result["txn_group_id"],
        "amount": facts["amount"],
        "reason": reason,
    })
    return {
        "state": "posted",
        "event_id": proposal["event_id"],
        "txn_group_id": result["txn_group_id"],
        "facts": facts,
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

    @router.get(base + "/supplier-invoices/{invoice_id}/preview")
    async def supplier_invoice_preview(
        invoice_id: str,
        user: dict = Depends(current_user),
    ):
        _, owner = await actor_scope(user, "accounting.inventory.view")
        return await prepare_supplier_invoice_post(
            db,
            owner=owner,
            invoice_id=invoice_id,
        )

    @router.post(base + "/supplier-invoices/{invoice_id}/post")
    async def supplier_invoice_post(
        invoice_id: str,
        payload: P03PostReasonIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await actor_scope(user, "accounting.purchases.post")

        async def commit(scoped):
            return await post_supplier_invoice(
                scoped,
                owner=owner,
                actor=actor,
                invoice_id=invoice_id,
                reason=payload.reason,
            )

        return await atomic_owner(db, owner, commit)

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
