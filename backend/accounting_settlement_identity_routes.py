"""Identity-safe statement-reference updates for P01 settlements.

Changing a statement reference changes the accounting idempotency identity.
This focused route validates that identity before the normal draft PATCH
recomputes the amounts and journal preview.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from accounting_module_contract import (
    accounting_owner_id,
    require_accounting_permission,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_settlement_service import settlement_idempotency_key
from ledger_core import write_audit

EDITABLE_STATUSES = ("draft", "needs_review", "rejected")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SettlementIdentityUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement_reference: str = Field(default="", max_length=180)
    reason: str = Field(default="statement_reference_update", min_length=3, max_length=1000)


def install_accounting_settlement_identity_routes(router, db, current_user):
    @router.patch("/accounting-module/settlements/drafts/{draft_id}/identity")
    async def update_settlement_identity(
        draft_id: str,
        payload: SettlementIdentityUpdateIn,
        user: dict[str, Any] = Depends(current_user),
    ):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, "accounting.drafts.create")
        owner_id = accounting_owner_id(actor)
        if not owner_id:
            raise HTTPException(403, "لا يوجد مالك بيانات محاسبية مرتبط بالمستخدم")

        current = await db.accounting_settlements_v2.find_one(
            {
                "id": draft_id,
                "user_id": owner_id,
                "status": {"$in": list(EDITABLE_STATUSES)},
            },
            {"_id": 0},
        )
        if not current:
            raise HTTPException(409, "المسودة غير موجودة أو لم تعد قابلة لتعديل المرجع")

        reference = str(payload.statement_reference or "").strip()
        next_key = settlement_idempotency_key(
            user_id=owner_id,
            provider=current.get("provider") or "",
            statement_reference=reference,
            source_hash=current.get("source_file_hash") or "",
        )
        duplicate = await db.accounting_settlements_v2.find_one(
            {
                "user_id": owner_id,
                "idempotency_key": next_key,
                "id": {"$ne": draft_id},
            },
            {"_id": 0, "id": 1, "status": 1, "statement_reference": 1},
        )
        if duplicate:
            raise HTTPException(
                409,
                {
                    "code": "duplicate_settlement_statement_reference",
                    "message": "مرجع الكشف مستخدم مسبقًا في تسوية أخرى",
                    "duplicate_draft_id": duplicate.get("id"),
                    "duplicate_status": duplicate.get("status"),
                },
            )

        now = _now()
        revision = {
            "version": int(current.get("version") or 1) + 1,
            "actor_id": actor.get("id"),
            "actor_name": actor.get("name") or actor.get("email"),
            "at": now,
            "reason": payload.reason,
            "before": {
                "statement_reference": current.get("statement_reference") or "",
                "idempotency_key": current.get("idempotency_key"),
            },
            "after": {
                "statement_reference": reference,
                "idempotency_key": next_key,
            },
        }
        try:
            result = await db.accounting_settlements_v2.update_one(
                {
                    "id": draft_id,
                    "user_id": owner_id,
                    "status": {"$in": list(EDITABLE_STATUSES)},
                },
                {
                    "$set": {
                        "statement_reference": reference,
                        "idempotency_key": next_key,
                        "version": revision["version"],
                        "updated_by": actor.get("id"),
                        "updated_at": now,
                    },
                    "$push": {"revision_log": revision},
                },
            )
        except DuplicateKeyError as exc:
            raise HTTPException(
                409,
                {
                    "code": "duplicate_settlement_statement_reference",
                    "message": "مرجع الكشف مستخدم مسبقًا في تسوية أخرى",
                },
            ) from exc
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(409, "تغيرت حالة المسودة قبل حفظ المرجع")

        await write_audit(
            db,
            user_id=owner_id,
            actor_id=str(actor.get("id") or ""),
            actor_name=actor.get("name") or actor.get("email") or "",
            entity_type="payment_gateway",
            entity_id=current.get("provider") or "",
            action="update_settlement_statement_identity",
            reason_code="settlement_identity_update",
            notes=payload.reason,
            before_state=revision["before"],
            after_state={
                **revision["after"],
                "draft_id": draft_id,
            },
        )
        return {
            "id": draft_id,
            "statement_reference": reference,
            "idempotency_key": next_key,
            "version": revision["version"],
            "updated_at": now,
        }

    return router


__all__ = [
    "SettlementIdentityUpdateIn",
    "install_accounting_settlement_identity_routes",
]
