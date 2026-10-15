"""Hybrid Snapchat dashboard KPIs using Salla's reported order source.

The merchant-facing card uses:
- spend, impressions and clicks from Snapchat Ads API;
- actual order count and gross order value from Salla ``unified_orders`` whose
  reported marketing source resolves to Snapchat;
- Snapchat-attributed purchases/value as separate diagnostics.

This preserves both truths for future AI analysis instead of overwriting one
source with the other. The module is read-only and does not mutate orders,
campaigns, accounting, or Qoyod.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import re
from typing import Any, Final

from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    canonical_marketing_source,
    order_source_candidates,
)

HYBRID_SOURCE: Final[str] = "mezan_v2_snapchat_hybrid_salla_source_v1"
HYBRID_CONTRACT_VERSION: Final[str] = "salla_reported_source_hybrid_v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    text = _text(value).casefold()
    text = re.sub(r"[_\-./]+", " ", text)
    return " ".join(text.split())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _order_total(order: dict[str, Any]) -> float:
    totals = _dict(order.get("totals"))
    for value in (
        order.get("total_amount"),
        order.get("total"),
        order.get("amount"),
        totals.get("total"),
    ):
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed >= 0:
            return parsed
    return 0.0


def _status_bucket(order: dict[str, Any]) -> str:
    value = _normalized(
        order.get("order_status")
        or order.get("status_native")
        or order.get("status")
    )
    compact = value.replace(" ", "")
    if value in {"cancelled", "canceled", "deleted", "ملغي", "ملغى", "محذوف"}:
        return "cancelled"
    if value in {"refunded", "returned", "restored", "مسترجع", "مرتجع", "تم الاسترجاع"}:
        return "refunded"
    if "cancel" in compact or "ملغ" in compact:
        return "cancelled"
    if "refund" in compact or "return" in compact or "استرجاع" in compact:
        return "refunded"
    return "active"


def aggregate_salla_reported_source(
    orders: list[dict[str, Any]],
    *,
    start: str,
    end: str,
    source: str = "snapchat",
) -> dict[str, Any]:
    """Aggregate gross orders whose Salla-reported source matches a platform."""
    period_orders = [
        order
        for order in orders
        if start <= _text(order.get("order_date")) <= end
    ]
    classified = [
        (order, canonical_marketing_source(order))
        for order in period_orders
    ]
    source_observed_orders = sum(1 for _, value in classified if value is not None)
    matched = [order for order, value in classified if value == source]
    status_breakdown = Counter(_status_bucket(order) for order in matched)
    revenue = round(sum(_order_total(order) for order in matched), 2)
    total_period_orders = len(period_orders)
    return {
        "orders": len(matched),
        "revenue": revenue,
        "source": source,
        "source_observed_orders": source_observed_orders,
        "total_period_orders": total_period_orders,
        "reported_source_coverage_pct": (
            round(source_observed_orders / total_period_orders * 100, 2)
            if total_period_orders > 0
            else None
        ),
        "active_orders": int(status_breakdown.get("active", 0)),
        "cancelled_orders": int(status_breakdown.get("cancelled", 0)),
        "refunded_orders": int(status_breakdown.get("refunded", 0)),
    }


def merge_hybrid_snapchat_metrics(
    provider_metrics: dict[str, Any],
    salla_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Replace card KPIs only when Salla source attribution is observable."""
    merged = dict(provider_metrics)
    attributed_orders = int(round(float(provider_metrics.get("orders") or 0)))
    attributed_revenue = round(float(provider_metrics.get("revenue") or 0), 2)
    spend = round(float(provider_metrics.get("spend") or 0), 2)

    merged.update({
        "attributed_orders": attributed_orders,
        "attributed_revenue": attributed_revenue,
        "attributed_roas": (
            round(attributed_revenue / spend, 2) if spend > 0 else 0.0
        ),
        "attributed_cpa": (
            round(spend / attributed_orders, 2) if attributed_orders > 0 else 0.0
        ),
        "orders_source": "snapchat_ads_api_attribution",
        "revenue_source": "snapchat_ads_api_attribution",
        "spend_source": "snapchat_ads_api",
        "hybrid_applied": False,
        "salla_reported_source": "snapchat",
        "salla_source_observed_orders": int(
            salla_metrics.get("source_observed_orders") or 0
        ),
        "salla_total_period_orders": int(
            salla_metrics.get("total_period_orders") or 0
        ),
        "salla_reported_source_coverage_pct": salla_metrics.get(
            "reported_source_coverage_pct"
        ),
    })

    # Zero Snapchat orders is valid only when Salla supplied source evidence for
    # at least one order in the period. Without any source evidence, preserve
    # the provider number rather than silently replacing it with zero.
    if int(salla_metrics.get("source_observed_orders") or 0) <= 0:
        return merged

    actual_orders = int(salla_metrics.get("orders") or 0)
    actual_revenue = round(float(salla_metrics.get("revenue") or 0), 2)
    merged.update({
        "orders": actual_orders,
        "revenue": actual_revenue,
        "roas": round(actual_revenue / spend, 2) if spend > 0 else 0.0,
        "cpa": round(spend / actual_orders, 2) if actual_orders > 0 else 0.0,
        "cost_per_order": (
            round(spend / actual_orders, 2)
            if spend > 0 and actual_orders > 0
            else None
        ),
        "actual_orders": actual_orders,
        "actual_revenue": actual_revenue,
        "orders_source": "salla_reported_source",
        "revenue_source": "salla_unified_orders_gross",
        "hybrid_applied": True,
        "attribution_gap_orders": actual_orders - attributed_orders,
        "attribution_coverage_pct": (
            round(attributed_orders / actual_orders * 100, 2)
            if actual_orders > 0
            else None
        ),
        "active_orders": int(salla_metrics.get("active_orders") or 0),
        "cancelled_orders": int(salla_metrics.get("cancelled_orders") or 0),
        "refunded_orders": int(salla_metrics.get("refunded_orders") or 0),
    })
    return merged


async def _load_salla_marketing_orders(
    dashboard: Any,
    db: Any,
    user_id: str,
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    settings = await dashboard.ensure_user_settings(db, user_id)
    query: dict[str, Any] = {
        "user_id": user_id,
        "order_date": {"$gte": start, "$lte": end},
    }
    if settings.get("hide_inferred_date_orders"):
        query["order_date_inferred"] = {"$ne": True}
    projection = {
        **SALLA_RAW_ATTRIBUTION_PROJECTION,
        "_id": 0,
        "order_number": 1,
        "order_date": 1,
        "order_status": 1,
        "total_amount": 1,
        "currency": 1,
        "source": 1,
        "source_native": 1,
        "channel": 1,
        "platform": 1,
        "utm_source": 1,
        "utm_medium": 1,
        "utm_campaign": 1,
        "traffic_source": 1,
        "marketing_source": 1,
        "source_name": 1,
    }
    return await dashboard._to_list(
        db.unified_orders.find(query, projection),
        100000,
    )


def install_snapchat_salla_source_hybrid() -> None:
    """Patch Dashboard V2 summary while preserving freshness and API facts."""
    try:
        import dashboard_v2_routes as dashboard
    except ModuleNotFoundError:
        return

    current = dashboard.build_provider_summary
    if getattr(current, "_mezan_snapchat_salla_hybrid_v1", False):
        return

    async def wrapped_summary(
        db: Any,
        user_id: str,
        provider: str,
    ) -> dict[str, Any]:
        result = await current(db, user_id, provider)
        if provider != "snapchat":
            return result

        today = dashboard._today_riyadh()
        today_s = today.isoformat()
        month_start = today.replace(day=1).isoformat()
        d30_start = (today - timedelta(days=29)).isoformat()
        try:
            orders = await _load_salla_marketing_orders(
                dashboard,
                db,
                user_id,
                start=d30_start,
                end=today_s,
            )
        except Exception:  # noqa: BLE001 - preserve existing provider response
            orders = []

        periods = {
            "today": (today_s, today_s),
            "month": (month_start, today_s),
            "last_30d": (d30_start, today_s),
        }
        for key, (start, end) in periods.items():
            provider_metrics = result.get(key)
            if not isinstance(provider_metrics, dict):
                continue
            salla_metrics = aggregate_salla_reported_source(
                orders,
                start=start,
                end=end,
                source="snapchat",
            )
            prefix = {"date": today_s} if key == "today" else {"start": start}
            result[key] = {
                **prefix,
                **merge_hybrid_snapchat_metrics(provider_metrics, salla_metrics),
            }

        result.update({
            "source": HYBRID_SOURCE,
            "measurement_contract": {
                "version": HYBRID_CONTRACT_VERSION,
                "spend_impressions_clicks": "snapchat_ads_api",
                "orders_revenue": "salla_unified_orders:reported_source=snapchat",
                "attributed_orders_revenue": "snapchat_ads_api:conversion_attribution",
                "business_timezone": "Asia/Riyadh",
                "order_scope": "all_orders_created_in_period",
                "warning": (
                    "Salla reported source is an operational source label, "
                    "not a complete multi-touch attribution model."
                ),
            },
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        })
        return result

    # Keep both installers idempotent if router composition is called again.
    wrapped_summary._mezan_snapchat_salla_hybrid_v1 = True  # type: ignore[attr-defined]
    wrapped_summary._mezan_snapchat_freshness_nested_v6 = True  # type: ignore[attr-defined]
    dashboard.build_provider_summary = wrapped_summary


__all__ = [
    "HYBRID_CONTRACT_VERSION",
    "HYBRID_SOURCE",
    "aggregate_salla_reported_source",
    "canonical_marketing_source",
    "install_snapchat_salla_source_hybrid",
    "merge_hybrid_snapchat_metrics",
    "order_source_candidates",
]
