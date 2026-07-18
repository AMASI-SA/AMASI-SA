"""Fill missing order-item images from Mezan's local product caches.

This enrichment never calls Salla.  It keeps order/detail reads local while
recovering catalogue images by product id, parent product id, or SKU.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate if candidate.startswith(("https://", "http://")) else ""
    if isinstance(value, dict):
        for key in (
            "url", "original", "main_image", "image_url", "image",
            "medium", "large", "thumbnail", "small",
        ):
            candidate = _url(value.get(key))
            if candidate:
                return candidate
        for child in value.values():
            candidate = _url(child)
            if candidate:
                return candidate
    if isinstance(value, list):
        for child in value:
            candidate = _url(child)
            if candidate:
                return candidate
    return ""


def _document_image(document: dict[str, Any]) -> str:
    for key in (
        "main_image", "image_url", "image", "thumbnail",
        "product_thumbnail", "images", "media",
    ):
        candidate = _url(document.get(key))
        if candidate:
            return candidate
    return ""


async def enrich_order_item_images(
    db: Any,
    *,
    user_id: str,
    items: list[Any],
) -> list[Any]:
    missing = [
        item for item in items
        if not _text(getattr(item, "image_url", None))
    ]
    if not missing:
        return items

    product_ids = {
        value
        for item in missing
        for value in (
            _text(getattr(item, "product_id", None)),
            _text(getattr(item, "parent_product_id", None)),
        )
        if value
    }
    skus = {
        _text(getattr(item, "sku", None))
        for item in missing
        if _text(getattr(item, "sku", None))
    }
    clauses = []
    if product_ids:
        clauses.extend([
            {"product_id": {"$in": list(product_ids)}},
            {"id": {"$in": list(product_ids)}},
        ])
    if skus:
        clauses.append({"sku": {"$in": list(skus)}})
    if not clauses:
        return items

    query = {"user_id": str(user_id), "$or": clauses}
    projection = {
        "_id": 0, "product_id": 1, "id": 1, "parent_product_id": 1,
        "sku": 1, "main_image": 1, "image_url": 1, "image": 1,
        "thumbnail": 1, "product_thumbnail": 1, "images": 1, "media": 1,
    }
    documents: list[dict[str, Any]] = []
    for collection_name in ("salla_products", "products"):
        cursor = db[collection_name].find(query, projection).limit(500)
        documents.extend([
            document async for document in cursor
            if isinstance(document, dict)
        ])

    by_product_id: dict[str, str] = {}
    by_sku: dict[str, str] = {}
    for document in documents:
        image = _document_image(document)
        if not image:
            continue
        for key in (
            document.get("product_id"),
            document.get("id"),
            document.get("parent_product_id"),
        ):
            normalized = _text(key)
            if normalized:
                by_product_id.setdefault(normalized, image)
        sku = _text(document.get("sku"))
        if sku:
            by_sku.setdefault(sku, image)

    enriched = []
    for item in items:
        existing = _text(getattr(item, "image_url", None))
        image = existing
        if not image:
            for key in (
                getattr(item, "product_id", None),
                getattr(item, "parent_product_id", None),
            ):
                image = by_product_id.get(_text(key), "")
                if image:
                    break
        if not image:
            image = by_sku.get(_text(getattr(item, "sku", None)), "")
        if image and not existing:
            current_urls = list(getattr(item, "image_urls", None) or [])
            urls = [image, *[url for url in current_urls if url != image]]
            item = item.model_copy(
                update={"image_url": image, "image_urls": urls}
            )
        enriched.append(item)
    return enriched
