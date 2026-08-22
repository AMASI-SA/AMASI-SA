"""Owner-only Production-safe controls for Mezan attribution ledger refresh.

These routes intentionally operate only on Mezan's internal Mongo database.
They never call Salla, ad providers, Qoyod, product catalogs, pricing, or stock
writes. Backfill is idempotent because the attribution ledger upserts by
``(user_id, order_key)``.

The Production backfill is deliberately bounded and checkpointed. A request may
process at most 50 orders and then returns; subsequent requests resume after the
last Mongo ``_id``. This prevents one long HTTP request from monopolising the
backend while still allowing the whole historical order set to be refreshed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from mezan_attribution_ledger_sync import safe_sync_order_to_attribution_ledger
from mezan_attribution_order_ledger import LEDGER_COLLECTION
from mezan_attribution_profit_bridge import build_attribution_profit_bridge
from warehouse_location_routes import _merchant_user_id, _text

BACKFILL_STATE_COLLECTION = "mezan_attribution_backfill_state_v1"
BACKFILL_BATCH_DEFAULT = 25
BACKFILL_BATCH_MAX = 50
BACKFILL_LEASE_SECONDS = 120


class AttributionBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["تشغيل"]
    limit: int = Field(default=BACKFILL_BATCH_DEFAULT, ge=1, le=BACKFILL_BATCH_MAX)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job_id(user_id: str) -> str:
    return f"attribution-backfill:{user_id}"


def require_owner(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_required"},
        )
    role = _text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "owner_required",
            "message": "تحديث دفتر الإسناد متاح لمالك المتجر فقط.",
        },
    )


async def _load_backfill_state(db: Any, *, user_id: str) -> dict[str, Any] | None:
    return await db[BACKFILL_STATE_COLLECTION].find_one(
        {"_id": _job_id(user_id), "user_id": user_id},
        {"_id": 0, "cursor_id": 0},
    )


async def build_backfill_preview(db: Any, *, user_id: str) -> dict[str, Any]:
    """Return counts and checkpoint state only; never writes or calls external systems."""
    unified = int(await db.unified_orders.count_documents({"user_id": user_id}))
    ledger = int(await db[LEDGER_COLLECTION].count_documents({"user_id": user_id}))
    state = await _load_backfill_state(db, user_id=user_id)
    return {
        "unified_orders": unified,
        "ledger_rows": ledger,
        "estimated_missing_rows": max(0, unified - ledger),
        "backfill_state": state,
        "batch_default": BACKFILL_BATCH_DEFAULT,
        "batch_max": BACKFILL_BATCH_MAX,
        "external_writes": False,
        "read_only": True,
    }


async def run_bounded_backfill_batch(
    db: Any,
    *,
    user_id: str,
    limit: int = BACKFILL_BATCH_DEFAULT,
) -> dict[str, Any]:
    """Run one small, resumable batch and return immediately after it completes."""
    batch_size = max(1, min(BACKFILL_BATCH_MAX, int(limit)))
    state_collection = db[BACKFILL_STATE_COLLECTION]
    now = _now()
    lease_until = now + timedelta(seconds=BACKFILL_LEASE_SECONDS)
    job_id = _job_id(user_id)

    # Atomic lease: if another request is active, the duplicate-key upsert path
    # fails closed instead of starting a second concurrent Production backfill.
    try:
        state = await state_collection.find_one_and_update(
            {
                "_id": job_id,
                "$or": [
                    {"running": {"$ne": True}},
                    {"lease_until": {"$lte": now}},
                    {"lease_until": None},
                ],
            },
            {
                "$set": {
                    "user_id": user_id,
                    "running": True,
                    "lease_until": lease_until,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                    "cursor_id": None,
                    "scanned": 0,
                    "synced": 0,
                    "failed": 0,
                    "profit_known": 0,
                    "decision_safe": 0,
                    "completed": False,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "backfill_already_running", "message": "دفعة تحديث دفتر الإسناد تعمل الآن."},
        ) from exc

    if not state:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "backfill_already_running", "message": "دفعة تحديث دفتر الإسناد تعمل الآن."},
        )

    cursor_id = state.get("cursor_id")
    query: dict[str, Any] = {"user_id": user_id}
    if cursor_id is not None:
        query["_id"] = {"$gt": cursor_id}

    orders_cursor = db.unified_orders.find(query).sort("_id", 1).limit(batch_size)
    orders = await orders_cursor.to_list(length=batch_size)

    scanned = 0
    synced = 0
    failed = 0
    profit_known = 0
    decision_safe = 0
    failures: list[dict[str, Any]] = []
    last_id = cursor_id

    try:
        for order in orders:
            scanned += 1
            last_id = order.get("_id", last_id)
            result = await safe_sync_order_to_attribution_ledger(
                db,
                user_id=user_id,
                order=order,
            )
            if result.get("synced") is True:
                synced += 1
                profit_known += int(result.get("profit_known") is True)
                decision_safe += int(result.get("decision_safe") is True)
            else:
                failed += 1
                if len(failures) < 20:
                    failures.append({
                        "order_number": order.get("order_number") or order.get("order_id"),
                        "reason": result.get("reason") or "ledger_sync_failed",
                        "error_type": result.get("error_type"),
                    })

        completed = len(orders) < batch_size
        update: dict[str, Any] = {
            "$set": {
                "running": False,
                "lease_until": None,
                "updated_at": _now(),
                "cursor_id": last_id,
                "completed": completed,
                "last_batch": {
                    "scanned": scanned,
                    "synced": synced,
                    "failed": failed,
                    "profit_known": profit_known,
                    "decision_safe": decision_safe,
                    "failures": failures,
                },
            },
            "$inc": {
                "scanned": scanned,
                "synced": synced,
                "failed": failed,
                "profit_known": profit_known,
                "decision_safe": decision_safe,
            },
        }
        await state_collection.update_one({"_id": job_id, "user_id": user_id}, update)
    except Exception:
        await state_collection.update_one(
            {"_id": job_id, "user_id": user_id},
            {"$set": {"running": False, "lease_until": None, "updated_at": _now()}},
        )
        raise

    public_state = await _load_backfill_state(db, user_id=user_id)
    return {
        "contract_version": "mezan_attribution_bounded_backfill_v1",
        "batch_size": batch_size,
        "batch": {
            "scanned": scanned,
            "synced": synced,
            "failed": failed,
            "profit_known": profit_known,
            "decision_safe": decision_safe,
            "failures": failures,
        },
        "state": public_state,
        "completed": bool(public_state and public_state.get("completed") is True),
        "external_writes": False,
    }


def make_mezan_attribution_owner_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/mezan-attribution-v1",
        tags=["Mezan Attribution Owner Controls"],
    )

    @router.get("/backfill-preview")
    async def backfill_preview(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = require_owner(user)
        user_id = _merchant_user_id(actor)
        preview = await build_backfill_preview(db, user_id=user_id)
        return {"ok": True, "user_id": user_id, **preview}

    @router.get("/backfill-status")
    async def backfill_status(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = require_owner(user)
        user_id = _merchant_user_id(actor)
        state = await _load_backfill_state(db, user_id=user_id)
        return {"ok": True, "user_id": user_id, "state": state, "external_writes": False}

    @router.post("/backfill")
    async def run_backfill(
        payload: AttributionBackfillRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = require_owner(user)
        user_id = _merchant_user_id(actor)
        result = await run_bounded_backfill_batch(
            db,
            user_id=user_id,
            limit=payload.limit,
        )
        return {
            "ok": True,
            "user_id": user_id,
            "backfill": result,
            "external_writes": False,
        }

    @router.get("/report")
    async def attribution_profit_report(
        from_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(default=20_000, ge=1, le=50_000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = require_owner(user)
        user_id = _merchant_user_id(actor)
        report = await build_attribution_profit_bridge(
            db,
            user_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
        return {
            "ok": True,
            "user_id": user_id,
            "report": report,
            "external_writes": False,
        }

    return router


__all__ = [
    "AttributionBackfillRequest",
    "BACKFILL_BATCH_DEFAULT",
    "BACKFILL_BATCH_MAX",
    "BACKFILL_STATE_COLLECTION",
    "build_backfill_preview",
    "make_mezan_attribution_owner_router",
    "require_owner",
    "run_bounded_backfill_batch",
]
