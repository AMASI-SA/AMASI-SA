"""Store-level checkout/payment/shipping corroborating evidence for AI V3.

Only aggregate operational data is exposed.  This source can corroborate a
checkout/payment/shipping hypothesis but cannot be attributed to a campaign
unless the underlying order has separate campaign attribution elsewhere.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from payment_gateway_metrics import compute_metrics


MAX_ORDERS = 20_000


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _window(start: date, end: date) -> tuple[str, str]:
    return start.isoformat(), end.isoformat()


async def _shipping_summary(db: Any, user_id: str, start: date, end: date) -> dict[str, Any]:
    rows = await db.unified_orders.find(
        {
            "user_id": user_id,
            "order_date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
        },
        {
            "_id": 0,
            "order_status": 1,
            "payment_status": 1,
            "payment_method": 1,
            "shipping_cost": 1,
            "shipping_company": 1,
            "shipping_method": 1,
            "shipping_city": 1,
            "shipping_country": 1,
            "total_amount": 1,
        },
    ).limit(MAX_ORDERS).to_list(length=MAX_ORDERS)
    shipping_values = [
        value for row in rows
        if (value := _number(row.get("shipping_cost"))) is not None
    ]
    companies = Counter(str(row.get("shipping_company") or "unknown") for row in rows)
    methods = Counter(str(row.get("shipping_method") or "unknown") for row in rows)
    cities = Counter(str(row.get("shipping_city") or "unknown") for row in rows if row.get("shipping_city"))
    countries = Counter(str(row.get("shipping_country") or "unknown") for row in rows if row.get("shipping_country"))
    statuses = Counter(str(row.get("order_status") or "unknown") for row in rows)
    payment_statuses = Counter(str(row.get("payment_status") or "unknown") for row in rows)
    return {
        "orders_observed": len(rows),
        "shipping_cost_observed_orders": len(shipping_values),
        "shipping_cost_avg_sar": round(sum(shipping_values) / len(shipping_values), 2) if shipping_values else None,
        "shipping_cost_max_sar": round(max(shipping_values), 2) if shipping_values else None,
        "shipping_company_counts": dict(companies.most_common(10)),
        "shipping_method_counts": dict(methods.most_common(10)),
        "order_status_counts": dict(statuses.most_common(20)),
        "payment_status_counts": dict(payment_statuses.most_common(20)),
        "top_shipping_cities_aggregate": dict(cities.most_common(10)),
        "shipping_country_counts_aggregate": dict(countries.most_common(10)),
        "privacy": "aggregate only; no customer identity/address is included",
    }


async def _payment_summary(db: Any, user_id: str, start: date, end: date) -> dict[str, Any]:
    try:
        metrics = await compute_metrics(
            db,
            user_id,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )
    except Exception as exc:
        return {
            "available": False,
            "error_type": type(exc).__name__,
            "payment_failure_events_available": False,
        }
    rows = metrics.get("methods") or metrics.get("rows") or metrics.get("payment_methods") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    compact = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        compact.append({key: row.get(key) for key in (
            "key", "canonical_key", "name", "name_ar", "orders_count",
            "pending_orders_count", "cancelled_orders_count", "refunded_orders_count",
            "gross", "net", "pending_gross", "actual_orders_count",
        )})
    return {
        "available": True,
        "methods": compact[:20],
        "summary": {key: metrics.get(key) for key in (
            "orders_count", "gross", "net", "excluded_orders_count", "excluded_gross",
            "salla_reference_count", "salla_reference_gross",
        )},
        "payment_failure_events_available": False,
        "limitation": (
            "Unified completed/pending/cancelled order evidence is available, but failed payment-attempt telemetry "
            "is not present in the current safe analytics contract. Do not claim a gateway outage from this source alone."
        ),
    }


async def build_store_friction_evidence(db: Any, user_id: str, *, end: date) -> dict[str, Any]:
    windows = {
        "today": (end, end),
        "yesterday": (end - timedelta(days=1), end - timedelta(days=1)),
        "day_minus_2": (end - timedelta(days=2), end - timedelta(days=2)),
        "baseline_7d": (end - timedelta(days=6), end),
        "baseline_30d": (end - timedelta(days=29), end),
    }
    output = {}
    for label, (start, stop) in windows.items():
        payment = await _payment_summary(db, user_id, start, stop)
        shipping = await _shipping_summary(db, user_id, start, stop)
        output[label] = {
            "range": {"from": start.isoformat(), "to": stop.isoformat()},
            "payment": payment,
            "shipping": shipping,
        }
    return {
        "schema_version": "campaign_ai_store_friction_evidence_v3",
        "scope": "store_level_corroborating_evidence",
        "windows": output,
        "checkout_error_events_available": False,
        "payment_failure_events_available": False,
        "causality_guard": (
            "This aggregate store evidence can support or challenge a hypothesis, but it is not campaign attribution."
        ),
    }


__all__ = ["build_store_friction_evidence"]
