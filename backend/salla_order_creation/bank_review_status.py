"""Set a verified Salla BANK order status to under_review, without paid.

This is a separate idempotent status action for an already-created unpaid
bank-transfer order with a plausible receipt and Mezan staff-review record.
Uses official Salla GET /orders/{id} then POST /orders/{id}/status with
{"slug":"under_review"}. It NEVER modifies payment.status, uploads a receipt
as "paid", posts GL/Qoyod or triggers order fulfillment.

Unknown status-write outcome is quarantined; later read-only GET may prove
it took effect, otherwise staff must reconcile. No blind status POST replay.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .bank_claims import claim_storage_id

CallSalla = Callable[..., Awaitable[dict]]


class BankReviewStatusDenied(ValueError):
    pass


def _provider_row(response: Any, expected_id: int) -> dict:
    if not isinstance(response,dict) or response.get("success") is not True:
        raise BankReviewStatusDenied("salla_bank_order_not_readable")
    row=response.get("data")
    if not isinstance(row,dict) or str(row.get("id"))!=str(expected_id):
        raise BankReviewStatusDenied("salla_order_identity_not_verified")
    method=row.get("payment_method")
    if isinstance(method,dict):
        method=method.get("slug") or method.get("code")
    if str(method or "").strip().casefold() not in (
        "bank","bank_transfer","تحويل بنكي",
    ):
        raise BankReviewStatusDenied("salla_order_not_bank")
    payment=row.get("payment")
    if isinstance(payment,dict) and str(
        payment.get("status") or ""
    ).lower() in ("paid","refunded","partially_paid"):
        raise BankReviewStatusDenied("salla_bank_payment_not_unpaid")
    status=row.get("status")
    if isinstance(status,dict):
        status=status.get("slug")
    normalized=str(status or "").strip().lower().replace("-","_")
    if normalized not in ("payment_pending","pending_payment","under_review"):
        raise BankReviewStatusDenied("salla_order_status_unexpected")
    return {"status":normalized}


async def ensure_salla_bank_order_under_review(
    db: Any, *,
    owner_id: str, intent_id: str,
    get_order: CallSalla, post_status: CallSalla,
) -> dict:
    if not owner_id or not callable(get_order) or not callable(post_status):
        raise BankReviewStatusDenied("trusted_bank_review_gateway_required")
    key=claim_storage_id(owner_id,intent_id)
    claim=await db.salla_ai_bank_receipt_reviews.find_one({
        "_id":key,"user_id":owner_id,
    })
    if (not isinstance(claim,dict) or claim.get("state") not in (
            "awaiting_human_bank_review","amount_mismatch_review") or
            claim.get("human_bank_credit_verified") is not False or
            claim.get("payment_marked_paid") is not False or
            claim.get("fulfillment_released") is not False):
        raise BankReviewStatusDenied("human_bank_review_record_required")
    sid=claim.get("salla_order_id")
    if type(sid) is not int or sid<1:
        raise BankReviewStatusDenied("verified_salla_bank_order_required")

    async def read_status():
        try:
            response=await get_order(
                db,owner_id,"GET",f"/orders/{sid}",
            )
        except Exception:
            raise BankReviewStatusDenied("salla_bank_status_read_uncertain") from None
        return _provider_row(response,sid)["status"]

    status=await read_status()
    if status=="under_review":
        await db.salla_ai_bank_receipt_reviews.update_one({
            "_id":key,"user_id":owner_id,
            "salla_order_id":sid,
        },{"$set":{"salla_review_status":"under_review"}})
        return {"state":"salla_under_review",
                "salla_order_id":sid,
                "bank_credit_verified":False,
                "replayed":True}

    # A previous (possibly successful) write may have timed out. Never
    # repeat it automatically; manual review is safer than status churn.
    if claim.get("salla_review_status") is not None:
        return {"state":"salla_review_status_requires_manual_reconciliation",
                "salla_order_id":sid,"bank_credit_verified":False}

    changed=await db.salla_ai_bank_receipt_reviews.update_one({
        "_id":key,"user_id":owner_id,
        "salla_order_id":sid,"salla_review_status":None,
    },{"$set":{"salla_review_status":"requesting"}})
    if getattr(changed,"modified_count",0)!=1:
        raise BankReviewStatusDenied("bank_review_status_already_claimed")
    try:
        # This is ORDER STATUS only; NEVER payment.status="paid".
        result=await post_status(
            db,owner_id,"POST",f"/orders/{sid}/status",
            json={"slug":"under_review","send_status_sms":False},
        )
        if not isinstance(result,dict) or result.get("success") is False:
            raise BankReviewStatusDenied("salla_bank_status_response_unverified")
    except Exception:
        await db.salla_ai_bank_receipt_reviews.update_one({
            "_id":key,"user_id":owner_id,"salla_review_status":"requesting",
        },{"$set":{"salla_review_status":"request_result_unknown"}})
        return {"state":"salla_review_status_write_uncertain",
                "salla_order_id":sid,"bank_credit_verified":False}

    try:
        actual=await read_status()
    except BankReviewStatusDenied:
        actual="unknown"
    if actual!="under_review":
        await db.salla_ai_bank_receipt_reviews.update_one({
            "_id":key,"user_id":owner_id,"salla_review_status":"requesting",
        },{"$set":{"salla_review_status":"request_result_unknown"}})
        return {"state":"salla_review_status_readback_unverified",
                "salla_order_id":sid,"bank_credit_verified":False}
    await db.salla_ai_bank_receipt_reviews.update_one({
        "_id":key,"user_id":owner_id,"salla_review_status":"requesting",
    },{"$set":{"salla_review_status":"under_review"}})
    return {
        "state":"salla_under_review","salla_order_id":sid,
        "bank_credit_verified":False,"replayed":False,
    }
