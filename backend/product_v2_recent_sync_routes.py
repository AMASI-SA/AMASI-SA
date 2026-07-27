"""Lightweight automatic sync for the newest Salla products.

This endpoint intentionally reads only Salla's first products page and upserts it
into Product V2. It never archives products and never traverses the whole
catalogue. The full manual sync remains the authority for complete reconciliation.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pymongo import ReturnDocument

from product_v2_routes import CHANGE_LOG, PRODUCTS, _now, ensure_product_v2_indexes, normalize_salla_product
from salla_integration.service import SallaError, call_salla

RECENT_SYNC_LIMIT = 30


async def sync_recent_products(db: Any, *, user_id: str) -> dict[str, Any]:
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
    for raw in rows:
        if not isinstance(raw, dict):
            errors += 1
            continue
        try:
            doc = normalize_salla_product(raw, user_id=user_id, synced_at=synced_at)
            selector = {"user_id": user_id, "salla_product_id": doc["salla_product_id"]}
            existing = await db[PRODUCTS].find_one(selector, {"_id": 0, "source_revision": 1})
            if existing and existing.get("source_revision") == doc.get("source_revision"):
                unchanged += 1
                await db[PRODUCTS].update_one(selector, {"$set": {"last_synced_at": synced_at, "archived": False}})
                continue
            previous = await db[PRODUCTS].find_one_and_update(
                selector,
                {
                    "$set": {**doc, "updated_at": synced_at, "archived": False},
                    "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": synced_at},
                },
                upsert=True,
                return_document=ReturnDocument.BEFORE,
            )
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
        except Exception:
            errors += 1

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
    async def sync_recent(user: dict = Depends(current_user)) -> dict[str, Any]:
        return await sync_recent_products(db, user_id=str(user["id"]))

    return router
