"""Read-only verification that a Salla BANK order awaits staff review.

A screenshot cannot establish bank settlement. This checks the SAME Salla ID
and human reference against Mezan's existing synced order and original
Chatwoot receipt evidence. No Salla status updates, no paid transition,
no cash/Qoyod/GL posting and no fulfillment authorization.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from .bank_staff_review import assess_bank_order_total
from .service import intent_storage_id

ReadOrder = Callable[[str, str], Awaitable[Any]]
# Real Amasi Salla status listing (read-only, 2026-11-27) confirms that
# "تم المراجعة" and other merchant statuses share under_review slug.
# The bank workflow must read back the exact merchant review *name*, never
# treat a generic slug as proof of the target custom status.
REVIEW_CUSTOM_NAME = "بإنتظار المراجعة"


def _text(value):
    return str(value or "").strip().casefold().replace("_", " ")


def _not_ready(state: str) -> dict:
    return {"state": state, "staff_review_required": True,
            "paid": False, "bank_credit_confirmed": False,
            "fulfillment_eligible": False}


async def read_synced_bank_review(
    db: Any, *,
    user_id: str, intent_id: str,
    load_order: ReadOrder,
    expected_review_customized_id: int | None = None,
) -> dict:
    if not user_id or not intent_id:
        return _not_ready("authenticated_scope_missing")
    intent = await db.salla_ai_order_intents.find_one({
        "_id": intent_storage_id(user_id,intent_id),
        "user_id": user_id, "intent_id": intent_id,
    })
    if (not isinstance(intent,dict) or
            intent.get("pricing_mode") != "salla_bank_review" or
            intent.get("state") not in (
                "created_bank_review_pending", "created_bank_review_unqueued",
            )):
        return _not_ready("bank_salla_creation_not_confirmed")
    order_id=intent.get("salla_order_id")
    reference=intent.get("reference_id")
    if type(order_id) is not int or order_id<1:
        return _not_ready("salla_order_id_missing")
    if type(reference) is not int or reference<1:
        return _not_ready("waiting_for_salla_reference")

    case = await db.salla_ai_bank_staff_reviews.find_one({
        "user_id":user_id,"intent_id":intent_id,
        "salla_order_id":order_id,
    })
    if not isinstance(case,dict):
        return _not_ready("staff_review_case_missing")
    if (case.get("review_state")!="awaiting_staff_bank_verification"
            or case.get("bank_credit_confirmed") is not False):
        return _not_ready("review_not_pending_or_already_resolved")

    try:
        order=await load_order(user_id,str(reference))
    except LookupError:
        return _not_ready("waiting_for_mezan_sync")
    if order is None:
        return _not_ready("waiting_for_mezan_sync")
    if (str(getattr(order,"order_id", ""))!=str(order_id)
            or str(getattr(order,"order_number", ""))!=str(reference)):
        return _not_ready("synced_bank_order_id_mismatch")
    method=_text(getattr(getattr(order,"payment",None),"method",None))
    native_method=_text(getattr(getattr(order,"payment",None),"method_native",None))
    if method!="bank" and native_method!="bank":
        return _not_ready("salla_payment_method_not_bank")

    payment=getattr(order,"payment",None)
    totals=getattr(order,"totals",None)
    if not payment or not totals:
        return _not_ready("bank_order_details_incomplete")
    if (_text(getattr(payment,"status",None)) in ("paid","refunded") or
            _text(getattr(payment,"collection_status",None)) in (
                "paid","partial"
            ) or getattr(payment,"paid_amount",None) != 0):
        return _not_ready("payment_not_pending_bank_review")
    # The top-level Salla status.name may be canonical "بإنتظار المراجعة"
    # for MANY custom statuses, including processed "تم المراجعة".
    # Resolve the actual active target ID from Salla merchant status metadata
    # and require the synced customized child ID + name and review slug.
    # Missing source identity is UNVERIFIED, never an approved bank state.
    status=_text(getattr(order,"status",None))
    custom_id=getattr(order,"status_customized_id",None)
    custom_name=_text(getattr(order,"status_customized_name",None))
    pending=(
        type(expected_review_customized_id) is int
        and expected_review_customized_id > 0
        and type(custom_id) is int
        and custom_id == expected_review_customized_id
        and custom_name == _text(REVIEW_CUSTOM_NAME)
        and status == "under review"
    )
    total=getattr(totals,"total",None)
    currency=str(getattr(totals,"currency","")).upper()
    assessment=assess_bank_order_total(
        case,actual_total_sar=total,actual_currency=currency,
        actual_method="bank",is_pending_review=pending,
    )
    return {
        **assessment,
        "salla_order_id":order_id,
        "reference_id":reference,
        "receipt_message_id":case["source_message_id"],
        "receipt_attachment_id":case["source_attachment_id"],
        "receipt_source_kind":case.get("source_kind", "image"),
        "customer_text_claim_only":case.get("source_kind") == "text",
        "review_owner":"customer_service",
        "checkout_url":None,
    }
