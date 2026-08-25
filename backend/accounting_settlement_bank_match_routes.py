"""Optional bank-movement matching for P01 provider settlements.

The selected bank transaction is evidence that the provider net reached the
bank. It never posts a second journal and never changes a balance. A selected
movement with a different amount blocks review/posting until corrected.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_module_status_routes import fresh_accounting_user
from ledger_core import write_audit

EDITABLE_STATUSES = ("draft", "needs_review", "rejected")
BANK_MATCH_BLOCKING_CODES = frozenset({
    "bank_movement_missing",
    "bank_movement_account_mismatch",
    "bank_movement_direction_invalid",
    "bank_movement_difference",
})
_INDEX_READY: set[int] = set()
_LEGACY_BANK_MATCH_INDEX = "uniq_accounting_settlement_bank_transaction_v2"
_BANK_MATCH_INDEX = "uniq_accounting_settlement_bank_transaction_v3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money(value: Any) -> float:
    return round(float(value or 0), 2)


def _date_only(value: Any) -> date | None:
    raw = str(value or "").strip()[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def bank_match_review_reasons(draft: dict[str, Any]) -> list[dict[str, str]]:
    transaction_id = str(draft.get("bank_transaction_id") or "").strip()
    if not transaction_id:
        return []
    snapshot = draft.get("bank_transaction_snapshot") or {}
    reasons: list[dict[str, str]] = []
    if not snapshot:
        reasons.append({
            "code": "bank_movement_missing",
            "message": "حركة البنك المختارة لم تعد متاحة",
        })
        return reasons
    if str(snapshot.get("account_id") or "") != str(draft.get("bank_account_id") or ""):
        reasons.append({
            "code": "bank_movement_account_mismatch",
            "message": "حركة البنك لا تتبع بنك التسوية المختار",
        })
    if str(snapshot.get("direction") or "").lower() != "in":
        reasons.append({
            "code": "bank_movement_direction_invalid",
            "message": "حركة البنك المختارة ليست حركة واردة",
        })
    expected = _money((draft.get("amounts") or {}).get("reported_net"))
    actual = _money(snapshot.get("amount"))
    difference = _money(actual - expected)
    if abs(difference) > 0.01:
        reasons.append({
            "code": "bank_movement_difference",
            "message": (
                f"فرق حركة البنك عن صافي الكشف {difference:.2f} SAR "
                f"(البنك {actual:.2f}، الكشف {expected:.2f})"
            ),
        })
    return reasons


class BankMatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_transaction_id: Optional[str] = Field(default=None, max_length=120)
    confirmed: bool = False
    notes: Optional[str] = Field(default=None, max_length=1000)


async def _scope(db, user: dict[str, Any], permission: str) -> tuple[dict[str, Any], str]:
    actor = await fresh_accounting_user(db, user)
    require_accounting_permission(actor, permission)
    owner_id = accounting_owner_id(actor)
    if not owner_id:
        raise HTTPException(403, "لا يوجد مالك بيانات محاسبية مرتبط بالمستخدم")
    return actor, owner_id


async def _draft(db, owner_id: str, draft_id: str) -> dict[str, Any]:
    doc = await db.accounting_settlements_v2.find_one(
        {"id": draft_id, "user_id": owner_id},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(404, "مسودة التسوية غير موجودة")
    return doc


async def _ensure_index(db) -> None:
    key = id(db)
    if key in _INDEX_READY:
        return
    try:
        await db.accounting_settlements_v2.create_index(
            [("user_id", 1), ("bank_transaction_id", 1)],
            unique=True,
            partialFilterExpression={"bank_transaction_id": {"$type": "string"}},
            name=_BANK_MATCH_INDEX,
        )
    except Exception:
        return
    try:
        await db.accounting_settlements_v2.drop_index(_LEGACY_BANK_MATCH_INDEX)
    except Exception as exc:
        # The v2 sparse compound index indexed every settlement because
        # ``user_id`` is always present. Consequently, clearing a match by
        # writing ``bank_transaction_id=null`` collided with another unlinked
        # draft. Retire that index after the correct partial index exists.
        if (
            getattr(exc, "code", None) != 27
            and "index not found" not in str(exc).lower()
        ):
            return
    _INDEX_READY.add(key)


async def _transaction(db, owner_id: str, transaction_id: str) -> dict[str, Any] | None:
    return await db.account_transactions.find_one(
        {"id": transaction_id, "user_id": owner_id},
        {
            "_id": 0,
            "id": 1,
            "account_id": 1,
            "amount": 1,
            "direction": 1,
            "transaction_date": 1,
            "created_at": 1,
            "description": 1,
            "reference": 1,
            "status": 1,
        },
    )


def _candidate_view(transaction: dict[str, Any], expected: float) -> dict[str, Any]:
    amount = _money(transaction.get("amount"))
    difference = _money(amount - expected)
    return {
        **transaction,
        "amount": amount,
        "expected_amount": expected,
        "difference": difference,
        "exact_amount_match": abs(difference) <= 0.01,
    }


def install_accounting_settlement_bank_match_routes(router, db, current_user):
    @router.get("/accounting-module/settlements/drafts/{draft_id}/bank-candidates")
    async def bank_candidates(
        draft_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        user: dict = Depends(current_user),
    ):
        _actor, owner_id = await _scope(db, user, "accounting.settlements.view")
        draft = await _draft(db, owner_id, draft_id)
        bank_id = str(draft.get("bank_account_id") or "").strip()
        if not bank_id:
            raise HTTPException(409, "اختر بنك التسوية قبل البحث عن حركة البنك")

        query: dict[str, Any] = {
            "user_id": owner_id,
            "account_id": bank_id,
            "direction": "in",
            "status": {"$nin": ["reversed", "cancelled", "deleted"]},
        }
        anchor = (
            _date_only(draft.get("statement_date"))
            or _date_only(draft.get("period_to"))
        )
        if anchor:
            query["transaction_date"] = {
                "$gte": (anchor - timedelta(days=14)).isoformat(),
                "$lte": (anchor + timedelta(days=14)).isoformat(),
            }

        transactions = await db.account_transactions.find(
            query,
            {
                "_id": 0,
                "id": 1,
                "account_id": 1,
                "amount": 1,
                "direction": 1,
                "transaction_date": 1,
                "created_at": 1,
                "description": 1,
                "reference": 1,
                "status": 1,
            },
        ).sort("transaction_date", -1).to_list(500)

        ids = [row.get("id") for row in transactions if row.get("id")]
        used: set[str] = set()
        if ids:
            async for row in db.accounting_settlements_v2.find(
                {
                    "user_id": owner_id,
                    "bank_transaction_id": {"$in": ids},
                    "id": {"$ne": draft_id},
                },
                {"_id": 0, "bank_transaction_id": 1},
            ):
                if row.get("bank_transaction_id"):
                    used.add(row["bank_transaction_id"])

        expected = _money((draft.get("amounts") or {}).get("reported_net"))
        items = [
            _candidate_view(row, expected)
            for row in transactions
            if row.get("id") not in used
        ]
        items.sort(key=lambda row: (
            abs(float(row.get("difference") or 0)),
            str(row.get("transaction_date") or ""),
        ))
        items = items[:limit]
        return {
            "draft_id": draft_id,
            "bank_account_id": bank_id,
            "expected_amount": expected,
            "selected_bank_transaction_id": draft.get("bank_transaction_id"),
            "items": items,
            "count": len(items),
            "matching_is_optional_when_no_bank_movement_exists": True,
        }

    @router.put("/accounting-module/settlements/drafts/{draft_id}/bank-match")
    async def save_bank_match(
        draft_id: str,
        payload: BankMatchIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(db, user, "accounting.drafts.create")
        draft = await _draft(db, owner_id, draft_id)
        if draft.get("status") not in EDITABLE_STATUSES:
            raise HTTPException(409, "لا يمكن تغيير مطابقة البنك بعد إرسال المسودة للمراجعة")
        transaction_id = str(payload.bank_transaction_id or "").strip()
        before = {
            "bank_transaction_id": draft.get("bank_transaction_id"),
            "bank_transaction_snapshot": draft.get("bank_transaction_snapshot"),
        }
        now = _now()

        if not transaction_id:
            remaining_reasons = [
                reason
                for reason in (draft.get("review_reasons") or [])
                if str(reason.get("code") or "") not in BANK_MATCH_BLOCKING_CODES
            ]
            update = {
                "bank_match_notes": "",
                "review_reasons": remaining_reasons,
                "status": "needs_review" if remaining_reasons else "draft",
                "updated_by": actor.get("id"),
                "updated_at": now,
            }
            cleared = {
                "bank_transaction_id": "",
                "bank_transaction_snapshot": "",
                "bank_transaction_difference": "",
                "bank_matched_by": "",
                "bank_matched_by_name": "",
                "bank_matched_at": "",
            }
        else:
            if not payload.confirmed:
                raise HTTPException(400, "يجب تأكيد مطابقة حركة البنك قبل الحفظ")
            transaction = await _transaction(db, owner_id, transaction_id)
            if not transaction:
                raise HTTPException(404, "حركة البنك غير موجودة")
            if str(transaction.get("account_id") or "") != str(draft.get("bank_account_id") or ""):
                raise HTTPException(409, "حركة البنك لا تتبع بنك التسوية المختار")
            if str(transaction.get("direction") or "").lower() != "in":
                raise HTTPException(409, "حركة البنك المختارة ليست واردة")
            used = await db.accounting_settlements_v2.find_one(
                {
                    "user_id": owner_id,
                    "bank_transaction_id": transaction_id,
                    "id": {"$ne": draft_id},
                },
                {"_id": 0, "id": 1, "status": 1},
            )
            if used:
                raise HTTPException(
                    409,
                    {
                        "code": "bank_transaction_already_matched",
                        "message": "حركة البنك مرتبطة بتسوية أخرى",
                        "settlement_draft_id": used.get("id"),
                        "settlement_status": used.get("status"),
                    },
                )
            expected = _money((draft.get("amounts") or {}).get("reported_net"))
            snapshot = _candidate_view(transaction, expected)
            difference = snapshot["difference"]
            update = {
                "bank_transaction_id": transaction_id,
                "bank_transaction_snapshot": snapshot,
                "bank_transaction_difference": difference,
                "bank_matched_by": actor.get("id"),
                "bank_matched_by_name": actor.get("name") or actor.get("email"),
                "bank_matched_at": now,
                "bank_match_notes": payload.notes or "",
                "status": "needs_review" if abs(difference) > 0.01 else "draft",
                "updated_by": actor.get("id"),
                "updated_at": now,
            }
            cleared = {}

        await _ensure_index(db)
        mongo_update: dict[str, Any] = {"$set": update}
        if cleared:
            mongo_update["$unset"] = cleared
        try:
            result = await db.accounting_settlements_v2.update_one(
                {
                    "id": draft_id,
                    "user_id": owner_id,
                    "status": {"$in": list(EDITABLE_STATUSES)},
                },
                mongo_update,
            )
        except DuplicateKeyError as exc:
            raise HTTPException(409, "حركة البنك مرتبطة بتسوية أخرى") from exc
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(409, "تغيرت حالة المسودة قبل حفظ مطابقة البنك")

        await write_audit(
            db,
            user_id=owner_id,
            actor_id=str(actor.get("id") or ""),
            actor_name=actor.get("name") or actor.get("email") or "",
            entity_type="payment_gateway",
            entity_id=draft.get("provider") or "",
            action="match_settlement_bank_movement",
            reason_code="settlement_bank_match",
            notes=payload.notes or "",
            before_state=before,
            after_state={
                "draft_id": draft_id,
                **update,
                **({field: None for field in cleared} if cleared else {}),
            },
        )
        refreshed = await _draft(db, owner_id, draft_id)
        return {
            **refreshed,
            "bank_match_review_reasons": bank_match_review_reasons(refreshed),
        }

    return router


__all__ = [
    "BANK_MATCH_BLOCKING_CODES",
    "BankMatchIn",
    "bank_match_review_reasons",
    "install_accounting_settlement_bank_match_routes",
]
