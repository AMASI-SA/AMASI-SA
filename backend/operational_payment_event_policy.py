"""Pure posting-decision policy for Mezan operational balances.

No database, external API or accounting-general-ledger writes. A confirmed
decision is NOT a posting: callers MUST enforce transactional idempotency,
persist provenance and use the authoritative operational balance ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional


class Decision(str, Enum):
    POST = "post"
    PENDING_ACCOUNTANT = "pending_accountant"
    PENDING_EVIDENCE = "pending_evidence"
    REJECT = "reject"


class Kind(str, Enum):
    PAYMENT = "payment"
    REFUND = "refund"
    COD = "cod"


class Evidence(str, Enum):
    PROVIDER_SUCCESS = "provider_success"
    MATCHED_PROVIDER_STATEMENT = "matched_provider_statement"
    MATCHED_BANK_STATEMENT = "matched_bank_statement"
    ACCOUNTANT_APPROVED = "accountant_approved"
    COURIER_CASH_COLLECTED = "courier_cash_collected"
    ORDER_STATUS_ONLY = "order_status_only"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class PaymentEvent:
    order_id: str
    provider: str
    amount: str
    kind: Kind
    evidence: Evidence
    transaction_id: Optional[str] = None
    installment_id: Optional[str] = None
    refund_id: Optional[str] = None
    currency: str = "SAR"
    delivered: bool = False
    cod_collection_method: Optional[str] = None  # cash | bank | pos
    statement_match_verified: bool = False


@dataclass(frozen=True)
class PostingDecision:
    action: Decision
    reason: str
    idempotency_key: Optional[str] = None
    balance_bucket: Optional[str] = None


def decide(event: PaymentEvent) -> PostingDecision:
    """Validate the event; never infer successful payment from order status."""
    if not event.order_id.strip() or not event.provider.strip():
        return PostingDecision(Decision.REJECT, "missing_order_or_provider")
    try:
        amount = Decimal(event.amount)
    except (InvalidOperation, TypeError, ValueError):
        return PostingDecision(Decision.REJECT, "invalid_amount")
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        return PostingDecision(Decision.REJECT, "invalid_amount")
    if event.currency != "SAR":
        return PostingDecision(Decision.PENDING_EVIDENCE, "currency_conversion_required")

    method = (event.cod_collection_method or "").lower().strip()
    if event.kind == Kind.COD:
        if method not in {"cash", "bank", "pos"}:
            return PostingDecision(Decision.REJECT, "unknown_cod_method")
        if not event.delivered:
            return PostingDecision(Decision.PENDING_EVIDENCE, "not_delivered")
        if method == "cash":
            authorized = event.evidence == Evidence.COURIER_CASH_COLLECTED
            bucket = "courier_receivable"
        else:
            authorized = event.evidence == Evidence.ACCOUNTANT_APPROVED or (
                event.evidence == Evidence.MATCHED_BANK_STATEMENT and event.statement_match_verified
            )
            bucket = "bank" if method == "bank" else "pos_receivable"
            if not authorized:
                return PostingDecision(Decision.PENDING_ACCOUNTANT, "awaiting_cod_collection_confirmation")
    elif event.kind == Kind.REFUND:
        authorized = event.evidence == Evidence.ACCOUNTANT_APPROVED or (
            event.evidence == Evidence.MATCHED_PROVIDER_STATEMENT and event.statement_match_verified
        )
        bucket = event.provider
        if not authorized:
            return PostingDecision(Decision.PENDING_ACCOUNTANT, "refund_needs_confirmation")
    elif event.kind == Kind.PAYMENT:
        authorized = (
            event.evidence == Evidence.PROVIDER_SUCCESS
            or (event.evidence == Evidence.MATCHED_PROVIDER_STATEMENT and event.statement_match_verified)
            or (event.evidence == Evidence.MATCHED_BANK_STATEMENT and event.statement_match_verified)
            or event.evidence == Evidence.ACCOUNTANT_APPROVED
        )
        bucket = event.provider
        if not authorized:
            return PostingDecision(Decision.PENDING_EVIDENCE, "payment_not_verified")
    else:
        return PostingDecision(Decision.REJECT, "unknown_kind")

    if not authorized:
        return PostingDecision(Decision.PENDING_EVIDENCE, "missing_valid_collection_evidence")

    # A stable transaction/refund/installment identifier is mandatory. Never
    # dedupe by order + amount: one order can have several equal installments.
    identifier = event.refund_id if event.kind == Kind.REFUND else (
        event.transaction_id or event.installment_id
    )
    if not identifier or not identifier.strip():
        return PostingDecision(Decision.PENDING_EVIDENCE, "missing_stable_financial_identifier")
    # A provider transaction belongs to ONE order. Do not scope the unique key
    # by order_id: that would allow the same external payment to credit two
    # different orders. The atomic posting layer must also compare the stored
    # owner/order/amount/currency against this incoming observation and flag
    # any mismatch as a conflict rather than replaying silently.
    key = "|".join((event.kind.value, event.provider.strip().lower(), identifier.strip()))
    return PostingDecision(Decision.POST, "verified", key, bucket)
