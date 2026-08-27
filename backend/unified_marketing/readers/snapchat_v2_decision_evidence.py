"""Decision-grade enrichments for the Snapchat V2 Unified Marketing reader.

This module stays inside the provider adapter boundary.  It does not write to
Snapchat or MongoDB.  It only derives missing decision evidence from already
persisted, authoritative V2 and Salla/Mezan facts.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from unified_marketing.readers import snapchat_v2 as base_reader


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _money_amount(value: Any) -> float | None:
    amount = _mapping(value).get("amount")
    if amount is None or isinstance(amount, bool):
        return None
    try:
        parsed = float(amount)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


async def load_snapchat_v2_account_identity(
    db: Any,
    user_id: str,
) -> dict[str, Any] | None:
    """Return account identity with a provable read-only freshness timestamp.

    Selected-account documents predate the V2 sync-run ledger and can have a
    null ``last_sync_at``.  In that case the latest completed sync run for the
    same selected account is authoritative freshness evidence.  No timestamp is
    invented: if no completed run exists, the field remains null and Phase 5
    continues to fail closed.
    """
    identity = await base_reader.load_snapchat_v2_account_identity(db, user_id)
    if not identity or identity.get("last_sync_at"):
        return identity

    account_id = str(identity.get("id") or "").strip()
    if not account_id:
        return identity

    run = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one(
        {
            "user_id": str(user_id),
            "provider": "snapchat_ads",
            "ad_account_id": account_id,
            "status": "complete",
        },
        {
            "_id": 0,
            "finished_at": 1,
            "started_at": 1,
            "sync_run_id": 1,
            "financial_sync_status": 1,
        },
        sort=[("finished_at", -1), ("started_at", -1)],
    )
    if not run:
        return identity

    last_sync_at = run.get("finished_at") or run.get("started_at")
    if not last_sync_at:
        return identity

    return {
        **identity,
        "last_sync_at": last_sync_at,
        "freshness_source": "snapchat_v2_latest_completed_sync_run",
        "freshness_sync_run_id": run.get("sync_run_id"),
        "freshness_financial_sync_status": run.get("financial_sync_status"),
    }


def _derive_account_profitability(
    account_report: dict[str, Any],
    campaign_report: dict[str, Any],
) -> dict[str, Any] | None:
    """Aggregate exact campaign profitability into an account-level envelope.

    The account report's commerce revenue already uses exact campaign-matched
    financial Salla sales.  We therefore aggregate only product costs proven by
    those same campaign rows and subtract the authoritative account spend once.
    Rows with zero exact-match revenue may legitimately have unavailable
    profitability and contribute zero product cost.  A non-zero revenue row
    without complete product cost evidence keeps the account profitability
    unavailable.
    """
    totals = _mapping(account_report.get("totals"))
    account_quality = _mapping(totals.get("quality"))
    campaign_totals = _mapping(campaign_report.get("totals"))
    campaign_quality = _mapping(campaign_totals.get("quality"))

    if account_quality.get("amount_complete") is not True:
        return None
    if campaign_quality.get("sync_status") != "complete":
        return None
    if campaign_quality.get("coverage_status") != "complete":
        return None

    sales = _money_amount(_mapping(totals.get("commerce_outcomes")).get("revenue"))
    spend_sar = _money_amount(_mapping(totals.get("delivery")).get("spend_sar"))
    if sales is None or spend_sar is None:
        return None

    product_cost = 0.0
    known_product_cost = 0.0
    financial_orders = 0
    product_count = 0
    matched_profit_sales = 0.0
    missing_cost_orders = 0

    for row in list(campaign_report.get("rows") or []):
        commerce = _mapping(row.get("commerce_outcomes"))
        row_sales = _money_amount(commerce.get("revenue"))
        if commerce.get("status") != "complete" or row_sales is None:
            return None

        profitability = _mapping(row.get("commerce_profitability"))
        status = str(profitability.get("status") or "unavailable")
        if status == "complete":
            cost = _money_amount(profitability.get("product_cost"))
            known_cost = _money_amount(profitability.get("known_product_cost"))
            profit_sales = _money_amount(profitability.get("sales"))
            if cost is None or known_cost is None or profit_sales is None:
                return None
            product_cost += cost
            known_product_cost += known_cost
            matched_profit_sales += profit_sales
            financial_orders += int(profitability.get("orders") or 0)
            product_count += int(profitability.get("product_count") or 0)
            missing_cost_orders += int(profitability.get("missing_cost_orders") or 0)
        elif abs(row_sales) <= 0.005:
            # Exact-match Salla revenue is proven zero for this row; there is no
            # product cost to add. Account ad spend is subtracted globally below.
            continue
        else:
            return None

    if missing_cost_orders != 0:
        return None
    if abs(round(matched_profit_sales, 2) - round(sales, 2)) > 0.01:
        return None

    product_cost = round(product_cost, 2)
    known_product_cost = round(known_product_cost, 2)
    contribution_profit = round(sales - product_cost - spend_sar, 2)
    margin = round((contribution_profit / sales) * 100, 6) if sales > 0 else None

    return {
        "status": "complete",
        "orders": financial_orders,
        "sales": {"amount": round(sales, 2), "currency": "SAR"},
        "product_cost": {"amount": product_cost, "currency": "SAR"},
        "known_product_cost": {"amount": known_product_cost, "currency": "SAR"},
        "ad_spend": {"amount": round(spend_sar, 2), "currency": "SAR"},
        "contribution_profit": {"amount": contribution_profit, "currency": "SAR"},
        "profit_margin_pct": margin,
        "cost_status": "complete",
        "missing_cost_orders": 0,
        "product_count": product_count,
        "products": [],
        "profit_scope": "account_sum_of_exact_campaign_matches",
        "allocation_method": "aggregate_exact_campaign_product_cost_then_subtract_account_ad_spend",
    }


async def load_snapchat_v2_account_report(
    db: Any,
    user_id: str,
    *,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    """Return the base account report with decision-grade profitability when provable."""
    account_report = await base_reader.load_snapchat_v2_account_report(
        db,
        user_id,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )
    totals = _mapping(account_report.get("totals"))
    current = _mapping(totals.get("commerce_profitability"))
    if current.get("status") == "complete":
        return account_report

    campaign_report = await base_reader.load_snapchat_v2_entity_report(
        db,
        user_id,
        entity_level="campaign",
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
        include_stale=False,
    )
    profitability = _derive_account_profitability(account_report, campaign_report)
    if profitability is None:
        return account_report

    totals["commerce_profitability"] = profitability
    account_report["totals"] = totals
    account_report["decision_evidence_enrichment"] = {
        "account_profitability": "derived_from_exact_campaign_profitability",
        "read_only": True,
        "database_writes_performed": False,
        "platform_writes_performed": False,
    }
    return account_report


__all__ = [
    "load_snapchat_v2_account_identity",
    "load_snapchat_v2_account_report",
]
