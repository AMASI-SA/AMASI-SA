"""Stable created-order counts for Snapchat Campaign Manager.

Campaign acquisition counts are based on Salla order creation time and include
orders even when their current status later becomes cancelled or otherwise
financially excluded. Sales, product cost and contribution profit remain based
only on the financially included order set.

This module replaces the account-timezone Salla outcome projection and wraps the
actual Production account-local campaign report. It is read-only and never
writes to Snapchat, Salla, accounting or Qoyod.
"""
from __future__ import annotations

from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any

from dashboard_v2_routes import _matches_any

from . import snapchat_account_timezone_manager as manager
from . import snapchat_campaign_profitability as profitability

SOURCE_MODE = "snapchat_salla_created_orders_fixed_v1"
PROFIT_CACHE_TTL_SECONDS = 5 * 60

_FINANCIAL_MATCHED: ContextVar[
    dict[tuple[str, str], list[dict[str, Any]]]
] = ContextVar("snapchat_financial_matched_orders", default={})

_PROFIT_CACHE: dict[
    tuple[str, str, str, str],
    tuple[datetime, dict[tuple[str, str], dict[str, Any]], dict[str, Any]],
] = {}

_CANCELLED_TOKENS = (
    "cancelled",
    "canceled",
    "cancel",
    "ملغي",
    "ملغى",
    "ملغية",
    "إلغاء",
    "الغاء",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _status_text(order: dict[str, Any]) -> str:
    value = (
        order.get("order_status_native")
        or order.get("status_native")
        or order.get("order_status")
        or order.get("status")
    )
    return " ".join(_text(value).casefold().replace("_", " ").split())


def is_cancelled_order(order: dict[str, Any]) -> bool:
    status = _status_text(order)
    return bool(status) and any(token in status for token in _CANCELLED_TOKENS)


def _financially_included(
    order: dict[str, Any],
    included_statuses: list[str],
) -> bool:
    return _matches_any(order.get("order_status", ""), included_statuses)


async def _all_orders_in_padded_window(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    hide_inferred: bool,
) -> list[dict[str, Any]]:
    start = date.fromisoformat(date_from) - timedelta(days=1)
    end = date.fromisoformat(date_to) + timedelta(days=1)
    query: dict[str, Any] = {
        "user_id": user_id,
        "order_date": {
            "$gte": start.isoformat(),
            "$lte": end.isoformat(),
        },
    }
    if hide_inferred:
        query["order_date_inferred"] = {"$ne": True}
    cursor = db.unified_orders.find(query, {"_id": 0, "raw_by_source": 0})
    return await manager._to_list(cursor, 100_000)


async def build_created_and_financial_outcomes(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    timezone_name: str,
    identities: list[dict[str, Any]],
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[tuple[str, str], list[dict[str, Any]]],
]:
    """Build fixed acquisition counts and current financial outcomes together."""
    from auth import ensure_user_settings

    settings = await ensure_user_settings(db, user_id)
    included_statuses = settings.get("report_included_statuses") or []
    orders = await _all_orders_in_padded_window(
        db,
        user_id,
        date_from=date_from,
        date_to=date_to,
        hide_inferred=bool(settings.get("hide_inferred_date_orders")),
    )
    id_lookup = manager._unique_lookup(identities, "campaign_id")
    name_lookup = manager._unique_lookup(identities, "campaign_name")
    zone = manager._timezone(timezone_name)

    by_campaign: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "orders": 0,
            "created_orders": 0,
            "financial_orders": 0,
            "cancelled_orders": 0,
            "excluded_orders": 0,
            "other_excluded_orders": 0,
            "sales_sar": 0.0,
        }
    )
    by_date: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "orders": 0,
            "created_orders": 0,
            "financial_orders": 0,
            "cancelled_orders": 0,
            "excluded_orders": 0,
            "other_excluded_orders": 0,
            "sales_sar": 0.0,
        }
    )
    financial_matched: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    matched_by_id = 0
    matched_by_name = 0
    ambiguous = 0
    unattributed = 0
    localized = 0
    fallback_dates = 0

    for order in orders:
        timestamp = manager._order_timestamp(order)
        if timestamp is not None:
            local_date = timestamp.astimezone(zone).date().isoformat()
            localized += 1
        else:
            local_date = _text(order.get("order_date"))[:10]
            fallback_dates += 1
        if not local_date or local_date < date_from or local_date > date_to:
            continue

        key, kind = manager._match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is None:
            if kind.startswith("ambiguous"):
                ambiguous += 1
            else:
                unattributed += 1
            continue

        if kind == "campaign_id":
            matched_by_id += 1
        elif kind == "campaign_name":
            matched_by_name += 1

        campaign_row = by_campaign[key]
        date_row = by_date[local_date]
        for row in (campaign_row, date_row):
            row["orders"] += 1
            row["created_orders"] += 1

        cancelled = is_cancelled_order(order)
        financial = _financially_included(order, included_statuses)
        if cancelled:
            campaign_row["cancelled_orders"] += 1
            date_row["cancelled_orders"] += 1
        if financial:
            amount = manager._number(
                order.get("total_amount") or order.get("total")
            ) or 0.0
            campaign_row["financial_orders"] += 1
            date_row["financial_orders"] += 1
            campaign_row["sales_sar"] += amount
            date_row["sales_sar"] += amount
            financial_matched[key].append(order)
        else:
            campaign_row["excluded_orders"] += 1
            date_row["excluded_orders"] += 1
            if not cancelled:
                campaign_row["other_excluded_orders"] += 1
                date_row["other_excluded_orders"] += 1

    for container in (by_campaign, by_date):
        for row in container.values():
            row["sales_sar"] = round(float(row["sales_sar"]), 2)

    created_total = sum(int(row["created_orders"]) for row in by_campaign.values())
    financial_total = sum(int(row["financial_orders"]) for row in by_campaign.values())
    cancelled_total = sum(int(row["cancelled_orders"]) for row in by_campaign.values())
    coverage = {
        "source_mode": SOURCE_MODE,
        "eligible_orders_in_padded_window_all_statuses": len(orders),
        "created_orders_matched": created_total,
        "financial_orders_matched": financial_total,
        "cancelled_orders_matched": cancelled_total,
        "excluded_orders_matched": created_total - financial_total,
        "matched_by_campaign_id": matched_by_id,
        "matched_by_campaign_name": matched_by_name,
        "ambiguous_orders": ambiguous,
        "unattributed_orders_excluded_from_campaigns": unattributed,
        "timestamp_localized_orders": localized,
        "fallback_order_date_orders": fallback_dates,
        "order_count_semantics": "created_orders_all_statuses_fixed_by_creation_time",
        "sales_semantics": "current_financially_included_orders_only",
        "profitability_semantics": "current_financially_included_orders_only",
        "date_timezone": timezone_name,
        "campaign_rows_exact_match_only": True,
        "read_only": True,
    }
    return dict(by_campaign), dict(by_date), coverage, dict(financial_matched)


async def calculate_financial_profitability(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    date_from: str,
    date_to: str,
    financial_matched: dict[tuple[str, str], list[dict[str, Any]]],
    campaign_spend: dict[tuple[str, str], float],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    cache_key = (user_id, account_id, date_from, date_to)
    now = datetime.now(timezone.utc)
    cached = _PROFIT_CACHE.get(cache_key)
    if cached and now - cached[0] < timedelta(seconds=PROFIT_CACHE_TTL_SECONDS):
        return deepcopy(cached[1]), deepcopy(cached[2])

    cost_context = await profitability._load_cost_context(db, user_id)
    by_campaign: dict[tuple[str, str], dict[str, Any]] = {}
    for key, orders in financial_matched.items():
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
    total_sales = round(
        sum(_float(row.get("sales_sar")) for row in by_campaign.values()), 2
    )
    total_known_cost = round(
        sum(_float(row.get("known_product_cost_sar")) for row in by_campaign.values()), 2
    )
    total_spend = round(
        sum(_float(row.get("ad_spend_sar")) for row in by_campaign.values()), 2
    )
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
    _PROFIT_CACHE[cache_key] = (now, deepcopy(by_campaign), deepcopy(totals))
    if len(_PROFIT_CACHE) > 32:
        oldest = min(_PROFIT_CACHE, key=lambda key: _PROFIT_CACHE[key][0])
        _PROFIT_CACHE.pop(oldest, None)
    return by_campaign, totals


def install_fixed_created_order_semantics() -> None:
    """Install fixed counts on the actual account-timezone Production route."""
    if getattr(manager._salla_account_outcomes, "_mezan_fixed_created_orders", False):
        return

    async def outcomes(
        db: Any,
        user_id: str,
        *,
        date_from: str,
        date_to: str,
        timezone_name: str,
        identities: list[dict[str, Any]],
    ):
        by_campaign, by_date, coverage, financial_matched = (
            await build_created_and_financial_outcomes(
                db,
                user_id,
                date_from=date_from,
                date_to=date_to,
                timezone_name=timezone_name,
                identities=identities,
            )
        )
        _FINANCIAL_MATCHED.set(financial_matched)
        return by_campaign, by_date, coverage

    outcomes._mezan_fixed_created_orders = True  # type: ignore[attr-defined]
    manager._salla_account_outcomes = outcomes

    current_report = manager.build_account_timezone_campaign_report
    if getattr(current_report, "_mezan_fixed_created_orders", False):
        return

    async def report(*args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _FINANCIAL_MATCHED.set({})
        try:
            result = dict(await current_report(*args, **kwargs) or {})
            result_source = _text(
                result.get("result_source") or kwargs.get("result_source")
            ).lower()
            if result_source != "salla":
                result.setdefault("policy", {}).update({
                    "salla_order_semantics_applied": False,
                    "provider_metrics_preserved_for_platform_source": True,
                })
                return result

            financial_matched = _FINANCIAL_MATCHED.get()
            campaigns = result.get("campaigns") or []

            created_total = 0
            financial_total = 0
            cancelled_total = 0
            excluded_total = 0
            for campaign in campaigns:
                salla = campaign.get("salla_results")
                salla = salla if isinstance(salla, dict) else {}
                created = int(salla.get("created_orders") or salla.get("orders") or 0)
                financial = int(salla.get("financial_orders") or 0)
                cancelled = int(salla.get("cancelled_orders") or 0)
                excluded = int(salla.get("excluded_orders") or max(created - financial, 0))
                campaign.update({
                    "orders": created,
                    "created_orders": created,
                    "financial_orders": financial,
                    "cancelled_orders": cancelled,
                    "excluded_orders": excluded,
                    "other_excluded_orders": int(
                        salla.get("other_excluded_orders") or 0
                    ),
                    "order_count_source": "salla_created_orders_all_statuses",
                })
                spend = manager._number(campaign.get("spend_sar"))
                campaign["cpa_sar"] = (
                    round(float(spend) / created, 6)
                    if spend is not None and created > 0 else None
                )
                created_total += created
                financial_total += financial
                cancelled_total += cancelled
                excluded_total += excluded

            totals = result.setdefault("totals", {})
            totals.update({
                "orders": created_total,
                "created_orders": created_total,
                "financial_orders": financial_total,
                "cancelled_orders": cancelled_total,
                "excluded_orders": excluded_total,
                "order_count_source": "salla_created_orders_all_statuses",
            })
            spend_total = manager._number(totals.get("spend_sar"))
            totals["cpa_sar"] = (
                round(float(spend_total) / created_total, 6)
                if spend_total is not None and created_total > 0 else None
            )

            db = args[0] if args else kwargs.get("db")
            user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
            account_id = _text(result.get("selected_account_id"))
            date_from = _text(result.get("date_from"))
            date_to = _text(result.get("date_to"))
            if db is not None and user_id and account_id and date_from and date_to:
                campaign_spend = {
                    (
                        _text(campaign.get("account_id")),
                        _text(campaign.get("campaign_id")),
                    ): _float(campaign.get("spend_sar"))
                    for campaign in campaigns
                    if _text(campaign.get("account_id"))
                    and _text(campaign.get("campaign_id"))
                }
                by_campaign, profit_totals = await calculate_financial_profitability(
                    db,
                    str(user_id),
                    account_id=account_id,
                    date_from=date_from,
                    date_to=date_to,
                    financial_matched=financial_matched,
                    campaign_spend=campaign_spend,
                )
                for campaign in campaigns:
                    key = (
                        _text(campaign.get("account_id")),
                        _text(campaign.get("campaign_id")),
                    )
                    if key in by_campaign:
                        campaign["profitability"] = by_campaign[key]
                totals["profitability"] = profit_totals

            source = result.setdefault("source", {})
            attribution = source.setdefault("salla_attribution", {})
            attribution.update({
                "source_mode": SOURCE_MODE,
                "created_orders_matched": created_total,
                "financial_orders_matched": financial_total,
                "cancelled_orders_matched": cancelled_total,
                "excluded_orders_matched": excluded_total,
                "order_count_semantics": (
                    "created_orders_all_statuses_fixed_by_creation_time"
                ),
                "sales_semantics": "current_financially_included_orders_only",
                "profitability_semantics": (
                    "current_financially_included_orders_only"
                ),
                "read_only": True,
            })
            result.setdefault("policy", {}).update({
                "created_order_counts_include_cancelled": True,
                "sales_exclude_financially_excluded_orders": True,
                "profitability_excludes_financially_excluded_orders": True,
                "provider_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
                "salla_order_semantics_applied": True,
                "provider_metrics_preserved_for_platform_source": False,
            })
            return result
        finally:
            _FINANCIAL_MATCHED.reset(token)

    report._mezan_fixed_created_orders = True  # type: ignore[attr-defined]
    manager.build_account_timezone_campaign_report = report


__all__ = [
    "PROFIT_CACHE_TTL_SECONDS",
    "SOURCE_MODE",
    "build_created_and_financial_outcomes",
    "calculate_financial_profitability",
    "install_fixed_created_order_semantics",
    "is_cancelled_order",
]
