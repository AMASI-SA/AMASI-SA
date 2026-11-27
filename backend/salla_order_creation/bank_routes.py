"""Default-OFF bank transfer receipt review API for assisted Salla orders.

Private receipt observation + bank quote are separate HMAC purposes. Staff
review is separate from the legacy /bank-transfer-review/confirm GL-writing
route. NOTHING here posts GL, marks Salla paid or releases fulfillment.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order, OrderNotFoundError
from order_review_routes import _require_reviewer, _merchant_user_id
from salla_integration.service import call_salla

from .bank_receipt_action import verify_bank_receipt_action
from .bank_receipt_preflight import (
    ReceiptReviewDenied, assess_visible_bank_receipt,
)
from .bank_receipt_review import (
    BankReviewDenied, _key, save_preliminary_bank_receipt,
)
from .bank_quote_writer import PrepareBankOrderDenied
from .bank_review_staff import (
    StaffBankReviewDenied, reconcile_bank_receipt_with_mezan_order,
    validate_human_bank_decision,
)
from .bank_transfer import BankSelectionDenied, get_verified_receiving_bank
from .bank_vault import prepare_bank_from_private_vault
from .bank_vault_action import verify_bank_vault_prepare
from .http_limits import BodyTooLarge, read_bounded_action_body
from .private_fields import require_private_fields_ttl, PrivateOrderFieldDenied
from .retention import RetentionNotReady, require_assisted_quote_ttl
from .signature import SignatureDenied


class StaffBankDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    bank_statement_reference: str | None = Field(default=None, max_length=100)
    bank_statement_amount_sar: str | None = Field(default=None, max_length=30)


def _require_enabled(key: str):
    if os.environ.get(key, "0") != "1":
        raise HTTPException(status_code=503, detail={"code": "bank_review_disabled"})


def _signed_scope(purpose: str):
    owner = str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
    account = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or "")
    inbox = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or "")
    secret = str(os.environ.get(purpose) or "").encode()
    if (not owner or len(secret) < 32 or
            not account.isdigit() or int(account) < 1 or
            not inbox.isdigit() or int(inbox) < 1):
        raise HTTPException(
            status_code=503, detail={"code": "bank_receipt_scope_not_configured"}
        )
    return owner, int(account), int(inbox), secret


async def _verified_signed(request, secret, verifier):
    try:
        body = await read_bounded_action_body(request)
    except BodyTooLarge as exc:
        raise HTTPException(
            status_code=413, detail={"code": str(exc)}
        ) from exc
    try:
        return verifier(
            body,
            request.headers.get("x-mezan-action-timestamp", ""),
            request.headers.get("x-mezan-action-delivery", ""),
            request.headers.get("x-mezan-action-signature", ""),
            secret,
        )
    except SignatureDenied as exc:
        raise HTTPException(
            status_code=401, detail={"code": str(exc)}
        ) from exc


async def _get_mezan_order(db, owner: str, reference: int | None):
    if type(reference) is not int or reference <= 0:
        return None
    try:
        return await get_order(
            MongoOrderRepository(db), user_id=owner,
            order_number=str(reference),
        )
    except OrderNotFoundError:
        return None


def _safe_review_row(row: dict) -> dict:
    """No receipt bytes, beneficiary, raw bank ref or customer contact PII."""
    return {
        "intent_id": row.get("intent_id"),
        "state": row.get("state"),
        "salla_order_id": row.get("salla_order_id"),
        "salla_reference_id": row.get("salla_reference_id"),
        "store_bank_id": row.get("store_bank_id"),
        "claimed_amount_sar": row.get("claimed_amount_sar"),
        "conversation_id": row.get("conversation_id"),
        "source_message_id": row.get("source_message_id"),
        "attachment_id": row.get("attachment_id"),
        "bank_credit_confirmed": row.get("bank_credit_confirmed", False),
        "fulfillment_released": False,
    }


def make_assisted_bank_review_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(
        prefix="/orders-v2/assisted-salla-bank", tags=["assisted-bank-receipts"]
    )

    @router.post("/signed-receipt-preflight")
    async def signed_receipt_preflight(request: Request) -> dict:
        _require_enabled("MEZAN_ASSISTED_SALLA_BANK_RECEIPT_ENABLED")
        owner, account, inbox, secret = _signed_scope(
            "MEZAN_ASSISTED_SALLA_BANK_RECEIPT_HMAC_SECRET"
        )
        action = await _verified_signed(
            request, secret, verify_bank_receipt_action
        )
        if (action.account_id != account or action.inbox_id != inbox):
            raise HTTPException(status_code=403, detail={"code": "wrong_inbox"})
        try:
            bank = await get_verified_receiving_bank(
                db, owner, action.observation.receiving_store_bank_id,
            )
            observation = assess_visible_bank_receipt(
                action.observation,
                expected_image_sha256=action.image_sha256,
                bank_details=bank,
                today=datetime.now(ZoneInfo("Asia/Riyadh")).date(),
            )
            return await save_preliminary_bank_receipt(
                db, owner_id=owner, intent_id=action.intent_id,
                account_id=action.account_id, inbox_id=action.inbox_id,
                conversation_id=action.conversation_id,
                contact_id=action.source_contact_id,
                source_message_id=action.source_message_id,
                attachment_id=action.attachment_id,
                assessment=observation,
            )
        except (BankSelectionDenied, ReceiptReviewDenied, BankReviewDenied) as exc:
            raise HTTPException(
                status_code=409, detail={"code": str(exc)}
            ) from None

    @router.post("/signed-prepare-bank")
    async def signed_prepare_bank(request: Request) -> dict:
        _require_enabled("MEZAN_ASSISTED_SALLA_BANK_QUOTE_ENABLED")
        _require_enabled("MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_ENABLED")
        _require_enabled("MEZAN_ASSISTED_SALLA_QUOTE_WRITE_ENABLED")
        owner, account, inbox, secret = _signed_scope(
            "MEZAN_ASSISTED_SALLA_BANK_PREPARE_HMAC_SECRET"
        )
        action = await _verified_signed(
            request, secret, verify_bank_vault_prepare,
        )
        if (action.account_id != account or action.inbox_id != inbox):
            raise HTTPException(status_code=403, detail={"code": "wrong_inbox"})
        try:
            await require_assisted_quote_ttl(db)
            await require_private_fields_ttl(db)
            return await prepare_bank_from_private_vault(
                db, owner_id=owner,
                account_id=action.account_id,
                inbox_id=action.inbox_id,
                conversation_id=action.conversation_id,
                intent_id=action.intent_id,
                contact_id=action.source_contact_id,
                message_id=action.source_message_id,
                receiver_country_code=action.receiver_country_code,
                country_id=action.country_id, city_id=action.city_id,
                district_id=action.district_id,
                courier_id=action.courier_id,
                selected_rate_id=action.selected_rate_id,
                products=[
                    p.model_dump(exclude_none=True) for p in action.products
                ],
                branch_id=action.branch_id, enabled=True,
            )
        except (RetentionNotReady, PrivateOrderFieldDenied,
                PrepareBankOrderDenied, ValueError) as exc:
            raise HTTPException(
                status_code=409, detail={"code": str(exc)}
            ) from None

    @router.get("/reviews")
    async def reviews(user: dict = Depends(current_user)) -> dict:
        _require_enabled("MEZAN_ASSISTED_SALLA_BANK_STAFF_REVIEW_ENABLED")
        reviewer = _require_reviewer(user)
        owner = _merchant_user_id(reviewer)
        rows = await db.salla_ai_bank_receipt_reviews.find(
            {"owner_id": owner},
        ).sort("created_at", -1).limit(50).to_list(50)
        return {"reviews": [_safe_review_row(x) for x in rows]}

    @router.get("/reviews/{intent_id}")
    async def review_details(
        intent_id: str, user: dict = Depends(current_user),
    ) -> dict:
        _require_enabled("MEZAN_ASSISTED_SALLA_BANK_STAFF_REVIEW_ENABLED")
        reviewer = _require_reviewer(user)
        owner = _merchant_user_id(reviewer)
        row = await db.salla_ai_bank_receipt_reviews.find_one({
            "_id": _key(owner, intent_id), "owner_id": owner,
        })
        if not row:
            raise HTTPException(status_code=404, detail={"code": "not_found"})
        order = await _get_mezan_order(
            db, owner, row.get("salla_reference_id"),
        )
        return {
            "review": _safe_review_row(row),
            "comparison": reconcile_bank_receipt_with_mezan_order(row, order),
            "payment_marked_paid": False,
            "accounting_posted": False,
        }

    @router.post("/reviews/{intent_id}/decision")
    async def staff_decision(
        intent_id: str, payload: StaffBankDecision,
        user: dict = Depends(current_user),
    ) -> dict:
        _require_enabled("MEZAN_ASSISTED_SALLA_BANK_STAFF_REVIEW_ENABLED")
        reviewer = _require_reviewer(user)
        owner = _merchant_user_id(reviewer)
        key = _key(owner, intent_id)
        row = await db.salla_ai_bank_receipt_reviews.find_one({
            "_id": key, "owner_id": owner,
        })
        if not row:
            raise HTTPException(status_code=404, detail={"code": "not_found"})
        current = await _get_mezan_order(
            db, owner, row.get("salla_reference_id"),
        )
        comparison = reconcile_bank_receipt_with_mezan_order(row, current)
        try:
            verdict = validate_human_bank_decision(
                decision=payload.decision, review=row,
                reconciliation=comparison,
                reviewer_id=str(reviewer.get("id") or ""),
                statement_reference=payload.bank_statement_reference,
                statement_amount_sar=payload.bank_statement_amount_sar,
            )
        except StaffBankReviewDenied as exc:
            raise HTTPException(
                status_code=409, detail={"code": str(exc)}
            ) from None
        changed = await db.salla_ai_bank_receipt_reviews.update_one(
            {"_id": key, "owner_id": owner,
             "state": "awaiting_human_bank_review"},
            {"$set": {
                **verdict, "reviewed_by": str(reviewer["id"]),
                "reviewed_at": datetime.now(ZoneInfo("Asia/Riyadh")),
            }},
        )
        if getattr(changed, "modified_count", 0) != 1:
            raise HTTPException(
                status_code=409, detail={"code": "review_already_decided"}
            )
        return {
            "state": verdict["state"],
            "salla_order_id": row["salla_order_id"],
            "payment_marked_paid": False,
            "accounting_posted": False,
            "fulfillment_released": False,
        }

    return router
