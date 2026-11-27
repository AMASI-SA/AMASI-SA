"""Visual bank-transfer receipt PRE-SCREEN: not proof funds were received.

The WhatsApp customer may report a completed transfer and send a screenshot.
A PRIVATELY APPROVED vision backend can assess whether the screenshot LOOKS
like a legible bank transfer receipt and extract fields. Mezan independently
checks the expected merchant bank ID/IBAN suffix and current amount details.

The screenshot NEVER proves a transaction settled. No model can set paid,
change balance, trigger fulfillment, or post GL/Qoyod. The final decision is
human customer service + verified incoming bank transaction.

This is a pure, no-network validation module; no OpenAI integration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal


HEX_SHA = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
IBAN_LAST4 = re.compile(r"^[A-Za-z0-9]{4}$", re.ASCII)
_MAX_AGE = timedelta(days=30)
_ALLOWED_FLAGS = frozenset({
    "cropped", "low_resolution", "recipient_unreadable",
    "amount_unreadable", "date_unreadable", "possible_edit",
    "duplicate_appearance", "unknown_bank",
})


class ReceiptAppearanceDenied(ValueError):
    pass


@dataclass(frozen=True)
class MerchantBankFact:
    """From Mezan's authorized Salla/bank settings, never from the LLM."""
    user_id: str
    store_bank_id: int
    iban_last4: str
    active: bool
    source: Literal["salla_active_merchant_bank"]


@dataclass(frozen=True)
class ReceiptVisionEvidence:
    """Structured assessment by a separately approved private vision service.

    Not a visual proof of an actual bank credit.
    """
    source: Literal["approved_private_receipt_vision_v1"]
    looks_like_bank_transfer_receipt: bool
    text_legible: bool
    amount_sar: str
    currency: str
    beneficiary_bank_id: int
    beneficiary_iban_last4: str
    transfer_at_utc: datetime
    source_image_sha256: str
    bank_reference_sha256: str
    flags: tuple[str, ...] = ()


def receipt_amount(value: str) -> Decimal:
    if type(value) is not str or not 0 < len(value) <= 24:
        raise ReceiptAppearanceDenied("receipt_amount_not_readable")
    try:
        amount = Decimal(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise ReceiptAppearanceDenied("receipt_amount_not_readable") from exc
    if (not amount.is_finite() or amount <= 0 or
            amount.as_tuple().exponent < -2 or amount > Decimal("5000000")):
        raise ReceiptAppearanceDenied("receipt_amount_invalid")
    return amount


def validate_receipt_appearance(
    evidence: ReceiptVisionEvidence,
    *,
    merchant_id: str,
    verified_bank: MerchantBankFact,
    now: datetime | None = None,
) -> dict:
    """Plausibility only. No paid/credit/settled status is ever returned."""
    instant = now or datetime.now(timezone.utc)
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise ReceiptAppearanceDenied("trusted_utc_clock_required")
    instant = instant.astimezone(timezone.utc)
    if not isinstance(evidence, ReceiptVisionEvidence):
        raise ReceiptAppearanceDenied("approved_visual_evidence_required")
    if (type(merchant_id) is not str or not merchant_id or
            not isinstance(verified_bank, MerchantBankFact) or
            verified_bank.user_id != merchant_id or
            verified_bank.source != "salla_active_merchant_bank" or
            verified_bank.active is not True or
            type(verified_bank.store_bank_id) is not int or
            verified_bank.store_bank_id <= 0):
        raise ReceiptAppearanceDenied("verified_receiving_bank_required")
    if evidence.source != "approved_private_receipt_vision_v1":
        raise ReceiptAppearanceDenied("unapproved_vision_source")
    if (evidence.looks_like_bank_transfer_receipt is not True or
            evidence.text_legible is not True):
        raise ReceiptAppearanceDenied("receipt_not_visually_clear")
    if evidence.currency != "SAR":
        raise ReceiptAppearanceDenied("receipt_currency_not_sar")
    if (type(evidence.beneficiary_bank_id) is not int or
            evidence.beneficiary_bank_id != verified_bank.store_bank_id):
        raise ReceiptAppearanceDenied("recipient_bank_does_not_match_store")
    if (type(verified_bank.iban_last4) is not str or
            not IBAN_LAST4.fullmatch(verified_bank.iban_last4) or
            type(evidence.beneficiary_iban_last4) is not str or
            evidence.beneficiary_iban_last4.upper() != verified_bank.iban_last4.upper()):
        raise ReceiptAppearanceDenied("recipient_iban_does_not_match_store")
    if (type(evidence.source_image_sha256) is not str or
            not HEX_SHA.fullmatch(evidence.source_image_sha256) or
            type(evidence.bank_reference_sha256) is not str or
            not HEX_SHA.fullmatch(evidence.bank_reference_sha256)):
        raise ReceiptAppearanceDenied("receipt_image_or_reference_unverified")
    when=evidence.transfer_at_utc
    if (not isinstance(when, datetime) or when.tzinfo is None or
            when.astimezone(timezone.utc) > instant + timedelta(minutes=5) or
            instant - when.astimezone(timezone.utc) > _MAX_AGE):
        raise ReceiptAppearanceDenied("receipt_date_outside_allowed_window")
    if (type(evidence.flags) is not tuple or len(evidence.flags)>12 or
            any(type(x) is not str or x not in _ALLOWED_FLAGS
                for x in evidence.flags) or len(evidence.flags)>0):
        raise ReceiptAppearanceDenied("receipt_requires_clearer_image_or_staff")
    amount=receipt_amount(evidence.amount_sar)
    return {
        "appearance": "plausible_for_staff_review",
        "bank_credit_verified": False,
        "eligible_to_mark_paid": False,
        "eligible_for_fulfillment": False,
        "human_bank_statement_check_required": True,
        "source_image_sha256": evidence.source_image_sha256,
        "bank_reference_sha256": evidence.bank_reference_sha256,
        "store_bank_id": verified_bank.store_bank_id,
        "receipt_amount_sar": f"{amount:.2f}",
        "receipt_transfer_at_utc": when.astimezone(timezone.utc),
    }
