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
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from order_option_cost_snapshot_routes import (
    COST_SEMANTICS_VERSION,
    classify_base_unit_cost,
)
from product_catalog_cost_resolution import (
    enrich_current_salla_cost,
    index_current_catalog_products,
    resolve_current_catalog_line_product,
)
from product_v2_details_routes import COST_PROFILES
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


def _matches_any(value: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    normalized = str(value or "").strip().casefold()
    return any(
        candidate and (
            candidate == normalized
            or candidate in normalized
            or normalized in candidate
        )
        for candidate in (str(item).strip().casefold() for item in allowed)
    )


def _parse_product_ids(value: str | None) -> list[str]:
    return list(dict.fromkeys(
        part.strip() for part in (value or "").split(",") if part.strip()
    ))[:500]


def _mongo_id_values(values: list[str]) -> list[Any]:
    result: list[Any] = list(values)
    result.extend(int(value) for value in values if value.isdigit())
    return list(dict.fromkeys(result))


def _restrict_missing_rows(
    rows: dict[str, dict[str, Any]],
    requested_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not requested_ids:
        return rows
    requested = set(requested_ids)
    return {
        product_id: row for product_id, row in rows.items()
        if product_id in requested
        or str(row.get("salla_product_id") or "") in requested
        or str(row.get("mezan_product_id") or "") in requested
    }


def _line_product(
    item: dict[str, Any],
    *,
    products_by_id: dict[str, dict[str, Any]],
    products_by_variant: dict[str, dict[str, Any]],
    products_by_sku: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in (item.get("parent_product_id"), item.get("product_id")):
        identity = str(key or "").strip()
        if identity and identity in products_by_id:
            return products_by_id[identity]
    variant_id = str(item.get("variant_id") or "").strip()
    if variant_id and variant_id in products_by_variant:
        return products_by_variant[variant_id]
    sku = str(item.get("sku") or "").strip().casefold()
    return products_by_sku.get(sku) if sku else None


async def _user_reporting_settings(db: Any, user_id: str) -> dict[str, Any]:
    """Load report settings without making product-only imports require auth extras."""
    from auth import ensure_user_settings

    return await ensure_user_settings(db, user_id)


async def _sold_missing_mezan_cost_products(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    payment_methods: list[str] | None = None,
    shipping_companies: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return sold V2 products whose sold lines lack an explicit Mezan cost."""
    products = await db[PRODUCTS].find(
        {
            "user_id": user_id,
            "archived": {"$ne": True},
            "cost_setup_complete": {"$ne": True},
        },
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "name": 1,
            "sku": 1,
            "cost_price_from_salla": 1,
            "cost_price": 1,
            "cost": 1,
            "variants": 1,
            "raw_salla_details": 1,
            "raw_salla": 1,
        },
    ).to_list(length=100000)
    products_by_id, products_by_variant, products_by_sku = (
        index_current_catalog_products(products)
    )

    product_ids = [
        str(product.get("salla_product_id") or "").strip()
        for product in products
        if product.get("salla_product_id") not in (None, "")
    ]
    profiles = await db[COST_PROFILES].find(
        {"user_id": user_id, "salla_product_id": {"$in": product_ids}},
        {"_id": 0},
    ).to_list(length=max(1, len(product_ids)))
    profile_map = {str(row.get("salla_product_id") or ""): row for row in profiles}

    order_query: dict[str, Any] = {
        "user_id": user_id,
        "order_date": {"$gte": from_date, "$lte": to_date},
    }
    settings = await _user_reporting_settings(db, user_id)
    if settings.get("hide_inferred_date_orders"):
        order_query["order_date_inferred"] = {"$ne": True}
    orders = await db["unified_orders"].find(
        order_query,
        {
            "_id": 0,
            "order_status": 1,
            "payment_method": 1,
            "shipping_company": 1,
            "products": 1,
        },
    ).to_list(length=100000)
    included_statuses = settings.get("report_included_statuses") or []

    missing: dict[str, dict[str, Any]] = {}
    for order in orders:
        if (
            not _matches_any(order.get("order_status", ""), included_statuses)
            or not _matches_any(order.get("payment_method", ""), payment_methods or [])
            or not _matches_any(order.get("shipping_company", ""), shipping_companies or [])
        ):
            continue
        for item in order.get("products") or []:
            if not isinstance(item, dict):
                continue
            product = resolve_current_catalog_line_product(
                item,
                products_by_id=products_by_id,
                products_by_variant=products_by_variant,
                products_by_sku=products_by_sku,
                base_resolver=_line_product,
            )
            if not product:
                continue
            salla_id = str(product.get("salla_product_id") or "").strip()
            if not salla_id:
                continue
            status = classify_base_unit_cost(item, profile_map.get(salla_id), product)
            if not status["mezan_cost_missing"]:
                continue
            row = missing.setdefault(salla_id, {
                "salla_product_id": salla_id,
                "mezan_product_id": str(product.get("mezan_product_id") or product.get("id") or ""),
                "name": product.get("name") or item.get("name") or "منتج بدون اسم",
                "uses_salla_fallback": False,
                "missing_everywhere": False,
                "calculation_cost_available": True,
                "fallback_sources": set(),
                "sold_lines": 0,
            })
            row["sold_lines"] += 1
            row["uses_salla_fallback"] = bool(
                row["uses_salla_fallback"] or status["uses_salla_fallback"]
            )
            row["missing_everywhere"] = bool(
                row["missing_everywhere"]
                or not status["calculation_cost_available"]
            )
            row["calculation_cost_available"] = bool(
                row["calculation_cost_available"]
                and status["calculation_cost_available"]
            )
            if status["uses_salla_fallback"]:
                row["fallback_sources"].add(status["source"])

    return {
        product_id: {**row, "fallback_sources": sorted(row["fallback_sources"])}
        for product_id, row in missing.items()
    }


async def _requested_missing_mezan_cost_products(
    db: Any,
    user_id: str,
    requested_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Revalidate a Dashboard V2 sold cohort against the current catalogue.

    ``product_ids`` is a snapshot of the exact sold/missing-Mezan cohort that
    Dashboard V2 already calculated with its order filters.  Re-reading orders
    here would create a second, potentially different cohort.  We therefore
    preserve that snapshot and only revalidate the current Mezan/Salla cost
    axes before returning products.
    """
    if not requested_ids:
        return {}

    identity_values = _mongo_id_values(requested_ids)
    products = await db[PRODUCTS].find(
        {
            "user_id": user_id,
            "archived": {"$ne": True},
            "cost_setup_complete": {"$ne": True},
            "$or": [
                {"salla_product_id": {"$in": identity_values}},
                {"mezan_product_id": {"$in": requested_ids}},
                {"id": {"$in": requested_ids}},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "name": 1,
            "sku": 1,
            "cost_price_from_salla": 1,
            "cost_price": 1,
            "cost": 1,
            "variants": 1,
            "raw_salla_details": 1,
            "raw_salla": 1,
        },
    ).to_list(length=min(500, max(1, len(requested_ids))))

    product_ids = [
        str(product.get("salla_product_id") or "").strip()
        for product in products
        if product.get("salla_product_id") not in (None, "")
    ]
    profiles = await db[COST_PROFILES].find(
        {"user_id": user_id, "salla_product_id": {"$in": product_ids}},
        {"_id": 0},
    ).to_list(length=max(1, len(product_ids)))
    profile_map = {str(row.get("salla_product_id") or ""): row for row in profiles}

    missing: dict[str, dict[str, Any]] = {}
    for raw_product in products:
        product = enrich_current_salla_cost(raw_product)
        salla_id = str(product.get("salla_product_id") or "").strip()
        if not salla_id:
            continue
        status = classify_base_unit_cost({}, profile_map.get(salla_id), product)
        if not status["mezan_cost_missing"]:
            continue
        missing[salla_id] = {
            "salla_product_id": salla_id,
            "mezan_product_id": str(
                product.get("mezan_product_id") or product.get("id") or ""
            ),
            "name": product.get("name") or "منتج بدون اسم",
            "uses_salla_fallback": bool(status["uses_salla_fallback"]),
            "missing_everywhere": not bool(status["calculation_cost_available"]),
            "calculation_cost_available": bool(status["calculation_cost_available"]),
            "fallback_sources": [status["source"]]
            if status["uses_salla_fallback"] else [],
            "sold_lines": None,
            "cohort_source": "dashboard_snapshot_product_ids",
        }
    return missing


class SkuApplyRequest(BaseModel):
    prefix: str = Field(default=DEFAULT_PREFIX, min_length=1, max_length=12, pattern=r"^[A-Za-z]+$")
    width: int = Field(default=DEFAULT_WIDTH, ge=3, le=10)
    limit: int = Field(default=50, ge=1, le=200)
    confirmation: str


def make_product_v2_workspace_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/products-v2/workspace", tags=["Mezan OS Product Workspace"])

    # Keep the sold/missing-cost contract on its own URL.  Production proxies
    # and older deployments may still cache the generic products endpoint; a
    # distinct route prevents a cached all-products payload from being treated
    # as the Dashboard V2 cohort.
    @router.get("/sold-missing-cost-products")
    @router.get("/products")
    async def workspace_products(
        user: dict = Depends(current_user),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=30, ge=1, le=100),
        q: str | None = Query(default=None, max_length=160),
        product_status: str | None = Query(default=None, alias="status"),
        sort: str = Query(default="newest"),
        missing_sku: bool = Query(default=False),
        missing_mezan_cost: bool = Query(default=False),
        sold_only: bool = Query(default=False),
        from_date: str | None = Query(default=None, alias="from"),
        to_date: str | None = Query(default=None, alias="to"),
        payment_methods: str | None = Query(default=None),
        shipping_companies: str | None = Query(default=None),
        product_ids: str | None = Query(default=None, max_length=12000),
    ) -> dict[str, Any]:
        await ensure_product_v2_indexes(db)
        user_id = str(user["id"])
        query: dict[str, Any] = {"user_id": user_id, "archived": {"$ne": True}}
        if product_status:
            query["status"] = product_status
        if missing_sku:
            query.update({k: v for k, v in _missing_sku_query(user_id).items() if k != "user_id"})
        missing_cost_rows: dict[str, dict[str, Any]] = {}
        effective_from = from_date
        effective_to = to_date
        requested_product_ids = _parse_product_ids(product_ids)
        cohort_source = "workspace_orders"
        if missing_mezan_cost and sold_only:
            today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Riyadh")).date()
            effective_from = effective_from or today.replace(day=1).isoformat()
            effective_to = effective_to or today.isoformat()
            payment_method_list = [
                part.strip() for part in (payment_methods or "").split(",") if part.strip()
            ]
            shipping_company_list = [
                part.strip() for part in (shipping_companies or "").split(",") if part.strip()
            ]
            if requested_product_ids:
                cohort_source = "dashboard_snapshot_product_ids"
                missing_cost_rows = await _requested_missing_mezan_cost_products(
                    db,
                    user_id,
                    requested_product_ids,
                )
            else:
                missing_cost_rows = await _sold_missing_mezan_cost_products(
                    db,
                    user_id,
                    from_date=effective_from,
                    to_date=effective_to,
                    payment_methods=payment_method_list,
                    shipping_companies=shipping_company_list,
                )
            query["salla_product_id"] = {
                "$in": _mongo_id_values(list(missing_cost_rows))
            }
        elif requested_product_ids:
            identity_values = _mongo_id_values(requested_product_ids)
            query["$and"] = query.get("$and", []) + [{"$or": [
                {"salla_product_id": {"$in": identity_values}},
                {"mezan_product_id": {"$in": requested_product_ids}},
                {"id": {"$in": requested_product_ids}},
            ]}]
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
        for row in items:
            cost_status = missing_cost_rows.get(str(row.get("salla_product_id") or ""))
            if cost_status:
                row["mezan_cost_missing"] = True
                row["mezan_cost_status"] = cost_status
        return {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            },
            "meta": {
                "contract_version": "sold-missing-cost-v3"
                if missing_mezan_cost and sold_only else "workspace-products-v1",
                "sort": sort,
                "legacy_dependency": False,
                "source": PRODUCTS,
                "missing_mezan_cost": missing_mezan_cost,
                "sold_only": sold_only,
                "from": effective_from,
                "to": effective_to,
                "payment_methods": payment_methods,
                "shipping_companies": shipping_companies,
                "matched_sold_products": len(missing_cost_rows),
                "requested_product_ids_count": len(requested_product_ids),
                "cohort_source": cohort_source,
                "cost_semantics": {
                    "version": COST_SEMANTICS_VERSION,
                    "missing_mezan_cost": "explicit_mezan_cost_only",
                    "calculation_cost": "mezan_then_salla_fallback",
                },
            },
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
