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
from datetime import datetime, timezone
from typing import Any, Optional


CRITICAL_FIELDS = {"total_amount", "order_status", "payment_status"}

# Scalar fields we copy across sources. Lists/dicts handled separately below.
TRACKED_FIELDS = (
    "order_id",
    "order_date",            # ISO date YYYY-MM-DD (normalized)
    "order_date_raw",        # original string from source
    "order_status",
    "payment_status",
    "customer_name",
    "customer_mobile",
    "payment_method",
    "shipping_company",
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


def _merge_into(existing: dict, incoming: dict, source: str) -> dict:
    """Return merged document. `existing` is the prior MongoDB doc (or empty dict)."""
    now = _now()
    merged: dict = dict(existing or {})
    field_sources: dict = dict(merged.get("field_sources") or {})

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
            new_empty = _is_empty(new_val)
            old_empty = _is_empty(old_val)
            if new_empty:
                continue  # never overwrite with empty
            if old_empty:
                merged[f] = new_val
                field_sources[f] = source
                continue
            if f in CRITICAL_FIELDS and new_val != old_val:
                merged[f] = new_val
                field_sources[f] = source
            # else: keep existing (first writer wins for non-critical)
        # Lists: take incoming if richer
        new_prods = incoming.get("products") or []
        if new_prods and len(new_prods) >= len(merged.get("products") or []):
            merged["products"] = new_prods
            field_sources["products"] = source
        new_tags = incoming.get("tags") or []
        if new_tags:
            merged["tags"] = sorted(set((merged.get("tags") or []) + new_tags))
            field_sources["tags"] = source

    merged["field_sources"] = field_sources
    merged["updated_at"] = now
    data_sources = list(merged.get("data_sources") or [])
    data_sources.append({"source": source, "at": now})
    merged["data_sources"] = data_sources[-20:]  # cap history
    # Primary data_source = last writer
    merged["data_source"] = source
    return merged


async def upsert_order(db, user_id: str, order_number: str, incoming: dict,
                       source: str, raw: Optional[dict] = None) -> dict:
    """Upsert a single order into `unified_orders`. Returns {"created": bool, "doc": dict}."""
    order_number = str(order_number).strip()
    if not order_number:
        raise ValueError("order_number is required")

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

    for o in orders:
        amount = float(o.get("total_amount") or 0)
        pay = (o.get("payment_method") or "غير محدد").strip() or "غير محدد"
        ship = (o.get("shipping_company") or "غير محدد").strip() or "غير محدد"
        src = (o.get("source") or "").strip() or (o.get("data_source") or "غير محدد")

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
        "detected_columns": {"unified": True},
    }
