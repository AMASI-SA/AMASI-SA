"""Governed Product Control Center for Mezan OS.

This module owns product-content drafts, approvals, publishing, verification and
rollback. Mezan cost collections are deliberately excluded from every payload.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ASCENDING

from product_v2_routes import PRODUCTS, _text
from salla_integration.service import SallaError, call_salla

DRAFTS = "mezan_product_change_drafts_v2"
REVISIONS = "mezan_product_change_revisions_v2"
POLICIES = "mezan_product_ai_policies_v2"
ACTIVE_DRAFT_STATUSES = ["draft", "approved"]

PROTECTED_FIELDS = {
    "base_cost", "variant_costs", "cost_price", "cost_price_from_salla",
    "unit_cost", "initial_unit_cost", "component_costs", "option_costs",
    "profit", "margin", "accounting", "qoyod",
}

PUBLISHABLE_FIELDS = {
    "name", "description", "short_description", "price", "sale_price",
    "salla_cost_price", "status", "sku", "barcode", "categories", "brand", "seo",
    "google_category", "local_category", "images", "options",
    "custom_fields", "variants", "slug",
}

DEFAULT_POLICY = {
    "mode": "proposal_only",
    "require_human_approval": True,
    "allow_content": True,
    "allow_images": False,
    "allow_price": False,
    "allow_categories": True,
    "allow_status": False,
    "min_margin_percent": 35.0,
    "max_price_change_percent": 10.0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_patch(payload: dict[str, Any]) -> dict[str, Any]:
    attempted = PROTECTED_FIELDS.intersection(payload)
    if attempted:
        raise HTTPException(status_code=422, detail={
            "code": "protected_mezan_cost_fields",
            "fields": sorted(attempted),
        })
    return {key: value for key, value in payload.items() if key in PUBLISHABLE_FIELDS}


def _serialize(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    result = dict(row)
    result.pop("_id", None)
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def _salla_payload(patch: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "name", "description", "short_description", "price", "sale_price",
        "status", "sku", "barcode", "categories", "brand", "images",
        "options", "custom_fields", "variants", "slug",
    ):
        if key in patch:
            payload[key] = patch[key]
    # `salla_cost_price` is deliberately a Product Control Center alias.
    # The canonical Mezan accounting/cost fields remain protected and are never
    # accepted from this draft path. Only the outbound Salla payload receives
    # the provider field name `cost_price`.
    if "salla_cost_price" in patch:
        payload["cost_price"] = patch["salla_cost_price"]
    seo = patch.get("seo")
    if isinstance(seo, dict):
        if seo.get("title") is not None:
            payload["seo_title"] = seo.get("title")
        if seo.get("description") is not None:
            payload["seo_description"] = seo.get("description")
        if seo.get("keywords") is not None:
            payload["keywords"] = seo.get("keywords")
    if patch.get("google_category") is not None:
        payload["google_product_category"] = patch.get("google_category")
    return payload


def _money_amount(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "total"):
            if key in value:
                return _money_amount(value.get(key))
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _salla_product(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _price_snapshot(product: dict[str, Any]) -> dict[str, float | None]:
    regular = _money_amount(product.get("regular_price"))
    if regular is None:
        regular = _money_amount(product.get("price"))
    return {
        "price": regular,
        "sale_price": _money_amount(product.get("sale_price")),
        "cost_price": _money_amount(product.get("cost_price") or product.get("cost")),
    }


def _salla_payload_with_preserved_prices(
    patch: dict[str, Any], current_product: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, float | None]]:
    """Build a complete price-safe Salla payload from a partial Mezan patch."""
    payload = _salla_payload(patch)
    snapshot = _price_snapshot(current_product)
    if snapshot["price"] is None:
        raise HTTPException(status_code=409, detail={"code": "salla_price_snapshot_missing"})
    if "price" not in payload:
        payload["price"] = snapshot["price"]
    if "sale_price" not in payload and snapshot["sale_price"] is not None:
        payload["sale_price"] = snapshot["sale_price"]
    expected = {
        "price": _money_amount(payload.get("price")),
        "sale_price": _money_amount(payload.get("sale_price")),
    }
    if "cost_price" in payload:
        expected["cost_price"] = _money_amount(payload.get("cost_price"))
    return payload, expected


def _verify_salla_prices(
    product: dict[str, Any], expected: dict[str, float | None]
) -> None:
    actual = _price_snapshot(product)
    mismatches: dict[str, dict[str, float | None]] = {}
    for key, expected_value in expected.items():
        if expected_value is None:
            continue
        actual_value = actual.get(key)
        if actual_value is None or abs(actual_value - expected_value) > 0.0001:
            mismatches[key] = {"expected": expected_value, "actual": actual_value}
    if mismatches:
        raise HTTPException(status_code=409, detail={
            "code": "salla_price_verification_failed",
            "mismatches": mismatches,
        })


def _before_value(product: dict[str, Any], key: str) -> Any:
    if key == "salla_cost_price":
        return product.get("cost_price_from_salla")
    return product.get(key)


async def _product(db: Any, user_id: str, product_id: str) -> dict[str, Any]:
    row = await db[PRODUCTS].find_one({
        "user_id": user_id,
        "$or": [
            {"id": product_id}, {"mezan_product_id": product_id},
            {"salla_product_id": product_id},
        ],
    }, {"_id": 0, "raw_salla": 0, "raw_salla_details": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
    return row


async def ensure_indexes(db: Any) -> None:
    await db[DRAFTS].create_index(
        [("user_id", ASCENDING), ("salla_product_id", ASCENDING), ("status", ASCENDING)],
        name="ix_product_drafts_v2",
    )
    await db[REVISIONS].create_index(
        [("user_id", ASCENDING), ("salla_product_id", ASCENDING), ("created_at", ASCENDING)],
        name="ix_product_revisions_v2",
    )
    await db[POLICIES].create_index(
        [("user_id", ASCENDING)], unique=True, name="uq_product_ai_policy_v2",
    )


async def supersede_active_drafts(db: Any, *, user_id: str, salla_id: str, keep_id: str | None = None, now: datetime | None = None) -> None:
    timestamp = now or _now()
    query: dict[str, Any] = {
        "user_id": user_id,
        "salla_product_id": salla_id,
        "status": {"$in": ACTIVE_DRAFT_STATUSES},
    }
    if keep_id:
        query["id"] = {"$ne": keep_id}
    await db[DRAFTS].update_many(query, {"$set": {
        "status": "superseded",
        "superseded_at": timestamp,
        "updated_at": timestamp,
        "superseded_by": keep_id,
    }})


def make_product_control_center_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product Control Center"])

    @router.get("/{product_id}/control-center")
    async def get_control_center(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_id = str(product["salla_product_id"])
        draft = await db[DRAFTS].find_one(
            {"user_id": user_id, "salla_product_id": salla_id, "status": {"$in": ACTIVE_DRAFT_STATUSES}},
            {"_id": 0}, sort=[("updated_at", -1)],
        )
        policy = await db[POLICIES].find_one({"user_id": user_id}, {"_id": 0}) or {
            "user_id": user_id, **DEFAULT_POLICY,
        }
        return {
            "product": _serialize(product),
            "draft": _serialize(draft),
            "policy": _serialize(policy),
            "protected_fields": sorted(PROTECTED_FIELDS),
            "capabilities": {
                "content": True, "seo": True, "categories": True,
                "images": True, "pricing": True, "options": True,
                "salla_cost_price": True,
                "rollback": True, "cost_engine_preserved": True,
            },
        }

    @router.put("/{product_id}/control-center/draft")
    async def save_draft(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        patch = _clean_patch(payload.get("changes") if isinstance(payload.get("changes"), dict) else payload)
        if not patch:
            raise HTTPException(status_code=422, detail={"code": "empty_product_change"})
        now = _now()
        salla_id = str(product["salla_product_id"])
        draft_id = uuid.uuid4().hex
        await supersede_active_drafts(db, user_id=user_id, salla_id=salla_id, keep_id=draft_id, now=now)
        row = {
            "id": draft_id,
            "user_id": user_id,
            "salla_product_id": salla_id,
            "mezan_product_id": product.get("mezan_product_id"),
            "status": "draft",
            "source": _text(payload.get("source")) or "human",
            "reason": _text(payload.get("reason")),
            "changes": patch,
            "before": {key: _before_value(product, key) for key in patch},
            "created_at": now,
            "updated_at": now,
        }
        await db[DRAFTS].insert_one(row)
        return {"ok": True, "draft": _serialize(row)}

    @router.post("/{product_id}/control-center/draft/{draft_id}/approve")
    async def approve_draft(product_id: str, draft_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        result = await db[DRAFTS].find_one_and_update(
            {"id": draft_id, "user_id": user_id, "salla_product_id": str(product["salla_product_id"]), "status": "draft"},
            {"$set": {"status": "approved", "approved_at": _now(), "updated_at": _now()}},
            return_document=True,
        )
        if not result:
            raise HTTPException(status_code=404, detail={"code": "draft_not_found"})
        await supersede_active_drafts(db, user_id=user_id, salla_id=str(product["salla_product_id"]), keep_id=draft_id)
        return {"ok": True, "draft": _serialize(result)}

    @router.post("/{product_id}/control-center/draft/{draft_id}/publish")
    async def publish_draft(product_id: str, draft_id: str, payload: dict = Body(default={}), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_id = str(product["salla_product_id"])
        draft = await db[DRAFTS].find_one({"id": draft_id, "user_id": user_id, "salla_product_id": salla_id}, {"_id": 0})
        if not draft or draft.get("status") != "approved":
            raise HTTPException(status_code=409, detail={"code": "draft_not_approved"})
        newest = await db[DRAFTS].find_one(
            {"user_id": user_id, "salla_product_id": salla_id, "status": {"$in": ACTIVE_DRAFT_STATUSES}},
            {"_id": 0}, sort=[("updated_at", -1)],
        )
        if newest and newest.get("id") != draft_id:
            raise HTTPException(status_code=409, detail={"code": "draft_superseded", "newest_draft_id": newest.get("id")})
        if payload.get("confirmation") != "نشر التعديل إلى سلة":
            raise HTTPException(status_code=409, detail={"code": "publish_confirmation_required"})
        patch = _clean_patch(draft.get("changes") or {})
        if not _salla_payload(patch):
            raise HTTPException(status_code=422, detail={"code": "no_salla_publishable_fields"})
        try:
            current_response = await call_salla(db, user_id, "GET", f"/products/{salla_id}")
            current_remote = _salla_product(current_response)
            remote_payload, expected_prices = _salla_payload_with_preserved_prices(patch, current_remote)
            response = await call_salla(db, user_id, "PUT", f"/products/{salla_id}", json=remote_payload)
            verified_response = await call_salla(db, user_id, "GET", f"/products/{salla_id}")
            verified_remote = _salla_product(verified_response)
            try:
                _verify_salla_prices(verified_remote, expected_prices)
            except HTTPException:
                rollback_payload = {key: value for key, value in expected_prices.items() if value is not None}
                if rollback_payload:
                    await call_salla(db, user_id, "PUT", f"/products/{salla_id}", json=rollback_payload)
                raise
        except SallaError as exc:
            raise HTTPException(status_code=exc.status_code if exc.status_code != 200 else 400, detail={"code": "salla_product_publish_failed", "message": str(exc), "needs_reauth": exc.needs_reauth}) from exc
        now = _now()
        revision = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "salla_product_id": salla_id,
            "mezan_product_id": product.get("mezan_product_id"),
            "draft_id": draft_id,
            "before": draft.get("before") or {},
            "after": patch,
            "source": draft.get("source"),
            "reason": draft.get("reason"),
            "salla_response": response,
            "created_at": now,
        }
        await db[REVISIONS].insert_one(revision)
        await db[DRAFTS].update_one({"id": draft_id, "user_id": user_id}, {"$set": {
            "status": "published", "published_at": now, "updated_at": now, "revision_id": revision["id"],
        }})
        await supersede_active_drafts(db, user_id=user_id, salla_id=salla_id, keep_id=draft_id, now=now)
        local_patch = {key: value for key, value in patch.items() if key != "salla_cost_price"}
        if "salla_cost_price" in patch:
            local_patch["cost_price_from_salla"] = _money_amount(
                verified_remote.get("cost_price") or verified_remote.get("cost")
            )
        await db[PRODUCTS].update_one({"user_id": user_id, "salla_product_id": salla_id}, {"$set": {**local_patch, "updated_at": now, "last_control_center_publish_at": now}})
        return {"ok": True, "revision": _serialize(revision), "cost_engine_preserved": True}

    @router.get("/{product_id}/control-center/history")
    async def history(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        rows = await db[REVISIONS].find({"user_id": user_id, "salla_product_id": str(product["salla_product_id"])}, {"_id": 0}).sort("created_at", -1).to_list(length=100)
        return {"items": [_serialize(row) for row in rows], "total": len(rows)}

    @router.put("/control-center/policy")
    async def save_policy(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        allowed = set(DEFAULT_POLICY)
        policy = {key: payload[key] for key in allowed if key in payload}
        policy["user_id"] = user_id
        policy["updated_at"] = _now()
        await db[POLICIES].update_one({"user_id": user_id}, {"$set": policy, "$setOnInsert": {"created_at": _now()}}, upsert=True)
        saved = await db[POLICIES].find_one({"user_id": user_id}, {"_id": 0})
        return {"ok": True, "policy": _serialize(saved)}

    return router