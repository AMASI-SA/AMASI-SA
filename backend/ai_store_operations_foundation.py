"""AI-ready store operations foundation for Mezan OS.

This module intentionally starts read-mostly. It defines the permission model and
builds a Product Intake Queue from Product V2 without duplicating Salla's import
features or changing Salla/Qoyod data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ai_store_access_contract import AI_ACTION_LOG, PERMISSIONS, ROLE_ASSIGNMENTS, ROLE_CATALOG
from product_intelligence_foundation import (
    action_candidates,
    product_intelligence_foundation,
    product_intelligence_readiness,
)
from product_v2_routes import PRODUCTS
from product_v2_details_routes import COST_PROFILES

PRODUCT_MEDIA = "mezan_product_media_v2"
PRODUCT_MEDIA_DRAFTS = "mezan_product_media_drafts_v2"


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
        status: Literal["needs_attention", "ready", "all"] = "needs_attention",
        limit: int = Query(50, ge=1, le=200),
        page: int = Query(1, ge=1),
        q: str = Query("", max_length=160),
        category_ids: str = Query("", max_length=12000),
        components: bool | None = None,
        services: bool | None = None,
        service_scope: Literal["option_service", "no_option_service", "independent_service", "service_surcharge"] | None = None,
        option_surcharge: bool | None = None,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        from product_intake_search import search_intake
        from web_page_access import INTAKE_PAGE, delegated_page_owner

        owner_id = await delegated_page_owner(db, user, INTAKE_PAGE) or str(user["id"])
        flags = {key: value for key, value in {
            "components": components, "services": services, "option_surcharge": option_surcharge,
        }.items() if value is not None}
        if service_scope == "no_option_service":
            flags["option_service"] = False
        elif service_scope:
            flags[service_scope] = True
        result = await search_intake(
            db, owner_id, q=q, category_ids=list(dict.fromkeys(value.strip() for value in category_ids.split(",") if value.strip())),
            status=status, page=page, limit=limit, flags=flags,
        )
        return {"ok": True, "status": status, "generated_at": _now().isoformat(), **result}

    return router
