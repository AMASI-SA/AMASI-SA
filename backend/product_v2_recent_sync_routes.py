"""Lightweight automatic sync for the newest Salla products.

This endpoint intentionally reads only Salla's first products page and upserts it
into Product V2. It never archives products and never traverses the whole
catalogue. The full manual sync remains the authority for complete reconciliation.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from product_cost_revision import bump_product_cost_revision, salla_cost_fingerprint
from product_v2_routes import CHANGE_LOG, PRODUCTS, _now, ensure_product_v2_indexes, normalize_salla_product
from salla_integration.service import SallaError, call_salla

RECENT_SYNC_LIMIT = 30
RECENT_SYNC_STATE = "mezan_product_recent_sync_state_v2"
AUTO_SYNC_INTERVAL_SECONDS = 60 * 60
RETRY_INTERVAL_SECONDS = 60
SYNC_TIMEOUT_SECONDS = 90
SYNC_LEASE_SECONDS = 120
STATE_WRITE_TIMEOUT_SECONDS = 5


class RecentSyncLeaseLost(RuntimeError):
    """A superseded request cannot change catalogue costs or their revision."""


async def _mark_cost_revision_pending(db: Any, user_id: str, token: str) -> None:
    updated = await db[RECENT_SYNC_STATE].update_one(
        {"_id": user_id, "lease_token": token, "lease_until": {"$gt": _now()}},
        {"$set": {"cost_revision_pending": True}},
    )
    if updated.matched_count != 1:
        raise RecentSyncLeaseLost()


async def _flush_pending_cost_revision(db: Any, user_id: str, token: str) -> None:
    state = db[RECENT_SYNC_STATE]
    owned = await state.find_one({"_id": user_id, "lease_token": token})
    if not owned:
        raise RecentSyncLeaseLost()
    if owned.get("cost_revision_pending") is not True:
        return
    await bump_product_cost_revision(db, user_id)
    # Repeating an already-confirmed revision bump after a lost acknowledgement
    # is harmless. Clearing this durable intent before the bump would not be.
    cleared = await state.update_one(
        {"_id": user_id, "lease_token": token},
        {"$unset": {"cost_revision_pending": ""}},
    )
    if cleared.matched_count != 1:
        raise RecentSyncLeaseLost()


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _skipped_result(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    last_success = _utc(state.get("last_success_at"))
    next_sync = last_success + timedelta(seconds=AUTO_SYNC_INTERVAL_SECONDS) if last_success else None
    lease_until = _utc(state.get("lease_until"))
    retry_after = _utc(state.get("retry_after"))
    if lease_until and lease_until > now:
        reason, next_retry = "in_progress", lease_until
    elif state.get("cost_revision_pending") is True:
        reason, next_retry = "retry_later", retry_after or now + timedelta(seconds=RETRY_INTERVAL_SECONDS)
    elif next_sync and next_sync > now:
        reason, next_retry = "up_to_date", next_sync
    else:
        reason, next_retry = "retry_later", retry_after or now + timedelta(seconds=RETRY_INTERVAL_SECONDS)
    return {
        "ok": reason == "up_to_date",
        "mode": "recent_first_page",
        "skipped": True,
        "reason": reason,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "seen_products": 0,
        "errors_count": 0,
        "synced_at": last_success.isoformat() if last_success else None,
        "next_sync_at": next_sync.isoformat() if next_sync else None,
        "next_retry_at": next_retry.isoformat(),
    }


async def sync_recent_products(db: Any, *, user_id: str, force: bool = False) -> dict[str, Any]:
    """Admit one bounded recent sync across tabs and application workers.

    A deliberate recovery request may bypass the hourly cadence, but cannot
    overlap an active provider operation. No browser-provided owner is trusted.
    The operation timeout is shorter than the durable lease, leaving time to
    invalidate changed costs and release the matching token after cancellation.
    """
    user_id = str(user_id)
    now = _now()
    selector: dict[str, Any] = {
        "_id": user_id,
        "$and": [{"$or": [{"lease_until": {"$lte": now}}, {"lease_until": {"$exists": False}}]}],
    }
    if not force:
        selector["$and"].extend([
            {"$or": [
                {"last_success_at": {"$lte": now - timedelta(seconds=AUTO_SYNC_INTERVAL_SECONDS)}},
                {"last_success_at": {"$exists": False}},
                {"cost_revision_pending": True},
            ]},
            {"$or": [{"retry_after": {"$lte": now}}, {"retry_after": {"$exists": False}}]},
        ])
    token = uuid.uuid4().hex
    state = db[RECENT_SYNC_STATE]
    try:
        claim = await state.find_one_and_update(
            selector,
            {"$set": {
                "user_id": user_id,
                "lease_token": token,
                "lease_until": now + timedelta(seconds=SYNC_LEASE_SECONDS),
                "last_attempt_at": now,
            }},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        claim = None
    if not claim or claim.get("lease_token") != token:
        return _skipped_result(await state.find_one({"_id": user_id}) or {}, now)

    successful = False
    result: dict[str, Any] = {}
    try:
        # A slow admission round trip must not leave a fresh 90-second task
        # running beyond a lease whose clock started before that round trip.
        lease_remaining = (_utc(claim["lease_until"]) - _now()).total_seconds()
        operation_timeout = min(SYNC_TIMEOUT_SECONDS, lease_remaining - 2 * STATE_WRITE_TIMEOUT_SECONDS)
        if operation_timeout <= 0:
            raise HTTPException(status_code=503, detail={"code": "salla_recent_products_sync_lease_expired"})
        result = await asyncio.wait_for(
            _perform_recent_sync(db, user_id=user_id, lease_token=token), timeout=operation_timeout,
        )
        successful = result.get("ok") is True
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail={"code": "salla_recent_products_sync_timeout"}) from exc
    except RecentSyncLeaseLost as exc:
        raise HTTPException(status_code=409, detail={"code": "salla_recent_products_sync_superseded"}) from exc
    finally:
        finished_at = _now()
        values: dict[str, Any] = {
            "lease_until": finished_at,
            "retry_after": finished_at + timedelta(seconds=0 if successful else RETRY_INTERVAL_SECONDS),
        }
        if successful:
            values["last_success_at"] = finished_at
        completion = await asyncio.wait_for(
            state.update_one(
                {"_id": user_id, "lease_token": token,
                 **({"cost_revision_pending": {"$ne": True}} if successful else {})},
                {"$set": values, "$unset": {"lease_token": ""}},
            ),
            timeout=STATE_WRITE_TIMEOUT_SECONDS,
        )
    if completion.matched_count != 1:
        raise HTTPException(status_code=409, detail={"code": "salla_recent_products_sync_superseded"})
    return {
        **result,
        "skipped": False,
        "next_sync_at": (
            finished_at + timedelta(seconds=AUTO_SYNC_INTERVAL_SECONDS)
        ).isoformat() if successful else None,
        "next_retry_at": (
            finished_at + timedelta(seconds=AUTO_SYNC_INTERVAL_SECONDS if successful else RETRY_INTERVAL_SECONDS)
        ).isoformat(),
    }


async def _perform_recent_sync(db: Any, *, user_id: str, lease_token: str) -> dict[str, Any]:
    # Recover an invalidation whose previous attempt wrote catalogue costs but
    # failed before confirming the revision bump, even if Salla is now offline.
    await asyncio.wait_for(
        _flush_pending_cost_revision(db, user_id, lease_token), timeout=STATE_WRITE_TIMEOUT_SECONDS,
    )
    await ensure_product_v2_indexes(db)
    user_id = str(user_id)
    try:
        response = await call_salla(
            db,
            user_id,
            "GET",
            "/products",
            params={"page": 1, "per_page": RECENT_SYNC_LIMIT},
        )
    except SallaError as exc:
        raise HTTPException(
            status_code=exc.status_code if exc.status_code != 200 else 400,
            detail={"code": "salla_recent_products_sync_failed", "message": str(exc), "needs_reauth": exc.needs_reauth},
        ) from exc

    rows = response.get("data") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail={"code": "salla_recent_products_invalid_response"})

    created = updated = unchanged = errors = 0
    synced_at = _now()
    try:
        for raw in rows:
            if not isinstance(raw, dict):
                errors += 1
                continue
            try:
                doc = normalize_salla_product(raw, user_id=user_id, synced_at=synced_at)
                selector = {"user_id": user_id, "salla_product_id": doc["salla_product_id"]}
                existing = await db[PRODUCTS].find_one(
                    selector,
                    {"_id": 0, "source_revision": 1, "cost_price_from_salla": 1, "variants": 1},
                )
                if existing and existing.get("source_revision") == doc.get("source_revision"):
                    unchanged += 1
                    await db[PRODUCTS].update_one(selector, {"$set": {"last_synced_at": synced_at, "archived": False}})
                    continue
                # Durable before the write: cancellation or a failed revision
                # update must remain recoverable on an unchanged-product retry.
                if existing is None or salla_cost_fingerprint(existing) != salla_cost_fingerprint(doc):
                    await _mark_cost_revision_pending(db, user_id, lease_token)
                update_selector = (
                    {**selector, "source_revision": existing.get("source_revision")}
                    if existing is not None else selector
                )
                previous = await db[PRODUCTS].find_one_and_update(
                    update_selector,
                    {
                        "$set": {**doc, "updated_at": synced_at, "archived": False},
                        "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": synced_at},
                    },
                    upsert=existing is None,
                    return_document=ReturnDocument.BEFORE,
                )
                if existing is not None and previous is None:
                    # Another catalogue writer changed the source revision after
                    # our read. Retry instead of overwriting that newer record.
                    errors += 1
                    continue
                if previous is None:
                    created += 1
                else:
                    updated += 1
                    await db[CHANGE_LOG].insert_one({
                        "id": uuid.uuid4().hex,
                        "user_id": user_id,
                        "mezan_product_id": doc.get("mezan_product_id"),
                        "salla_product_id": doc.get("salla_product_id"),
                        "event_type": "salla_recent_product_updated",
                        "previous_revision": previous.get("source_revision"),
                        "next_revision": doc.get("source_revision"),
                        "occurred_at": synced_at,
                    })
            except RecentSyncLeaseLost:
                raise
            except Exception:
                errors += 1

    finally:
        await asyncio.wait_for(
            _flush_pending_cost_revision(db, user_id, lease_token), timeout=STATE_WRITE_TIMEOUT_SECONDS,
        )

    return {
        "ok": errors == 0,
        "mode": "recent_first_page",
        "seen_products": len(rows),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "errors_count": errors,
        "synced_at": synced_at.isoformat(),
    }


def make_product_v2_recent_sync_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Products V2 Recent Sync"])

    @router.post("/sync-recent")
    async def sync_recent(force: bool = False, user: dict = Depends(current_user)) -> dict[str, Any]:
        return await sync_recent_products(db, user_id=str(user["id"]), force=force)

    return router
