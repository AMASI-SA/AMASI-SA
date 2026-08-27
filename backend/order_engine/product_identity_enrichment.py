"""Resolve missing order-item catalogue identity from trusted local caches.

The order-item id remains immutable.  This module only fills missing product,
variant, SKU and barcode facts when one cached Salla/Product V2 document is a
unique exact match for an existing strong identifier.  Product names are
deliberately not used as identity evidence.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_put(index: dict[str, dict[str, Any] | None], key: Any, row: dict[str, Any]) -> None:
    value = _text(key)
    if not value:
        return
    current = index.get(value)
    if current is None and value in index:
        return
    if current is not None and current is not row:
        # The same Salla product is commonly mirrored in more than one local
        # cache.  Treat identical strong identities as one fact and keep the
        # first (Product V2 has collection priority); only genuinely distinct
        # identities make the lookup ambiguous.
        def signature(candidate: dict[str, Any]) -> tuple[str, ...]:
            product = (
                candidate.get("product")
                if isinstance(candidate.get("product"), dict)
                else candidate
            )
            variant = (
                candidate.get("variant")
                if isinstance(candidate.get("variant"), dict)
                else {}
            )
            return (
                _text(product.get("salla_product_id") or product.get("product_id") or product.get("id")),
                _text(product.get("parent_product_id")),
                _text(variant.get("id")),
                _text(variant.get("sku") or product.get("sku")),
            )

        if signature(current) == signature(row):
            return
        index[value] = None
        return
    index[value] = row


def _variant_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (document.get("variants") or []) if isinstance(row, dict)]


async def enrich_order_item_identity(
    db: Any,
    *,
    user_id: str,
    items: list[Any],
) -> list[Any]:
    """Fill missing identity fields without replacing historical item ids."""
    product_ids = {
        value
        for item in items
        for value in (
            _text(getattr(item, "product_id", None)),
            _text(getattr(item, "parent_product_id", None)),
        )
        if value
    }
    variant_ids = {
        _text(getattr(item, "variant_id", None))
        for item in items
        if _text(getattr(item, "variant_id", None))
    }
    skus = {
        _text(getattr(item, "sku", None))
        for item in items
        if _text(getattr(item, "sku", None))
    }
    clauses: list[dict[str, Any]] = []
    if product_ids:
        clauses.extend([
            {"salla_product_id": {"$in": sorted(product_ids)}},
            {"product_id": {"$in": sorted(product_ids)}},
            {"id": {"$in": sorted(product_ids)}},
            {"parent_product_id": {"$in": sorted(product_ids)}},
        ])
    if variant_ids:
        clauses.append({"variants.id": {"$in": sorted(variant_ids)}})
    if skus:
        clauses.extend([
            {"sku": {"$in": sorted(skus)}},
            {"variants.sku": {"$in": sorted(skus)}},
        ])
    if not clauses:
        return items

    projection = {
        "_id": 0,
        "salla_product_id": 1,
        "product_id": 1,
        "id": 1,
        "parent_product_id": 1,
        "sku": 1,
        "barcode": 1,
        "variants": 1,
    }
    documents: list[dict[str, Any]] = []
    for collection_name in ("mezan_products_v2", "salla_products", "products"):
        cursor = db[collection_name].find(
            {"user_id": str(user_id), "$or": clauses},
            projection,
        ).limit(1000)
        documents.extend([
            row async for row in cursor if isinstance(row, dict)
        ])

    by_product: dict[str, dict[str, Any] | None] = {}
    by_variant: dict[str, dict[str, Any] | None] = {}
    by_sku: dict[str, dict[str, Any] | None] = {}
    for document in documents:
        for value in (
            document.get("salla_product_id"),
            document.get("product_id"),
            document.get("id"),
            document.get("parent_product_id"),
        ):
            _unique_put(by_product, value, document)
        _unique_put(by_sku, document.get("sku"), document)
        for variant in _variant_rows(document):
            wrapped = {"product": document, "variant": variant}
            _unique_put(by_variant, variant.get("id"), wrapped)
            _unique_put(by_sku, variant.get("sku"), wrapped)

    enriched: list[Any] = []
    for item in items:
        match: dict[str, Any] | None = None
        variant_id = _text(getattr(item, "variant_id", None))
        product_id = _text(getattr(item, "product_id", None))
        parent_product_id = _text(getattr(item, "parent_product_id", None))
        sku = _text(getattr(item, "sku", None))
        if variant_id:
            match = by_variant.get(variant_id)
        if match is None and product_id:
            match = by_product.get(product_id)
        if match is None and parent_product_id:
            match = by_product.get(parent_product_id)
        if match is None and sku:
            match = by_sku.get(sku)
        if not match:
            enriched.append(item)
            continue

        document = match.get("product") if isinstance(match.get("product"), dict) else match
        variant = match.get("variant") if isinstance(match.get("variant"), dict) else None
        canonical_product_id = _text(
            document.get("salla_product_id")
            or document.get("product_id")
            or document.get("id")
        )
        patch = {
            "product_id": product_id or canonical_product_id or None,
            "parent_product_id": parent_product_id or _text(document.get("parent_product_id")) or None,
            "variant_id": variant_id or _text((variant or {}).get("id")) or None,
            "sku": sku or _text((variant or {}).get("sku")) or _text(document.get("sku")) or None,
            "barcode": (
                _text(getattr(item, "barcode", None))
                or _text((variant or {}).get("barcode"))
                or _text(document.get("barcode"))
                or None
            ),
        }
        if any(getattr(item, key, None) != value for key, value in patch.items()):
            item = item.model_copy(update=patch)
        enriched.append(item)
    return enriched


__all__ = ["enrich_order_item_identity"]
