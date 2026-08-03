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

        # A unique campaign match is sufficient proof. Otherwise the Salla
        # source fields must explicitly resolve to Snapchat.
        belongs_to_snapchat = key is not None or source_is_snapchat
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

    routes._salla_outcomes = salla_campaign_outcomes


__all__ = [
    "install_snapchat_salla_campaign_outcomes",
    "salla_campaign_outcomes",
]
