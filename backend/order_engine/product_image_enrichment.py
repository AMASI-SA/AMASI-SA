"""Complete sold order-item image galleries from Mezan's local catalog."""
from __future__ import annotations

import logging
from typing import Any

from product_v2_routes import PRODUCTS

IMAGE_PROFILES = "mezan_product_image_profiles_v2"
logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate if candidate.startswith(("https://", "http://")) else ""
    if isinstance(value, dict):
        for key in (
            "url", "original", "src", "full", "main_image", "image_url",
            "image", "medium", "large", "thumbnail", "small",
        ):
            candidate = _url(value.get(key))
            if candidate:
                return candidate
    return ""


def _urls(value: Any) -> list[str]:
    if isinstance(value, list):
        return [candidate for child in value for candidate in _urls(child)]
    candidate = _url(value)
    return [candidate] if candidate else []


def _document_images(document: dict[str, Any]) -> list[str]:
    images: list[str] = []
    for key in (
        "main_image", "image_url", "image", "thumbnail",
        "product_thumbnail", "images", "media",
    ):
        _merge_images(images, _urls(document.get(key)))
    return images


def _merge_images(target: list[str], candidates: list[str]) -> None:
    for candidate in candidates:
        normalized = _text(candidate)
        if (
            normalized.startswith(("https://", "http://"))
            and normalized not in target
        ):
            target.append(normalized)


async def _documents(
    db: Any,
    collection_name: str,
    query: dict[str, Any],
    projection: dict[str, int],
) -> list[dict[str, Any]]:
    try:
        cursor = db[collection_name].find(query, projection).limit(500)
        return [
            document async for document in cursor
            if isinstance(document, dict)
        ]
    except Exception:
        logger.exception(
            "Could not read local product images from %s",
            collection_name,
        )
        return []


async def enrich_order_item_images(
    db: Any,
    *,
    user_id: str,
    items: list[Any],
) -> list[Any]:
    """Add local catalog alternatives without calling Salla.

    The order snapshot remains the first choice. Catalog/profile URLs become
    fallbacks, allowing the frontend to move to the next URL if one is broken.
    """
    if not items or not hasattr(db, "__getitem__"):
        return items

    product_ids = {
        value
        for item in items
        for value in (
            _text(getattr(item, "product_id", None)),
            _text(getattr(item, "parent_product_id", None)),
            _text(getattr(item, "variant_id", None)),
        )
        if value
    }
    skus = {
        _text(getattr(item, "sku", None))
        for item in items
        if _text(getattr(item, "sku", None))
    }
    clauses: list[dict[str, Any]] = []
    if product_ids:
        ids = list(product_ids)
        clauses.extend([
            {"product_id": {"$in": ids}},
            {"id": {"$in": ids}},
            {"parent_product_id": {"$in": ids}},
            {"salla_product_id": {"$in": ids}},
            {"mezan_product_id": {"$in": ids}},
            {"variants.id": {"$in": ids}},
        ])
    if skus:
        sku_values = list(skus)
        clauses.extend([
            {"sku": {"$in": sku_values}},
            {"variants.sku": {"$in": sku_values}},
        ])
    if not clauses:
        return items

    query = {"user_id": str(user_id), "$or": clauses}
    projection = {
        "_id": 0,
        "product_id": 1,
        "id": 1,
        "parent_product_id": 1,
        "salla_product_id": 1,
        "mezan_product_id": 1,
        "sku": 1,
        "main_image": 1,
        "image_url": 1,
        "image": 1,
        "thumbnail": 1,
        "product_thumbnail": 1,
        "images": 1,
        "media": 1,
        "variants": 1,
    }
    documents: list[dict[str, Any]] = []
    for collection_name in (PRODUCTS, "salla_products", "products"):
        documents.extend(
            await _documents(db, collection_name, query, projection)
        )

    profile_documents: list[dict[str, Any]] = []
    catalog_ids = {
        _text(document.get("salla_product_id"))
        for document in documents
        if _text(document.get("salla_product_id"))
    }
    if catalog_ids:
        profile_documents = await _documents(
            db,
            IMAGE_PROFILES,
            {
                "user_id": str(user_id),
                "salla_product_id": {"$in": list(catalog_ids)},
            },
            {
                "_id": 0,
                "salla_product_id": 1,
                "default_image_url": 1,
            },
        )

    profile_by_product_id = {
        _text(profile.get("salla_product_id")): _urls(
            profile.get("default_image_url")
        )
        for profile in profile_documents
        if _text(profile.get("salla_product_id"))
    }
    by_product_id: dict[str, list[str]] = {}
    by_sku: dict[str, list[str]] = {}
    for document in documents:
        product_images: list[str] = []
        salla_product_id = _text(document.get("salla_product_id"))
        _merge_images(
            product_images,
            profile_by_product_id.get(salla_product_id, []),
        )
        _merge_images(product_images, _document_images(document))
        for key in (
            document.get("product_id"),
            document.get("id"),
            document.get("parent_product_id"),
            document.get("salla_product_id"),
            document.get("mezan_product_id"),
        ):
            normalized = _text(key)
            if normalized:
                _merge_images(
                    by_product_id.setdefault(normalized, []),
                    product_images,
                )
        sku = _text(document.get("sku"))
        if sku:
            _merge_images(by_sku.setdefault(sku, []), product_images)

        for variant in document.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            variant_images: list[str] = []
            _merge_images(
                variant_images,
                _urls(variant.get("image") or variant.get("image_url")),
            )
            _merge_images(variant_images, product_images)
            variant_id = _text(variant.get("id"))
            variant_sku = _text(variant.get("sku"))
            if variant_id:
                _merge_images(
                    by_product_id.setdefault(variant_id, []),
                    variant_images,
                )
            if variant_sku:
                _merge_images(
                    by_sku.setdefault(variant_sku, []),
                    variant_images,
                )

    enriched = []
    for item in items:
        existing = _text(getattr(item, "image_url", None))
        urls: list[str] = []
        _merge_images(
            urls,
            [existing, *(getattr(item, "image_urls", None) or [])],
        )
        for key in (
            getattr(item, "variant_id", None),
            getattr(item, "parent_product_id", None),
            getattr(item, "product_id", None),
        ):
            _merge_images(urls, by_product_id.get(_text(key), []))
        _merge_images(
            urls,
            by_sku.get(_text(getattr(item, "sku", None)), []),
        )

        image = existing or (urls[0] if urls else "")
        if (
            image != existing
            or urls != list(getattr(item, "image_urls", None) or [])
        ):
            item = item.model_copy(
                update={"image_url": image or None, "image_urls": urls}
            )
        enriched.append(item)
    return enriched
