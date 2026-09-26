"""MZ2 shipping/COD settlements driven by imported bank/cash evidence.

A settlement never invents cash.  The user classifies one previously imported
MZ2 daily movement, and the accounting service consumes that evidence exactly
once.

Supported:
- COD remittance: actual inbound bank/cash closes COD receivable.
- Fee payment: actual outbound bank/cash closes shipping/driver payable.
- Net settlement: actual inbound cash plus an explicit payable offset closes
  the corresponding gross COD receivable.

No expense, revenue, or sales-tax leg is created by settlement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounting_atomic import atomic_owner
from accounting_mz2_balances import read_mz2_write_balances
from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_module_status_routes import fresh_accounting_user
from accounting_shipping_p02 import SOURCE, ShippingAccountingError, _money
from ledger_core import post_txn_group
from store_delivery_accounting import require_p02_shipping_financial_writes


RIYADH = ZoneInfo("Asia/Riyadh")


def _hash(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _movement_accounting_at(value: str) -> str:
    try:
        day = datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise ShippingAccountingError("daily_movement_date_invalid") from None
    # Bank/cash import is day-precision evidence; Saudi midnight preserves the
    # source day without fabricating an exact transfer time.
    return datetime(
        day.year, day.month, day.day, 0, 0, tzinfo=RIYADH
    ).astimezone(timezone.utc).isoformat()


class ShippingSettlementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movement_id: str = Field(min_length=1, max_length=200)
    counterparty_type: Literal["store_driver", "courier"]
    counterparty_id: str = Field(min_length=1, max_length=200)
    settlement_type: Literal["cod_remittance", "fee_payment", "net_settlement"]
    offset_amount: Decimal = Decimal("0.00")
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("movement_id", "counterparty_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("offset_amount")
    @classmethod
    def offset_valid(cls, value: Decimal) -> Decimal:
        amount = _money(value)
        if amount < 0:
            raise ValueError("offset cannot be negative")
        return amount


def _subaccounts(counterparty_type: str) -> tuple[str, str]:
    if counterparty_type == "store_driver":
        return "cod_receivable", "delivery_fee_payable"
    return "cod_receivable", "payable"


async def _counterparty_exists(
    db,
    *,
    owner: str,
    counterparty_type: str,
    counterparty_id: str,
) -> bool:
    if counterparty_type == "store_driver":
        return bool(await db.store_drivers.find_one({
            "user_id": owner,
            "id": counterparty_id,
            "status": {"$ne": "inactive"},
        }, {"_id": 1}))

    policy = await db.mz2_shipping_rate_policies.find_one(
        {"_id": owner, "user_id": owner},
        {"_id": 0, "versions": 1},
    ) or {}
    return any(
        isinstance(version, dict)
        and version.get("verification_status") == "approved"
        and str(version.get("courier_id") or "").strip() == counterparty_id
        for version in (policy.get("versions") or [])
    )


async def prepare_shipping_settlement(
    db,
    *,
    owner: str,
    payload: ShippingSettlementIn,
) -> dict[str, Any]:
    movement = await db.mz2_daily_movements.find_one(
        {"user_id": owner, "id": payload.movement_id}
    )
    if not movement:
        raise ShippingAccountingError("daily_movement_not_found")

    event_id = _hash([owner, "shipping_settlement", payload.movement_id])
    prior = await db.mz2_shipping_accounting_events.find_one({
        "_id": event_id,
        "user_id": owner,
    })
    if prior and prior.get("status") != "posted":
        raise ShippingAccountingError("shipping_event_requires_recovery")

    if not await _counterparty_exists(
        db,
        owner=owner,
        counterparty_type=payload.counterparty_type,
        counterparty_id=payload.counterparty_id,
    ):
        raise ShippingAccountingError("shipping_counterparty_missing")

    if movement.get("receipt_id") or movement.get("confirmed_provider") or movement.get("explicit_provider"):
        raise ShippingAccountingError("provider_movement_cannot_be_shipping_settlement")
    if movement.get("suggested_provider"):
        raise ShippingAccountingError("provider_suggestion_must_be_resolved_first")

    existing_accounting_event = str(movement.get("accounting_event_id") or "").strip()
    if existing_accounting_event and existing_accounting_event != event_id:
        raise ShippingAccountingError("daily_movement_already_consumed")
    if movement.get("status") not in {"unclassified", "accounting_posted"}:
        raise ShippingAccountingError("daily_movement_not_available")
    if movement.get("status") == "accounting_posted":
        expected_action = "shipping_" + payload.settlement_type
        if (
            movement.get("accounting_action") != expected_action
            or movement.get("accounting_counterparty_type") != payload.counterparty_type
            or movement.get("accounting_counterparty_id") != payload.counterparty_id
        ):
            raise ShippingAccountingError("daily_movement_already_consumed")

    bank_account_id = str(movement.get("bank_account_id") or "").strip()
    if not bank_account_id:
        raise ShippingAccountingError("daily_movement_bank_missing")
    amount = _money(movement.get("amount"))
    if amount <= 0:
        raise ShippingAccountingError("shipping_settlement_amount_required")
    direction = str(movement.get("direction") or "")
    if payload.settlement_type in {"cod_remittance", "net_settlement"}:
        if direction != "in":
            raise ShippingAccountingError("shipping_settlement_requires_inflow")
    elif direction != "out":
        raise ShippingAccountingError("shipping_fee_payment_requires_outflow")

    offset = _money(payload.offset_amount)
    if payload.settlement_type == "net_settlement":
        if offset <= 0:
            raise ShippingAccountingError("shipping_net_offset_required")
    elif offset != Decimal("0.00"):
        raise ShippingAccountingError("shipping_offset_not_allowed")

    accounting_at = _movement_accounting_at(movement["movement_date"])
    facts = {
        "event_id": event_id,
        "movement_id": payload.movement_id,
        "movement_file_id": movement.get("file_id"),
        "bank_account_id": bank_account_id,
        "bank_reference": movement.get("reference"),
        "movement_date": movement.get("movement_date"),
        "movement_amount": format(amount, ".2f"),
        "movement_direction": direction,
        "counterparty_type": payload.counterparty_type,
        "counterparty_id": payload.counterparty_id,
        "settlement_type": payload.settlement_type,
        "offset_amount": format(offset, ".2f"),
        "accounting_at": accounting_at,
        "reason": payload.reason,
    }
    economic_hash = _hash(facts)

    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise ShippingAccountingError("shipping_event_source_conflict")
        return {
            "state": "already_posted",
            "event_id": event_id,
            "facts": facts,
            "txn_group_id": prior.get("txn_group_id"),
        }

    return {
        "state": "eligible",
        "event_id": event_id,
        "facts": facts,
        "economic_hash": economic_hash,
        "movement": movement,
    }


async def post_shipping_settlement(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    payload: ShippingSettlementIn,
) -> dict[str, Any]:
    async def commit(scoped):
        proposal = await prepare_shipping_settlement(
            scoped,
            owner=owner,
            payload=payload,
        )
        if proposal["state"] == "already_posted":
            return proposal

        facts = proposal["facts"]
        movement = proposal["movement"]
        await require_p02_shipping_financial_writes(
            scoped,
            user_id=owner,
            event_at=facts["accounting_at"],
        )

        cod_sub, payable_sub = _subaccounts(payload.counterparty_type)
        required = [
            ("bank", facts["bank_account_id"], "main"),
            (
                payload.counterparty_type,
                payload.counterparty_id,
                cod_sub,
            ),
            (
                payload.counterparty_type,
                payload.counterparty_id,
                payable_sub,
            ),
        ]
        balances = await read_mz2_write_balances(
            scoped,
            owner=owner,
            required_accounts=required,
        )
        amount = Decimal(facts["movement_amount"])
        offset = Decimal(facts["offset_amount"])
        bank = balances.net_balance(
            entity_type="bank",
            entity_id=facts["bank_account_id"],
            sub_account="main",
        )
        cod_open = max(
            balances.net_balance(
                entity_type=payload.counterparty_type,
                entity_id=payload.counterparty_id,
                sub_account=cod_sub,
            ),
            Decimal("0"),
        )
        payable_open = max(
            -balances.net_balance(
                entity_type=payload.counterparty_type,
                entity_id=payload.counterparty_id,
                sub_account=payable_sub,
            ),
            Decimal("0"),
        )

        if payload.settlement_type == "fee_payment":
            if amount > payable_open:
                raise HTTPException(409, detail={
                    "code": "shipping_fee_payment_exceeds_payable",
                    "available": format(payable_open, ".2f"),
                    "requested": format(amount, ".2f"),
                })
            if amount > bank:
                raise HTTPException(409, detail={
                    "code": "insufficient_mz2_bank_balance",
                    "available": format(max(bank, Decimal("0")), ".2f"),
                    "required": format(amount, ".2f"),
                })
            entries = [
                {
                    "entity_type": payload.counterparty_type,
                    "entity_id": payload.counterparty_id,
                    "sub_account": payable_sub,
                    "side": "debit",
                    "amount": format(amount, ".2f"),
                    "entry_type": "shipping_settlement",
                },
                {
                    "entity_type": "bank",
                    "entity_id": facts["bank_account_id"],
                    "sub_account": "main",
                    "side": "credit",
                    "amount": format(amount, ".2f"),
                    "entry_type": "shipping_settlement",
                },
            ]
            gross_cleared = Decimal("0")
            payable_cleared = amount
        elif payload.settlement_type == "cod_remittance":
            if amount > cod_open:
                raise HTTPException(409, detail={
                    "code": "shipping_cod_remittance_exceeds_receivable",
                    "available": format(cod_open, ".2f"),
                    "requested": format(amount, ".2f"),
                })
            entries = [
                {
                    "entity_type": "bank",
                    "entity_id": facts["bank_account_id"],
                    "sub_account": "main",
                    "side": "debit",
                    "amount": format(amount, ".2f"),
                    "entry_type": "shipping_settlement",
                },
                {
                    "entity_type": payload.counterparty_type,
                    "entity_id": payload.counterparty_id,
                    "sub_account": cod_sub,
                    "side": "credit",
                    "amount": format(amount, ".2f"),
                    "entry_type": "shipping_settlement",
                },
            ]
            gross_cleared = amount
            payable_cleared = Decimal("0")
        else:
            gross_cleared = amount + offset
            payable_cleared = offset
            if gross_cleared > cod_open:
                raise HTTPException(409, detail={
                    "code": "shipping_net_settlement_exceeds_cod",
                    "available": format(cod_open, ".2f"),
                    "requested": format(gross_cleared, ".2f"),
                })
            if offset > payable_open:
                raise HTTPException(409, detail={
                    "code": "shipping_net_offset_exceeds_payable",
                    "available": format(payable_open, ".2f"),
                    "requested": format(offset, ".2f"),
                })
            entries = [
                {
                    "entity_type": "bank",
                    "entity_id": facts["bank_account_id"],
                    "sub_account": "main",
                    "side": "debit",
                    "amount": format(amount, ".2f"),
                    "entry_type": "shipping_settlement",
                },
                {
                    "entity_type": payload.counterparty_type,
                    "entity_id": payload.counterparty_id,
                    "sub_account": payable_sub,
                    "side": "debit",
                    "amount": format(offset, ".2f"),
                    "entry_type": "shipping_settlement",
                },
                {
                    "entity_type": payload.counterparty_type,
                    "entity_id": payload.counterparty_id,
                    "sub_account": cod_sub,
                    "side": "credit",
                    "amount": format(gross_cleared, ".2f"),
                    "entry_type": "shipping_settlement",
                },
            ]

        await scoped.mz2_shipping_accounting_events.insert_one({
            "_id": proposal["event_id"],
            "id": proposal["event_id"],
            "user_id": owner,
            "operation_id": "MZ2-FIN-CUTOVER-001",
            "source": SOURCE,
            "kind": "shipping_settlement",
            "economic_hash": proposal["economic_hash"],
            "facts": facts,
            "status": "posting",
            "actor_id": actor["id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        result = await post_txn_group(
            scoped,
            user_id=owner,
            actor_id=actor["id"],
            actor_name=actor.get("name") or actor.get("email") or actor["id"],
            txn_type="mz2_shipping_settlement",
            notes=payload.reason,
            metadata={
                "operation_id": "MZ2-FIN-CUTOVER-001",
                "source": SOURCE,
                "shipping_event_id": proposal["event_id"],
                "shipping_event_kind": "shipping_settlement",
                "accounting_at": facts["accounting_at"],
                "daily_movement_id": facts["movement_id"],
                "daily_movement_file_id": facts["movement_file_id"],
                "bank_reference": facts["bank_reference"],
                "counterparty_type": payload.counterparty_type,
                "counterparty_id": payload.counterparty_id,
                "settlement_type": payload.settlement_type,
                "bank_amount": facts["movement_amount"],
                "offset_amount": facts["offset_amount"],
                "gross_cod_cleared": format(gross_cleared, ".2f"),
                "payable_cleared": format(payable_cleared, ".2f"),
            },
            entries=entries,
        )
        now = datetime.now(timezone.utc).isoformat()
        await scoped.mz2_shipping_accounting_events.update_one(
            {
                "_id": proposal["event_id"],
                "user_id": owner,
                "status": "posting",
            },
            {"$set": {
                "status": "posted",
                "txn_group_id": result["txn_group_id"],
                "posted_at": now,
            }},
        )
        changed = await scoped.mz2_daily_movements.update_one(
            {
                "_id": movement["_id"],
                "user_id": owner,
                "accounting_event_id": {"$in": [None, ""]},
            },
            {"$set": {
                "status": "accounting_posted",
                "accounting_event_id": proposal["event_id"],
                "accounting_action": "shipping_" + payload.settlement_type,
                "accounting_txn_group_id": result["txn_group_id"],
                "accounting_counterparty_type": payload.counterparty_type,
                "accounting_counterparty_id": payload.counterparty_id,
                "accounting_reason": payload.reason,
                "consumed_at": now,
                "consumed_by": actor["id"],
            }},
        )
        if changed.modified_count != 1:
            raise ShippingAccountingError(
                "daily_movement_concurrent_consumption"
            )
        return {
            **proposal,
            "state": "posted",
            "txn_group_id": result["txn_group_id"],
            "gross_cod_cleared": format(gross_cleared, ".2f"),
            "payable_cleared": format(payable_cleared, ".2f"),
        }

    return await atomic_owner(db, owner, commit)


def install_shipping_settlement_routes(router, db, current_user) -> None:
    base = "/accounting-module/shipping-p02/settlements"

    async def scope(user: dict[str, Any], permission: str):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, permission)
        owner = accounting_owner_id(actor)
        if not owner:
            raise HTTPException(403, "accounting_owner_scope_missing")
        return actor, owner

    @router.post(base + "/preview")
    async def preview(
        payload: ShippingSettlementIn,
        user: dict = Depends(current_user),
    ):
        _, owner = await scope(user, "accounting.shipping.view")
        try:
            return await prepare_shipping_settlement(
                db, owner=owner, payload=payload
            )
        except ShippingAccountingError as exc:
            return {"state": "rejected", "reasons": [str(exc)]}

    @router.post(base + "/post")
    async def post(
        payload: ShippingSettlementIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(user, "accounting.settlements.post")
        try:
            return await post_shipping_settlement(
                db,
                owner=owner,
                actor=actor,
                payload=payload,
            )
        except ShippingAccountingError as exc:
            raise HTTPException(
                409, detail={"code": str(exc), "message": str(exc)}
            ) from None
