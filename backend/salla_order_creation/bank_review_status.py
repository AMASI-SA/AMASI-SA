"""Move an UNPAID, bank-only Salla order to merchant UNDER REVIEW.

Salla exposes POST /orders/{id}/status with slug 'under_review'. This is an
order-lifecycle status, NOT a payment confirmation. Never call
PUT /orders/{id} payment.status='paid', never post GL/Qoyod.

Only after a trusted private Chatwoot bank receipt was placed in the
human-review queue, and only once per Salla order, may the approved Mezan
service attempt the status transition. Unknown POST outcomes are quarantined
for GET-only reconciliation; no blind second status POST.
"""

from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable

from .service import intent_storage_id


class BankStatusDenied(ValueError):
    pass


async def ensure_bank_order_under_review(
    db: Any, *,
    owner_id: str, intent_id: str, review_id: str,
    get_salla: Callable[..., Awaitable[dict]],
    set_salla_status: Callable[..., Awaitable[dict]],
    enabled: bool = False,
) -> dict:
    if not enabled:
        raise BankStatusDenied("bank_review_transition_disabled")
    if not owner_id or not intent_id or not review_id:
        raise BankStatusDenied("trusted_bank_review_scope_required")

    intent = await db.salla_ai_order_intents.find_one({
        "_id": intent_storage_id(owner_id, intent_id),
        "user_id": owner_id,
    })
    if (not isinstance(intent, dict) or intent.get("pricing_mode") != "salla_bank_review"
            or intent.get("state") not in ("created","created_amount_unverified")
            or type(intent.get("salla_order_id")) is not int
            or intent["salla_order_id"] <= 0):
        raise BankStatusDenied("confirmed_unpaid_bank_order_required")
    receipt = await db.salla_ai_bank_receipt_reviews.find_one({
        "_id": review_id,
        "user_id": owner_id, "intent_id": intent_id,
        "salla_order_id": intent["salla_order_id"],
    })
    if (not isinstance(receipt, dict) or
            receipt.get("status") != "awaiting_human_bank_confirmation" or
            receipt.get("bank_settlement_confirmed") is not False or
            receipt.get("salla_paid") is not False):
        raise BankStatusDenied("pending_human_review_evidence_required")

    order_id=intent["salla_order_id"]
    def _check(data):
        if not isinstance(data,dict) or data.get("success") is False:
            raise BankStatusDenied("salla_order_read_unavailable")
        row=data.get("data")
        if not isinstance(row,dict) or str(row.get("id"))!=str(order_id):
            raise BankStatusDenied("salla_order_identity_mismatch")
        method=str(row.get("payment_method") or "").lower()
        allowed=row.get("accepted_payment_methods")
        if method and method not in ("bank", "bank_transfer"):
            # A genuine paid card/COD order may never be stolen by bank review.
            raise BankStatusDenied("salla_payment_method_not_bank")
        if allowed and isinstance(allowed,list) and "bank" not in allowed and not method:
            raise BankStatusDenied("salla_bank_payment_not_allowed")
        # Bank transfer must remain UNPAID; no payment confirmation here.
        actions=row.get("payment_actions") or {}
        refund=(actions.get("refund_action") or {}) if isinstance(actions,dict) else {}
        paid=refund.get("paid_amount") or {}
        if (isinstance(paid,dict) and
                paid.get("amount") not in (None,0,0.0,"0","0.00")):
            raise BankStatusDenied("salla_order_already_has_paid_amount")
        return row
    try:
        initial=_check(await get_salla(
            db,owner_id,"GET",f"/orders/{order_id}",
        ))
    except BankStatusDenied:
        raise
    except Exception as exc:
        raise BankStatusDenied("salla_order_read_unavailable") from exc
    if (initial.get("status") or {}).get("slug")=="under_review":
        return {
            "state":"under_review","order_id":order_id,
            "review_id":review_id,"paid":False,"replayed":True,
        }

    key=hashlib.sha256(
        f"assisted-bank-status:{owner_id}:{order_id}".encode()
    ).hexdigest()
    try:
        await db.salla_ai_bank_status_deliveries.insert_one({
            "_id":key,"user_id":owner_id,"intent_id":intent_id,
            "salla_order_id":order_id,"review_id":review_id,
            "state":"sending",
        })
    except Exception:
        prev=await db.salla_ai_bank_status_deliveries.find_one({"_id":key})
        if (not isinstance(prev,dict) or prev.get("user_id")!=owner_id
                or prev.get("review_id")!=review_id):
            raise BankStatusDenied("bank_status_delivery_conflict")
        # Unknown result must be independently reconciled by a GET before
        # calling this again; there is NEVER a second status POST.
        return {
            "state":"unknown_status_transition",
            "order_id":order_id,"review_id":review_id,"paid":False,
            "replayed":True,
        }
    try:
        response=await set_salla_status(
            db,owner_id,"POST",f"/orders/{order_id}/status",
            {"slug":"under_review","send_status_sms":False},
        )
        if (not isinstance(response,dict) or response.get("success") is False):
            raise ValueError("untrusted status POST response")
    except Exception:
        await db.salla_ai_bank_status_deliveries.update_one(
            {"_id":key,"state":"sending"},
            {"$set":{"state":"unknown"}},
        )
        return {
            "state":"unknown_status_transition",
            "order_id":order_id,"review_id":review_id,"paid":False,
            "replayed":False,
        }
    await db.salla_ai_bank_status_deliveries.update_one(
        {"_id":key,"state":"sending"},
        {"$set":{"state":"provider_acknowledged"}},
    )
    # Status POST success is not proof that Salla order has already synced.
    return {
        "state":"awaiting_status_readback",
        "order_id":order_id,"review_id":review_id,"paid":False,
        "replayed":False,
    }
