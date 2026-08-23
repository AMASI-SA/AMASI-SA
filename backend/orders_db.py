"""Unified orders store with intelligent merge across data sources.

Single collection `unified_orders` keyed by (user_id, order_number).
Both Excel uploads AND Make.com webhook write here.

Merge rules (when the same order_number arrives twice from different sources):
- Empty existing field + new value  → take new value, record field provenance.
- Filled existing field + empty new value → keep existing (never lose data).
- Both non-empty:
    * Critical fields (total_amount, order_status, payment_status) → newer wins.
    * Non-critical fields → first writer wins.
- `data_sources` list accumulates {source, at} entries (capped).
- `field_sources` dict tracks which source last wrote each scalar field.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from salla_marketing_attribution import (
    canonical_marketing_source,
    promoted_salla_attribution,
)

logger = logging.getLogger(__name__)


CRITICAL_FIELDS = {
    "total_amount",
    "order_status",
    "payment_status",
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
    "receiving_bank_name",
    "payment_receipt_url",
    "shipping_label_url",
}
ZERO_VALID_FIELDS = {"paid_amount", "remaining_amount", "has_remaining_amount"}
COLLECTION_FIELDS = {
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
}
PAYMENT_EVIDENCE_FIELDS = {
    "receiving_bank_name",
    "payment_receipt_url",
}
SHIPPING_CLEARABLE_FIELDS = {
    "shipping_label_url",
    "tracking_number",
    "tracking_url",
}
SHIPPING_WITHOUT_ACTIVE_LABEL = {
    "pending",
    "creating",
    "processing",
    "cancelled",
    "canceled",
    "void",
    "deleted",
}
MARKETING_ATTRIBUTION_FIELDS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "campaign_id",
    "campaign_name",
    "source_campaign_id",
    "source_campaign_name",
    "source_native",
    "traffic_source",
    "marketing_source",
    "ad_platform_source",
}

# Scalar fields we copy across sources. Lists/dicts handled separately below.
TRACKED_FIELDS = (
    "order_id",
    "order_date",            # ISO date YYYY-MM-DD (normalized)
    "order_date_raw",        # original string from source
    "order_date_inferred",   # True if order_date was guessed (no created_at in payload)
    "order_status",
    "order_status_slug",
    "payment_status",
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
    "receiving_bank_name",
    "payment_receipt_url",
    "customer_name",
    "customer_mobile",
    "payment_method",

    # Salla webhook shipping fields.
    # These must be tracked explicitly or upsert_order drops them.
    "shipping_company",
    "shipping_label_url",
    "shipping_company_code",
    "shipping_company_logo",
    "shipping_method",
    "shipping_status",
    "shipment_status",
    "salla_shipment_id",
    "tracking_number",
    "tracking_url",

    "shipping_address",
    "shipping_address_raw",
    "shipping_address_source_path",
    "shipping_address_found",
    "shipping_address_keys",
    "shipping_city",
    "customer_city",
    "shipping_district",
    "shipping_street",
    "shipping_national_address",
    "shipping_short_address",
    "shipping_postal_code",
    "shipping_building_number",
    "shipping_additional_number",
    "shipping_country",
    "shipping_latitude",
    "shipping_longitude",
    "shipping_map_url",
    "shipping_location_url",

    "shipping_cost",
    "subtotal",
    "discount",
    "tax",
    "total_amount",
    "currency",
    "source",                # Salla traffic source ("store", "snapchat" ...)
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "campaign_id",
    "campaign_name",
    "source_campaign_id",
    "source_campaign_name",
    "source_native",
    "traffic_source",
    "marketing_source",
    "ad_platform_source",
    "device",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (int, float)):
        return v == 0
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False
    
    # ── Phase 1: Auto-seed /products from order line-items ──────────────
# Safe scope:
# - Creates missing products from order products[].
# - Does NOT change profit.
# - Does NOT change inventory.
# - New products start with needs_cost=True.
# - Variant/options are preserved so later phases can assign different costs.

_AR_DIACRITICS_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def _norm_product_text(value: Any) -> str:
    s = "" if value is None else str(value)
    s = s.strip()
    s = _AR_DIACRITICS_RE.sub("", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = s.replace("ؤ", "و").replace("ئ", "ي")
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_product_key(value: Any) -> str:
    return _norm_product_text(value).casefold()


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _extract_variant_info(line: dict) -> tuple[str | None, str | None, dict]:
    """Return (variant_key, variant_label, variant_attributes).

    This protects products like:
    - لوحة فنية 100×70
    - لوحة فنية 200×100

    from being merged into one cost row later.
    """
    if not isinstance(line, dict):
        return None, None, {}

    raw_parts = []
    label_parts = []

    for key in (
        "variant_id",
        "variant_name",
        "variant",
        "options",
        "option",
        "product_options",
        "attributes",
        "attribute",
        "variant_options",
        "option_values",
    ):
        val = line.get(key)
        if val in (None, "", [], {}):
            continue
        raw_parts.append({key: val})

        if isinstance(val, str):
            label_parts.append(val.strip())
        elif isinstance(val, dict):
            for k, v in val.items():
                if v not in (None, "", [], {}):
                    label_parts.append(f"{k}: {v}")
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("label") or item.get("key") or ""
                    value = item.get("value") or item.get("option") or item.get("text") or ""
                    txt = " ".join(str(x).strip() for x in (name, value) if str(x).strip())
                    if txt:
                        label_parts.append(txt)
                elif item not in (None, ""):
                    label_parts.append(str(item).strip())

    if not raw_parts:
        return None, None, {}

    raw = _stable_json(raw_parts)
    digest = hashlib.sha1(_norm_product_key(raw).encode("utf-8")).hexdigest()[:12]
    variant_key = f"VAR-{digest}"

    # Short readable label for UI.
    label = " / ".join([p for p in label_parts if p])[:160] or variant_key

    return variant_key, label, {"raw": raw_parts}

    
def _line_product_identity(line: dict) -> Optional[dict]:
    if not isinstance(line, dict):
        return None

    raw_product_id = str(
        line.get("product_id")
        or line.get("id")
        or line.get("salla_product_id")
        or ""
    ).strip()

    sku = str(
        line.get("sku")
        or line.get("SKU")
        or line.get("barcode")
        or ""
    ).strip().upper()

    barcode = str(line.get("barcode") or "").strip()

    base_name = _norm_product_text(
        line.get("name")
        or line.get("product_name")
        or line.get("title")
        or ""
    )

    image_url = str(
        line.get("image_url")
        or line.get("image")
        or line.get("imageUrl")
        or ""
    ).strip() or None

    variant_key, variant_label, variant_attributes = _extract_variant_info(line)

    if not raw_product_id and not sku and not base_name:
        return None

    # Internal catalogue product_id.
    # If this line has variant/options, make the catalogue row variant-specific.
    if raw_product_id:
        catalog_product_id = (
            f"{raw_product_id}::{variant_key}"
            if variant_key else raw_product_id
        )
    else:
        seed = "|".join([
            sku or "",
            _norm_product_key(base_name),
            variant_key or "",
        ])
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
        catalog_product_id = f"AUTO-{digest}"

    if not base_name:
        base_name = sku or catalog_product_id

    display_name = base_name
    if variant_label and variant_label not in display_name:
        display_name = f"{base_name} — {variant_label}"

    return {
        "product_id": catalog_product_id,
        "parent_product_id": raw_product_id or None,
        "sku": sku or None,
        "sku_normalized": sku or None,
        "barcode": barcode or None,
        "name": display_name,
        "base_name": base_name,
        "name_lower": _norm_product_key(display_name),
        "variant_key": variant_key,
        "variant_label": variant_label,
        "variant_attributes": variant_attributes,
        "image_url": image_url,
        "image_urls": [image_url] if image_url else [],
    }


def _is_auto_product_id(value: Any) -> bool:
    return str(value or "").strip().upper().startswith("AUTO-")
async def _find_existing_catalog_product(db, user_id: str, ident: dict) -> Optional[dict]:
    """Find an existing /products row before creating AUTO-*.

    Handles old Excel-imported products where:
    - SKU may be missing,
    - product_id may not equal Salla SKU,
    - name_lower was generated with older Arabic normalization.
    """
    product_id = str(ident.get("product_id") or "").strip()
    parent_product_id = str(ident.get("parent_product_id") or "").strip()
    sku = str(ident.get("sku") or "").strip().upper()
    variant_key = ident.get("variant_key")
    name_lower = ident.get("name_lower") or _norm_product_key(ident.get("name"))
    auto_catalog_key = product_id if _is_auto_product_id(product_id) else ""

    or_terms = []

    # Use real product_id only. Do NOT match by generated AUTO-* as identity.
    if product_id and not _is_auto_product_id(product_id):
        or_terms.append({"product_id": product_id})
     
    if auto_catalog_key:
        if variant_key:
            or_terms.append({
                "auto_catalog_key": auto_catalog_key,
                "variant_key": variant_key,
            })
        else:
            or_terms.append({"auto_catalog_key": auto_catalog_key})

    if parent_product_id and variant_key:
        or_terms.append({
            "parent_product_id": parent_product_id,
            "variant_key": variant_key,
        })

    if sku:
        if variant_key:
            or_terms.extend([
                {"sku": sku, "variant_key": variant_key},
                {"sku_normalized": sku, "variant_key": variant_key},
                {"product_id": sku, "variant_key": variant_key},
                {"barcode": sku, "variant_key": variant_key},
            ])
        else:
            or_terms.extend([
                {"sku": sku},
                {"sku_normalized": sku},
                {"product_id": sku},
                {"barcode": sku},
            ])

    if name_lower:
        if variant_key:
            or_terms.append({"name_lower": name_lower, "variant_key": variant_key})
        else:
            or_terms.append({"name_lower": name_lower})

    if or_terms:
        existing = await db.products.find_one(
            {
                "user_id": user_id,
                "is_active": {"$ne": False},
                "$or": or_terms,
            },
            sort=[("updated_at", -1)],
        )
        if existing:
            return existing

    # Runtime Arabic-normalized name fallback for legacy Excel products.
    # This catches: الأنيق vs الانيق, أماسي vs اماسي, etc.
    raw_name = ident.get("base_name") or ident.get("name") or ""
    norm_target = _norm_product_key(raw_name)

    if not norm_target:
        return None

    tokens = [
        t for t in _norm_product_text(raw_name).split()
        if len(t) >= 3
    ][:4]

    q = {
        "user_id": user_id,
        "is_active": {"$ne": False},
    }

    if tokens:
        q["$or"] = [
            {"name": {"$regex": re.escape(t), "$options": "i"}}
            for t in tokens
        ]

    candidates = await db.products.find(q).limit(100).to_list(100)

    for c in candidates:
        # Do not merge different explicit variants.
        if variant_key and c.get("variant_key") and c.get("variant_key") != variant_key:
            continue

        candidate_names = [
            c.get("name"),
            c.get("base_name"),
            c.get("product_name"),
        ]

        for cand_name in candidate_names:
            if cand_name and _norm_product_key(cand_name) == norm_target:
                return c

    return None
    
   


async def _ensure_order_products_catalogued(
    db,
    user_id: str,
    order_number: str,
    order_doc: dict,
    source: str,
) -> dict:
    """Create/update db.products rows from order products[].

    Safe scope:
    - No profit calculation.
    - No inventory movement.
    - No cost is assumed.
    """
    products = order_doc.get("products") or []
    if not products:
        return {"created": 0, "updated": 0, "skipped": 0}

    now = _now()
    created = 0
    updated = 0
    skipped = 0

    for line in products:
        ident = _line_product_identity(line)
        if not ident:
            skipped += 1
            continue

        existing = await _find_existing_catalog_product(db, user_id, ident)

        if existing:
            set_doc = {
                "last_seen_order_number": str(order_number),
                "last_seen_source": source,
                "last_seen_at": now,
                "updated_at": now,
            }
            

            for key in (
                "product_id",  # موجود فقط لتفادي مشاكل المحرر — يتم تخطيه تحت
                "parent_product_id",
                "sku",
                "sku_normalized",
                "barcode",
                "name",
                "base_name",
                "name_lower",
                "variant_key",
                "variant_label",
                "variant_attributes",
                "image_url",
            ):
                if key == "product_id":
                    continue

                if ident.get(key) and not existing.get(key):
                    set_doc[key] = ident[key]


            if ident.get("image_urls") and not existing.get("image_urls"):
                set_doc["image_urls"] = ident["image_urls"]

            # Only fill product_id if it is a real source id.
            # Never fill product_id with generated AUTO-*.
            if (
                ident.get("product_id")
                and not _is_auto_product_id(ident.get("product_id"))
                and not existing.get("product_id")
            ):
                set_doc["product_id"] = ident["product_id"]

            # Keep generated AUTO-* as internal key only.
            # It must not remain in the user-facing product_id field.
            generated_key = ident.get("product_id")

            if (
                _is_auto_product_id(generated_key)
                and not existing.get("auto_catalog_key")
            ):
                set_doc["auto_catalog_key"] = generated_key

            if _is_auto_product_id(existing.get("product_id")):
                set_doc["auto_catalog_key"] = existing.get("product_id")
                set_doc["product_id"] = None

            if (
                existing.get("needs_cost") is None
                and existing.get("cost_current") in (None, "")
                and existing.get("cost_avg") in (None, "")
            ):
                set_doc["needs_cost"] = True

            await db.products.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": set_doc,
                    "$addToSet": {"seen_order_numbers": str(order_number)},
                },
            )
            updated += 1
            continue
        generated_key = ident["product_id"]
        is_auto = _is_auto_product_id(generated_key)

        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,           
            "product_id": None if is_auto else generated_key,
            "auto_catalog_key": generated_key if is_auto else None,
            "parent_product_id": ident.get("parent_product_id"),
            "sku": ident.get("sku"),
            "sku_normalized": ident.get("sku_normalized"),
            "barcode": ident.get("barcode"),
            "name": ident["name"],
            "base_name": ident.get("base_name"),
            "name_lower": ident["name_lower"],
            "variant_key": ident.get("variant_key"),
            "variant_label": ident.get("variant_label"),
            "variant_attributes": ident.get("variant_attributes") or {},
            "product_type": "service",
            "category_ids": [],
            "category_paths": [],
            "image_url": ident.get("image_url"),
            "image_urls": ident.get("image_urls") or [],
            "cost_current": None,
            "cost_avg": None,
            "cost_history": [],
            "needs_cost": True,
            "is_active": True,
            "imported": {
                "source": "order-auto-created",
                "at": now,
            },
            "first_seen_order_number": str(order_number),
            "last_seen_order_number": str(order_number),
            "last_seen_source": source,
            "first_seen_at": now,
            "last_seen_at": now,
            "seen_order_numbers": [str(order_number)],
            "created_at": now,
            "updated_at": now,
        }

        await db.products.insert_one(doc)
        created += 1

    return {"created": created, "updated": updated, "skipped": skipped}
    
def _merge_into(existing: dict, incoming: dict, source: str) -> dict:
    """Return merged document. `existing` is the prior MongoDB doc (or empty dict).

    Iter-59 merge rule
    -------------------
    Make is the authoritative *live* source. Once an order has been
    touched by Make (`last_make_update_at` is set), subsequent Excel
    writes only fill EMPTY fields — they never overwrite values Make
    already provided, even for fields tagged "critical". Excel still
    behaves as before on orders Make hasn't touched.
    """
    now = _now()
    merged: dict = dict(existing or {})
    field_sources: dict = dict(merged.get("field_sources") or {})

    # `make_has_touched` controls whether Excel writes can override.
    make_has_touched = bool((existing or {}).get("last_make_update_at"))
    # Iter-105 — custom_app is the merchant's own application. It is
    # treated as authoritative (same precedence as Make), since it is
    # also a real-time push from the source-of-truth system.
    excel_only_fills_empty = (
        source == "excel"
        and (make_has_touched or bool(merged.get("last_custom_app_update_at")))
    )
    # Iter-73 (Salla Direct, Phase 2) — Salla Direct is being validated
    # against Make/Excel. Until the merchant confirms parity, we treat
    # salla_direct exactly like Excel: it never overwrites a field that
    # Make has already populated. It can still create brand-new orders
    # and fill empty fields on Excel-only orders.
    salla_direct_only_fills_empty = (source == "salla_direct" and make_has_touched)
    fills_empty_only = excel_only_fills_empty or salla_direct_only_fills_empty

    # First-time insert path
    if not existing:
        for f in TRACKED_FIELDS:
            v = incoming.get(f)
            if not _is_empty(v):
                merged[f] = v
                field_sources[f] = source
            elif v is not None:
                # Preserve explicit 0 / empty string for completeness
                merged.setdefault(f, v)
        # Lists
        prods = incoming.get("products") or []
        if prods:
            merged["products"] = prods
            field_sources["products"] = source
        tags = incoming.get("tags") or []
        if tags:
            merged["tags"] = tags
            field_sources["tags"] = source
    else:
        # Update path
        for f in TRACKED_FIELDS:
            new_val = incoming.get(f)
            old_val = merged.get(f)
            new_empty = _is_empty(new_val) and not (
                f in ZERO_VALID_FIELDS and new_val is not None
            )
            old_empty = _is_empty(old_val) and not (
                f in ZERO_VALID_FIELDS and old_val is not None
            )
            if new_empty:
                shipping_status = str(
                    incoming.get("shipment_status")
                    or incoming.get("shipping_status")
                    or ""
                ).strip().lower().replace("-", "_").replace(" ", "_")
                if (
                    source == "salla_direct"
                    and f in SHIPPING_CLEARABLE_FIELDS
                    and shipping_status in SHIPPING_WITHOUT_ACTIVE_LABEL
                ):
                    # Empty is authoritative after Salla cancels or resets an
                    # AWB. Keeping the old value would print a void label.
                    merged[f] = None
                    field_sources[f] = source
                continue
            if old_empty:
                merged[f] = new_val
                field_sources[f] = source
                continue
            # Salla Direct remains fill-empty-only after Make, except for
            # the two authoritative order-status fields. Salla is the source
            # of truth for the current order lifecycle state, while products,
            # totals, customer and payment data remain protected.
            salla_authoritative_override = (
                source == "salla_direct"
                and f in {
                    "order_status",
                    "order_status_slug",
                    *COLLECTION_FIELDS,
                    *PAYMENT_EVIDENCE_FIELDS,
                    *MARKETING_ATTRIBUTION_FIELDS,
                    "shipping_label_url",
                }
            )
            if fills_empty_only and not salla_authoritative_override:
                continue
            if (
                f in CRITICAL_FIELDS or salla_authoritative_override
            ) and new_val != old_val:
                merged[f] = new_val
                field_sources[f] = source
            # else: keep existing (first writer wins for non-critical)

        # Special-case: if the stored order_date was an inferred value (Make.com
        # webhook arrived without created_at and we used today as a guess)
        # AND the new payload carries an authoritative date (raw value set,
        # i.e. NOT inferred), let the authoritative date win.
        if merged.get("order_date_inferred"):
            incoming_inferred = incoming.get("order_date_inferred")
            incoming_raw = incoming.get("order_date_raw")
            new_order_date = incoming.get("order_date")
            if (
                incoming_inferred is False
                and incoming_raw
                and new_order_date
                and not _is_empty(new_order_date)
            ):
                merged["order_date"] = new_order_date
                merged["order_date_raw"] = incoming_raw
                merged["order_date_inferred"] = False
                field_sources["order_date"] = source
        # Lists: take incoming if richer — UNLESS Make already has products
        # and this is an Excel write (Excel exports rarely carry products[]
        # but we never want a sparser Excel list to clobber Make's full list).
        new_prods = incoming.get("products") or []
        if new_prods and not fills_empty_only:
            if len(new_prods) >= len(merged.get("products") or []):
                merged["products"] = new_prods
                field_sources["products"] = source
        elif new_prods and fills_empty_only and not (merged.get("products") or []):
            merged["products"] = new_prods
            field_sources["products"] = source
        new_tags = incoming.get("tags") or []
        if new_tags:
            merged["tags"] = sorted(set((merged.get("tags") or []) + new_tags))
            field_sources["tags"] = source

    # Append an auditable collection snapshot only when Salla supplied
    # collection facts and the canonical values changed.
    if any(incoming.get(key) is not None for key in COLLECTION_FIELDS):
        collection_snapshot = {
            "at": now,
            "source": source,
            "paid_amount": merged.get("paid_amount"),
            "remaining_amount": merged.get("remaining_amount"),
            "has_remaining_amount": merged.get("has_remaining_amount"),
            "status": merged.get("payment_collection_status"),
        }
        collection_history = list(merged.get("payment_collection_history") or [])
        comparable = {
            key: collection_snapshot.get(key)
            for key in (
                "paid_amount",
                "remaining_amount",
                "has_remaining_amount",
                "status",
            )
        }
        last_comparable = (
            {
                key: collection_history[-1].get(key)
                for key in comparable
            }
            if collection_history
            else None
        )
        if comparable != last_comparable:
            collection_history.append(collection_snapshot)
            merged["payment_collection_history"] = collection_history[-100:]

    merged["field_sources"] = field_sources
    merged["updated_at"] = now
    data_sources = list(merged.get("data_sources") or [])
    data_sources.append({"source": source, "at": now})
    merged["data_sources"] = data_sources[-20:]  # cap history

    # Iter-59 — explicit per-source timestamps so the UI / diagnostics
    # can answer "when did Make last touch this order" without scanning
    # the data_sources history.
    if source == "make":
        merged["last_make_update_at"] = now
    elif source == "excel":
        merged["last_excel_import_at"] = now
    elif source == "salla_direct":
        merged["last_salla_direct_sync_at"] = now
    elif source == "custom_app":
        merged["last_custom_app_update_at"] = now
    merged["last_source"] = source
    merged["updated_by_source"] = source
    # Iteration 31 — data_source precedence: make > excel.
    # Make is the AUTHORITATIVE source because:
    #   • it carries the full products[] array (Excel exports usually don't)
    #   • it is real-time webhook (vs. Excel which is periodic)
    # Once an order has any Make write, the unified `data_source` stays
    # 'make' forever, regardless of subsequent Excel re-imports. This
    # prevents the Dashboard's source-bucket counters (orders_make_count,
    # make_orders_count, source filters) from "losing" Make orders every
    # time the merchant uploads an Excel snapshot of the same period.
    # `data_sources[]` (history) still records the Excel write for audit.
    existing_ds = (existing or {}).get("data_source")
    has_make = (
        existing_ds == "make"
        or source == "make"
        or any((s or {}).get("source") == "make" for s in data_sources)
    )
    # Iter-105 — custom_app takes precedence over everything else
    # (merchant's authoritative system). Falls back to make/source.
    has_custom = (
        existing_ds == "custom_app"
        or source == "custom_app"
        or any((s or {}).get("source") == "custom_app" for s in data_sources)
    )
    if has_custom:
        merged["data_source"] = "custom_app"
    elif has_make:
        merged["data_source"] = "make"
    else:
        merged["data_source"] = source
    return merged


async def upsert_order(db, user_id: str, order_number: str, incoming: dict,
                       source: str, raw: Optional[dict] = None) -> dict:
    """Upsert a single order into `unified_orders`. Returns {"created": bool, "doc": dict}."""
    order_number = str(order_number).strip()
    if not order_number:
        raise ValueError("order_number is required")

    if source == "salla_direct":
        incoming = dict(incoming)
        for field, value in promoted_salla_attribution(raw or incoming).items():
            if value not in (None, ""):
                incoming[field] = value

    existing = await db.unified_orders.find_one(
        {"user_id": user_id, "order_number": order_number}
    ) or {}
    merged = _merge_into(existing, incoming, source)
    merged["user_id"] = user_id
    merged["order_number"] = order_number
    if raw is not None:
        # Track raw per source so we can audit later
        raws = dict(merged.get("raw_by_source") or {})
        raws[source] = raw
        merged["raw_by_source"] = raws
    if not existing:
        merged["received_at"] = _now()

    await db.unified_orders.update_one(
        {"user_id": user_id, "order_number": order_number},
        {"$set": merged},
        upsert=True,
    )
          # Phase 1: auto-create missing product catalogue rows from order lines.
    # This is intentionally non-accounting: no COGS, no inventory movement.
    try:
        await _ensure_order_products_catalogued(
            db=db,
            user_id=user_id,
            order_number=order_number,
            order_doc=merged,
            source=source,
        )
    except Exception as exc:
        logger.warning(
            "product catalogue auto-seed skipped for order=%s source=%s: %s",
            order_number,
            source,
            exc,
        )
    # Iter-146 — Tamara billing-eligible propagation.
    # When the merged order's status is one of the "billable" statuses
    # (shipped / prepared / out-for-delivery / delivered / executed), we
    # stamp `billing_eligible_at` on every Tamara `payment_transactions`
    # row tied to this order.  Idempotent — first stamp wins.  Refunds
    # are NOT touched (they keep their `refunded_at` aggregation rule).
    try:
        from bnpl.billing_eligible import propagate_status_to_billing_eligible
        new_status = merged.get("order_status")
        if new_status and new_status != (existing or {}).get("order_status"):
            await propagate_status_to_billing_eligible(
                db, user_id,
                order_reference_id=merged.get("order_reference_id"),
                order_number=order_number,
                new_status=new_status,
                event_at=merged.get("updated_at") or _now(),
            )
    except Exception:
        # Never let billing-eligible bookkeeping break order ingestion.
        pass

    return {"created": not bool(existing), "doc": merged}


def orders_to_parsed(orders: list[dict]) -> dict:
    """Reduce unified orders → the dict shape parse_salla_excel produces.

    Lets us reuse match_settings() + build_report() unchanged.
    """
    total_sales = 0.0
    total_orders = 0
    payments: dict[str, dict] = {}
    shippings: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    sample: list[dict] = []
    individual: list[dict] = []

    for o in orders:
        amount = float(o.get("total_amount") or 0)
        pay = (o.get("payment_method") or "غير محدد").strip() or "غير محدد"
        ship = (o.get("shipping_company") or "غير محدد").strip() or "غير محدد"
        src = (
            canonical_marketing_source(o)
            or str(o.get("source") or "").strip()
            or str(o.get("data_source") or "غير محدد")
        )

        total_sales += amount
        total_orders += 1

        p = payments.setdefault(pay, {"name": pay, "orders_count": 0, "total_sales": 0.0})
        p["orders_count"] += 1
        p["total_sales"] += amount

        s = shippings.setdefault(ship, {"name": ship, "orders_count": 0})
        s["orders_count"] += 1

        sr = sources.setdefault(src, {"name": src, "orders_count": 0, "total_sales": 0.0})
        sr["orders_count"] += 1
        sr["total_sales"] += amount

        # Preserve the minimum per-order inputs needed for processor-accurate
        # fee rounding.  Salla rounds the commission and its VAT per order;
        # aggregating a rail first can drift by several halalas.
        individual.append({
            "order_number": str(o.get("order_number") or ""),
            "total_amount": amount,
            "payment_method": pay,
        })

        if len(sample) < 10:
            sample.append({
                "order_id": str(o.get("order_number") or ""),
                "amount": amount,
                "payment_method": pay,
                "shipping_company": ship,
                "status": o.get("order_status") or "",
                "date": o.get("order_date") or "",
            })

    return {
        "total_sales": round(total_sales, 2),
        "total_orders": total_orders,
        "payment_methods": [
            {**v, "total_sales": round(v["total_sales"], 2)}
            for v in sorted(payments.values(), key=lambda x: -x["total_sales"])
        ],
        "shipping_companies": [
            v for v in sorted(shippings.values(), key=lambda x: -x["orders_count"])
        ],
        "order_sources": [
            {**v, "total_sales": round(v["total_sales"], 2)}
            for v in sorted(sources.values(), key=lambda x: -x["orders_count"])
        ],
        "orders_sample": sample,
        "orders_individual": individual,
        "detected_columns": {"unified": True},
    }
