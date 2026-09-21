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
from pymongo.errors import DuplicateKeyError

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
PURCHASE_INVOICES = "purchase_invoices"
MEZAN_PRODUCTS_V2 = "mezan_products_v2"
P03_PURCHASE_REQUESTS = "mz2_inventory_p03_purchase_requests"
P03_AUDIT = "mz2_inventory_p03_audit"
P03_EVENTS = "mz2_inventory_p03_events"
MONEY = Decimal("0.01")
HALALA = Decimal("1")


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


def _money(value: Any, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except Exception:
        raise HTTPException(422, "p03_amount_invalid") from None
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise HTTPException(422, "p03_amount_invalid")
    return result


def _halalas(value: Decimal) -> int:
    return int((value * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))


def _date_text(value: Any) -> str:
    text = _text(value)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "p03_date_invalid") from None
    return parsed.isoformat()


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


class P03PurchaseLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str | None = Field(default=None, max_length=160)
    product_name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=120)
    quantity: int = Field(ge=1, le=100000)
    unit_price: Decimal

    @field_validator("product_id", "product_name", "sku")
    @classmethod
    def clean_line_text(cls, value):
        if value is None:
            return None
        cleaned = _text(value)
        return cleaned or None

    @field_validator("unit_price")
    @classmethod
    def valid_unit_price(cls, value: Decimal) -> Decimal:
        return _money(value)


class P03PurchaseInvoiceCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=200)
    supplier_id: str = Field(min_length=1, max_length=160)
    invoice_number: str | None = Field(default=None, max_length=120)
    invoice_date: str = Field(min_length=10, max_length=10)
    due_date: str | None = Field(default=None, min_length=10, max_length=10)
    lines: list[P03PurchaseLineIn] = Field(min_length=1, max_length=500)
    tax_amount: Decimal = Decimal("0")
    tax_treatment: Literal[
        "recoverable_input_vat",
        "included_in_inventory_cost",
    ] = "recoverable_input_vat"
    notes: str = Field(default="", max_length=2000)

    @field_validator("supplier_id", "invoice_number", "notes")
    @classmethod
    def clean_purchase_text(cls, value):
        if value is None:
            return None
        cleaned = _text(value)
        return cleaned or None

    @field_validator("invoice_date")
    @classmethod
    def valid_invoice_date(cls, value: str) -> str:
        return _date_text(value)

    @field_validator("due_date")
    @classmethod
    def valid_due_date(cls, value: str | None) -> str | None:
        return _date_text(value) if value else None

    @field_validator("tax_amount")
    @classmethod
    def valid_tax_amount(cls, value: Decimal) -> Decimal:
        return _money(value, allow_zero=True)


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


async def create_p03_purchase_invoice(
    db: Any,
    *,
    owner: str,
    actor: dict[str, Any],
    payload: P03PurchaseInvoiceCreateIn,
) -> dict[str, Any]:
    require_accounting_permission(actor, "accounting.purchases.post")
    supplier = await db[MEZAN_SUPPLIERS_V2].find_one(
        {
            "user_id": owner,
            "id": payload.supplier_id,
            "status": {"$ne": "inactive"},
        },
        {"_id": 0, "id": 1, "company_name": 1},
    )
    if not supplier:
        raise HTTPException(404, "p03_supplier_not_found")

    product_ids = sorted({
        _text(line.product_id)
        for line in payload.lines
        if _text(line.product_id)
    })
    if product_ids:
        products = await db[MEZAN_PRODUCTS_V2].find(
            {
                "user_id": owner,
                "$or": [
                    {"mezan_product_id": {"$in": product_ids}},
                    {"salla_product_id": {"$in": product_ids}},
                ],
                "archived": {"$ne": True},
            },
            {
                "_id": 0,
                "mezan_product_id": 1,
                "salla_product_id": 1,
                "name": 1,
                "sku": 1,
            },
        ).to_list(len(product_ids) * 2 + 1)
        resolved = set()
        for row in products:
            for key in ("mezan_product_id", "salla_product_id"):
                value = _text(row.get(key))
                if value:
                    resolved.add(value)
        missing = sorted(set(product_ids) - resolved)
        if missing:
            raise HTTPException(
                409,
                detail={"code": "p03_purchase_product_missing", "product_ids": missing},
            )

    enriched = []
    subtotal = Decimal("0")
    for index, line in enumerate(payload.lines, start=1):
        unit_price = _money(line.unit_price)
        line_total = (unit_price * Decimal(line.quantity)).quantize(
            MONEY, rounding=ROUND_HALF_UP
        )
        subtotal += line_total
        enriched.append({
            "id": _digest([
                owner,
                "p03_purchase_line",
                payload.request_id,
                index,
            ])[:32],
            "product_id": _text(line.product_id) or None,
            "product_name": _text(line.product_name),
            "sku": _text(line.sku) or None,
            "quantity": int(line.quantity),
            "unit_price": float(unit_price),
            "unit_price_halalas": _halalas(unit_price),
            "line_total": float(line_total),
            "line_total_halalas": _halalas(line_total),
        })
    subtotal = subtotal.quantize(MONEY, rounding=ROUND_HALF_UP)
    tax = _money(payload.tax_amount, allow_zero=True)
    total = (subtotal + tax).quantize(MONEY, rounding=ROUND_HALF_UP)
    if subtotal <= 0 or total <= 0:
        raise HTTPException(422, "p03_purchase_total_required")

    remaining_tax_halalas = _halalas(tax)
    subtotal_halalas = _halalas(subtotal)
    for index, line in enumerate(enriched):
        if remaining_tax_halalas <= 0:
            allocated = 0
        elif index == len(enriched) - 1:
            allocated = remaining_tax_halalas
        else:
            raw = (
                Decimal(_halalas(tax))
                * Decimal(line["line_total_halalas"])
                / Decimal(subtotal_halalas)
            ).quantize(HALALA, rounding=ROUND_HALF_UP)
            allocated = min(int(raw), remaining_tax_halalas)
        line["line_tax_halalas"] = allocated
        line["line_tax_amount"] = float(
            (Decimal(allocated) / Decimal(100)).quantize(MONEY)
        )
        line["line_gross_halalas"] = line["line_total_halalas"] + allocated
        line["line_gross_total"] = float(
            (
                Decimal(line["line_gross_halalas"]) / Decimal(100)
            ).quantize(MONEY)
        )
        remaining_tax_halalas -= allocated

    facts = {
        "supplier_id": payload.supplier_id,
        "invoice_number": payload.invoice_number,
        "invoice_date": payload.invoice_date,
        "due_date": payload.due_date,
        "lines": enriched,
        "subtotal": format(subtotal, ".2f"),
        "tax_amount": format(tax, ".2f"),
        "total": format(total, ".2f"),
        "tax_treatment": payload.tax_treatment,
        "notes": payload.notes or "",
    }
    request_key = _digest([owner, "p03_purchase_request", payload.request_id])
    prior = await db[P03_PURCHASE_REQUESTS].find_one(
        {"_id": request_key, "user_id": owner},
        {"_id": 0},
    )
    if prior:
        if prior.get("facts_hash") != _digest(facts):
            raise HTTPException(409, "p03_purchase_request_conflict")
        existing = await db[PURCHASE_INVOICES].find_one(
            {"user_id": owner, "id": prior["purchase_invoice_id"]},
            {"_id": 0},
        )
        if not existing:
            raise HTTPException(409, "p03_purchase_request_requires_recovery")
        return {**existing, "duplicate": True}

    invoice_id = "mz2pi_" + _digest([
        owner,
        payload.request_id,
        payload.supplier_id,
    ])[:32]
    now = datetime.now(timezone.utc).isoformat()
    invoice = {
        "id": invoice_id,
        "user_id": owner,
        "supplier_id": payload.supplier_id,
        "supplier_name": supplier.get("company_name") or payload.supplier_id,
        "invoice_number": payload.invoice_number,
        "invoice_date": payload.invoice_date,
        "due_date": payload.due_date,
        "lines": enriched,
        "subtotal": float(subtotal),
        "tax_amount": float(tax),
        "total": float(total),
        "subtotal_halalas": _halalas(subtotal),
        "tax_halalas": _halalas(tax),
        "total_halalas": _halalas(total),
        "tax_treatment": payload.tax_treatment,
        "notes": payload.notes or "",
        "status": "open_for_receiving",
        "payment_status": "not_recognized_until_received",
        "liability_id": None,
        "source": SOURCE,
        "accounting_authority": SOURCE,
        "legacy_liability_used": False,
        "request_id": payload.request_id,
        "created_by": actor["id"],
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db[PURCHASE_INVOICES].insert_one(dict(invoice))
        await db[P03_PURCHASE_REQUESTS].insert_one({
            "_id": request_key,
            "user_id": owner,
            "purchase_invoice_id": invoice_id,
            "facts_hash": _digest(facts),
            "created_at": now,
            "created_by": actor["id"],
        })
    except DuplicateKeyError:
        existing = await db[PURCHASE_INVOICES].find_one(
            {"user_id": owner, "id": invoice_id},
            {"_id": 0},
        )
        if existing and existing.get("request_id") == payload.request_id:
            return {**existing, "duplicate": True}
        raise

    await db[P03_AUDIT].insert_one({
        "user_id": owner,
        "action": "purchase_invoice_created",
        "purchase_invoice_id": invoice_id,
        "supplier_id": payload.supplier_id,
        "actor_id": actor["id"],
        "at": now,
        "subtotal": format(subtotal, ".2f"),
        "tax_amount": format(tax, ".2f"),
        "total": format(total, ".2f"),
        "tax_treatment": payload.tax_treatment,
    })
    return invoice


async def prepare_inventory_receipt_post(
    db: Any,
    *,
    owner: str,
    receipt_id: str,
) -> dict[str, Any]:
    receipt = await db[INVENTORY_RECEIPTS_V2].find_one(
        {"user_id": owner, "id": receipt_id, "status": "posted"},
        {"_id": 0},
    )
    if not receipt:
        raise HTTPException(404, "p03_inventory_receipt_not_found")
    purchase_invoice_id = _text(receipt.get("purchase_invoice_id"))
    line_id = _text(receipt.get("purchase_invoice_line_id"))
    invoice = await db[PURCHASE_INVOICES].find_one(
        {
            "user_id": owner,
            "id": purchase_invoice_id,
            "accounting_authority": SOURCE,
            "source": SOURCE,
        },
        {"_id": 0},
    )
    if not invoice:
        raise HTTPException(409, "p03_purchase_invoice_not_mz2_native")

    supplier_id = _text(invoice.get("supplier_id"))
    supplier = await db[MEZAN_SUPPLIERS_V2].find_one(
        {"user_id": owner, "id": supplier_id},
        {"_id": 0, "id": 1, "company_name": 1},
    )
    if not supplier:
        raise HTTPException(409, "p03_supplier_identity_missing")
    line = next(
        (
            row
            for row in invoice.get("lines") or []
            if _text(row.get("id")) == line_id
        ),
        None,
    )
    if not line:
        raise HTTPException(409, "p03_purchase_invoice_line_missing")

    receipt_qty = int(receipt.get("quantity") or 0)
    line_qty = int(line.get("quantity") or 0)
    if receipt_qty <= 0 or line_qty <= 0:
        raise HTTPException(409, "p03_inventory_receipt_quantity_invalid")

    event_at = _aware(receipt.get("posted_at"))
    if not event_at:
        raise HTTPException(409, "p03_inventory_receipt_date_invalid")
    event_id = _digest([owner, "inventory_receipt_v2", receipt_id])

    prior_event = await db[P03_EVENTS].find_one(
        {"_id": event_id, "user_id": owner},
        {"_id": 0},
    )
    if prior_event and prior_event.get("status") == "posted":
        return {
            "state": "already_posted",
            "event_id": event_id,
            "facts": prior_event.get("facts") or {},
            "txn_group_id": prior_event.get("txn_group_id"),
        }
    if prior_event:
        raise HTTPException(409, "p03_inventory_receipt_requires_recovery")

    prior_rows = await db[P03_EVENTS].find(
        {
            "user_id": owner,
            "kind": "inventory_receipt",
            "status": "posted",
            "facts.purchase_invoice_id": purchase_invoice_id,
            "facts.purchase_invoice_line_id": line_id,
        },
        {
            "_id": 0,
            "facts.received_quantity": 1,
            "facts.tax_halalas": 1,
        },
    ).to_list(10000)
    prior_qty = sum(
        int((row.get("facts") or {}).get("received_quantity") or 0)
        for row in prior_rows
    )
    prior_tax_halalas = sum(
        int((row.get("facts") or {}).get("tax_halalas") or 0)
        for row in prior_rows
    )
    remaining_qty = line_qty - prior_qty
    if receipt_qty > remaining_qty:
        raise HTTPException(
            409,
            detail={
                "code": "p03_inventory_receipt_exceeds_invoice_line",
                "remaining_quantity": max(0, remaining_qty),
                "receipt_quantity": receipt_qty,
            },
        )

    unit_price_halalas = int(line.get("unit_price_halalas") or 0)
    if unit_price_halalas <= 0:
        raise HTTPException(409, "p03_purchase_line_unit_cost_invalid")
    net_halalas = unit_price_halalas * receipt_qty
    line_tax_halalas = int(line.get("line_tax_halalas") or 0)
    remaining_tax = max(0, line_tax_halalas - prior_tax_halalas)
    if receipt_qty == remaining_qty:
        tax_halalas = remaining_tax
    elif line_tax_halalas <= 0:
        tax_halalas = 0
    else:
        raw_tax = (
            Decimal(line_tax_halalas)
            * Decimal(receipt_qty)
            / Decimal(line_qty)
        ).quantize(HALALA, rounding=ROUND_HALF_UP)
        tax_halalas = min(int(raw_tax), remaining_tax)

    gross_halalas = net_halalas + tax_halalas
    tax_treatment = _text(invoice.get("tax_treatment")) or "recoverable_input_vat"
    if tax_treatment not in {
        "recoverable_input_vat",
        "included_in_inventory_cost",
    }:
        raise HTTPException(409, "p03_purchase_tax_treatment_invalid")

    facts = {
        "receipt_id": receipt_id,
        "purchase_invoice_id": purchase_invoice_id,
        "purchase_invoice_line_id": line_id,
        "invoice_number": _text(invoice.get("invoice_number")),
        "supplier_id": supplier_id,
        "supplier_name": supplier.get("company_name") or supplier_id,
        "received_quantity": receipt_qty,
        "product_id": _text(receipt.get("mezan_product_id")) or None,
        "product_name": _text(receipt.get("product_name")) or _text(line.get("product_name")),
        "sku": _text(receipt.get("sku")) or _text(line.get("sku")) or None,
        "warehouse_id": _text(receipt.get("warehouse_id")) or None,
        "location_id": _text(receipt.get("location_id")) or None,
        "net_halalas": net_halalas,
        "tax_halalas": tax_halalas,
        "gross_halalas": gross_halalas,
        "net_amount": format(Decimal(net_halalas) / Decimal(100), ".2f"),
        "tax_amount": format(Decimal(tax_halalas) / Decimal(100), ".2f"),
        "gross_amount": format(Decimal(gross_halalas) / Decimal(100), ".2f"),
        "tax_treatment": tax_treatment,
        "accounting_at": event_at.isoformat(),
    }
    return {
        "state": "eligible",
        "event_id": event_id,
        "facts": facts,
        "economic_hash": _digest(facts),
    }


async def post_inventory_receipt(
    db: Any,
    *,
    owner: str,
    actor: dict[str, Any],
    receipt_id: str,
    reason: str,
) -> dict[str, Any]:
    proposal = await prepare_inventory_receipt_post(
        db,
        owner=owner,
        receipt_id=receipt_id,
    )
    if proposal["state"] == "already_posted":
        return proposal
    facts = proposal["facts"]
    await require_p03_inventory_financial_writes(
        db,
        owner=owner,
        event_at=facts["accounting_at"],
    )

    required = [
        ("supplier", facts["supplier_id"], "payable"),
        ("asset", "inventory", "inventory"),
    ]
    if (
        facts["tax_halalas"] > 0
        and facts["tax_treatment"] == "recoverable_input_vat"
    ):
        required.append(("tax", "input_vat", "input_vat"))
    await read_mz2_write_balances(
        db,
        owner=owner,
        required_accounts=required,
    )

    now = datetime.now(timezone.utc).isoformat()
    await db[P03_EVENTS].insert_one({
        "_id": proposal["event_id"],
        "id": proposal["event_id"],
        "user_id": owner,
        "kind": "inventory_receipt",
        "status": "posting",
        "economic_hash": proposal["economic_hash"],
        "facts": facts,
        "reason": reason,
        "created_at": now,
        "created_by": actor["id"],
    })

    inventory_debit_halalas = facts["net_halalas"]
    if facts["tax_treatment"] == "included_in_inventory_cost":
        inventory_debit_halalas += facts["tax_halalas"]
    entries = [
        {
            "entity_type": "asset",
            "entity_id": "inventory",
            "sub_account": "inventory",
            "side": "debit",
            "amount": format(
                Decimal(inventory_debit_halalas) / Decimal(100),
                ".2f",
            ),
            "entry_type": "inventory_purchase_receipt",
        }
    ]
    if (
        facts["tax_halalas"] > 0
        and facts["tax_treatment"] == "recoverable_input_vat"
    ):
        entries.append({
            "entity_type": "tax",
            "entity_id": "input_vat",
            "sub_account": "input_vat",
            "side": "debit",
            "amount": facts["tax_amount"],
            "entry_type": "inventory_purchase_receipt",
        })
    entries.append({
        "entity_type": "supplier",
        "entity_id": facts["supplier_id"],
        "sub_account": "payable",
        "side": "credit",
        "amount": facts["gross_amount"],
        "entry_type": "inventory_purchase_receipt",
    })

    result = await post_txn_group(
        db,
        user_id=owner,
        actor_id=actor["id"],
        actor_name=actor.get("name") or actor.get("email") or actor["id"],
        txn_type="mz2_inventory_purchase_receipt",
        notes=(
            f"استلام مخزون — {facts['invoice_number']} — "
            f"{facts['supplier_name']} — {facts['product_name']}"
        ),
        metadata={
            "operation_id": OPERATION_ID,
            "source": SOURCE,
            "p03_event_id": proposal["event_id"],
            "p03_kind": "inventory_receipt",
            "inventory_receipt_v2_id": facts["receipt_id"],
            "purchase_invoice_id": facts["purchase_invoice_id"],
            "purchase_invoice_line_id": facts["purchase_invoice_line_id"],
            "supplier_id": facts["supplier_id"],
            "accounting_at": facts["accounting_at"],
            "tax_treatment": facts["tax_treatment"],
            "reason": reason,
        },
        entries=entries,
    )
    entry_ids = [row.get("id") for row in result.get("entries") or [] if row.get("id")]

    changed = await db[INVENTORY_RECEIPTS_V2].update_one(
        {
            "user_id": owner,
            "id": facts["receipt_id"],
            "$or": [
                {"accounting_event_id": {"$exists": False}},
                {"accounting_event_id": None},
                {"accounting_event_id": ""},
            ],
        },
        {"$set": {
            "accounting_event_id": proposal["event_id"],
            "accounting_txn_group_id": result["txn_group_id"],
            "accounting_entry_ids": entry_ids,
            "accounting_source": SOURCE,
            "accounting_net_amount": facts["net_amount"],
            "accounting_tax_amount": facts["tax_amount"],
            "accounting_gross_amount": facts["gross_amount"],
            "accounting_posted_at": now,
            "accounting_posted_by": actor["id"],
            "updated_at": now,
        }},
    )
    if changed.modified_count != 1:
        raise HTTPException(409, "p03_inventory_receipt_concurrent_post")

    invoice = await db[PURCHASE_INVOICES].find_one(
        {"user_id": owner, "id": facts["purchase_invoice_id"]},
        {"_id": 0, "lines": 1, "total_halalas": 1},
    ) or {}
    receipt_events = await db[P03_EVENTS].find(
        {
            "user_id": owner,
            "kind": "inventory_receipt",
            "status": {"$in": ["posting", "posted"]},
            "facts.purchase_invoice_id": facts["purchase_invoice_id"],
        },
        {"_id": 0, "facts": 1},
    ).to_list(10000)
    recognized_halalas = sum(
        int((row.get("facts") or {}).get("gross_halalas") or 0)
        for row in receipt_events
    )
    received_by_line = {}
    for row in receipt_events:
        row_facts = row.get("facts") or {}
        key = _text(row_facts.get("purchase_invoice_line_id"))
        received_by_line[key] = received_by_line.get(key, 0) + int(
            row_facts.get("received_quantity") or 0
        )
    fully_received = bool(invoice.get("lines")) and all(
        received_by_line.get(_text(line.get("id")), 0)
        >= int(line.get("quantity") or 0)
        for line in invoice.get("lines") or []
    )
    await db[PURCHASE_INVOICES].update_one(
        {"user_id": owner, "id": facts["purchase_invoice_id"]},
        {"$set": {
            "status": "fully_received" if fully_received else "partially_received",
            "recognized_payable_halalas": recognized_halalas,
            "recognized_payable": float(
                (Decimal(recognized_halalas) / Decimal(100)).quantize(MONEY)
            ),
            "accounting_authority": SOURCE,
            "legacy_liability_used": False,
            "updated_at": now,
        }},
    )

    await db[P03_EVENTS].update_one(
        {
            "_id": proposal["event_id"],
            "user_id": owner,
            "status": "posting",
        },
        {"$set": {
            "status": "posted",
            "txn_group_id": result["txn_group_id"],
            "entry_ids": entry_ids,
            "posted_at": now,
            "posted_by": actor["id"],
        }},
    )
    await db[P03_AUDIT].insert_one({
        "user_id": owner,
        "action": "inventory_receipt_posted",
        "actor_id": actor["id"],
        "at": now,
        "event_id": proposal["event_id"],
        "receipt_id": facts["receipt_id"],
        "purchase_invoice_id": facts["purchase_invoice_id"],
        "supplier_id": facts["supplier_id"],
        "txn_group_id": result["txn_group_id"],
        "net_amount": facts["net_amount"],
        "tax_amount": facts["tax_amount"],
        "gross_amount": facts["gross_amount"],
        "reason": reason,
    })
    return {
        "state": "posted",
        "event_id": proposal["event_id"],
        "txn_group_id": result["txn_group_id"],
        "facts": facts,
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

    purchase_invoices = await db[PURCHASE_INVOICES].find(
        {
            "user_id": owner,
            "accounting_authority": SOURCE,
            "source": SOURCE,
        },
        {
            "_id": 0,
            "id": 1,
            "supplier_id": 1,
            "supplier_name": 1,
            "invoice_number": 1,
            "invoice_date": 1,
            "due_date": 1,
            "subtotal": 1,
            "tax_amount": 1,
            "total": 1,
            "tax_treatment": 1,
            "status": 1,
            "recognized_payable": 1,
            "recognized_payable_halalas": 1,
            "lines": 1,
            "created_at": 1,
        },
    ).sort([("invoice_date", -1), ("created_at", -1)]).limit(300).to_list(300)

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
        "purchase_invoices": purchase_invoices,
        "inventory_receipts": inventory_receipts,
        "summary": {
            "active_suppliers": len(suppliers),
            "supplier_invoices": len(supplier_invoices),
            "purchase_invoices": len(purchase_invoices),
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

    @router.post(base + "/purchase-invoices")
    async def create_purchase_invoice(
        payload: P03PurchaseInvoiceCreateIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await actor_scope(user, "accounting.purchases.post")

        async def commit(scoped):
            return await create_p03_purchase_invoice(
                scoped,
                owner=owner,
                actor=actor,
                payload=payload,
            )

        return await atomic_owner(db, owner, commit)

    @router.get(base + "/inventory-receipts/{receipt_id}/preview")
    async def inventory_receipt_preview(
        receipt_id: str,
        user: dict = Depends(current_user),
    ):
        _, owner = await actor_scope(user, "accounting.inventory.view")
        return await prepare_inventory_receipt_post(
            db,
            owner=owner,
            receipt_id=receipt_id,
        )

    @router.post(base + "/inventory-receipts/{receipt_id}/post")
    async def inventory_receipt_post(
        receipt_id: str,
        payload: P03PostReasonIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await actor_scope(user, "accounting.purchases.post")

        async def commit(scoped):
            return await post_inventory_receipt(
                scoped,
                owner=owner,
                actor=actor,
                receipt_id=receipt_id,
                reason=payload.reason,
            )

        return await atomic_owner(db, owner, commit)

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
