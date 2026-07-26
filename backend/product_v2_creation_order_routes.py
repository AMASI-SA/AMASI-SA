"""Product V2 listing with Salla's live catalogue order as authority."""
from __future__ import annotations

import re
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from pymongo import ASCENDING, DESCENDING

from product_v2_catalog_rank import NEWEST_CATALOG_SORT, OLDEST_CATALOG_SORT
from product_v2_routes import PRODUCTS, ensure_product_v2_indexes
from salla_integration.service import SallaError, call_salla


def _serialize_dates(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "created_at", "updated_at", "last_synced_at", "archived_at",
        "source_created_at", "source_updated_at", "salla_write_confirmed_at",
    ):
        value = row.get(key)
        if hasattr(value, "isoformat"):
            row[key] = value.isoformat()
    return row


def _salla_rows(response: Any) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    rows = response.get("data")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


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

        # The unfiltered newest listing must match Salla exactly.  Do not rebuild
        # that order from timestamps because Salla omits creation dates for some
        # list payloads.  Preserve the exact order returned by GET /products.
        if sort == "newest" and not (q and q.strip()) and not product_status and not missing_sku:
            try:
                response = await call_salla(
                    db,
                    user_id,
                    "GET",
                    "/products",
                    params={"page": page, "per_page": per_page},
                )
                remote_rows = _salla_rows(response)
                remote_ids = [str(row.get("id") or row.get("product_id") or "").strip() for row in remote_rows]
                remote_ids = [value for value in remote_ids if value]
                docs = await db[PRODUCTS].find(
                    {"user_id": user_id, "salla_product_id": {"$in": remote_ids}, "archived": {"$ne": True}},
                    {"_id": 0, "raw_salla": 0, "raw_salla_details": 0},
                ).to_list(length=len(remote_ids))
                by_id = {str(row.get("salla_product_id")): row for row in docs}
                items = [_serialize_dates(by_id[product_id]) for product_id in remote_ids if product_id in by_id]
                total = await db[PRODUCTS].count_documents({"user_id": user_id, "archived": {"$ne": True}})
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
                        "sort_authority": "live_salla_catalog_order",
                        "legacy_dependency": False,
                        "source": PRODUCTS,
                    },
                }
            except SallaError:
                # A temporary Salla read failure must not break the page. Fall
                # back to the last rank captured during a successful full sync.
                pass

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
            pattern = re.escape(q.strip())
            query["$and"] = query.get("$and", []) + [{"$or": [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"sku": {"$regex": pattern, "$options": "i"}},
                {"barcode": {"$regex": pattern, "$options": "i"}},
                {"salla_product_id": {"$regex": pattern, "$options": "i"}},
            ]}]

        sort_spec = {
            "newest": NEWEST_CATALOG_SORT,
            "oldest": OLDEST_CATALOG_SORT,
            "name": [("name", ASCENDING)],
            "price_high": [("price", DESCENDING), ("name", ASCENDING)],
            "price_low": [("price", ASCENDING), ("name", ASCENDING)],
        }.get(sort, NEWEST_CATALOG_SORT)

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
                "sort_authority": "stored_salla_catalog_rank",
                "legacy_dependency": False,
                "source": PRODUCTS,
            },
        }

    return router
