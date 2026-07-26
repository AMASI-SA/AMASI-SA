"""Salla-style Product Workspace for Mezan OS V2.

This module extends the independent ``mezan_products_v2`` catalogue without
reading legacy product collections.  It owns newest-first browsing and guarded
SKU allocation/write-back to Salla.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from product_v2_routes import PRODUCTS, ensure_product_v2_indexes
from salla_integration.service import SallaError, call_salla

SEQUENCES = "mezan_product_sequences_v2"
WRITE_LOG = "mezan_product_write_log_v2"
DEFAULT_PREFIX = "AMS"
DEFAULT_WIDTH = 5
SKU_CONFIRMATION = "تحديث SKU في سلة"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _missing_sku_query(user_id: str) -> dict[str, Any]:
    return {
        "user_id": str(user_id),
        "archived": {"$ne": True},
        "$or": [
            {"sku": {"$exists": False}},
            {"sku": None},
            {"sku": ""},
        ],
    }


def _sku_number(value: Any, prefix: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", str(value or "").strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


async def _current_max_sku_number(db: Any, user_id: str, prefix: str) -> int:
    maximum = 0
    cursor = db[PRODUCTS].find(
        {"user_id": str(user_id), "sku": {"$regex": rf"^{re.escape(prefix)}\d+$", "$options": "i"}},
        {"_id": 0, "sku": 1},
    )
    async for row in cursor:
        number = _sku_number(row.get("sku"), prefix)
        if number is not None:
            maximum = max(maximum, number)
    return maximum


async def _reserve_sku_number(db: Any, user_id: str, prefix: str) -> int:
    key = f"{user_id}:{prefix.upper()}"
    current_max = await _current_max_sku_number(db, user_id, prefix)
    await db[SEQUENCES].update_one(
        {"_id": key},
        {
            "$max": {"value": current_max},
            "$set": {"user_id": str(user_id), "prefix": prefix.upper(), "updated_at": _now()},
            "$setOnInsert": {"created_at": _now()},
        },
        upsert=True,
    )
    row = await db[SEQUENCES].find_one_and_update(
        {"_id": key},
        {"$inc": {"value": 1}, "$set": {"updated_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return int(row["value"])


def _serialize_dates(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "created_at", "updated_at", "last_synced_at", "archived_at",
        "source_created_at", "source_updated_at", "salla_write_confirmed_at",
    ):
        value = row.get(key)
        if hasattr(value, "isoformat"):
            row[key] = value.isoformat()
    return row


class SkuApplyRequest(BaseModel):
    prefix: str = Field(default=DEFAULT_PREFIX, min_length=1, max_length=12, pattern=r"^[A-Za-z]+$")
    width: int = Field(default=DEFAULT_WIDTH, ge=3, le=10)
    limit: int = Field(default=50, ge=1, le=200)
    confirmation: str


def make_product_v2_workspace_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/products-v2/workspace", tags=["Mezan OS Product Workspace"])

    @router.get("/products")
    async def workspace_products(
        user: dict = Depends(current_user),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=30, ge=1, le=100),
        q: str | None = Query(default=None, max_length=160),
        product_status: str | None = Query(default=None, alias="status"),
        sort: str = Query(default="newest"),
        missing_sku: bool = Query(default=False),
    ) -> dict[str, Any]:
        await ensure_product_v2_indexes(db)
        user_id = str(user["id"])
        query: dict[str, Any] = {"user_id": user_id, "archived": {"$ne": True}}
        if product_status:
            query["status"] = product_status
        if missing_sku:
            query.update({k: v for k, v in _missing_sku_query(user_id).items() if k != "user_id"})
        if q and q.strip():
            pattern = q.strip()
            query["$and"] = query.get("$and", []) + [{"$or": [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"sku": {"$regex": pattern, "$options": "i"}},
                {"barcode": {"$regex": pattern, "$options": "i"}},
                {"salla_product_id": {"$regex": pattern, "$options": "i"}},
            ]}]

        sort_spec = {
            "newest": [
                ("source_created_at", DESCENDING),
                ("source_updated_at", DESCENDING),
                ("created_at", DESCENDING),
                ("salla_product_id", DESCENDING),
            ],
            "oldest": [("source_created_at", ASCENDING), ("created_at", ASCENDING)],
            "name": [("name", ASCENDING)],
            "price_high": [("price", DESCENDING), ("name", ASCENDING)],
            "price_low": [("price", ASCENDING), ("name", ASCENDING)],
        }.get(sort, [("source_created_at", DESCENDING), ("created_at", DESCENDING)])

        total = await db[PRODUCTS].count_documents(query)
        cursor = (
            db[PRODUCTS]
            .find(query, {"_id": 0, "raw_salla": 0})
            .sort(sort_spec)
            .skip((page - 1) * per_page)
            .limit(per_page)
        )
        items = [_serialize_dates(row) for row in await cursor.to_list(length=per_page)]
        return {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            },
            "meta": {"sort": sort, "legacy_dependency": False, "source": PRODUCTS},
        }

    @router.get("/sku/preview")
    async def preview_missing_skus(
        user: dict = Depends(current_user),
        prefix: str = Query(default=DEFAULT_PREFIX, min_length=1, max_length=12, pattern=r"^[A-Za-z]+$"),
        width: int = Query(default=DEFAULT_WIDTH, ge=3, le=10),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        current_max = await _current_max_sku_number(db, user_id, prefix)
        rows = await (
            db[PRODUCTS]
            .find(_missing_sku_query(user_id), {"_id": 0, "salla_product_id": 1, "name": 1, "main_image": 1})
            .sort([("source_created_at", DESCENDING), ("created_at", DESCENDING)])
            .limit(limit)
            .to_list(length=limit)
        )
        preview = [
            {**row, "proposed_sku": f"{prefix.upper()}{current_max + index:0{width}d}"}
            for index, row in enumerate(rows, start=1)
        ]
        missing_total = await db[PRODUCTS].count_documents(_missing_sku_query(user_id))
        return {
            "prefix": prefix.upper(),
            "width": width,
            "current_max": current_max,
            "missing_total": missing_total,
            "items": preview,
            "write_performed": False,
        }

    @router.post("/sku/apply")
    async def apply_missing_skus(payload: SkuApplyRequest, user: dict = Depends(current_user)) -> dict[str, Any]:
        if payload.confirmation.strip() != SKU_CONFIRMATION:
            raise HTTPException(status_code=400, detail={"code": "confirmation_required", "expected": SKU_CONFIRMATION})

        user_id = str(user["id"])
        rows = await (
            db[PRODUCTS]
            .find(_missing_sku_query(user_id), {"_id": 0, "id": 1, "salla_product_id": 1, "name": 1})
            .sort([("source_created_at", DESCENDING), ("created_at", DESCENDING)])
            .limit(payload.limit)
            .to_list(length=payload.limit)
        )

        results: list[dict[str, Any]] = []
        for row in rows:
            number = await _reserve_sku_number(db, user_id, payload.prefix)
            sku = f"{payload.prefix.upper()}{number:0{payload.width}d}"
            product_id = str(row.get("salla_product_id") or "").strip()
            try:
                response = await call_salla(
                    db,
                    user_id,
                    "PUT",
                    f"/products/{product_id}",
                    json={"sku": sku},
                )
                now = _now()
                await db[PRODUCTS].update_one(
                    {"user_id": user_id, "salla_product_id": product_id},
                    {"$set": {
                        "sku": sku,
                        "salla_write_confirmed_at": now,
                        "updated_at": now,
                        "sku_source": "mezan_auto_sequence",
                    }},
                )
                await db[WRITE_LOG].insert_one({
                    "id": uuid.uuid4().hex,
                    "user_id": user_id,
                    "salla_product_id": product_id,
                    "action": "assign_sku",
                    "before": {"sku": None},
                    "after": {"sku": sku},
                    "source": "mezan_products_v2_workspace",
                    "salla_response": response,
                    "created_at": now,
                })
                results.append({"ok": True, "salla_product_id": product_id, "name": row.get("name"), "sku": sku})
            except SallaError as exc:
                results.append({
                    "ok": False,
                    "salla_product_id": product_id,
                    "name": row.get("name"),
                    "sku": sku,
                    "error": str(exc),
                    "needs_reauth": exc.needs_reauth,
                })

        return {
            "ok": all(row.get("ok") for row in results) if results else True,
            "processed": len(results),
            "succeeded": sum(bool(row.get("ok")) for row in results),
            "failed": sum(not bool(row.get("ok")) for row in results),
            "results": results,
            "remaining_missing": await db[PRODUCTS].count_documents(_missing_sku_query(user_id)),
        }

    return router
