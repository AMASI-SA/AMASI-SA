"""Observed change history for products with active advertising spend.

Salla/Product V2 does not expose trustworthy per-field changed-at timestamps for
all fields.  Instead of pretending that ``last_synced_at`` means price/title/
visibility changed at that moment, Product Watch records a bounded state
snapshot every operational cycle and marks the first observation of each actual
field transition.

The first snapshot cannot reconstruct older history and explicitly reports that
limitation. Subsequent evidence can correlate a real observed product change
with funnel deterioration without turning that correlation into a rule.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from advertising_product_watch_v3 import PRODUCT_WATCH_HISTORY
from product_v2_routes import PRODUCTS as PRODUCT_V2_COLLECTION


CONTENT_HISTORY_COLLECTION = "mezan_advertising_product_content_history_v1"
RECENT_WATCH_LOOKBACK = timedelta(minutes=10)
MAX_HISTORY_PER_PRODUCT = 200

TRACKED_FIELDS = (
    "title",
    "price",
    "sale_price",
    "visibility",
    "status",
    "quantity",
    "description_hash",
    "hero_image",
    "gallery_hash",
    "options_hash",
    "variants_hash",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, limit: int = 2000) -> str | None:
    rendered = " ".join(str(value or "").split()).strip()[:limit]
    return rendered or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _stable_hash(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _image_url(value: Any) -> str | None:
    if isinstance(value, str):
        return _text(value, 2000)
    if not isinstance(value, dict):
        return None
    for key in ("url", "image_url", "original", "large", "medium", "small"):
        if value.get(key):
            return _text(value.get(key), 2000)
    return None


def _visibility(product: dict[str, Any]) -> str:
    if product.get("archived") is True:
        return "hidden_or_inactive"
    status = str(product.get("status") or "").casefold()
    if status in {"active", "sale", "available", "published", "enabled"}:
        return "public_status_expected"
    if status in {"hidden", "draft", "inactive", "disabled", "archived"}:
        return "hidden_or_inactive"
    if status in {"out", "out_of_stock", "sold_out"}:
        return "out_of_stock"
    return "unknown"


def _state(product: dict[str, Any]) -> dict[str, Any]:
    images = product.get("images") if isinstance(product.get("images"), list) else []
    options = product.get("options") if isinstance(product.get("options"), list) else []
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    compact_variants = [
        {
            "id": row.get("id"),
            "sku": row.get("sku"),
            "quantity": row.get("quantity") or row.get("stock_quantity"),
            "price": row.get("price"),
            "sale_price": row.get("sale_price") or row.get("discount_price"),
            "selections": row.get("selections") or row.get("options") or row.get("values") or [],
        }
        for row in variants[:100]
        if isinstance(row, dict)
    ]
    return {
        "title": _text(product.get("name"), 500),
        "price": _number(product.get("price")),
        "sale_price": _number(product.get("sale_price")),
        "visibility": _visibility(product),
        "status": _text(product.get("status"), 100),
        "quantity": _number(product.get("quantity")),
        "description_hash": _stable_hash(_text(product.get("description"), 12_000)),
        "hero_image": _image_url(product.get("main_image")),
        "gallery_hash": _stable_hash([_image_url(value) for value in images[:30]]),
        "options_hash": _stable_hash(options[:30]),
        "variants_hash": _stable_hash(compact_variants),
    }


def _changes(previous: dict[str, Any] | None, current: dict[str, Any], observed_at: datetime) -> dict[str, str]:
    if not previous:
        return {}
    return {
        field: observed_at.isoformat()
        for field in TRACKED_FIELDS
        if previous.get(field) != current.get(field)
    }


async def ensure_change_history_indexes(db: Any) -> None:
    await db[CONTENT_HISTORY_COLLECTION].create_index(
        [("user_id", 1), ("product_id", 1), ("observed_at", -1)],
        name="advertising_product_content_history_user_product_time",
    )
    await db[CONTENT_HISTORY_COLLECTION].create_index(
        [("user_id", 1), ("observed_at", -1)],
        name="advertising_product_content_history_user_time",
    )


async def snapshot_recently_watched_products(
    db: Any,
    user_id: str,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    current = (observed_at or _utcnow()).astimezone(timezone.utc)
    await ensure_change_history_indexes(db)
    recent = await db[PRODUCT_WATCH_HISTORY].find(
        {
            "user_id": user_id,
            "observed_at": {"$gte": current - RECENT_WATCH_LOOKBACK},
        },
        {"_id": 0, "product_id": 1},
    ).limit(1000).to_list(length=1000)
    product_ids = sorted({str(row.get("product_id") or "") for row in recent if row.get("product_id")})
    inserted = 0
    changed = 0
    for product_id in product_ids:
        product = await db[PRODUCT_V2_COLLECTION].find_one(
            {"user_id": user_id, "salla_product_id": product_id},
            {"_id": 0, "raw_salla": 0, "raw_salla_details": 0},
        )
        if not product:
            continue
        state = _state(product)
        previous = await db[CONTENT_HISTORY_COLLECTION].find_one(
            {"user_id": user_id, "product_id": product_id},
            {"_id": 0},
            sort=[("observed_at", -1)],
        )
        transition = _changes(previous, state, current)
        if transition:
            changed += 1
        await db[CONTENT_HISTORY_COLLECTION].insert_one({
            "user_id": user_id,
            "product_id": product_id,
            "product_name": product.get("name"),
            **state,
            "field_changes_observed_at": transition,
            "source_updated_at": product.get("source_updated_at"),
            "last_synced_at": product.get("last_synced_at"),
            "details_synced_at": product.get("details_synced_at"),
            "observed_at": current,
            "history_started_before_this_snapshot": previous is not None,
            "source_mode": "advertising_product_content_history_v3",
        })
        inserted += 1
    return {"products_snapshotted": inserted, "products_with_changes": changed}


async def build_product_change_history_evidence(
    db: Any,
    user_id: str,
    product_ids: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or _utcnow()).astimezone(timezone.utc)
    cutoff = current - timedelta(days=30)
    products: dict[str, Any] = {}
    for product_id in sorted(set(str(value) for value in product_ids if value)):
        rows = await db[CONTENT_HISTORY_COLLECTION].find(
            {
                "user_id": user_id,
                "product_id": product_id,
                "observed_at": {"$gte": cutoff},
            },
            {"_id": 0, "user_id": 0},
        ).sort("observed_at", -1).limit(MAX_HISTORY_PER_PRODUCT).to_list(length=MAX_HISTORY_PER_PRODUCT)
        latest_change: dict[str, str] = {}
        changes = []
        for row in rows:
            observed_at = row.get("observed_at")
            observed_text = observed_at.isoformat() if hasattr(observed_at, "isoformat") else str(observed_at or "")
            field_changes = row.get("field_changes_observed_at") if isinstance(row.get("field_changes_observed_at"), dict) else {}
            if field_changes:
                changes.append({
                    "observed_at": observed_text,
                    "fields": sorted(field_changes),
                    "state_after_change": {field: row.get(field) for field in field_changes},
                })
                for field, value in field_changes.items():
                    if field not in latest_change:
                        latest_change[field] = str(value or observed_text)
        first = rows[-1] if rows else None
        products[product_id] = {
            "available": bool(rows),
            "history_started_at": (
                first.get("observed_at").isoformat()
                if first and hasattr(first.get("observed_at"), "isoformat")
                else str(first.get("observed_at") or "") if first else None
            ),
            "latest_observed_changes": latest_change,
            "recent_change_events": changes[:20],
            "price_changed_at": latest_change.get("price"),
            "discount_changed_at": latest_change.get("sale_price"),
            "visibility_changed_at": latest_change.get("visibility") or latest_change.get("status"),
            "stock_changed_at": latest_change.get("quantity") or latest_change.get("variants_hash"),
            "title_changed_at": latest_change.get("title"),
            "description_changed_at": latest_change.get("description_hash"),
            "image_changed_at": latest_change.get("hero_image") or latest_change.get("gallery_hash"),
            "limitations": (
                []
                if rows and first.get("history_started_before_this_snapshot")
                else ["history_begins_when_v3_product_watch_starts; earlier per-field changes are unknown"]
            ),
        }
    return {
        "schema_version": "campaign_ai_product_change_history_v3",
        "products": products,
        "interpretation_contract": (
            "Observed chronology is evidence for correlation and counterfactual review; code does not infer causation."
        ),
    }


__all__ = [
    "CONTENT_HISTORY_COLLECTION",
    "build_product_change_history_evidence",
    "ensure_change_history_indexes",
    "snapshot_recently_watched_products",
]
