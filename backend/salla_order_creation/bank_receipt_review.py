"""Conservative bank-transfer receipt appearance review, NOT payment proof.

A vision agent can suggest *visible* fields, but cannot verify bank settlement.
The trusted Mezan service compares candidate values against an active Salla
store bank and later against the actual Salla order amount. A screenshot is
never accepted as ledger money, bank credit, paid order, or fulfillment proof.

Do not store customer receipt image or raw IBAN/reference in the AI draft.
The original attachment stays with the private Chatwoot conversation, and
authorized staff decide final settlement using actual bank account records.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReceiptAppearanceDenied(ValueError):
    pass


class ReceiptVisibleFields(BaseModel):
    """Untrusted OCR/vision assertions: display ONLY as preliminary evidence."""
    model_config = ConfigDict(extra="forbid", strict=True)

    readable: bool
    bank_transfer_shown: bool
    appears_edited_or_cropped: bool
    payer_bank_name_visible: bool
    recipient_bank_name: str | None = Field(default=None, max_length=120)
    recipient_account_name: str | None = Field(default=None, max_length=160)
    recipient_iban: str | None = Field(default=None, max_length=34)
    amount: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, max_length=4)
    transfer_reference: str | None = Field(default=None, max_length=100)
    timestamp: datetime | None = None


def _normalize_text(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    plain = unicodedata.normalize("NFKC", value)
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return " ".join(plain.casefold().strip().split())


def _iban(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    iban = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"SA[0-9]{2}[0-9A-Z]{18}", iban):
        return ""
    # ISO 13616 / Saudi IBAN check digit validation.
    shifted = iban[4:] + iban[:4]
    converted = "".join(
        str(ord(c) - 55) if "A" <= c <= "Z" else c
        for c in shifted
    )
    return iban if int(converted) % 97 == 1 else ""


def _money(value: str | None) -> Decimal | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{1,8}(?:\.[0-9]{1,2})?", value
    ):
        return None
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def inspect_visible_bank_receipt(
    visible: ReceiptVisibleFields,
    *,
    salla_bank: dict,
    checked_at: datetime,
    now: datetime | None = None,
    actual_order_total_sar: str | None = None,
) -> dict:
    """Deterministic quality check; output NEVER says bank balance confirmed.

    Use this only after a separately verified customer attachment message and
    genuine active Salla bank details. If the actual Salla order does not yet
    exist, amount comparison is postponed; the record remains review-only.
    """
    moment = now or datetime.now(timezone.utc)
    if (not isinstance(visible, ReceiptVisibleFields)
            or not isinstance(salla_bank, dict)
            or checked_at.tzinfo is None or moment.tzinfo is None
            or not timedelta(0) <= moment - checked_at <= timedelta(minutes=10)):
        raise ReceiptAppearanceDenied("fresh_authorized_bank_source_required")
    if (salla_bank.get("source") != "salla_store_payment_banks"
            or salla_bank.get("status") != "active"
            or type(salla_bank.get("bank_id")) is not int
            or salla_bank["bank_id"] <= 0):
        raise ReceiptAppearanceDenied("active_store_bank_account_required")

    flags: list[str] = []
    if not visible.readable or not visible.bank_transfer_shown:
        flags.append("receipt_not_legible_as_bank_transfer")
    if visible.appears_edited_or_cropped:
        flags.append("receipt_suspicious_or_incomplete")
    if not visible.payer_bank_name_visible:
        flags.append("source_bank_not_visible")
    beneficiary = _normalize_text(visible.recipient_account_name)
    account = _normalize_text(salla_bank.get("account_name"))
    if not beneficiary or not account or beneficiary != account:
        flags.append("recipient_name_mismatch")
    expected_iban = _iban(salla_bank.get("iban"))
    observed_iban = _iban(visible.recipient_iban)
    if not expected_iban or not observed_iban or observed_iban != expected_iban:
        flags.append("recipient_iban_unverified")
    amount = _money(visible.amount)
    if amount is None or (visible.currency or "").upper() != "SAR":
        flags.append("receipt_amount_or_currency_unverified")
    reference = visible.transfer_reference
    if (not isinstance(reference, str) or not re.fullmatch(
        r"[A-Za-z0-9/_-]{6,100}", reference
    )):
        flags.append("transaction_reference_missing")
    when = visible.timestamp
    if (when is None or when.tzinfo is None
            or when.astimezone(timezone.utc) > moment
            or moment - when.astimezone(timezone.utc) > timedelta(days=30)):
        flags.append("transfer_time_unverified")
    if actual_order_total_sar is not None:
        actual = _money(actual_order_total_sar)
        if actual is None or amount is None or abs(actual - amount) > Decimal("0.01"):
            flags.append("receipt_amount_differs_from_salla_order")

    return {
        "state": "appearance_plausible" if not flags else "needs_customer_clarification",
        "flags": sorted(set(flags)),
        "bank_id": salla_bank["bank_id"],
        "receipt_amount_sar": f"{amount:.2f}" if amount is not None else None,
        "bank_credit_confirmed": False,
        "paid": False,
        "may_mark_paid": False,
        "requires_staff_review": True,
        "may_begin_fulfillment": False,
    }


def bank_pending_payload(order: Any) -> dict:
    """Explicit Salla bank-transfer method with strictly UNPAID status."""
    from .payload import PendingOrder, build_pending_order_payload
    if not isinstance(order, PendingOrder) or order.accepted_methods != ["bank"]:
        raise ReceiptAppearanceDenied("bank_only_pending_order_required")
    result = build_pending_order_payload(order)
    result["payment"] = {
        "status": "pending_payment",
        "method": "bank",
        "accepted_methods": ["bank"],
    }
    # Never transmit model-provided receipt URLs or request paid=true.
    return result
