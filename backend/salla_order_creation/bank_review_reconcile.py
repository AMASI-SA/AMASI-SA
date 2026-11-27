"""Link a claimed WhatsApp bank receipt to the SAME unpaid Salla order.

Mezan's canonical order engine supplies the post-create actual SAR amount;
the receipt screenshot is only visually plausible. Different amount, status,
order identity, bank payment method or contact stops automated progress.
A human must independently inspect bank credit before paid/fulfillment/GL.

There is NO Salla payment mutation or accounting in this module.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from .bank_claims import BankReviewDenied, claim_storage_id, get_claim_for_order
from .service import intent_storage_id, quote_pricing_mode

ReadMezanOrder = Callable[[str,str], Awaitable[Any]]


class BankReviewSyncDenied(ValueError):
    pass


def _amount(value) -> Decimal:
    if type(value) not in (int,float,str,Decimal):
        raise BankReviewSyncDenied("bank_review_total_invalid")
    try:
        n=Decimal(str(value))
    except (TypeError,ValueError,InvalidOperation) as exc:
        raise BankReviewSyncDenied("bank_review_total_invalid") from exc
    if not n.is_finite() or n<=0 or n.as_tuple().exponent < -2:
        raise BankReviewSyncDenied("bank_review_total_invalid")
    return n


def _method(method) -> str:
    return str(method or "").strip().casefold().replace("-","_")


def _bank_payment_uncollected(order) -> None:
    payment=getattr(order,"payment",None)
    if payment is None:
        raise BankReviewSyncDenied("missing_mezan_bank_payment")
    values=(
        _method(getattr(payment,"method",None)),
        _method(getattr(payment,"method_native",None)),
    )
    if not any(m in ("bank","bank_transfer","تحويل بنكي",
                      "حوالة بنكية") for m in values):
        raise BankReviewSyncDenied("mezan_order_not_bank_transfer")
    if (str(getattr(payment,"status","") or "").lower() in (
            "paid","refunded","cancelled","partially_paid") or
            getattr(payment,"collection_status",None) in ("paid","partial") or
            getattr(payment,"paid_amount",None) is None or
            Decimal(str(getattr(payment,"paid_amount",0))) != 0):
        raise BankReviewSyncDenied("bank_credit_must_remain_unverified")


async def reconcile_bank_review_from_mezan(
    db: Any, *, user_id: str, intent_id: str,
    inbox_id: str, conversation_id: str, contact_id: int,
    load_order: ReadMezanOrder,
) -> dict:
    """Queue an existing bank order for staff review, no money movement."""
    intent=await db.salla_ai_order_intents.find_one({
        "_id":intent_storage_id(user_id,intent_id),"user_id":user_id,
    })
    if (not isinstance(intent,dict) or
            intent.get("state") not in ("created","created_amount_unverified") or
            intent.get("pricing_mode")!="bank_receipt_review" or
            intent.get("inbox_id")!=inbox_id or
            intent.get("conversation_id")!=conversation_id):
        raise BankReviewSyncDenied("confirmed_bank_order_intent_required")
    sid=intent.get("salla_order_id")
    ref=intent.get("reference_id")
    if type(sid) is not int or sid<1:
        raise BankReviewSyncDenied("salla_bank_order_id_missing")
    if ref is None:
        return {"state":"waiting_for_salla_reference","bank_credit_verified":False}
    if type(ref) is not int or ref<1:
        raise BankReviewSyncDenied("salla_bank_order_reference_invalid")
    quote=await db.salla_ai_order_quotes.find_one({
        "user_id":user_id,"intent_id":intent_id,
        "inbox_id":inbox_id,"conversation_id":conversation_id,
    })
    if (quote_pricing_mode(quote)!="bank_receipt_review" or
            quote.get("source_contact_id")!=contact_id or
            quote.get("bank_review_ref")!=intent.get("bank_review_ref")):
        raise BankReviewSyncDenied("bank_receipt_quote_not_verified")
    try:
        claim=await get_claim_for_order(
            db,user_id=user_id,intent_id=intent_id,
            review_ref=quote["bank_review_ref"],inbox_id=inbox_id,
            conversation_id=conversation_id,contact_id=contact_id,
        )
    except (BankReviewDenied,TypeError,ValueError):
        raise BankReviewSyncDenied("bank_claim_not_verified") from None

    try:
        order=await load_order(user_id,str(ref))
    except LookupError:
        return {"state":"waiting_for_mezan_sync","bank_credit_verified":False}
    if order is None:
        return {"state":"waiting_for_mezan_sync","bank_credit_verified":False}
    if (str(getattr(order,"order_id",None))!=str(sid) or
            str(getattr(order,"order_number",None))!=str(ref)):
        raise BankReviewSyncDenied("salla_bank_order_identity_mismatch")
    status=str(getattr(order,"status",None) or "").casefold()
    if status not in (
        "payment_pending","pending_payment","waiting","under_review",
        "payment pending","bank_transfer_pending",
    ):
        raise BankReviewSyncDenied("bank_order_status_not_reviewable")
    _bank_payment_uncollected(order)
    totals=getattr(order,"totals",None)
    if totals is None or getattr(totals,"currency",None)!="SAR":
        raise BankReviewSyncDenied("bank_order_currency_not_sar")
    total=_amount(getattr(totals,"total",None))
    claimed=_amount(claim.get("receipt_amount_sar"))
    matches=abs(total-claimed)<=Decimal("0.01")
    new_state=(
        "awaiting_human_bank_review" if matches else
        "amount_mismatch_review"
    )
    key=claim_storage_id(user_id,intent_id)
    if claim.get("salla_order_id") not in (None,sid):
        raise BankReviewSyncDenied("bank_review_order_already_linked")
    if claim.get("salla_reference_id") not in (None,ref):
        raise BankReviewSyncDenied("bank_review_reference_changed")
    # Existing review documents are idempotent and never overwritten by a
    # different order/amount; this is NOT a payment-credit confirmation.
    changes={
        "state":new_state,"salla_order_id":sid,"salla_reference_id":ref,
        "salla_actual_total_sar":f"{total:.2f}",
        "amount_matches_salla":matches,
        "human_bank_credit_verified":False,
        "payment_marked_paid":False,
        "fulfillment_released":False,
    }
    if claim.get("state")==new_state:
        if all(claim.get(k)==v for k,v in changes.items()):
            return {
                "state":new_state,"salla_order_id":sid,
                "reference_id":ref,"receipt_amount_matches_order":matches,
                "bank_credit_verified":False,"replayed":True,
            }
        raise BankReviewSyncDenied("bank_review_reconciliation_changed")
    updated=await db.salla_ai_bank_receipt_reviews.update_one({
        "_id":key,"user_id":user_id,
        "salla_order_id":None,
        "state":{"$in":["visual_preflight_accepted","pending_mezan_sync"]},
    },{"$set":changes})
    if getattr(updated,"modified_count",0)!=1:
        raise BankReviewSyncDenied("bank_review_concurrent_reconcile")
    return {
        "state":new_state,"salla_order_id":sid,
        "reference_id":ref,"receipt_amount_matches_order":matches,
        "bank_credit_verified":False,"replayed":False,
    }
