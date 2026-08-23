"""General-ledger bridge for Mezan 2 store-delivery drivers.

``store_delivery`` is a presentation group only.  Every financial leg is
posted against the individual ``store_drivers.id`` so one driver's COD custody
or delivery earnings can never be hidden inside another driver's balance.

Only new canonical events call this bridge.  It never scans or backfills old
orders, earnings, collections, or settlements.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException

from ledger_core import compute_balance, post_txn_group
from store_delivery_domain import money, normalize_text


STORE_DRIVER_ENTITY_TYPE = "store_driver"
COD_RECEIVABLE = "cod_receivable"
DELIVERY_FEE_PAYABLE = "delivery_fee_payable"
DELIVERY_EXPENSE = "store_delivery"
STORE_DELIVERY_REVENUE = "store_delivery_sales"
OPERATION_ID = "MZ2-FIN-CUTOVER-001"

SettlementType = Literal[
    "cod_remittance",
    "earning_payment",
    "net_settlement",
]


def _aware_timestamp(value: Any) -> datetime | None:
    raw = normalize_text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


async def financial_cutover_is_active(
    db: Any,
    *,
    user_id: str,
    event_at: Any = None,
) -> bool:
    """Fail closed until the signed Mezan 2 cutover is explicitly activated.

    Expected tenant setting::

        mezan2_financial_cutover = {
            "operation_id": "MZ2-FIN-CUTOVER-001",
            "status": "active",
            "cutover_at": "<approved timezone-aware timestamp>"
        }

    The activation workflow is intentionally outside this change; no code here
    invents a cutover date or mutates the setting.
    """
    settings = await db.settings.find_one(
        {"user_id": user_id},
        {"_id": 0, "mezan2_financial_cutover": 1},
    )
    cutover = (settings or {}).get("mezan2_financial_cutover") or {}
    cutover_at = _aware_timestamp(cutover.get("cutover_at"))
    event_time = _aware_timestamp(event_at) or datetime.now(timezone.utc)
    return bool(
        normalize_text(cutover.get("operation_id")) == OPERATION_ID
        and normalize_text(cutover.get("status")).casefold() == "active"
        and cutover_at
        and event_time >= cutover_at
    )


def _amount(value: Any) -> float:
    return float(money(value))


def delivery_journal_entries(*, cod_custody_amount: Any, delivery_fee: Any) -> list[dict[str, Any]]:
    """Build the balanced delivery journal for one assigned driver.

    Cash COD becomes a receivable from the driver.  Card-terminal and bank
    transfer collections pass ``cod_custody_amount=0`` because the money is
    not physically in the driver's custody.  The snapshotted delivery fee is
    payable to the same driver for every successfully delivered shipment.
    """
    cod = _amount(cod_custody_amount)
    fee = _amount(delivery_fee)
    entries: list[dict[str, Any]] = []
    if cod > 0:
        entries.extend([
            {
                "entity_type": STORE_DRIVER_ENTITY_TYPE,
                "entity_id": "__driver__",
                "sub_account": COD_RECEIVABLE,
                "side": "debit",
                "amount": cod,
                "entry_type": "store_delivery_accrual",
                "notes": "عهدة تحصيل نقدي على موصل المتجر",
            },
            {
                "entity_type": "revenue",
                "entity_id": STORE_DELIVERY_REVENUE,
                "side": "credit",
                "amount": cod,
                "entry_type": "store_delivery_accrual",
                "notes": "مبيعات نقدية سلّمها موصل المتجر",
            },
        ])
    if fee > 0:
        entries.extend([
            {
                "entity_type": "expense",
                "entity_id": DELIVERY_EXPENSE,
                "side": "debit",
                "amount": fee,
                "entry_type": "store_delivery_accrual",
                "notes": "تكلفة توصيل موصل المتجر",
            },
            {
                "entity_type": STORE_DRIVER_ENTITY_TYPE,
                "entity_id": "__driver__",
                "sub_account": DELIVERY_FEE_PAYABLE,
                "side": "credit",
                "amount": fee,
                "entry_type": "store_delivery_accrual",
                "notes": "أجرة توصيل مستحقة لموصل المتجر",
            },
        ])
    return entries


def settlement_journal_entries(
    *,
    driver_id: str,
    account_id: str,
    settlement_type: SettlementType,
    bank_amount: Any,
    earning_offset: Any = 0,
) -> tuple[list[dict[str, Any]], float, float]:
    """Build settlement legs and return ``(entries, cod_settled, fee_settled)``."""
    bank = _amount(bank_amount)
    offset = _amount(earning_offset)
    entries: list[dict[str, Any]] = []

    if settlement_type == "cod_remittance":
        if bank <= 0 or offset > 0:
            raise HTTPException(422, detail={"code": "store_driver_cod_remittance_invalid"})
        cod_settled = bank
        fee_settled = 0.0
        entries = [
            {
                "entity_type": "bank", "entity_id": account_id,
                "sub_account": "main", "side": "debit", "amount": bank,
                "entry_type": "store_delivery_settlement",
            },
            {
                "entity_type": STORE_DRIVER_ENTITY_TYPE, "entity_id": driver_id,
                "sub_account": COD_RECEIVABLE, "side": "credit", "amount": bank,
                "entry_type": "store_delivery_settlement",
            },
        ]
    elif settlement_type == "earning_payment":
        if bank <= 0 or offset > 0:
            raise HTTPException(422, detail={"code": "store_driver_earning_payment_invalid"})
        cod_settled = 0.0
        fee_settled = bank
        entries = [
            {
                "entity_type": STORE_DRIVER_ENTITY_TYPE, "entity_id": driver_id,
                "sub_account": DELIVERY_FEE_PAYABLE, "side": "debit", "amount": bank,
                "entry_type": "store_delivery_settlement",
            },
            {
                "entity_type": "bank", "entity_id": account_id,
                "sub_account": "main", "side": "credit", "amount": bank,
                "entry_type": "store_delivery_settlement",
            },
        ]
    elif settlement_type == "net_settlement":
        if offset <= 0:
            raise HTTPException(422, detail={"code": "store_driver_net_offset_required"})
        cod_settled = round(bank + offset, 2)
        fee_settled = offset
        if bank > 0:
            entries.append({
                "entity_type": "bank", "entity_id": account_id,
                "sub_account": "main", "side": "debit", "amount": bank,
                "entry_type": "store_delivery_settlement",
            })
        entries.extend([
            {
                "entity_type": STORE_DRIVER_ENTITY_TYPE, "entity_id": driver_id,
                "sub_account": DELIVERY_FEE_PAYABLE, "side": "debit", "amount": offset,
                "entry_type": "store_delivery_settlement",
            },
            {
                "entity_type": STORE_DRIVER_ENTITY_TYPE, "entity_id": driver_id,
                "sub_account": COD_RECEIVABLE, "side": "credit", "amount": cod_settled,
                "entry_type": "store_delivery_settlement",
            },
        ])
    else:  # pragma: no cover - Literal protects runtime callers
        raise HTTPException(422, detail={"code": "store_driver_settlement_type_invalid"})

    return entries, round(cod_settled, 2), round(fee_settled, 2)


async def _posted_group(db: Any, user_id: str, idempotency_key: str) -> str | None:
    row = await db.general_ledger.find_one(
        {
            "user_id": user_id,
            "status": "posted",
            "metadata.idempotency_key": idempotency_key,
        },
        {"_id": 0, "txn_group_id": 1},
    )
    return normalize_text((row or {}).get("txn_group_id")) or None


async def post_delivery_journal(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    actor_name: str,
    driver: dict[str, Any],
    assignment: dict[str, Any],
    cod_custody_amount: Any,
    delivery_fee: Any,
) -> dict[str, Any]:
    """Post one idempotent delivered-shipment journal for one driver."""
    driver_id = normalize_text(driver.get("id"))
    assignment_id = normalize_text(assignment.get("id"))
    if not driver_id or not assignment_id:
        raise HTTPException(422, detail={"code": "store_delivery_accounting_identity_missing"})
    idem = f"store_delivery:delivered:{assignment_id}"
    existing = await _posted_group(db, user_id, idem)
    if existing:
        return {"ok": True, "skipped": True, "reason": "idempotent_duplicate", "txn_group_id": existing}

    entries = delivery_journal_entries(
        cod_custody_amount=cod_custody_amount,
        delivery_fee=delivery_fee,
    )
    if not entries:
        return {"ok": True, "skipped": True, "reason": "zero_financial_effect", "txn_group_id": None}
    for entry in entries:
        if entry.get("entity_id") == "__driver__":
            entry["entity_id"] = driver_id

    group = await post_txn_group(
        db,
        user_id=user_id,
        actor_id=actor_id,
        actor_name=actor_name,
        txn_type="store_delivery_accrual",
        notes=f"توصيل مندوب المتجر — {driver.get('name') or driver_id}",
        metadata={
            "source": "store_delivery_driver_app",
            "idempotency_key": idem,
            "operation_id": OPERATION_ID,
            "assignment_id": assignment_id,
            "order_id": normalize_text(assignment.get("order_id")),
            "order_number": normalize_text(assignment.get("order_number")),
            "driver_id": driver_id,
            "driver_name": normalize_text(driver.get("name")),
            "historical_backfill": False,
        },
        entries=entries,
    )
    return {"ok": True, "skipped": False, "txn_group_id": group["txn_group_id"]}


async def store_driver_ledger_balances(db: Any, *, user_id: str, driver_id: str) -> dict[str, float]:
    cod = await compute_balance(
        db,
        user_id=user_id,
        entity_type=STORE_DRIVER_ENTITY_TYPE,
        entity_id=driver_id,
        sub_account=COD_RECEIVABLE,
    )
    fee = await compute_balance(
        db,
        user_id=user_id,
        entity_type=STORE_DRIVER_ENTITY_TYPE,
        entity_id=driver_id,
        sub_account=DELIVERY_FEE_PAYABLE,
    )
    cod_receivable = max(round(float(cod.get("net_balance") or 0), 2), 0.0)
    fee_payable = max(round(-float(fee.get("net_balance") or 0), 2), 0.0)
    return {
        "cod_receivable": cod_receivable,
        "delivery_fee_payable": fee_payable,
        "net_due_from_driver": round(max(cod_receivable - fee_payable, 0), 2),
        "net_due_to_driver": round(max(fee_payable - cod_receivable, 0), 2),
        "net_balance": round(cod_receivable - fee_payable, 2),
    }


async def post_settlement_journal(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    actor_name: str,
    settlement_id: str,
    driver: dict[str, Any],
    account: dict[str, Any],
    settlement_type: SettlementType,
    bank_amount: Any,
    earning_offset: Any = 0,
    reference: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Post a driver remittance, fee payment, or explicit net settlement."""
    driver_id = normalize_text(driver.get("id"))
    account_id = normalize_text(account.get("id"))
    idem = f"store_delivery:settlement:{normalize_text(settlement_id)}"
    existing = await _posted_group(db, user_id, idem)
    if existing:
        return {"ok": True, "skipped": True, "txn_group_id": existing}

    entries, cod_settled, fee_settled = settlement_journal_entries(
        driver_id=driver_id,
        account_id=account_id,
        settlement_type=settlement_type,
        bank_amount=bank_amount,
        earning_offset=earning_offset,
    )
    balances = await store_driver_ledger_balances(
        db, user_id=user_id, driver_id=driver_id,
    )
    if cod_settled > balances["cod_receivable"] + 0.001:
        raise HTTPException(409, detail={
            "code": "store_driver_settlement_exceeds_ledger_cod",
            "available": balances["cod_receivable"],
        })
    if fee_settled > balances["delivery_fee_payable"] + 0.001:
        raise HTTPException(409, detail={
            "code": "store_driver_settlement_exceeds_ledger_fee",
            "available": balances["delivery_fee_payable"],
        })

    group = await post_txn_group(
        db,
        user_id=user_id,
        actor_id=actor_id,
        actor_name=actor_name,
        txn_type="store_delivery_settlement",
        notes=note or f"تسوية موصل المتجر — {driver.get('name') or driver_id}",
        metadata={
            "source": "store_delivery_settlements",
            "idempotency_key": idem,
            "operation_id": OPERATION_ID,
            "settlement_id": settlement_id,
            "settlement_type": settlement_type,
            "driver_id": driver_id,
            "driver_name": normalize_text(driver.get("name")),
            "account_id": account_id,
            "account_name": normalize_text(account.get("name") or account.get("provider")),
            "reference": normalize_text(reference),
            "cod_settled_amount": cod_settled,
            "delivery_fee_settled_amount": fee_settled,
            "historical_backfill": False,
        },
        entries=entries,
    )
    return {
        "ok": True,
        "skipped": False,
        "txn_group_id": group["txn_group_id"],
        "cod_settled_amount": cod_settled,
        "delivery_fee_settled_amount": fee_settled,
    }


__all__ = [
    "COD_RECEIVABLE",
    "DELIVERY_FEE_PAYABLE",
    "STORE_DRIVER_ENTITY_TYPE",
    "delivery_journal_entries",
    "financial_cutover_is_active",
    "post_delivery_journal",
    "post_settlement_journal",
    "settlement_journal_entries",
    "store_driver_ledger_balances",
]
