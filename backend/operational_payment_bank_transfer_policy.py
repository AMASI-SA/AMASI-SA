"""Pure gate for AMASI's internally verified bank-transfer collection.

A transition to 'in progress' is an operational attestation ONLY when made
by an authorized approver who confirms the bank credit in the workflow.
An external Salla status change, order screenshot or uploaded transfer slip
cannot by itself increase bank balances.

Decision only: no database, money posting, Qoyod or account mutation.
The eventual writer must revalidate actor permission, transition provenance,
bank identity, amount, order and unique key inside owner-scoped CAS.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from operational_payment_event_policy import Decision, PostingDecision


@dataclass(frozen=True)
class BankTransferApproval:
    owner_id: str
    order_id: str
    bank_account_id: str
    amount: str
    currency: str = "SAR"
    payment_method: str = "bank"
    previous_status: str = ""
    new_status: str = ""
    approved_by: str = ""
    approver_authorized: bool = False
    receipt_confirmed: bool = False
    trusted_internal_transition: bool = False
    transition_id: str = ""


def decide_bank_transfer_start(event: BankTransferApproval) -> PostingDecision:
    """Admit one internally attested transition; caller must enforce CAS."""
    if not all(isinstance(value, str) and value.strip() for value in (
        event.owner_id, event.order_id, event.bank_account_id,
        event.approved_by, event.transition_id,
    )):
        return PostingDecision(Decision.PENDING_EVIDENCE, "bank_approval_identity_missing")
    if event.payment_method != "bank":
        return PostingDecision(Decision.REJECT, "not_bank_transfer")
    if event.currency != "SAR":
        return PostingDecision(Decision.PENDING_EVIDENCE, "currency_conversion_required")
    try:
        amount = Decimal(event.amount)
    except (InvalidOperation, TypeError, ValueError):
        return PostingDecision(Decision.REJECT, "invalid_amount")
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        return PostingDecision(Decision.REJECT, "invalid_amount")
    if (event.previous_status not in {"awaiting_review", "pending_review"}
            or event.new_status != "in_progress"):
        return PostingDecision(Decision.PENDING_EVIDENCE, "verified_start_transition_missing")
    if not event.trusted_internal_transition or not event.approver_authorized:
        return PostingDecision(Decision.PENDING_EVIDENCE, "internal_approval_unverified")
    if not event.receipt_confirmed:
        return PostingDecision(Decision.PENDING_EVIDENCE, "bank_credit_not_attested")
    # Order-scoped key prevents two different approval transitions posting
    # the same order a second time; partial/extra payments require review.
    key = "bank_transfer_first|" + event.owner_id.strip() + "|" + event.order_id.strip()
    return PostingDecision(Decision.POST, "verified_internal_bank_receipt", key,
                           event.bank_account_id.strip())
