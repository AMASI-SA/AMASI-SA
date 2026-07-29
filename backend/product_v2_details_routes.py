"""Full product details and independent Mezan cost/image profiles for Product V2."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ASCENDING

from product_v2_routes import PRODUCTS, _now, _number, _text, normalize_salla_product
from salla_integration.service import SallaError, call_salla

COST_PROFILES = "mezan_product_cost_profiles_v2"
IMAGE_PROFILES = "mezan_product_image_profiles_v2"
DETAIL_LOG = "mezan_product_detail_log_v2"
IMAGE_PROFILE_LOG = "mezan_product_image_profile_log_v2"


def _unwrap(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _image_record(value: Any, index: int) -> dict[str, Any] | None:
    if isinstance(value, str):
        url = value.strip()
        return {"id": str(index), "url": url, "is_main": index == 0, "sort": index} if url else None
    if not isinstance(value, dict):
        return None
    url = _text(value.get("url") or value.get("original") or value.get("src") or value.get("full") or value.get("medium"))
    if not url:
        return None
    return {
        "id": _text(value.get("id")) or str(index),
        "url": url,
        "thumbnail": _text(value.get("thumbnail") or value.get("small")) or None,
        "alt": _text(value.get("alt") or value.get("title")) or None,
        "is_main": bool(value.get("is_main") or value.get("main") or index == 0),
        "sort": value.get("sort") if value.get("sort") is not None else index,
    }


def _image_identity(url: Any) -> str:
    """Return a stable identity for the same Salla image across URL variants."""
    text = _text(url)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        path = unquote(parsed.path).rstrip("/")
    except Exception:
        path = text.split("?", 1)[0].rstrip("/")
    filename = PurePosixPath(path).name.lower()
    return filename or path.lower()


def _dedupe_images(rows: list[dict[str, Any]], main_image: Any = None, product_name: Any = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for row in rows:
        image_id = _text(row.get("id"))
        identity = _image_identity(row.get("url"))
        if (image_id and image_id in seen_ids) or (identity and identity in seen_urls):
            continue
        if image_id:
            seen_ids.add(image_id)
        if identity:
            seen_urls.add(identity)
        result.append(dict(row))
    main_identity = _image_identity(main_image)
    if main_image and main_identity and main_identity not in seen_urls:
        result.insert(0, {
            "id": "main",
            "url": _text(main_image),
            "thumbnail": None,
            "alt": _text(product_name) or None,
            "is_main": True,
            "sort": 0,
        })
    elif main_identity:
        for row in result:
            if _image_identity(row.get("url")) == main_identity:
                row["is_main"] = True
                break
    result.sort(key=lambda row: (0 if row.get("is_main") else 1, row.get("sort") if row.get("sort") is not None else 999999))
    for index, row in enumerate(result):
        row["sort"] = index
    return result


def _option_value(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"id": str(index), "name": _text(value), "price": None, "sku": None, "quantity": None}
    return {
        "id": _text(value.get("id")) or str(index),
        "name": _text(value.get("name") or value.get("value") or value.get("label")),
        "price": _number(value.get("price") or value.get("additional_price")),
        "sku": _text(value.get("sku")) or None,
        "barcode": _text(value.get("barcode")) or None,
        "quantity": _number(value.get("quantity") or value.get("stock_quantity")),
        "image": _text(value.get("image") or value.get("image_url")) or None,
    }


def _normalize_options(raw: Any) -> list[dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    result = []
    for index, option in enumerate(rows):
        if not isinstance(option, dict):
            continue
        values = option.get("values") or option.get("options") or []
        result.append({
            "id": _text(option.get("id")) or str(index),
            "name": _text(option.get("name") or option.get("title") or option.get("label")),
            "type": _text(option.get("type")) or "select",
            "required": bool(option.get("required") or option.get("is_required")),
            "values": [_option_value(value, value_index) for value_index, value in enumerate(values if isinstance(values, list) else [])],
        })
    return result


def _normalize_variants(raw: Any) -> list[dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    result = []
    for index, variant in enumerate(rows):
        if not isinstance(variant, dict):
            continue
        selections = variant.get("options") or variant.get("values") or variant.get("attributes") or []
        if isinstance(selections, dict):
            selections = [{"name": key, "value": value} for key, value in selections.items()]
        result.append({
            "id": _text(variant.get("id")) or str(index),
            "name": _text(variant.get("name") or variant.get("title")) or None,
            "sku": _text(variant.get("sku")) or None,
            "barcode": _text(variant.get("barcode") or variant.get("gtin")) or None,
            "price": _number(variant.get("price")),
            "sale_price": _number(variant.get("sale_price") or variant.get("discount_price")),
            "cost_price_from_salla": _number(variant.get("cost_price") or variant.get("cost")),
            "quantity": _number(variant.get("quantity") or variant.get("stock_quantity")),
            "unlimited_quantity": bool(variant.get("unlimited_quantity") or variant.get("is_infinite")),
            "image": _text(variant.get("image") or variant.get("image_url")) or None,
            "selections": selections if isinstance(selections, list) else [],
        })
    return result


def _details_patch(raw: dict[str, Any], *, user_id: str) -> dict[str, Any]:
    normalized = normalize_salla_product(raw, user_id=user_id, synced_at=_now())
    images_raw = raw.get("images") or raw.get("media") or []
    image_rows = []
    for index, value in enumerate(images_raw if isinstance(images_raw, list) else []):
        record = _image_record(value, index)
        if record:
            image_rows.append(record)
    images = _dedupe_images(image_rows, normalized.get("main_image"), normalized.get("name"))
    options = _normalize_options(raw.get("options") or raw.get("product_options"))
    variants = _normalize_variants(raw.get("variants") or raw.get("skus") or raw.get("product_variants"))
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    seo = raw.get("seo") if isinstance(raw.get("seo"), dict) else {}
    return {
        **normalized,
        "description_html": raw.get("description") if isinstance(raw.get("description"), str) else normalized.get("description"),
        "short_description": _text(raw.get("short_description")) or None,
        "images": images,
        "options": options,
        "variants": variants,
        "options_count": len(options),
        "variants_count": len(variants),
        "seo": {
            "title": _text(seo.get("title") or raw.get("seo_title") or metadata.get("title")) or None,
            "description": _text(seo.get("description") or raw.get("seo_description") or metadata.get("description")) or None,
            "keywords": seo.get("keywords") or raw.get("keywords") or metadata.get("keywords") or [],
            "slug": _text(raw.get("slug") or raw.get("url_slug")) or None,
        },
        "custom_fields": raw.get("custom_fields") if isinstance(raw.get("custom_fields"), list) else [],
        "details_loaded": True,
        "details_synced_at": _now(),
        "raw_salla_details": raw,
    }


async def ensure_detail_indexes(db: Any) -> None:
    await db[COST_PROFILES].create_index([("user_id", ASCENDING), ("salla_product_id", ASCENDING)], unique=True, name="uq_product_cost_profile_v2")
    await db[IMAGE_PROFILES].create_index([("user_id", ASCENDING), ("salla_product_id", ASCENDING)], unique=True, name="uq_product_image_profile_v2")
    await db[IMAGE_PROFILE_LOG].create_index([("user_id", ASCENDING), ("salla_product_id", ASCENDING), ("occurred_at", ASCENDING)], name="ix_product_image_profile_log_v2")


def _product_lookup(product_id: str, user_id: str) -> dict[str, Any]:
    return {"user_id": user_id, "$or": [{"id": product_id}, {"mezan_product_id": product_id}, {"salla_product_id": product_id}]}


def _serialize_profile(profile: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    updated_at = profile.get("updated_at")
    return {
        "salla_product_id": str(product.get("salla_product_id") or ""),
        "default_image_url": profile.get("default_image_url"),
        "rules": profile.get("rules") or [],
        "images": product.get("images") or [],
        "options": product.get("options") or [],
        "fallback_image_url": product.get("main_image"),
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


def _valid_image_urls(product: dict[str, Any]) -> set[str]:
    urls = {_text(product.get("main_image"))}
    for row in product.get("images") or []:
        if isinstance(row, dict):
            urls.add(_text(row.get("url")))
    return {url for url in urls if url}


def _normalize_conditions(raw: Any, product: dict[str, Any]) -> list[dict[str, str]]:
    conditions = raw if isinstance(raw, list) else []
    option_map: dict[str, dict[str, Any]] = {
        str(option.get("id")): option for option in (product.get("options") or []) if isinstance(option, dict) and option.get("id") is not None
    }
    normalized: list[dict[str, str]] = []
    seen_options: set[str] = set()
    for row in conditions:
        if not isinstance(row, dict):
            raise HTTPException(status_code=422, detail={"code": "invalid_image_rule_condition"})
        option_id = _text(row.get("option_id"))
        value_id = _text(row.get("value_id"))
        option = option_map.get(option_id)
        if not option or option_id in seen_options:
            raise HTTPException(status_code=422, detail={"code": "invalid_or_duplicate_image_rule_option", "option_id": option_id})
        value = next((item for item in (option.get("values") or []) if str(item.get("id")) == value_id), None)
        if not value:
            raise HTTPException(status_code=422, detail={"code": "invalid_image_rule_value", "option_id": option_id, "value_id": value_id})
        seen_options.add(option_id)
        normalized.append({
            "option_id": option_id,
            "option_name": _text(option.get("name")),
            "value_id": value_id,
            "value_name": _text(value.get("name")),
        })
    normalized.sort(key=lambda row: (row["option_id"], row["value_id"]))
    return normalized


def _rule_signature(conditions: list[dict[str, str]]) -> str:
    return "|".join(f'{row["option_id"]}:{row["value_id"]}' for row in conditions)


def make_product_v2_details_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Products V2 Details"])

    @router.post("/{product_id}/refresh-details")
    async def refresh_product_details(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await db[PRODUCTS].find_one(_product_lookup(product_id, user_id), {"_id": 0})
        if not product:
            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
        salla_id = str(product["salla_product_id"])
        try:
            response = await call_salla(db, user_id, "GET", f"/products/{salla_id}")
        except SallaError as exc:
            raise HTTPException(status_code=exc.status_code if exc.status_code != 200 else 400, detail={"message": str(exc), "needs_reauth": exc.needs_reauth}) from exc
        raw = _unwrap(response)
        if not raw:
            raise HTTPException(status_code=502, detail={"code": "salla_product_details_empty"})
        patch = _details_patch(raw, user_id=user_id)
        now = _now()
        await db[PRODUCTS].update_one({"user_id": user_id, "salla_product_id": salla_id}, {"$set": {**patch, "updated_at": now}})
        await db[DETAIL_LOG].insert_one({"id": uuid.uuid4().hex, "user_id": user_id, "salla_product_id": salla_id, "event_type": "details_refreshed", "occurred_at": now, "options_count": len(patch["options"]), "variants_count": len(patch["variants"]), "images_count": len(patch["images"])})
        updated = await db[PRODUCTS].find_one({"user_id": user_id, "salla_product_id": salla_id}, {"_id": 0, "raw_salla": 0, "raw_salla_details": 0})
        for key in ("created_at", "updated_at", "last_synced_at", "details_synced_at"):
            if updated and hasattr(updated.get(key), "isoformat"):
                updated[key] = updated[key].isoformat()
        return {"ok": True, "product": updated}

    @router.get("/{product_id}/costs")
    async def get_product_costs(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_detail_indexes(db)
        user_id = str(user["id"])
        product = await db[PRODUCTS].find_one(_product_lookup(product_id, user_id), {"_id": 0, "salla_product_id": 1, "cost_price_from_salla": 1, "variants": 1})
        if not product:
            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
        profile = await db[COST_PROFILES].find_one({"user_id": user_id, "salla_product_id": str(product["salla_product_id"])}, {"_id": 0}) or {}
        return {
            "salla_product_id": str(product["salla_product_id"]),
            "cost_price_from_salla": product.get("cost_price_from_salla"),
            "base_cost": profile.get("base_cost"),
            "variant_costs": profile.get("variant_costs") or {},
            "notes": profile.get("notes") or "",
            "updated_at": profile.get("updated_at").isoformat() if hasattr(profile.get("updated_at"), "isoformat") else profile.get("updated_at"),
        }

    @router.put("/{product_id}/costs")
    async def save_product_costs(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_detail_indexes(db)
        user_id = str(user["id"])
        product = await db[PRODUCTS].find_one(_product_lookup(product_id, user_id), {"_id": 0, "salla_product_id": 1, "variants": 1})
        if not product:
            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
        base_cost = _number(payload.get("base_cost"))
        variant_costs_raw = payload.get("variant_costs") if isinstance(payload.get("variant_costs"), dict) else {}
        valid_variant_ids = {str(row.get("id")) for row in (product.get("variants") or []) if isinstance(row, dict) and row.get("id") is not None}
        variant_costs = {}
        for key, value in variant_costs_raw.items():
            if str(key) not in valid_variant_ids:
                continue
            parsed = _number(value)
            if parsed is not None and parsed >= 0:
                variant_costs[str(key)] = parsed
        if base_cost is not None and base_cost < 0:
            raise HTTPException(status_code=422, detail={"code": "negative_base_cost"})
        now = _now()
        salla_id = str(product["salla_product_id"])
        await db[COST_PROFILES].update_one({"user_id": user_id, "salla_product_id": salla_id}, {"$set": {"user_id": user_id, "salla_product_id": salla_id, "base_cost": base_cost, "variant_costs": variant_costs, "notes": _text(payload.get("notes")), "updated_at": now}, "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": now}}, upsert=True)
        return {"ok": True, "salla_product_id": salla_id, "base_cost": base_cost, "variant_costs": variant_costs, "updated_at": now.isoformat()}

    @router.get("/{product_id}/image-profile")
    async def get_product_image_profile(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_detail_indexes(db)
        user_id = str(user["id"])
        product = await db[PRODUCTS].find_one(_product_lookup(product_id, user_id), {"_id": 0, "salla_product_id": 1, "main_image": 1, "images": 1, "options": 1})
        if not product:
            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
        profile = await db[IMAGE_PROFILES].find_one({"user_id": user_id, "salla_product_id": str(product["salla_product_id"])}, {"_id": 0}) or {}
        return _serialize_profile(profile, product)

    @router.put("/{product_id}/image-profile")
    async def save_product_image_profile(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_detail_indexes(db)
        user_id = str(user["id"])
        product = await db[PRODUCTS].find_one(_product_lookup(product_id, user_id), {"_id": 0, "salla_product_id": 1, "main_image": 1, "images": 1, "options": 1})
        if not product:
            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
        valid_urls = _valid_image_urls(product)
        default_image_url = _text(payload.get("default_image_url")) or None
        if default_image_url and default_image_url not in valid_urls:
            raise HTTPException(status_code=422, detail={"code": "image_not_in_product_gallery"})
        raw_rules = payload.get("rules") if isinstance(payload.get("rules"), list) else []
        rules: list[dict[str, Any]] = []
        signatures: set[str] = set()
        for index, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                raise HTTPException(status_code=422, detail={"code": "invalid_image_rule"})
            image_url = _text(raw_rule.get("image_url"))
            if not image_url or image_url not in valid_urls:
                raise HTTPException(status_code=422, detail={"code": "image_rule_url_not_in_product_gallery", "index": index})
            conditions = _normalize_conditions(raw_rule.get("conditions"), product)
            if not conditions:
                raise HTTPException(status_code=422, detail={"code": "image_rule_requires_condition", "index": index})
            signature = _rule_signature(conditions)
            if signature in signatures:
                raise HTTPException(status_code=409, detail={"code": "duplicate_image_rule_conditions", "signature": signature})
            signatures.add(signature)
            rules.append({
                "id": _text(raw_rule.get("id")) or uuid.uuid4().hex,
                "image_url": image_url,
                "conditions": conditions,
                "condition_count": len(conditions),
                "signature": signature,
                "enabled": raw_rule.get("enabled") is not False,
            })
        rules.sort(key=lambda row: (-row["condition_count"], row["signature"]))
        now = _now()
        salla_id = str(product["salla_product_id"])
        document = {
            "user_id": user_id,
            "salla_product_id": salla_id,
            "default_image_url": default_image_url,
            "rules": rules,
            "updated_by": user_id,
            "updated_at": now,
        }
        await db[IMAGE_PROFILES].update_one(
            {"user_id": user_id, "salla_product_id": salla_id},
            {"$set": document, "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": now}},
            upsert=True,
        )
        await db[IMAGE_PROFILE_LOG].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "salla_product_id": salla_id,
            "event_type": "image_profile_saved",
            "default_image_url": default_image_url,
            "rules_count": len(rules),
            "occurred_at": now,
        })
        return {"ok": True, **_serialize_profile(document, product)}

    return router
