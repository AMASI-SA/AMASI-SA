"""Decision-only gate for external courier COD at verified delivery.

This does not post money or duplicate the canonical delivery/reconciliation
engine. A courier receivable requires BOTH bound delivered-shipment proof and
a matching collection attestation. Delivery status alone is insufficient.
Carrier fees belong to the existing approved shipping tariff calculation,
not to this COD customer collection decision.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from operational_payment_event_policy import (
    Decision, Evidence, Kind, PaymentEvent, PostingDecision, decide,
)


@dataclass(frozen=True)
class CourierCODDelivery:
    owner_id: str
    order_id: str
    courier_id: str
    shipment_id: str
    delivery_evidence_id: str
    amount: str
    currency: str = "SAR"
    payment_method: str = "cod"
    delivered: bool = False
    verified_carrier_delivery: bool = False
    collection_method: str = "cash"  # cash | bank | pos
    collected_confirmed: bool = False
    collection_evidence_id: str = ""
    accountant_approved: bool = False
    independently_matched: bool = False


def decide_courier_cod_delivery(event: CourierCODDelivery) -> PostingDecision:
    """Return posting eligibility, NOT a financial write authorization."""
    if not all(isinstance(v, str) and v.strip() for v in (
        event.owner_id, event.order_id, event.courier_id,
        event.shipment_id, event.delivery_evidence_id,
    )):
        return PostingDecision(Decision.PENDING_EVIDENCE, "courier_delivery_identity_missing")
    if event.payment_method != "cod":
        return PostingDecision(Decision.REJECT, "not_cod")
    if event.currency != "SAR":
        return PostingDecision(Decision.PENDING_EVIDENCE, "currency_conversion_required")
    try:
        amount = Decimal(event.amount)
    except (TypeError, ValueError, InvalidOperation):
        return PostingDecision(Decision.REJECT, "invalid_amount")
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        return PostingDecision(Decision.REJECT, "invalid_amount")
    if not event.delivered or not event.verified_carrier_delivery:
        return PostingDecision(Decision.PENDING_EVIDENCE, "verified_courier_delivery_missing")
    if event.collection_method not in {"cash", "bank", "pos"}:
        return PostingDecision(Decision.REJECT, "unknown_cod_method")
    if not event.collected_confirmed or not isinstance(event.collection_evidence_id, str) or not event.collection_evidence_id.strip():
        return PostingDecision(Decision.PENDING_EVIDENCE, "courier_collection_unconfirmed")

    if event.collection_method == "cash":
        evidence = Evidence.COURIER_CASH_COLLECTED
        destination = "courier_receivable"
    else:
        if not (event.accountant_approved or event.independently_matched):
            return PostingDecision(Decision.PENDING_ACCOUNTANT, "awaiting_cod_collection_confirmation")
        evidence = (Evidence.ACCOUNTANT_APPROVED if event.accountant_approved
                    else Evidence.MATCHED_BANK_STATEMENT if event.collection_method == "bank"
                    else Evidence.MATCHED_POS_STATEMENT)
        destination = "bank" if event.collection_method == "bank" else "pos_receivable"

    policy = decide(PaymentEvent(
        order_id=event.order_id, provider=event.courier_id,
        amount=event.amount, kind=Kind.COD, evidence=evidence,
        transaction_id=event.collection_evidence_id, currency=event.currency,
        delivered=True, cod_collection_method=event.collection_method,
        statement_match_verified=event.independently_matched,
    ))
    if policy.action != Decision.POST:
        return policy
    # A second status update or duplicated courier collection event cannot
    # post a second initial COD collection for the same order and shipment.
    # The CAS writer MUST additionally reject collisions across shipments and
    # prior manually recorded collections, and must never treat this decision
    # as proof that bank funds have arrived.
    key = "courier_cod_first|" + event.owner_id.strip() + "|" + event.order_id.strip()
    return PostingDecision(Decision.POST, "verified_courier_cod_collection", key,
                           destination)
