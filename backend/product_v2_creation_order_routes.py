"""Corrected Product V2 workspace listing ordered by Salla creation date."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from pymongo import ASCENDING, DESCENDING

from product_v2_routes import PRODUCTS, ensure_product_v2_indexes


def _serialize_dates(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "created_at", "updated_at", "last_synced_at", "archived_at",
        "source_created_at", "source_updated_at", "salla_write_confirmed_at",
    ):
        value = row.get(key)
        if hasattr(value, "isoformat"):
            row[key] = value.isoformat()
    return row


def make_product_v2_creation_order_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/products-v2/workspace", tags=["Mezan OS Product Workspace"])

    @router.get("/products")
    async def workspace_products_creation_order(
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
            query["$or"] = [
                {"sku": {"$exists": False}},
                {"sku": None},
                {"sku": ""},
            ]
        if q and q.strip():
            pattern = q.strip()
            query["$and"] = query.get("$and", []) + [{"$or": [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"sku": {"$regex": pattern, "$options": "i"}},
                {"barcode": {"$regex": pattern, "$options": "i"}},
                {"salla_product_id": {"$regex": pattern, "$options": "i"}},
            ]}]

        # "المضافة حديثًا" means original creation in Salla only. Never use
        # source_updated_at, updated_at, last_synced_at, or local created_at.
        sort_spec = {
            "newest": [("source_created_at", DESCENDING), ("salla_product_id", DESCENDING)],
            "oldest": [("source_created_at", ASCENDING), ("salla_product_id", ASCENDING)],
            "name": [("name", ASCENDING)],
            "price_high": [("price", DESCENDING), ("name", ASCENDING)],
            "price_low": [("price", ASCENDING), ("name", ASCENDING)],
        }.get(sort, [("source_created_at", DESCENDING), ("salla_product_id", DESCENDING)])

        total = await db[PRODUCTS].count_documents(query)
        cursor = (
            db[PRODUCTS]
            .find(query, {"_id": 0, "raw_salla": 0, "raw_salla_details": 0})
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
            "meta": {
                "sort": sort,
                "sort_authority": "salla_product_created_at",
                "legacy_dependency": False,
                "source": PRODUCTS,
            },
        }

    return router
