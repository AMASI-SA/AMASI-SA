"""Compatibility adapter from V3 items to the existing order document shape."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from salla_integration.sync import _salla_order_to_doc


COMPATIBILITY_FIELDS = {
    "order_number",
    "order_id",
    "source",
    "products",
    "order_status",
    "order_status_slug",
    "payment_status",
    "payment_method",
    "subtotal",
    "discount",
    "tax",
    "total_amount",
    "currency",
    "provider_created_at",
}


def _iso(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("date") or value.get("value") or value
    return value


def _source_item_id(normalized: dict[str, Any]) -> str:
    return str(normalized.get("source_item_id") or "").strip()


def _compatibility_item_id(normalized: dict[str, Any]) -> str:
    return _source_item_id(normalized) or str(
        normalized.get("order_item_id") or ""
    ).strip()


def _legacy_product(
    legacy: Optional[dict[str, Any]],
    normalized: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(legacy or {})
    result.update({
        "order_item_id": _compatibility_item_id(normalized),
        "product_id": normalized.get("product_id") or "",
        "parent_product_id": normalized.get("parent_product_id") or "",
        "variant_id": normalized.get("variant_id") or "",
        "sku": normalized.get("sku") or "",
        "name": normalized.get("name") or "",
        "quantity": (
            normalized.get("quantity")
            if normalized.get("quantity") is not None
            else 1
        ),
        "price": normalized.get("unit_price") or 0,
        "total": normalized.get("total_price") or 0,
        "discount": normalized.get("discount") or 0,
        "tax": normalized.get("tax") or 0,
        "image_url": normalized.get("image_url") or "",
        "options": deepcopy(normalized.get("options") or []),
        "custom_fields": deepcopy(normalized.get("custom_fields") or []),
        "raw_item": deepcopy(normalized.get("raw_item") or {}),
        "canonical_order_item_id": normalized.get("order_item_id"),
    })
    return result


def build_compatibility_order(
    base_order: dict[str, Any],
    *,
    normalized_items: Iterable[dict[str, Any]],
    items_sync_status: str,
    items_payload_valid: bool,
    items_sync_error: Optional[str] = None,
    items_synced_at: Optional[str] = None,
    event_created_at: Any = None,
    sync_revision: int = 1,
) -> dict[str, Any]:
    """Emit the current order/product schema plus V3 audit metadata."""
    base = deepcopy(base_order or {})
    normalized = [deepcopy(item) for item in normalized_items]
    base["items"] = [deepcopy(item.get("raw_item") or {}) for item in normalized]
    legacy_doc = _salla_order_to_doc(base)

    legacy_by_id = {
        str(item.get("order_item_id") or "").strip(): item
        for item in legacy_doc.get("products") or []
        if isinstance(item, dict)
    }
    products = [
        _legacy_product(legacy_by_id.get(_source_item_id(item)), item)
        for item in normalized
    ]

    now = datetime.now(timezone.utc).isoformat()
    legacy_doc.update({
        "products": products,
        "provider_created_at": _iso(
            base.get("created_at") or base.get("date") or base.get("order_date")
        ),
        "provider_updated_at": _iso(
            base.get("updated_at") or base.get("modified_at")
        ),
        "event_created_at": _iso(event_created_at),
        "ingested_at": now,
        "sync_revision": max(1, int(sync_revision)),
        "items_sync_status": str(items_sync_status or "not_requested"),
        "items_synced_at": items_synced_at or (
            now if items_sync_status == "succeeded" else None
        ),
        "items_sync_error": items_sync_error,
        "items_payload_valid": bool(items_payload_valid),
        "items_count": len(products) if items_payload_valid else None,
    })

    # Preserve the marketing compatibility surface exactly as supplied. The
    # legacy mapper already promotes supported fields; this loop also protects
    # uncommon campaign/UTM keys consumed by existing analytics.
    for key, value in base.items():
        lowered = str(key).lower()
        if (
            lowered.startswith("utm_")
            or "campaign" in lowered
            or lowered in {"source", "medium", "click_id"}
        ) and value is not None:
            legacy_doc[key] = deepcopy(value)

    return legacy_doc
