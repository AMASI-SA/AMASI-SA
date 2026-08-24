"""Authoritative P01 settlement lifecycle routes.

Installed before the compatibility routes so these handlers own submit,
review, reject, and post. They add bank-movement evidence checks and expose the
approved ``matched`` state without changing unrelated legacy endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException

from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_module_status_routes import fresh_accounting_user
from accounting_settlement_bank_match_routes import bank_match_review_reasons
from accounting_settlement_routes import (
    DRAFT_EDITABLE_STATUSES,
    DraftActionIn,
    DraftRejectIn,
    _draft_or_404,
    _recomputed_draft,
)
from accounting_settlement_service import has_blocking_reasons, post_reviewed_settlement
from ledger_core import write_audit

MATCHABLE_STATUSES = frozenset(DRAFT_EDITABLE_STATUSES)
REVIEWABLE_STATUSES = frozenset({"matched", "ready_for_review"})
REJECTABLE_STATUSES = frozenset({"matched", "ready_for_review", "reviewed"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _scope(db, user: dict[str, Any], permission: str) -> tuple[dict[str, Any], str]:
    actor = await fresh_accounting_user(db, user)
    require_accounting_permission(actor, permission)
    owner_id = accounting_owner_id(actor)
    if not owner_id:
        raise HTTPException(403, "لا يوجد مالك بيانات محاسبية مرتبط بالمستخدم")
    return actor, owner_id


def _combined_reasons(draft: dict[str, Any]) -> list[dict[str, str]]:
    reasons = list(draft.get("review_reasons") or [])
    reasons.extend(bank_match_review_reasons(draft))
    unique: dict[str, dict[str, str]] = {}
    for reason in reasons:
        unique.setdefault(str(reason.get("code") or "unknown"), reason)
    return list(unique.values())


def _blocking(reasons: list[dict[str, Any]]) -> bool:
    return has_blocking_reasons(reasons) or bool(reasons)


async def _audit_state(
    db,
    *,
    owner_id: str,
    actor: dict[str, Any],
    draft: dict[str, Any],
    action: str,
    before_status: str,
    after_status: str,
    notes: str = "",
) -> None:
    await write_audit(
        db,
        user_id=owner_id,
        actor_id=str(actor.get("id") or ""),
        actor_name=actor.get("name") or actor.get("email") or "",
        entity_type="payment_gateway",
        entity_id=draft.get("provider") or "",
        action=action,
        reason_code="accounting_settlement_lifecycle",
        notes=notes,
        before_state={
            "draft_id": draft.get("id"),
            "status": before_status,
        },
        after_state={
            "draft_id": draft.get("id"),
            "status": after_status,
            "statement_reference": draft.get("statement_reference"),
            "bank_transaction_id": draft.get("bank_transaction_id"),
        },
    )


def install_accounting_settlement_lifecycle_routes(router, db, current_user):
    @router.post("/accounting-module/settlements/drafts/{draft_id}/submit")
    async def submit_matched_settlement(
        draft_id: str,
        payload: DraftActionIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(db, user, "accounting.drafts.create")
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") not in MATCHABLE_STATUSES:
            raise HTTPException(409, "المسودة ليست في حالة قابلة للمطابقة")
        recomputed = await _recomputed_draft(db, owner_id=owner_id, draft=current)
        reasons = _combined_reasons(recomputed)
        if _blocking(reasons):
            await db.accounting_settlements_v2.update_one(
                {"id": draft_id, "user_id": owner_id},
                {"$set": {
                    "status": "needs_review",
                    "workflow_state": "needs_review",
                    "review_reasons": reasons,
                    "calculation": recomputed.get("calculation"),
                    "journal_preview": recomputed.get("journal_preview"),
                    "updated_at": _now(),
                }},
            )
            raise HTTPException(
                409,
                {
                    "code": "settlement_review_reasons_open",
                    "message": "لا يمكن اعتماد المطابقة قبل معالجة جميع الفروقات",
                    "reasons": reasons,
                },
            )
        now = _now()
        update = {
            "status": "matched",
            "workflow_state": "matched",
            "matched_by": actor.get("id"),
            "matched_by_name": actor.get("name") or actor.get("email"),
            "matched_at": now,
            "submitted_by": actor.get("id"),
            "submitted_by_name": actor.get("name") or actor.get("email"),
            "submitted_at": now,
            "submission_notes": payload.notes or "",
            "review_reasons": [],
            "calculation": recomputed.get("calculation"),
            "journal_preview": recomputed.get("journal_preview"),
            "bank_account_id": recomputed.get("bank_account_id"),
            "bank_account_name": recomputed.get("bank_account_name"),
            "bank_account_type": recomputed.get("bank_account_type"),
            "updated_at": now,
        }
        result = await db.accounting_settlements_v2.update_one(
            {
                "id": draft_id,
                "user_id": owner_id,
                "status": {"$in": list(MATCHABLE_STATUSES)},
            },
            {"$set": update},
        )
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(409, "تغيرت حالة المسودة قبل اعتماد المطابقة")
        await _audit_state(
            db,
            owner_id=owner_id,
            actor=actor,
            draft=current,
            action="match_accounting_settlement",
            before_status=str(current.get("status") or "draft"),
            after_status="matched",
            notes=payload.notes or "",
        )
        return {**recomputed, **update}

    @router.post("/accounting-module/settlements/drafts/{draft_id}/review")
    async def review_matched_settlement(
        draft_id: str,
        payload: DraftActionIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(db, user, "accounting.settlements.post")
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") not in REVIEWABLE_STATUSES:
            raise HTTPException(409, "التسوية لم تصل إلى حالة المطابقة")
        recomputed = await _recomputed_draft(db, owner_id=owner_id, draft=current)
        reasons = _combined_reasons(recomputed)
        if _blocking(reasons):
            await db.accounting_settlements_v2.update_one(
                {"id": draft_id, "user_id": owner_id},
                {"$set": {
                    "status": "needs_review",
                    "workflow_state": "needs_review",
                    "review_reasons": reasons,
                    "updated_at": _now(),
                }},
            )
            raise HTTPException(409, "ظهرت فروقات جديدة؛ أُعيدت التسوية للمعالجة")
        now = _now()
        update = {
            "status": "reviewed",
            "workflow_state": "reviewed",
            "reviewed_by": actor.get("id"),
            "reviewed_by_name": actor.get("name") or actor.get("email"),
            "reviewed_at": now,
            "review_notes": payload.notes or "",
            "review_reasons": [],
            "calculation": recomputed.get("calculation"),
            "journal_preview": recomputed.get("journal_preview"),
            "updated_at": now,
        }
        result = await db.accounting_settlements_v2.update_one(
            {
                "id": draft_id,
                "user_id": owner_id,
                "status": {"$in": list(REVIEWABLE_STATUSES)},
            },
            {"$set": update},
        )
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(409, "تغيرت حالة التسوية قبل حفظ المراجعة")
        await _audit_state(
            db,
            owner_id=owner_id,
            actor=actor,
            draft=current,
            action="review_accounting_settlement",
            before_status=str(current.get("status") or "matched"),
            after_status="reviewed",
            notes=payload.notes or "",
        )
        return {**recomputed, **update}

    @router.post("/accounting-module/settlements/drafts/{draft_id}/reject")
    async def reject_settlement(
        draft_id: str,
        payload: DraftRejectIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(db, user, "accounting.settlements.post")
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") not in REJECTABLE_STATUSES:
            raise HTTPException(409, "لا يمكن إعادة التسوية في حالتها الحالية")
        now = _now()
        update = {
            "status": "rejected",
            "workflow_state": "rejected",
            "rejected_by": actor.get("id"),
            "rejected_by_name": actor.get("name") or actor.get("email"),
            "rejected_at": now,
            "rejection_reason": payload.reason,
            "updated_at": now,
        }
        result = await db.accounting_settlements_v2.update_one(
            {
                "id": draft_id,
                "user_id": owner_id,
                "status": {"$in": list(REJECTABLE_STATUSES)},
            },
            {"$set": update},
        )
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(409, "تغيرت حالة التسوية قبل إعادتها")
        await _audit_state(
            db,
            owner_id=owner_id,
            actor=actor,
            draft=current,
            action="reject_accounting_settlement",
            before_status=str(current.get("status") or "matched"),
            after_status="rejected",
            notes=payload.reason,
        )
        return {**current, **update}

    @router.post("/accounting-module/settlements/drafts/{draft_id}/post")
    async def post_settlement(
        draft_id: str,
        payload: DraftActionIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(db, user, "accounting.settlements.post")
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") != "reviewed":
            raise HTTPException(409, "يجب مراجعة التسوية قبل ترحيلها")
        recomputed = await _recomputed_draft(db, owner_id=owner_id, draft=current)
        reasons = _combined_reasons(recomputed)
        if _blocking(reasons):
            await db.accounting_settlements_v2.update_one(
                {"id": draft_id, "user_id": owner_id},
                {"$set": {
                    "status": "needs_review",
                    "workflow_state": "needs_review",
                    "review_reasons": reasons,
                    "updated_at": _now(),
                }},
            )
            raise HTTPException(409, "لا يمكن الترحيل بعد ظهور فرق جديد")

        claim = await db.accounting_settlements_v2.update_one(
            {"id": draft_id, "user_id": owner_id, "status": "reviewed"},
            {"$set": {
                "status": "posting",
                "workflow_state": "posting",
                "posting_by": actor.get("id"),
                "posting_at": _now(),
            }},
        )
        if getattr(claim, "matched_count", 0) != 1:
            raise HTTPException(409, "بدأ مستخدم آخر ترحيل هذه التسوية")
        try:
            posted = await post_reviewed_settlement(
                db,
                owner_id=owner_id,
                actor=actor,
                draft={**recomputed, "status": "reviewed", "review_reasons": []},
            )
        except Exception as exc:
            await db.accounting_settlements_v2.update_one(
                {"id": draft_id, "user_id": owner_id, "status": "posting"},
                {"$set": {
                    "status": "reviewed",
                    "workflow_state": "reviewed",
                    "last_post_error": str(getattr(exc, "detail", exc))[:1000],
                    "updated_at": _now(),
                }},
            )
            raise

        now = _now()
        update = {
            "status": "posted",
            "workflow_state": "posted",
            "posted_by": actor.get("id"),
            "posted_by_name": actor.get("name") or actor.get("email"),
            "posted_at": now,
            "post_notes": payload.notes or "",
            "ledger_txn_group_id": posted["txn_group_id"],
            "bank_snapshot": posted["bank_snapshot"],
            "bank_transaction_snapshot_at_post": current.get("bank_transaction_snapshot"),
            "posted_preview": posted["preview"],
            "updated_at": now,
            "last_post_error": None,
        }
        await db.accounting_settlements_v2.update_one(
            {"id": draft_id, "user_id": owner_id, "status": "posting"},
            {"$set": update},
        )
        await _audit_state(
            db,
            owner_id=owner_id,
            actor=actor,
            draft=current,
            action="post_accounting_settlement_lifecycle",
            before_status="reviewed",
            after_status="posted",
            notes=payload.notes or "",
        )
        return {**recomputed, **update, "ledger": posted}

    return router


__all__ = [
    "MATCHABLE_STATUSES",
    "REVIEWABLE_STATUSES",
    "REJECTABLE_STATUSES",
    "install_accounting_settlement_lifecycle_routes",
]
