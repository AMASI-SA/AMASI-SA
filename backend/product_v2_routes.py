"""Independent Mezan OS Product Domain V2.

This module intentionally does not read from or write to the legacy product
collections. Salla is the commerce source; ``mezan_products_v2`` is the new
canonical Mezan OS product catalog.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from salla_integration.service import SallaError, call_salla

PRODUCTS = "mezan_products_v2"
SYNC_RUNS = "mezan_product_sync_runs_v2"
CHANGE_LOG = "mezan_product_change_log_v2"
PRODUCTS_PER_PAGE = 60
MAX_PRODUCT_PAGES = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "label", "value", "text", "title", "url", "original"):
            candidate = value.get(key)
            if candidate not in (None, "", [], {}):
                return _text(candidate)
        return ""
    return str(value).strip()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "total"):
            if key in value:
                parsed = _number(value.get(key))
                if parsed is not None:
                    return parsed
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _image_url(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("url", "original", "src", "full", "medium", "thumbnail"):
            result = _image_url(value.get(key))
            if result:
                return result
        return ""
    if isinstance(value, list):
        for entry in value:
            result = _image_url(entry)
            if result:
                return result
    return ""


def _status(raw: Any) -> str:
    value = _text(raw).lower()
    if value in {"sale", "active", "available", "published", "enabled"}:
        return "active"
    if value in {"out", "out_of_stock", "sold_out"}:
        return "out_of_stock"
    if value in {"hidden", "draft", "inactive", "disabled"}:
        return "inactive"
    return value or "unknown"


def normalize_salla_product(raw: dict[str, Any], *, user_id: str, synced_at: datetime) -> dict[str, Any]:
    """Convert one Salla product into the stable V2 catalog contract."""
    salla_id = _text(raw.get("id") or raw.get("product_id"))
    if not salla_id:
        raise ValueError("missing_salla_product_id")

    sku = _text(raw.get("sku") or raw.get("product_sku"))
    barcode = _text(raw.get("barcode") or raw.get("gtin") or raw.get("mpn"))
    quantities = raw.get("quantity")
    if quantities is None:
        quantities = raw.get("stock_quantity")

    categories_raw = raw.get("categories") or raw.get("category") or []
    if isinstance(categories_raw, dict):
        categories_raw = [categories_raw]
    categories = []
    for row in categories_raw if isinstance(categories_raw, list) else []:
        if isinstance(row, dict):
            categories.append({
                "id": _text(row.get("id")),
                "name": _text(row.get("name") or row.get("title")),
            })

    image = _image_url(
        raw.get("main_image")
        or raw.get("thumbnail")
        or raw.get("image")
        or raw.get("images")
    )
    price = _number(raw.get("price"))
    sale_price = _number(raw.get("sale_price") or raw.get("discount_price"))
    cost_price = _number(raw.get("cost_price") or raw.get("cost"))
    status_value = _status(raw.get("status") or raw.get("availability") or raw.get("is_available"))
    revision_payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    source_revision = hashlib.sha256(revision_payload.encode("utf-8")).hexdigest()

    return {
        "user_id": str(user_id),
        "mezan_product_id": f"mpv2_{salla_id}",
        "salla_product_id": salla_id,
        "name": _text(raw.get("name") or raw.get("title")) or f"منتج {salla_id}",
        "sku": sku or None,
        "barcode": barcode or None,
        "description": _text(raw.get("description") or raw.get("short_description")) or None,
        "status": status_value,
        "product_type": _text(raw.get("type") or raw.get("product_type")) or "product",
        "price": price,
        "sale_price": sale_price,
        "cost_price_from_salla": cost_price,
        "currency": _text(raw.get("currency") or (raw.get("price") or {}).get("currency") if isinstance(raw.get("price"), dict) else "") or "SAR",
        "quantity": _number(quantities),
        "unlimited_quantity": bool(raw.get("unlimited_quantity") or raw.get("is_infinite")),
        "main_image": image or None,
        "categories": categories,
        "options_count": len(raw.get("options") or []) if isinstance(raw.get("options"), list) else 0,
        "variants_count": len(raw.get("variants") or raw.get("skus") or []) if isinstance(raw.get("variants") or raw.get("skus") or [], list) else 0,
        "source": "salla",
        "source_revision": source_revision,
        "source_updated_at": raw.get("updated_at") or raw.get("date_updated"),
        "last_synced_at": synced_at,
        "archived": False,
        "raw_salla": raw,
    }


async def ensure_product_v2_indexes(db: Any) -> None:
    await db[PRODUCTS].create_index(
        [("user_id", ASCENDING), ("salla_product_id", ASCENDING)],
        unique=True,
        name="uq_mezan_products_v2_salla",
    )
    await db[PRODUCTS].create_index(
        [("user_id", ASCENDING), ("name", ASCENDING)],
        name="ix_mezan_products_v2_name",
    )
    await db[PRODUCTS].create_index(
        [("user_id", ASCENDING), ("status", ASCENDING), ("last_synced_at", DESCENDING)],
        name="ix_mezan_products_v2_status",
    )
    await db[SYNC_RUNS].create_index(
        [("user_id", ASCENDING), ("started_at", DESCENDING)],
        name="ix_product_sync_runs_v2",
    )


async def run_product_v2_sync(db: Any, user_id: str) -> dict[str, Any]:
    await ensure_product_v2_indexes(db)
    run_id = uuid.uuid4().hex
    started_at = _now()
    await db[SYNC_RUNS].insert_one({
        "id": run_id,
        "user_id": str(user_id),
        "status": "running",
        "started_at": started_at,
        "ended_at": None,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "errors_count": 0,
        "pages_fetched": 0,
        "collection": PRODUCTS,
    })

    created = updated = unchanged = errors_count = pages_fetched = 0
    seen_ids: set[str] = set()
    error_sample: list[dict[str, Any]] = []

    try:
        page = 1
        while page <= MAX_PRODUCT_PAGES:
            response = await call_salla(
                db,
                str(user_id),
                "GET",
                "/products",
                params={"page": page, "per_page": PRODUCTS_PER_PAGE, "format": "light"},
            )
            rows = response.get("data") if isinstance(response, dict) else None
            pages_fetched += 1
            if not isinstance(rows, list) or not rows:
                break

            synced_at = _now()
            for raw in rows:
                if not isinstance(raw, dict):
                    errors_count += 1
                    continue
                try:
                    doc = normalize_salla_product(raw, user_id=str(user_id), synced_at=synced_at)
                    seen_ids.add(doc["salla_product_id"])
                    existing = await db[PRODUCTS].find_one(
                        {"user_id": str(user_id), "salla_product_id": doc["salla_product_id"]},
                        {"_id": 0, "source_revision": 1, "mezan_product_id": 1},
                    )
                    if existing and existing.get("source_revision") == doc["source_revision"]:
                        unchanged += 1
                        await db[PRODUCTS].update_one(
                            {"user_id": str(user_id), "salla_product_id": doc["salla_product_id"]},
                            {"$set": {"last_synced_at": synced_at, "archived": False}},
                        )
                        continue

                    now = _now()
                    result = await db[PRODUCTS].find_one_and_update(
                        {"user_id": str(user_id), "salla_product_id": doc["salla_product_id"]},
                        {
                            "$set": {**doc, "updated_at": now},
                            "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": now},
                        },
                        upsert=True,
                        return_document=ReturnDocument.BEFORE,
                    )
                    if result is None:
                        created += 1
                    else:
                        updated += 1
                        await db[CHANGE_LOG].insert_one({
                            "id": uuid.uuid4().hex,
                            "user_id": str(user_id),
                            "mezan_product_id": doc["mezan_product_id"],
                            "salla_product_id": doc["salla_product_id"],
                            "event_type": "salla_product_updated",
                            "previous_revision": result.get("source_revision"),
                            "next_revision": doc["source_revision"],
                            "occurred_at": now,
                            "sync_run_id": run_id,
                        })
                except Exception as exc:  # defensive per-row isolation
                    errors_count += 1
                    if len(error_sample) < 20:
                        error_sample.append({"product_id": _text(raw.get("id")), "error": str(exc)[:300]})

            pagination = response.get("pagination") or {}
            current_page = int(pagination.get("currentPage") or pagination.get("current_page") or page)
            total_pages = int(pagination.get("totalPages") or pagination.get("total_pages") or pagination.get("last_page") or 0)
            if total_pages and current_page >= total_pages:
                break
            page += 1

        # Products absent from a complete run are archived, never hard-deleted.
        if seen_ids:
            await db[PRODUCTS].update_many(
                {
                    "user_id": str(user_id),
                    "salla_product_id": {"$nin": list(seen_ids)},
                    "archived": {"$ne": True},
                },
                {"$set": {"archived": True, "archived_at": _now(), "updated_at": _now()}},
            )

        ended_at = _now()
        await db[SYNC_RUNS].update_one(
            {"id": run_id, "user_id": str(user_id)},
            {"$set": {
                "status": "completed" if errors_count == 0 else "completed_with_errors",
                "ended_at": ended_at,
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "errors_count": errors_count,
                "errors_sample": error_sample,
                "pages_fetched": pages_fetched,
                "seen_products": len(seen_ids),
            }},
        )
        return {
            "run_id": run_id,
            "status": "completed" if errors_count == 0 else "completed_with_errors",
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "errors_count": errors_count,
            "pages_fetched": pages_fetched,
            "seen_products": len(seen_ids),
            "collection": PRODUCTS,
        }
    except SallaError as exc:
        await db[SYNC_RUNS].update_one(
            {"id": run_id, "user_id": str(user_id)},
            {"$set": {"status": "failed", "ended_at": _now(), "last_error": str(exc)[:500]}},
        )
        raise


def make_product_v2_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Mezan OS Products V2"])

    @router.post("/sync", status_code=status.HTTP_200_OK)
    async def sync_products_v2(user: dict = Depends(current_user)) -> dict[str, Any]:
        try:
            return {"ok": True, **(await run_product_v2_sync(db, str(user["id"])))}
        except SallaError as exc:
            raise HTTPException(
                status_code=exc.status_code if exc.status_code != 200 else 400,
                detail={"message": str(exc), "needs_reauth": exc.needs_reauth},
            ) from exc

    @router.get("")
    async def list_products_v2(
        user: dict = Depends(current_user),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=30, ge=1, le=100),
        q: str | None = Query(default=None, max_length=160),
        product_status: str | None = Query(default=None, alias="status"),
        include_archived: bool = Query(default=False),
    ) -> dict[str, Any]:
        await ensure_product_v2_indexes(db)
        query: dict[str, Any] = {"user_id": str(user["id"])}
        if not include_archived:
            query["archived"] = {"$ne": True}
        if product_status:
            query["status"] = product_status
        if q and q.strip():
            pattern = q.strip()
            query["$or"] = [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"sku": {"$regex": pattern, "$options": "i"}},
                {"barcode": {"$regex": pattern, "$options": "i"}},
                {"salla_product_id": {"$regex": pattern, "$options": "i"}},
            ]

        total = await db[PRODUCTS].count_documents(query)
        cursor = (
            db[PRODUCTS]
            .find(query, {"_id": 0, "raw_salla": 0})
            .sort([("archived", ASCENDING), ("name", ASCENDING)])
            .skip((page - 1) * per_page)
            .limit(per_page)
        )
        items = await cursor.to_list(length=per_page)
        for item in items:
            for key in ("created_at", "updated_at", "last_synced_at", "archived_at"):
                value = item.get(key)
                if hasattr(value, "isoformat"):
                    item[key] = value.isoformat()
        return {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            },
            "meta": {"source": PRODUCTS, "legacy_dependency": False, "read_only": True},
        }

    @router.get("/summary")
    async def products_v2_summary(user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        match = {"user_id": user_id, "archived": {"$ne": True}}
        total = await db[PRODUCTS].count_documents(match)
        active = await db[PRODUCTS].count_documents({**match, "status": "active"})
        archived = await db[PRODUCTS].count_documents({"user_id": user_id, "archived": True})
        last_run = await db[SYNC_RUNS].find_one({"user_id": user_id}, {"_id": 0}, sort=[("started_at", -1)])
        if last_run:
            for key in ("started_at", "ended_at"):
                value = last_run.get(key)
                if hasattr(value, "isoformat"):
                    last_run[key] = value.isoformat()
        return {
            "total": total,
            "active": active,
            "archived": archived,
            "last_sync": last_run,
            "collection": PRODUCTS,
            "legacy_dependency": False,
        }

    @router.get("/{product_id}")
    async def get_product_v2(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        product = await db[PRODUCTS].find_one(
            {
                "user_id": str(user["id"]),
                "$or": [
                    {"id": product_id},
                    {"mezan_product_id": product_id},
                    {"salla_product_id": product_id},
                ],
            },
            {"_id": 0},
        )
        if not product:
            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
        for key in ("created_at", "updated_at", "last_synced_at", "archived_at"):
            value = product.get(key)
            if hasattr(value, "isoformat"):
                product[key] = value.isoformat()
        return product

    return router
