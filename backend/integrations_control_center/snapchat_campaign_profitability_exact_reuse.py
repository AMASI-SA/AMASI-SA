"""Reuse the exact Salla campaign matches for campaign profitability.

The campaign result-source report already establishes the authoritative set of
Salla orders matched to each Snapchat campaign. This adapter captures that exact
set during the same report request and calculates product cost/profit from those
orders, preventing a second attribution pass from diverging from visible Salla
sales and order counts.
"""
from __future__ import annotations

from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from dashboard_v2_routes import _filtered_orders

from . import snapchat_campaign_profitability as profitability
from . import snapchat_campaign_result_source_routes as routes

SOURCE_MODE = "snapchat_salla_visible_matches_profitability_v2"
CACHE_TTL_SECONDS = 5 * 60
_MATCHED_ORDERS: ContextVar[dict[tuple[str, str], list[dict[str, Any]]]] = (
    ContextVar("snapchat_campaign_matched_orders", default={})
)
_CACHE: dict[
    tuple[str, str, str],
    tuple[datetime, dict[tuple[str, str], dict[str, Any]], dict[str, Any]],
] = {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


async def capture_exact_matched_orders(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    identities: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Run the same matcher and retain only the exact orders used by Salla results."""
    orders = await _filtered_orders(
        db,
        user_id,
        from_date=date_from,
        to_date=date_to,
        payment_methods=None,
        shipping_companies=None,
    )
    id_lookup = routes._unique_lookup(identities, "campaign_id")
    name_lookup = routes._unique_lookup(identities, "campaign_name")
    matched: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        key, _match_kind = routes._match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is not None:
            matched[key].append(order)
    return dict(matched)


async def calculate_profitability_from_exact_matches(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    matched_orders: dict[tuple[str, str], list[dict[str, Any]]],
    campaign_spend: dict[tuple[str, str], float],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    cache_key = (user_id, date_from, date_to)
    now = datetime.now(timezone.utc)
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < timedelta(seconds=CACHE_TTL_SECONDS):
        return deepcopy(cached[1]), deepcopy(cached[2])

    cost_context = await profitability._load_cost_context(db, user_id)
    by_campaign: dict[tuple[str, str], dict[str, Any]] = {}
    for key, orders in matched_orders.items():
        bucket = profitability._new_campaign_bucket()
        for order in orders:
            profitability._add_order_to_campaign(
                bucket,
                profitability._order_cost_and_products(order, cost_context),
            )
        by_campaign[key] = profitability._finalize_campaign(
            bucket,
            spend_sar=round(_float(campaign_spend.get(key)), 6),
        )

    all_complete = all(
        row.get("product_cost_sar") is not None
        for row in by_campaign.values()
    )
    total_sales = round(sum(_float(row.get("sales_sar")) for row in by_campaign.values()), 2)
    total_known_cost = round(sum(_float(row.get("known_product_cost_sar")) for row in by_campaign.values()), 2)
    total_spend = round(sum(_float(row.get("ad_spend_sar")) for row in by_campaign.values()), 2)
    total_profit = (
        round(total_sales - total_known_cost - total_spend, 2)
        if all_complete else None
    )
    totals = {
        "orders": sum(int(row.get("orders") or 0) for row in by_campaign.values()),
        "sales_sar": total_sales,
        "product_cost_sar": total_known_cost if all_complete else None,
        "known_product_cost_sar": total_known_cost,
        "ad_spend_sar": total_spend,
        "contribution_profit_sar": total_profit,
        "profit_margin_pct": (
            round(total_profit / total_sales * 100, 2)
            if total_profit is not None and total_sales > 0 else None
        ),
        "campaigns_with_orders": len(by_campaign),
        "campaigns_with_missing_cost": sum(
            int(row.get("cost_status") == "missing")
            for row in by_campaign.values()
        ),
    }
    _CACHE[cache_key] = (now, deepcopy(by_campaign), deepcopy(totals))
    if len(_CACHE) > 32:
        oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
        _CACHE.pop(oldest, None)
    return by_campaign, totals


def install_exact_salla_profitability_reuse() -> None:
    if getattr(routes._salla_outcomes, "_mezan_exact_match_capture", False):
        return

    original_outcomes = routes._salla_outcomes

    async def outcomes_with_capture(*args: Any, **kwargs: Any):
        result = await original_outcomes(*args, **kwargs)
        db = args[0] if args else kwargs.get("db")
        user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
        identities = kwargs.get("identities") or []
        date_from = _text(kwargs.get("date_from"))
        date_to = _text(kwargs.get("date_to"))
        if db is not None and user_id and date_from and date_to:
            matched = await capture_exact_matched_orders(
                db,
                str(user_id),
                date_from=date_from,
                date_to=date_to,
                identities=identities,
            )
            _MATCHED_ORDERS.set(matched)
        return result

    outcomes_with_capture._mezan_exact_match_capture = True  # type: ignore[attr-defined]
    routes._salla_outcomes = outcomes_with_capture

    current_report = routes.build_snapchat_result_source_report
    if getattr(current_report, "_mezan_exact_profitability_reuse", False):
        return

    async def report_with_exact_profitability(*args: Any, **kwargs: Any):
        token = _MATCHED_ORDERS.set({})
        try:
            result = await current_report(*args, **kwargs)
            matched = _MATCHED_ORDERS.get()
            if not matched:
                return result
            db = args[0] if args else kwargs.get("db")
            user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
            date_from = _text(result.get("date_from"))
            date_to = _text(result.get("date_to"))
            if db is None or not user_id or not date_from or not date_to:
                return result

            campaign_spend = {
                (
                    _text(campaign.get("account_id")),
                    _text(campaign.get("campaign_id")),
                ): _float(campaign.get("spend_sar"))
                for campaign in result.get("campaigns") or []
                if _text(campaign.get("account_id"))
                and _text(campaign.get("campaign_id"))
            }
            by_campaign, totals = await calculate_profitability_from_exact_matches(
                db,
                str(user_id),
                date_from=date_from,
                date_to=date_to,
                matched_orders=matched,
                campaign_spend=campaign_spend,
            )
            for campaign in result.get("campaigns") or []:
                key = (
                    _text(campaign.get("account_id")),
                    _text(campaign.get("campaign_id")),
                )
                if key in by_campaign:
                    campaign["profitability"] = by_campaign[key]

            result.setdefault("totals", {})["profitability"] = totals
            result.setdefault("source", {}).setdefault(
                "campaign_profitability", {}
            ).update({
                "source_mode": SOURCE_MODE,
                "reuses_visible_salla_matches": True,
                "matched_orders": sum(len(rows) for rows in matched.values()),
                "campaigns_with_orders": len(matched),
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "read_only": True,
            })
            return result
        finally:
            _MATCHED_ORDERS.reset(token)

    report_with_exact_profitability._mezan_exact_profitability_reuse = True  # type: ignore[attr-defined]
    routes.build_snapchat_result_source_report = report_with_exact_profitability


__all__ = [
    "CACHE_TTL_SECONDS",
    "SOURCE_MODE",
    "calculate_profitability_from_exact_matches",
    "capture_exact_matched_orders",
    "install_exact_salla_profitability_reuse",
]
