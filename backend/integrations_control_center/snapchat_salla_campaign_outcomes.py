"""Salla outcomes for Snapchat campaign reports.

Headline and daily totals include every financially included Salla order that
is proven to be Snapchat. Campaign and account rows include only exact,
non-ambiguous campaign matches. This prevents both silent under-counting and
invented campaign attribution.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round(numerator / denominator, 6)


async def salla_campaign_outcomes(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    identities: list[dict[str, Any]],
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    # Lazy imports keep the native campaign route importable in focused tests.
    from dashboard_v2_routes import _filtered_orders
    from .snapchat_campaign_result_source_routes import (
        _match_order_campaign,
        _source_is_snapchat,
        _unique_lookup,
    )

    orders = await _filtered_orders(
        db,
        user_id,
        from_date=date_from,
        to_date=date_to,
        payment_methods=None,
        shipping_companies=None,
    )
    id_lookup = _unique_lookup(identities, "campaign_id")
    name_lookup = _unique_lookup(identities, "campaign_name")

    by_campaign: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    by_account: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    by_date: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )

    source_total = {"orders": 0, "sales_sar": 0.0}
    matched_by_id = 0
    matched_by_name = 0
    matched_sales_sar = 0.0
    ambiguous = 0
    ambiguous_sales_sar = 0.0
    unmatched_snapchat = 0
    unmatched_snapchat_sales_sar = 0.0

    for order in orders:
        key, match_kind = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        amount = _number(order.get("total_amount") or order.get("total")) or 0.0
        order_date = _text(order.get("order_date"))[:10]
        source_is_snapchat = _source_is_snapchat(order)

        # A unique or ambiguous match against Snapchat campaign identities is
        # enough to count the order in platform totals. Only a unique match is
        # assigned to an individual campaign row.
        belongs_to_snapchat = (
            key is not None
            or source_is_snapchat
            or match_kind.startswith("ambiguous")
        )
        if belongs_to_snapchat:
            source_total["orders"] += 1
            source_total["sales_sar"] += amount
            if order_date:
                by_date[order_date]["orders"] += 1
                by_date[order_date]["sales_sar"] += amount

        if key is None:
            if match_kind.startswith("ambiguous"):
                ambiguous += 1
                ambiguous_sales_sar += amount
            elif source_is_snapchat:
                unmatched_snapchat += 1
                unmatched_snapchat_sales_sar += amount
            continue

        if match_kind == "campaign_id":
            matched_by_id += 1
        elif match_kind == "campaign_name":
            matched_by_name += 1
        matched_sales_sar += amount
        by_campaign[key]["orders"] += 1
        by_campaign[key]["sales_sar"] += amount
        by_account[key[0]]["orders"] += 1
        by_account[key[0]]["sales_sar"] += amount

    for container in (by_campaign, by_account, by_date):
        for value in container.values():
            value["sales_sar"] = round(float(value["sales_sar"]), 2)
    source_total["sales_sar"] = round(float(source_total["sales_sar"]), 2)

    coverage = {
        "eligible_salla_orders": len(orders),
        "salla_snapchat_orders": int(source_total["orders"]),
        "salla_snapchat_sales_sar": source_total["sales_sar"],
        "matched_orders": matched_by_id + matched_by_name,
        "matched_sales_sar": round(matched_sales_sar, 2),
        "matched_by_campaign_id": matched_by_id,
        "matched_by_campaign_name": matched_by_name,
        "ambiguous_orders": ambiguous,
        "ambiguous_sales_sar": round(ambiguous_sales_sar, 2),
        "unattributed_snapchat_orders": unmatched_snapchat,
        "unattributed_snapchat_sales_sar": round(
            unmatched_snapchat_sales_sar, 2
        ),
        "provider_conversion_sales_excluded": True,
        "campaign_rows_exact_match_only": True,
        "headline_includes_unattributed_snapchat": True,
    }
    return dict(by_campaign), dict(by_account), dict(by_date), coverage


def install_snapchat_salla_campaign_outcomes() -> None:
    from . import snapchat_campaign_result_source_routes as routes

    current_selected_metrics = routes._selected_metrics
    if not getattr(
        current_selected_metrics, "_mezan_native_sales_fallback", False
    ):
        def wrapped_selected_metrics(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = current_selected_metrics(*args, **kwargs)
            if _number(result.get("sales_native")) is not None:
                return result
            rate = _number(kwargs.get("rate"))
            sales_sar = _number(result.get("sales_sar"))
            result["sales_native"] = (
                round(sales_sar / rate, 6)
                if sales_sar is not None and rate not in {None, 0}
                else None
            )
            return result

        wrapped_selected_metrics._mezan_native_sales_fallback = True  # type: ignore[attr-defined]
        routes._selected_metrics = wrapped_selected_metrics

    current_build = routes.build_snapchat_result_source_report
    if getattr(current_build, "_mezan_salla_headline_totals", False):
        routes._salla_outcomes = salla_campaign_outcomes
        return

    routes._salla_outcomes = salla_campaign_outcomes

    async def wrapped_build_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = await current_build(*args, **kwargs)
        result_source = str(
            kwargs.get("result_source") or result.get("result_source") or ""
        )
        if result_source != routes.RESULT_SOURCE_SALLA:
            return result

        coverage = (
            (result.get("source") or {}).get("salla_attribution") or {}
        )
        total_orders = int(coverage.get("salla_snapchat_orders") or 0)
        total_sales = round(
            float(coverage.get("salla_snapchat_sales_sar") or 0), 2
        )
        totals = result.setdefault("totals", {})
        spend_sar = _number(totals.get("spend_sar"))
        totals.update({
            "orders": total_orders,
            "sales_sar": total_sales,
            "roas": _ratio(total_sales, spend_sar),
            "cpa_sar": _ratio(spend_sar, total_orders),
        })
        return result

    wrapped_build_report._mezan_salla_headline_totals = True  # type: ignore[attr-defined]
    routes.build_snapchat_result_source_report = wrapped_build_report


# The package imports the original route before this module, so installation is
# safe here and also covers focused tests that import the route directly.
install_snapchat_salla_campaign_outcomes()


__all__ = [
    "install_snapchat_salla_campaign_outcomes",
    "salla_campaign_outcomes",
]
