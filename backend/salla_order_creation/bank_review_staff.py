"""Human receipt-appearance triage; NEVER a bank receipt/payment approval.

A staff member may request a clearer receipt or mark visible details
reviewed. Neither state confirms bank settlement, marks the Salla order
paid, starts fulfillment or posts any GL/Qoyod entry.

A separate authoritative bank credit reconciliation must precede any
financial confirmation through established Mezan permissioned workflows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StaffBankReviewDenied(ValueError):
    pass


class StaffBankReceiptDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["request_clearer_receipt", "appearance_checked"]
    # No free-form bank statement, IBAN, OTP or paid amount text here.
    review_note_code: Literal[
        "illegible", "amount_differs", "recipient_unknown",
        "date_or_reference_missing", "appears_consistent",
        "suspected_duplicate", "other_staff_followup",
    ]


async def record_staff_receipt_triage(
    db: Any, *,
    owner_id: str, review_id: str, staff_id: str,
    decision: StaffBankReceiptDecision,
    now: datetime | None = None,
) -> dict:
    if (not owner_id or not staff_id or
            type(review_id) is not str or len(review_id)!=64 or
            any(x not in "0123456789abcdef" for x in review_id) or
            not isinstance(decision,StaffBankReceiptDecision)):
        raise StaffBankReviewDenied("authenticated_staff_receipt_required")
    instant=now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise StaffBankReviewDenied("utc_staff_review_clock_required")
    if decision.decision=="appearance_checked" and (
            decision.review_note_code not in (
                "appears_consistent","other_staff_followup"
            )):
        raise StaffBankReviewDenied("incompatible_receipt_triage_code")
    state=(
        "receipt_appearance_checked_awaiting_bank_credit"
        if decision.decision=="appearance_checked"
        else "receipt_clarification_required"
    )
    # One human decision from pending, idempotent on replay. This module
    # never looks up bank-account balances, so cannot assert bank credit.
    result=await db.salla_ai_bank_receipt_reviews.update_one({
        "_id":review_id, "user_id":owner_id,
        "status":"awaiting_human_bank_confirmation",
        "bank_settlement_confirmed":False, "salla_paid":False,
        "gl_posted":False,
    },{
        "$set":{
            "status":state,
            "staff_triage":decision.decision,
            "staff_note_code":decision.review_note_code,
            "reviewed_by":staff_id,
            "reviewed_at":instant,
            "updated_at":instant,
        }
    })
    if getattr(result,"modified_count",0)!=1:
        previous=await db.salla_ai_bank_receipt_reviews.find_one({
            "_id":review_id,"user_id":owner_id,
        })
        if (not isinstance(previous,dict) or
                previous.get("status")!=state or
                previous.get("staff_triage")!=decision.decision or
                previous.get("reviewed_by")!=staff_id):
            raise StaffBankReviewDenied("staff_receipt_review_not_pending")
    return {
        "state":state,
        "review_id":review_id,
        "payment_confirmed":False,
        "bank_settlement_confirmed":False,
        "ready_for_fulfillment":False,
        "gl_posted":False,
    }
