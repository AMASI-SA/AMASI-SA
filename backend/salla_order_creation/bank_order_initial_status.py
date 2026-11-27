"""Immediately put newly created UNPAID bank orders under human REVIEW.

The owner requires Salla under_review even before the customer sends a
receipt. Only an independently confirmed Salla order POST with a bank-only
Mezan quote authorizes this order-status transition. NEVER mark paid,
accept a receipt as bank settlement, post GL or allow fulfillment.

The transition is protected by the SAME idempotent delivery fence as the
later receipt-review status path, so an uncertain outcome never causes a
second status POST. No current production paths invoke this by default.
"""

from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable

from .bank_review_status import BankStatusDenied
from .service import intent_storage_id, quote_pricing_mode


async def put_new_bank_order_under_review(
    db: Any, *,
    owner_id: str,
    intent_id: str,
    get_salla: Callable[..., Awaitable[dict]],
    set_salla_status: Callable[..., Awaitable[dict]],
    enabled: bool = False,
) -> dict:
    if not enabled:
        raise BankStatusDenied("bank_review_transition_disabled")
    if not owner_id or not intent_id:
        raise BankStatusDenied("trusted_bank_order_scope_required")
    intent=await db.salla_ai_order_intents.find_one({
        "_id":intent_storage_id(owner_id,intent_id),
        "user_id":owner_id,
    })
    if (not isinstance(intent,dict) or intent.get("pricing_mode")!="salla_bank_review"
            or intent.get("state") not in ("created","created_amount_unverified")
            or type(intent.get("salla_order_id")) is not int
            or intent["salla_order_id"]<=0):
        raise BankStatusDenied("confirmed_bank_order_required")
    quote=await db.salla_ai_order_quotes.find_one({
        "user_id":owner_id,"intent_id":intent_id,
        "inbox_id":intent.get("inbox_id"),
        "conversation_id":intent.get("conversation_id"),
    })
    if (quote_pricing_mode(quote)!="salla_bank_review" or
            quote.get("payload_digest")!=intent.get("payload_digest")):
        raise BankStatusDenied("verified_bank_only_quote_required")
    order_id=intent["salla_order_id"]
    try:
        response=await get_salla(db,owner_id,"GET",f"/orders/{order_id}")
    except Exception:
        return {"state":"awaiting_salla_bank_order_readback","paid":False,
                "order_id":order_id}
    row=response.get("data") if isinstance(response,dict) else None
    if (not isinstance(row,dict) or response.get("success") is False or
            str(row.get("id"))!=str(order_id)):
        raise BankStatusDenied("salla_bank_order_identity_unverified")
    method=row.get("payment_method")
    if method is not None and str(method).strip().lower() not in (
        "bank","bank_transfer",
    ):
        raise BankStatusDenied("salla_order_not_bank_transfer")
    allowed=row.get("accepted_payment_methods")
    if (method is None and
            isinstance(allowed,list) and "bank" not in allowed):
        raise BankStatusDenied("salla_order_bank_method_unverified")
    # If source says paid, this is not an unpaid bank order to hold for review.
    if row.get("payment_status") in ("paid","partially_paid"):
        raise BankStatusDenied("salla_order_already_paid")
    actions=row.get("payment_actions")
    if isinstance(actions,dict):
        refund=actions.get("refund_action")
        if isinstance(refund,dict):
            amount=refund.get("paid_amount")
            if (isinstance(amount,dict) and
                    amount.get("amount") not in (None,0,0.0,"0","0.00")):
                raise BankStatusDenied("salla_order_has_paid_amount")

    status=row.get("status")
    if ((isinstance(status,dict) and status.get("slug")=="under_review")
            or status=="under_review"):
        return {"state":"under_review","paid":False,"order_id":order_id,
                "replayed":True}

    fence=hashlib.sha256(
        f"assisted-bank-status:{owner_id}:{order_id}".encode()
    ).hexdigest()
    try:
        await db.salla_ai_bank_status_deliveries.insert_one({
            "_id":fence,"user_id":owner_id,"intent_id":intent_id,
            "salla_order_id":order_id,
            "review_id":"bank_review_at_create",
            "state":"sending",
        })
    except Exception:
        prev=await db.salla_ai_bank_status_deliveries.find_one({"_id":fence})
        if (not isinstance(prev,dict) or
                prev.get("user_id")!=owner_id or
                prev.get("intent_id")!=intent_id or
                prev.get("salla_order_id")!=order_id):
            raise BankStatusDenied("conflicting_bank_status_delivery")
        # Never repeat an uncertain bank status POST even on webhook replay.
        return {"state":"status_attempt_already_recorded","paid":False,
                "order_id":order_id,"replayed":True}
    try:
        ack=await set_salla_status(
            db,owner_id,"POST",f"/orders/{order_id}/status",
            json={"slug":"under_review","send_status_sms":False},
        )
        if not isinstance(ack,dict) or ack.get("success") is False:
            raise ValueError("Salla status not confirmed")
    except Exception:
        await db.salla_ai_bank_status_deliveries.update_one(
            {"_id":fence,"state":"sending"},
            {"$set":{"state":"unknown"}},
        )
        return {"state":"unknown_status_transition","paid":False,
                "order_id":order_id,"replayed":False}
    await db.salla_ai_bank_status_deliveries.update_one(
        {"_id":fence,"state":"sending"},
        {"$set":{"state":"provider_acknowledged"}},
    )
    return {"state":"awaiting_status_readback","paid":False,
            "order_id":order_id,"replayed":False}
