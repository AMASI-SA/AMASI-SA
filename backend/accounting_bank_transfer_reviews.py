"""Manual review bridge for Salla bank-transfer receipts into MZ2.

The reviewer compares two independent pieces of evidence:
1) Salla's customer-uploaded transfer receipt (amount/order + receiving bank);
2) the actual imported inbound bank movement.

No amount/date-only auto-link is allowed. The reviewer explicitly selects the
bank movement and approves once. A bank movement is consumed exactly once.

Because imported bank statements are day-precision evidence, the cash leg is
recorded as a customer advance at Saudi-local midnight of the bank day, then
cleared into the delivered sale at the Salla delivery timestamp. This avoids
back-dating cash to delivery and preserves the existing "advance before
revenue" accounting rule. A bank movement dated after delivery remains blocked
for a dedicated receivable treatment rather than silently shifting revenue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_atomic import atomic_owner
from accounting_mz2_balances import read_mz2_write_balances
from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_order_recognition import _source_time
from accounting_sales_tax import TaxError
from accounting_sales_tax_service import read_policy, sale_snapshot
from ledger_core import post_txn_group


RIYADH = ZoneInfo("Asia/Riyadh")
MAX_REVIEW_ROWS = 300
MAX_MOVEMENT_SCAN = 1000


class BankTransferReviewError(ValueError):
    pass


def _hash(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _amount(value: Any, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise BankTransferReviewError("bank_transfer_amount_invalid") from None
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise BankTransferReviewError("bank_transfer_amount_invalid")
    return result


def _money(value: Any) -> Decimal:
    return _amount(value, positive=True)


def _bank_day(value: str) -> datetime:
    try:
        day = datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise BankTransferReviewError("bank_transfer_movement_date_invalid") from None
    return datetime(day.year, day.month, day.day, 0, 0, tzinfo=RIYADH).astimezone(
        timezone.utc
    )


def _safe_order_view(order: dict[str, Any] | None) -> dict[str, Any]:
    order = order or {}
    return {
        "receiving_bank_name": str(order.get("receiving_bank_name") or "").strip()
        or None,
        "receipt_url": str(order.get("payment_receipt_url") or "").strip()
        or None,
        "payment_method": str(order.get("payment_method") or "").strip() or None,
    }


async def _operational_order(db, owner: str, order_number: str) -> dict[str, Any] | None:
    return await db.unified_orders.find_one(
        {"user_id": owner, "order_number": order_number},
        {
            "_id": 0,
            "order_number": 1,
            "payment_method": 1,
            "receiving_bank_name": 1,
            "receiving_bank_id": 1,
            "payment_receipt_url": 1,
            "total_amount": 1,
            "total_amount_sar": 1,
            "order_status": 1,
            "delivered_at": 1,
        },
    )


async def _movement_candidates(
    db,
    *,
    owner: str,
    amount: Decimal,
) -> list[dict[str, Any]]:
    rows = await db.mz2_daily_movements.find(
        {
            "user_id": owner,
            "direction": "in",
            "status": "unclassified",
            "$or": [
                {"accounting_event_id": {"$exists": False}},
                {"accounting_event_id": None},
                {"accounting_event_id": ""},
            ],
        },
        {"_id": 0},
    ).sort([("movement_date", -1), ("row_no", -1)]).limit(MAX_MOVEMENT_SCAN).to_list(
        MAX_MOVEMENT_SCAN
    )
    result = []
    for row in rows:
        if row.get("receipt_id"):
            continue
        if row.get("explicit_provider") or row.get("suggested_provider"):
            continue
        try:
            value = _money(row.get("amount"))
        except BankTransferReviewError:
            continue
        if value != amount:
            continue
        result.append(
            {
                "id": row.get("id"),
                "movement_date": row.get("movement_date"),
                "amount": format(value, ".2f"),
                "bank_account_id": row.get("bank_account_id"),
                "bank_account_name": row.get("bank_account_name"),
                "reference": row.get("reference"),
                "description": row.get("description"),
            }
        )
    return result


async def _review_row(db, *, owner: str, evidence: dict[str, Any]) -> dict[str, Any]:
    amount = _money(evidence.get("current_net_sar"))
    order = await _operational_order(db, owner, evidence["order_number"])
    operational = _safe_order_view(order)
    all_candidates = await _movement_candidates(db, owner=owner, amount=amount)
    candidates = all_candidates
    late_candidates = []
    if evidence.get("delivery_source_text"):
        delivered_at = _source_time(evidence.get("delivery_source_text"))
        candidates = []
        for candidate in all_candidates:
            try:
                bank_at = _bank_day(candidate.get("movement_date"))
            except BankTransferReviewError:
                continue
            if bank_at <= delivered_at:
                candidates.append(candidate)
            else:
                late_candidates.append(candidate)

    reasons = []
    if evidence.get("conflict"):
        reasons.append("order_evidence_conflict")
    if evidence.get("accounting_provider") != "bank_transfer":
        reasons.append("order_is_not_bank_transfer")
    if _amount(evidence.get("refunded_sar") or "0.00") != Decimal("0.00"):
        reasons.append("bank_transfer_refund_requires_review")
    if evidence.get("order_status") != "تم التوصيل" or not evidence.get(
        "delivery_source_text"
    ):
        reasons.append("fulfilment_timestamp_required")
    if not operational["receipt_url"]:
        reasons.append("customer_transfer_receipt_missing")
    if not operational["receiving_bank_name"]:
        reasons.append("receiving_bank_name_missing")
    if not candidates:
        reasons.append(
            "bank_transfer_after_delivery_requires_receivable_workflow"
            if late_candidates
            else "matching_bank_movement_missing"
        )

    prior = await db.mz2_bank_transfer_reviews.find_one(
        {"user_id": owner, "order_number": evidence["order_number"]},
        {"_id": 0},
    )
    return {
        "evidence_id": evidence["id"],
        "order_number": evidence["order_number"],
        "order_amount": format(amount, ".2f"),
        "receipt_url": operational["receipt_url"],
        "receipt_bank_name": operational["receiving_bank_name"],
        "payment_method": operational["payment_method"],
        "order_status": evidence.get("order_status"),
        "delivery_source_text": evidence.get("delivery_source_text"),
        "bank_movements": candidates,
        "late_bank_movements": late_candidates,
        "review": prior,
        "state": "approved"
        if prior and prior.get("status") == "posted"
        else "ready_for_review"
        if not reasons
        else "waiting",
        "reasons": reasons,
    }


async def bank_transfer_review_queue(
    db,
    *,
    owner: str,
    limit: int = 100,
) -> dict[str, Any]:
    rows = await db.mz2_salla_order_evidence.find(
        {
            "user_id": owner,
            "accounting_provider": "bank_transfer",
            "status": {
                "$in": [
                    "needs_bank_transfer_evidence",
                    "recognized",
                    "recognized_refund_pending_evidence",
                ]
            },
        },
        {"_id": 0},
    ).sort([("updated_source_text", -1), ("order_number", -1)]).limit(
        min(limit, MAX_REVIEW_ROWS)
    ).to_list(min(limit, MAX_REVIEW_ROWS))

    items = [await _review_row(db, owner=owner, evidence=row) for row in rows]
    return {
        "items": items,
        "total": len(items),
        "ready_count": sum(1 for row in items if row["state"] == "ready_for_review"),
        "waiting_count": sum(1 for row in items if row["state"] == "waiting"),
        "approved_count": sum(1 for row in items if row["state"] == "approved"),
    }


class BankTransferApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movement_id: str = Field(min_length=1, max_length=200)
    confirmation: str = Field(pattern="^APPROVE_BANK_TRANSFER_RECEIPT$")


async def approve_bank_transfer(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    evidence_id: str,
    movement_id: str,
) -> dict[str, Any]:
    async def commit(scoped):
        evidence = await scoped.mz2_salla_order_evidence.find_one(
            {"user_id": owner, "id": evidence_id},
            {"_id": 0},
        )
        if not evidence:
            raise BankTransferReviewError("order_evidence_missing")
        review = await _review_row(scoped, owner=owner, evidence=evidence)
        prior = review.get("review") or {}
        if prior.get("status") == "posted":
            if prior.get("movement_id") != movement_id:
                raise BankTransferReviewError(
                    "bank_transfer_review_already_approved_with_other_movement"
                )
            return prior

        if review["state"] != "ready_for_review":
            raise BankTransferReviewError(
                review["reasons"][0] if review["reasons"] else "bank_transfer_not_ready"
            )
        candidate = next(
            (row for row in review["bank_movements"] if row["id"] == movement_id),
            None,
        )
        if not candidate:
            raise BankTransferReviewError("selected_bank_movement_not_candidate")

        movement = await scoped.mz2_daily_movements.find_one(
            {
                "user_id": owner,
                "id": movement_id,
                "status": "unclassified",
                "$or": [
                    {"accounting_event_id": {"$exists": False}},
                    {"accounting_event_id": None},
                    {"accounting_event_id": ""},
                ],
            }
        )
        if not movement:
            raise BankTransferReviewError("daily_movement_already_consumed")
        if movement.get("receipt_id") or movement.get("explicit_provider") or movement.get(
            "suggested_provider"
        ):
            raise BankTransferReviewError("provider_movement_cannot_be_bank_transfer_order")

        amount = _money(evidence["current_net_sar"])
        if _money(movement.get("amount")) != amount or movement.get("direction") != "in":
            raise BankTransferReviewError("bank_transfer_movement_amount_conflict")
        bank_id = str(movement.get("bank_account_id") or "").strip()
        if not bank_id:
            raise BankTransferReviewError("daily_movement_bank_missing")

        delivered_at = _source_time(evidence.get("delivery_source_text"))
        bank_at = _bank_day(movement.get("movement_date"))
        if bank_at > delivered_at:
            raise BankTransferReviewError(
                "bank_transfer_after_delivery_requires_receivable_workflow"
            )

        settings = await scoped.settings.find_one(
            {"user_id": owner}, {"_id": 0, "mezan2_financial_cutover": 1}
        )
        cutover = ((settings or {}).get("mezan2_financial_cutover") or {})
        try:
            cutover_at = datetime.fromisoformat(
                str(cutover.get("cutover_at") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError:
            raise BankTransferReviewError(
                "recognition_cutoff_not_configured"
            ) from None
        if (
            cutover.get("operation_id") != OPERATION_ID
            or cutover.get("status") != "active"
            or bank_at < cutover_at
            or delivered_at < cutover_at
        ):
            raise BankTransferReviewError("bank_transfer_before_cutover")

        await read_mz2_write_balances(
            scoped,
            owner=owner,
            required_accounts=[("bank", bank_id, "main")],
        )

        existing = await scoped.general_ledger.find_one(
            {
                "user_id": owner,
                "status": {"$in": ["posted", "reversed"]},
                "$or": [
                    {"metadata.order_reference_id": evidence["order_number"]},
                    {"metadata.daily_movement_id": movement_id},
                ],
            }
        )
        if existing:
            raise BankTransferReviewError("existing_journal_requires_review")

        review_id = _hash(
            [owner, "bank_transfer_review", evidence["order_number"]]
        )
        recognition_key = _hash(
            [owner, "bank_transfer_sale", evidence["order_number"], movement_id]
        )
        tax_event = {
            "kind": "sale",
            "provider": "bank_transfer",
            "provider_payment_id": movement_id,
            "canonical_event_id": movement_id,
            "order_number": evidence["order_number"],
            "amount": format(amount, ".2f"),
            "sale_amount": format(amount, ".2f"),
            "currency": "SAR",
            "recognized_at": delivered_at.isoformat(),
        }
        tax = sale_snapshot(
            await read_policy(scoped, owner),
            tax_event,
            {"tax_amount": evidence.get("source_tax_sar")},
        )

        now = datetime.now(timezone.utc).isoformat()
        review_record = {
            "_id": review_id,
            "id": review_id,
            "user_id": owner,
            "status": "posting",
            "order_number": evidence["order_number"],
            "evidence_id": evidence_id,
            "movement_id": movement_id,
            "order_amount": format(amount, ".2f"),
            "receipt_url": review["receipt_url"],
            "receipt_bank_name": review["receipt_bank_name"],
            "actual_bank_account_id": bank_id,
            "actual_bank_account_name": movement.get("bank_account_name"),
            "bank_reference": movement.get("reference"),
            "bank_movement_date": movement.get("movement_date"),
            "delivery_source_text": evidence.get("delivery_source_text"),
            "reviewed_by": actor["id"],
            "reviewed_at": now,
        }
        await scoped.mz2_bank_transfer_reviews.insert_one(review_record)
        await scoped.mz2_recognition_events.insert_one(
            {
                "_id": recognition_key,
                "user_id": owner,
                "status": "posting",
                "economic_hash": _hash(
                    {
                        "order_number": evidence["order_number"],
                        "movement_id": movement_id,
                        "amount": format(amount, ".2f"),
                        "bank_at": bank_at.isoformat(),
                        "delivered_at": delivered_at.isoformat(),
                    }
                ),
                "proposal": {
                    "state": "eligible",
                    "event": tax_event,
                    "tax": tax,
                    "evidence_id": evidence_id,
                    "bank_transfer_review_id": review_id,
                },
                "actor_id": actor["id"],
            }
        )

        advance = await post_txn_group(
            scoped,
            user_id=owner,
            actor_id=actor["id"],
            actor_name=actor.get("name") or actor.get("email") or actor["id"],
            txn_type="customer_advance_capture",
            notes=f"تحويل بنكي مقدم — طلب {evidence['order_number']}",
            metadata={
                "operation_id": OPERATION_ID,
                "source": "accounting_bank_transfer_review",
                "accounting_at": bank_at.isoformat(),
                "customer_advance_id": review_id,
                "order_reference_id": evidence["order_number"],
                "daily_movement_id": movement_id,
                "bank_reference": movement.get("reference"),
                "receipt_url": review["receipt_url"],
                "receipt_bank_name": review["receipt_bank_name"],
                "evidence_ref": review["receipt_url"],
            },
            entries=[
                {
                    "entity_type": "bank",
                    "entity_id": bank_id,
                    "sub_account": "main",
                    "side": "debit",
                    "amount": format(amount, ".2f"),
                    "entry_type": "customer_advance_capture",
                },
                {
                    "entity_type": "liability",
                    "entity_id": review_id,
                    "sub_account": "customer_advance",
                    "side": "credit",
                    "amount": format(amount, ".2f"),
                    "entry_type": "customer_advance_capture",
                },
            ],
        )

        sale_entries = [
            {
                "entity_type": "liability",
                "entity_id": review_id,
                "sub_account": "customer_advance",
                "side": "debit",
                "amount": tax["gross"],
                "entry_type": "bnpl_sale",
            },
            {
                "entity_type": "revenue",
                "entity_id": "bnpl_sales",
                "side": "credit",
                "amount": tax["net"],
                "entry_type": "bnpl_sale",
            },
        ]
        if Decimal(tax["tax"]) > 0:
            sale_entries.append(
                {
                    "entity_type": "tax",
                    "entity_id": "sales_vat_payable",
                    "side": "credit",
                    "amount": tax["tax"],
                    "entry_type": "bnpl_sale",
                }
            )
        sale = await post_txn_group(
            scoped,
            user_id=owner,
            actor_id=actor["id"],
            actor_name=actor.get("name") or actor.get("email") or actor["id"],
            txn_type="bnpl_sale",
            notes=f"بيع تحويل بنكي — طلب {evidence['order_number']}",
            metadata={
                "operation_id": OPERATION_ID,
                "source": "accounting_bank_transfer_review",
                "recognition_event_key": recognition_key,
                "recognized_at": delivered_at.isoformat(),
                "order_reference_id": evidence["order_number"],
                "provider": "bank_transfer",
                "provider_id": movement_id,
                "daily_movement_id": movement_id,
                "bank_transfer_review_id": review_id,
                "sales_tax": tax,
                "receipt_url": review["receipt_url"],
                "receipt_bank_name": review["receipt_bank_name"],
            },
            entries=sale_entries,
        )

        changed = await scoped.mz2_daily_movements.update_one(
            {
                "_id": movement["_id"],
                "user_id": owner,
                "status": "unclassified",
                "$or": [
                    {"accounting_event_id": {"$exists": False}},
                    {"accounting_event_id": None},
                    {"accounting_event_id": ""},
                ],
            },
            {
                "$set": {
                    "status": "accounting_posted",
                    "accounting_event_id": review_id,
                    "accounting_action": "bank_transfer_order_sale",
                    "accounting_txn_group_id": sale["txn_group_id"],
                    "accounting_advance_txn_group_id": advance["txn_group_id"],
                    "accounting_order_number": evidence["order_number"],
                    "consumed_at": now,
                    "consumed_by": actor["id"],
                }
            },
        )
        if changed.modified_count != 1:
            raise BankTransferReviewError("daily_movement_concurrent_consumption")

        await scoped.mz2_bank_transfer_reviews.update_one(
            {"_id": review_id, "user_id": owner, "status": "posting"},
            {
                "$set": {
                    "status": "posted",
                    "advance_txn_group_id": advance["txn_group_id"],
                    "sale_txn_group_id": sale["txn_group_id"],
                    "recognition_event_key": recognition_key,
                    "tax": tax,
                    "posted_at": now,
                }
            },
        )
        await scoped.mz2_recognition_events.update_one(
            {"_id": recognition_key, "user_id": owner, "status": "posting"},
            {
                "$set": {
                    "status": "posted",
                    "txn_group_id": sale["txn_group_id"],
                }
            },
        )
        await scoped.mz2_salla_order_evidence.update_one(
            {"user_id": owner, "id": evidence_id},
            {
                "$set": {
                    "status": "recognized",
                    "review_reasons": [],
                    "recognition_event_key": recognition_key,
                    "recognition_txn_group_id": sale["txn_group_id"],
                    "recognized_at": delivered_at.isoformat(),
                    "recognized_provider": "bank_transfer",
                    "recognized_gross_sar": tax["gross"],
                    "recognized_tax_sar": tax["tax"],
                    "recognized_net_sar": tax["net"],
                    "recognized_by": actor["id"],
                    "recognition_posted_at": now,
                    "bank_transfer_review_id": review_id,
                    "bank_transfer_movement_id": movement_id,
                    "bank_transfer_advance_txn_group_id": advance["txn_group_id"],
                }
            },
        )
        result = dict(review_record)
        result.update(
            {
                "status": "posted",
                "advance_txn_group_id": advance["txn_group_id"],
                "sale_txn_group_id": sale["txn_group_id"],
                "recognition_event_key": recognition_key,
                "tax": tax,
                "posted_at": now,
            }
        )
        result.pop("_id", None)
        return result

    return await atomic_owner(db, owner, commit)


def install_bank_transfer_review_routes(router, db, current_user) -> None:
    base = "/accounting-module/bank-transfer-reviews"

    async def scope(user: dict[str, Any], permission: str):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, permission)
        owner = accounting_owner_id(actor)
        if not owner:
            raise HTTPException(403, "accounting_owner_scope_missing")
        return actor, owner

    @router.get(base)
    async def queue(limit: int = 100, user: dict = Depends(current_user)):
        _, owner = await scope(user, "accounting.movements.view")
        return await bank_transfer_review_queue(
            db, owner=owner, limit=min(max(limit, 1), MAX_REVIEW_ROWS)
        )

    @router.post(base + "/{evidence_id}/approve")
    async def approve(
        evidence_id: str,
        payload: BankTransferApproveIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(user, "accounting.receivables.post")
        try:
            return await approve_bank_transfer(
                db,
                owner=owner,
                actor=actor,
                evidence_id=evidence_id,
                movement_id=payload.movement_id,
            )
        except (BankTransferReviewError, TaxError) as exc:
            raise HTTPException(
                409, detail={"code": str(exc), "message": str(exc)}
            ) from None
