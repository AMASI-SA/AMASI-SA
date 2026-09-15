"""Hybrid executive advertising metrics for Mezan Dashboard V2.

Commerce outcomes are sourced only from Salla unified orders. Advertising
spend and platform CPA are sourced only from native provider facts. The module
is pure and read-only: it performs no database, accounting, campaign, or Qoyod
writes.
"""
from __future__ import annotations

import re
from typing import Any

from order_currency import order_total_sar
from salla_marketing_attribution import canonical_ad_platform


PROVIDER_ORDER = ("snapchat", "tiktok", "meta", "google")
PROVIDER_LABELS = {
    "snapchat": "Snapchat",
    "tiktok": "TikTok",
    "meta": "Meta",
    "google": "Google Ads",
}


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if parsed != parsed or abs(parsed) == float("inf"):
        return fallback
    return parsed


def _optional_nonnegative(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf") or parsed < 0:
        return None
    return parsed


def _fragments(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for key in (
            "source", "channel", "platform", "name", "value",
            "utm_source", "utm_medium", "utm_campaign",
        ):
            result.extend(_fragments(value.get(key)))
        return result
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_fragments(item))
        return result
    text = str(value).strip()
    return [text] if text else []


def resolve_salla_ad_platform(order: dict[str, Any]) -> str | None:
    """Resolve Salla traffic attribution without provider conversion data."""
    provider = canonical_ad_platform(order)
    if provider:
        return provider
    fragments: list[str] = []
    for key in (
        "source", "utm_source", "utm_medium", "utm_campaign",
        "traffic_source", "marketing_source", "source_native",
        "platform", "channel",
    ):
        fragments.extend(_fragments(order.get(key)))
    normalized = " ".join(fragments).replace("_", " ").casefold()
    words = {
        word for word in re.split(r"[^0-9a-z\u0600-\u06ff]+", normalized)
        if word
    }
    if {"snap", "snapchat", "سناب"}.intersection(words) or "سناب شات" in normalized:
        return "snapchat"
    if "tiktok" in words or {"tik", "tok"}.issubset(words) or "تيك توك" in normalized:
        return "tiktok"
    if (
        {"meta", "facebook", "instagram", "fb", "ig", "فيسبوك", "انستقرام", "انستغرام"}.intersection(words)
        or "فيس بوك" in normalized
    ):
        return "meta"
    if {"google", "adwords", "gads", "جوجل", "قوقل"}.intersection(words):
        return "google"
    return None


def _salla_sales(order: dict[str, Any]) -> float | None:
    """Return only a proven SAR outcome; native GCC numbers are not SAR."""
    return order_total_sar(order)


def build_salla_ads_executive_breakdown(
    orders: list[dict[str, Any]],
    ads: dict[str, Any],
) -> dict[str, Any]:
    """Return Salla outcomes plus provider spend and platform CPA."""
    salla = {
        provider: {
            "orders": 0,
            "known_sales_sar": 0.0,
            "conversion_complete": True,
        }
        for provider in PROVIDER_ORDER
    }
    unattributed_orders = 0
    unattributed_known_sales = 0.0
    unattributed_conversion_complete = True
    unverified_currency_orders: list[str] = []
    for order in orders:
        provider = resolve_salla_ad_platform(order)
        sales = _salla_sales(order)
        if sales is None:
            unverified_currency_orders.append(
                str(order.get("order_number") or "unknown")
            )
        if provider not in salla:
            unattributed_orders += 1
            if sales is None:
                unattributed_conversion_complete = False
            else:
                unattributed_known_sales += sales
            continue
        salla[provider]["orders"] += 1
        if sales is None:
            salla[provider]["conversion_complete"] = False
        else:
            salla[provider]["known_sales_sar"] += sales

    providers: dict[str, dict[str, Any]] = {}
    known_total_spend = 0.0
    spend_complete = True
    total_salla_orders = 0
    total_salla_sales = 0.0
    total_platform_orders = 0
    total_cpa_complete = True

    for provider in PROVIDER_ORDER:
        spend_key = "google_transitional" if provider == "google" else provider
        raw_spend = (ads.get("breakdown") or {}).get(spend_key)
        parsed_spend = _optional_nonnegative(raw_spend)
        spend = round(parsed_spend, 2) if parsed_spend is not None else None
        if spend is None:
            spend_complete = False
            total_cpa_complete = False
        provider_metrics = (ads.get("providers") or {}).get(provider) or {}
        if spend is None:
            platform_orders = None
        elif provider == "google":
            platform_orders = None
            if spend > 0:
                total_cpa_complete = False
        else:
            parsed_orders = _optional_nonnegative(provider_metrics.get("orders"))
            platform_orders = int(round(parsed_orders)) if parsed_orders is not None else None
            if platform_orders is None and spend > 0:
                total_cpa_complete = False
        platform_cpa = (
            round(spend / platform_orders, 2)
            if spend is not None and spend > 0 and platform_orders is not None and platform_orders > 0
            else None
        )
        salla_orders = int(salla[provider]["orders"])
        provider_conversion_complete = bool(
            salla[provider]["conversion_complete"]
        )
        known_salla_sales = round(
            _number(salla[provider]["known_sales_sar"]),
            2,
        )
        salla_sales = (
            known_salla_sales if provider_conversion_complete else None
        )
        actual_roas = (
            round(salla_sales / spend, 2)
            if salla_sales is not None and spend is not None and spend > 0
            else None
        )
        providers[provider] = {
            "provider": provider,
            "label": PROVIDER_LABELS[provider],
            "spend_sar": spend,
            "data_state": provider_metrics.get("data_state"),
            "coverage_complete": provider_metrics.get("coverage_complete"),
            "amount_complete": provider_metrics.get("amount_complete"),
            "salla_orders": salla_orders,
            "salla_sales_sar": salla_sales,
            "known_salla_sales_sar": known_salla_sales,
            "sales_currency_conversion_complete": provider_conversion_complete,
            "platform_reported_orders": platform_orders,
            "platform_cost_per_order_sar": platform_cpa,
            "actual_roas": actual_roas,
            "sources": {
                "spend": "ad_platform",
                "cost_per_order": "ad_platform",
                "orders": "salla",
                "sales": "salla",
                "roas": "salla_sales_divided_by_ad_platform_spend",
            },
        }
        if spend is not None:
            known_total_spend += spend
        total_salla_orders += salla_orders
        total_salla_sales += known_salla_sales
        if platform_orders is not None:
            total_platform_orders += platform_orders

    total_spend = round(known_total_spend, 2) if spend_complete else None
    sales_conversion_complete = not unverified_currency_orders
    known_total_salla_sales = round(total_salla_sales, 2)
    total_salla_sales = (
        known_total_salla_sales if sales_conversion_complete else None
    )
    total_cpa = (
        round(total_spend / total_platform_orders, 2)
        if total_cpa_complete
        and total_spend is not None
        and total_spend > 0
        and total_platform_orders > 0
        else None
    )
    return {
        "providers": providers,
        "total": {
            "spend_sar": total_spend,
            "salla_orders": total_salla_orders,
            "salla_sales_sar": total_salla_sales,
            "platform_reported_orders": total_platform_orders if total_cpa_complete else None,
            "platform_cost_per_order_sar": total_cpa,
            "actual_roas": (
                round(total_salla_sales / total_spend, 2)
                if total_salla_sales is not None
                and total_spend is not None
                and total_spend > 0
                else None
            ),
            "known_salla_sales_sar": known_total_salla_sales,
        },
        "coverage": {
            "salla_orders_in_scope": len(orders),
            "salla_attributed_orders": total_salla_orders,
            "salla_unattributed_orders": unattributed_orders,
            "salla_unattributed_sales_sar": (
                round(unattributed_known_sales, 2)
                if unattributed_conversion_complete
                else None
            ),
            "known_salla_unattributed_sales_sar": round(
                unattributed_known_sales,
                2,
            ),
            "sales_currency_conversion_complete": sales_conversion_complete,
            "unverified_currency_orders": len(unverified_currency_orders),
            "unverified_currency_order_numbers": unverified_currency_orders[:100],
            "platform_cpa_denominator_complete": total_cpa_complete,
            "spend_amount_complete": spend_complete,
        },
        "source_contract": {
            "orders": "unified_orders.source/utm_source:salla",
            "sales": "unified_orders.total_amount_sar:salla_order_fx",
            "spend": "native_ad_platform_facts_with_mezan2_account_fx",
            "cost_per_order": "ad_platform_spend_divided_by_ad_platform_reported_orders",
            "roas": "salla_sales_divided_by_ad_platform_spend",
            "provider_conversion_sales_excluded": True,
        },
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "PROVIDER_ORDER",
    "build_salla_ads_executive_breakdown",
    "resolve_salla_ad_platform",
]
