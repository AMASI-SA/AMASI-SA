"""AI-ready store operations foundation for Mezan OS.

This module intentionally starts read-mostly. It defines the permission model and
builds a Product Intake Queue from Product V2 without duplicating Salla's import
features or changing Salla/Qoyod data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from product_intelligence_foundation import (
    action_candidates,
    product_intelligence_foundation,
    product_intelligence_readiness,
)
from product_v2_routes import PRODUCTS
from product_v2_details_routes import COST_PROFILES

ROLE_ASSIGNMENTS = "mezan_role_assignments_v2"
AI_ACTION_LOG = "mezan_ai_action_log_v2"
PRODUCT_MEDIA = "mezan_product_media_v2"
PRODUCT_MEDIA_DRAFTS = "mezan_product_media_drafts_v2"

PERMISSIONS = {
    "products.read",
    "products.create",
    "products.review",
    "products.publish",
    "products.cost.read",
    "products.cost.write",
    "products.media.read",
    "products.media.upload",
    "products.media.edit",
    "products.media.delete",
    "products.media.reorder",
    "products.media.publish",
    "products.media.ai_generate",
    "products.media.ai_edit",
    "products.ai.recommend",
    "products.ai.execute_low_risk",
    "products.ai.execute_high_risk",
    "employees.read",
    "employees.manage",
    "roles.manage",
    "audit.read",
    "fulfillment.ready.read",
    "fulfillment.batch.claim",
    "fulfillment.labels.print",
    "fulfillment.labels.reprint",
    "fulfillment.pack.confirm",
    "fulfillment.carrier.handoff",
    "inventory.receipts.read",
    "inventory.receipts.write",
    "inventory.preparation.read",
    "inventory.preparation.create",
    "inventory.preparation.work",
    "inventory.preparation.receive",
    "supplier_receiving.product_price.edit",
    "supplier_receiving.service_price.edit",
    "supplier_receiving.service.add",
    "suppliers.read",
    "suppliers.manage",
    "inventory.salla_sync.read",
    "inventory.salla_sync.manage_mappings",
    "inventory.salla_sync.publish",
}

ROLE_CATALOG = {
    "owner": sorted(PERMISSIONS),
    "product_manager": sorted({
        "products.read", "products.create", "products.review", "products.publish",
        "products.cost.read", "products.media.read", "products.media.upload",
        "products.media.edit", "products.media.delete", "products.media.reorder",
        "products.media.publish", "products.ai.recommend", "audit.read",
        "suppliers.read",
    }),
    "product_operator": sorted({
        "products.read", "products.create", "products.review", "products.cost.read",
        "products.media.read", "products.media.upload", "products.media.edit",
        "products.media.reorder", "products.ai.recommend",
    }),
    "cost_manager": sorted({
        "products.read", "products.cost.read", "products.cost.write", "audit.read",
        "inventory.receipts.read", "inventory.receipts.write",
        "inventory.preparation.read", "inventory.preparation.create",
        "inventory.preparation.receive",
        "inventory.salla_sync.read",
        "suppliers.read",
    }),
    "warehouse_operator": sorted({
        "products.read", "products.cost.read",
        "fulfillment.ready.read", "fulfillment.batch.claim",
        "fulfillment.pack.confirm",
        "inventory.receipts.read", "inventory.receipts.write",
        "inventory.preparation.read", "inventory.preparation.create",
        "inventory.preparation.work", "inventory.preparation.receive",
        "inventory.salla_sync.read",
        "suppliers.read",
    }),
    "shipping_operator": sorted({
        "products.read",
        "fulfillment.ready.read",
        "fulfillment.batch.claim",
        "fulfillment.labels.print",
        "fulfillment.pack.confirm",
        "fulfillment.carrier.handoff",
    }),
    "marketing_manager": sorted({
        "products.read", "products.review", "products.media.read",
        "products.media.upload", "products.media.edit", "products.media.reorder",
        "products.ai.recommend", "audit.read",
    }),
    "ai_product_optimizer": sorted({
        "products.read", "products.cost.read", "products.media.read",
        "products.media.ai_generate", "products.media.ai_edit",
        "products.ai.recommend", "products.ai.execute_low_risk",
    }),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _has_description(product: dict[str, Any]) -> bool:
    return bool(_text(product.get("description_html") or product.get("description")))


def _image_count(product: dict[str, Any]) -> int:
    images = product.get("images")
    if isinstance(images, list):
        return len([row for row in images if row])
    return 1 if product.get("main_image") else 0


def _option_cost_links(product: dict[str, Any]) -> int:
    value = product.get("option_cost_links_count")
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def product_readiness(product: dict[str, Any], cost_profile: dict[str, Any] | None) -> dict[str, Any]:
    """Return deterministic readiness facts for human and AI operations."""
    cost_profile = cost_profile or {}
    checks = {
        "has_sku": bool(_text(product.get("sku"))),
        "has_images": _image_count(product) > 0,
        "has_description": _has_description(product),
        "has_category": bool(product.get("categories")),
        "has_base_cost": cost_profile.get("base_cost") is not None,
        "details_loaded": bool(product.get("details_loaded")),
    }
    options_count = int(product.get("options_count") or len(product.get("options") or []))
    checks["option_costs_ready"] = options_count == 0 or _option_cost_links(product) > 0

    weights = {
        "has_sku": 15,
        "has_images": 15,
        "has_description": 15,
        "has_category": 10,
        "has_base_cost": 25,
        "details_loaded": 10,
        "option_costs_ready": 10,
    }
    score = sum(weights[key] for key, passed in checks.items() if passed)
    blockers = [key for key, passed in checks.items() if not passed]
    return {
        "score": score,
        "ready": score == 100,
        "checks": checks,
        "blockers": blockers,
        "recommended_action": blockers[0] if blockers else None,
    }


def make_ai_store_operations_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/ai-store-operations", tags=["AI Store Operations"])

    @router.get("/foundation")
    async def foundation(user: dict = Depends(current_user)) -> dict[str, Any]:
        return {
            "ok": True,
            "principle": "mezan_ai_full_store_management",
            "mode": "governed_ai_operations",
            "role_catalog": ROLE_CATALOG,
            "permissions": sorted(PERMISSIONS),
            "media_collections": {
                "media": PRODUCT_MEDIA,
                "drafts": PRODUCT_MEDIA_DRAFTS,
            },
            "safety_flow": ["proposal", "preview", "approval", "execute", "verify", "audit", "rollback"],
        }

    @router.get("/product-intelligence/foundation")
    async def product_intelligence_rules(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        return product_intelligence_foundation()

    @router.get("/product-intelligence/products/{product_id}")
    async def product_intelligence_product(
        product_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await db[PRODUCTS].find_one(
            {
                "user_id": user_id,
                "$or": [
                    {"id": product_id},
                    {"mezan_product_id": product_id},
                    {"salla_product_id": product_id},
                ],
            },
            {"_id": 0, "raw_salla": 0, "raw_salla_details": 0},
        )
        if not product:
            raise HTTPException(
                status_code=404,
                detail={"code": "product_v2_not_found"},
            )
        salla_id = str(product.get("salla_product_id") or "")
        cost_profile = await db[COST_PROFILES].find_one(
            {"user_id": user_id, "salla_product_id": salla_id},
            {"_id": 0},
        )
        # Connector states remain explicitly absent until their V2 adapters
        # provide verified local snapshots.  This endpoint never calls them.
        readiness = product_intelligence_readiness(
            product,
            cost_profile,
            source_states={},
        )
        return {
            "ok": True,
            "mode": "rules_only",
            "legacy_dependency": False,
            "external_calls_made": False,
            "writes_made": False,
            "product": {
                "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
                "salla_product_id": product.get("salla_product_id"),
                "name": product.get("name"),
                "sku": product.get("sku"),
            },
            "readiness": readiness,
            "action_candidates": action_candidates(readiness),
        }

    @router.get("/product-intake")
    async def product_intake(
        status: str = Query("needs_attention"),
        limit: int = Query(50, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        products = await db[PRODUCTS].find(
            {"user_id": user_id},
            {"_id": 0, "raw_salla": 0, "raw_salla_details": 0},
        ).sort([("salla_created_at", -1), ("created_at", -1)]).limit(limit * 3).to_list(length=limit * 3)

        salla_ids = [str(row.get("salla_product_id")) for row in products if row.get("salla_product_id") is not None]
        profiles = await db[COST_PROFILES].find(
            {"user_id": user_id, "salla_product_id": {"$in": salla_ids}},
            {"_id": 0},
        ).to_list(length=max(len(salla_ids), 1))
        profile_by_product = {str(row.get("salla_product_id")): row for row in profiles}

        items = []
        for product in products:
            readiness = product_readiness(product, profile_by_product.get(str(product.get("salla_product_id"))))
            if status == "ready" and not readiness["ready"]:
                continue
            if status == "needs_attention" and readiness["ready"]:
                continue
            items.append({
                "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
                "salla_product_id": product.get("salla_product_id"),
                "name": product.get("name"),
                "sku": product.get("sku"),
                "status": product.get("status"),
                "main_image": product.get("main_image"),
                "created_at": product.get("salla_created_at") or product.get("created_at"),
                "readiness": readiness,
            })
            if len(items) >= limit:
                break

        return {
            "ok": True,
            "status": status,
            "items": items,
            "total": len(items),
            "generated_at": _now().isoformat(),
        }

    return router
