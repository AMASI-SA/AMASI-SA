"""Read Snapchat V2 through the provider-neutral marketing contract."""
from __future__ import annotations

from datetime import date
from typing import Any

from snapchat_v2.accounts import get_selected_account
from snapchat_v2.entities import list_entities
from snapchat_v2.projections import (
    RIYADH_TIMEZONE,
    SNAPCHAT_DAILY_PROJECTIONS_COLLECTION,
    list_daily_projections,
)
from snapchat_v2.reconciliation import (
    calculate_cost_components,
    list_reconciliation,
)
from snapchat_v2.salla_outcomes import load_salla_campaign_outcomes
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from unified_marketing.adapters.snapchat_v2 import build_snapchat_v2_unified_report

INT_FIELDS = (
    "impressions",
    "swipes",
    "video_views",
    "view_content",
    "add_to_cart",
    "start_checkout",
    "add_billing",
    "purchases",
)
FLOAT_FIELDS = (
    "base_spend_native",
    "view_completion",
    "purchase_value_native",
)


def _sum(rows: list[dict[str, Any]], field: str) -> float:
    return round(sum(float(row.get(field) or 0) for row in rows), 6)


async def _projection_financial_status(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    projections: list[dict[str, Any]],
) -> str:
    """Prove the selected range from the runs that produced its facts.

    A newer rolling run can legitimately be partial while the current local
    day is open. It must not downgrade an already closed historical range.
    Daily projections retain immutable source run ids for this proof.
    """
    run_ids = sorted({
        str(run_id)
        for projection in projections
        for run_id in list(projection.get("source_sync_run_ids") or [])
        if run_id
    })
    if not projections or not run_ids:
        return "partial"
    cursor = db[SNAPCHAT_SYNC_RUNS_COLLECTION].find(
        {
            "user_id": str(user_id),
            "ad_account_id": str(ad_account_id),
            "sync_run_id": {"$in": run_ids},
        },
        {"_id": 0, "sync_run_id": 1, "financial_sync_status": 1},
    )
    try:
        rows = list(await cursor.to_list(length=len(run_ids)))
    except TypeError:
        rows = list(await cursor.to_list(len(run_ids)))
    complete_ids = {
        str(row.get("sync_run_id"))
        for row in rows
        if row.get("financial_sync_status") == "complete"
    }
    return "complete" if set(run_ids).issubset(complete_ids) else "partial"


def _reconciliation_status(
    reconciliations: list[dict[str, Any]],
    *,
    date_from: date,
    date_to: date,
) -> str:
    expected_days = (date_to - date_from).days + 1
    reconciled_dates = {
        str(row.get("report_date"))
        for row in reconciliations
        if row.get("reconciled") is True
    }
    return "reconciled" if len(reconciled_dates) == expected_days else "partial"


async def load_snapchat_v2_account_report(
    db: Any,
    user_id: str,
    *,
    date_from: date,
    date_to: date,
    timezone_name: str = RIYADH_TIMEZONE,
) -> dict[str, Any]:
    account = await get_selected_account(db, str(user_id))
    if not account:
        raise ValueError("unified_marketing_snapchat_selected_account_missing")
    account_id = str(account["ad_account_id"])
    projections = await list_daily_projections(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        projection_timezone=timezone_name,
        action_report_time="conversion",
    )
    expected_days = (date_to - date_from).days + 1
    financial_status = await _projection_financial_status(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        projections=projections,
    )
    reconciliations = await list_reconciliation(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        action_report_time="conversion",
    )
    reconciliation_status = _reconciliation_status(
        reconciliations,
        date_from=date_from,
        date_to=date_to,
    )
    amount_complete = (
        len(projections) == expected_days
        and all(row.get("amount_complete") is True for row in projections)
    )
    sync_status = (
        "complete"
        if amount_complete
        and financial_status == "complete"
        and reconciliation_status == "reconciled"
        else "partial"
    )
    totals: dict[str, Any] = {
        "source_collection": SNAPCHAT_DAILY_PROJECTIONS_COLLECTION,
        "source_fact_count": sum(
            int(row.get("source_fact_count") or 0) for row in projections
        ),
        "performance_sync_status": sync_status,
        "amount_complete": amount_complete,
        "reconciliation_status": reconciliation_status,
        "performance_reason": (
            None
            if sync_status == "complete"
            else "riyadh_projection_financial_or_reconciliation_incomplete"
        ),
        "reach_frequency_scope": "exact_total_window_required",
    }
    for field in INT_FIELDS:
        totals[field] = int(_sum(projections, field))
    for field in FLOAT_FIELDS:
        totals[field] = _sum(projections, field)
    totals["spend_native"] = totals.pop("base_spend_native")
    totals["ctr_pct"] = (
        round((totals["swipes"] / totals["impressions"]) * 100, 6)
        if totals["impressions"] > 0
        else None
    )
    totals["roas"] = (
        round(totals["purchase_value_native"] / totals["spend_native"], 6)
        if totals["spend_native"] > 0
        else None
    )

    try:
        cost = await calculate_cost_components(
            db,
            user_id=str(user_id),
            account=account,
            spend_native=1.0,
        )
        exchange_rate = float(cost.get("exchange_rate_to_sar") or 0) or None
    except Exception:  # noqa: BLE001 - contract remains partial and read-only
        exchange_rate = None
    totals["exchange_rate_to_sar"] = exchange_rate
    totals["spend_sar"] = (
        round(totals["spend_native"] * exchange_rate, 2)
        if exchange_rate is not None and amount_complete
        else None
    )

    campaigns = await list_entities(
        db,
        user_id=str(user_id),
        ad_account_id=account_id,
        entity_type="campaign",
        active_only=False,
        limit=20_000,
    )
    identities = [
        {
            "account_id": account_id,
            "campaign_id": str(row.get("external_id") or ""),
            "campaign_name": row.get("name") or row.get("external_id"),
        }
        for row in campaigns
        if row.get("external_id")
    ]
    try:
        salla = await load_salla_campaign_outcomes(
            db,
            str(user_id),
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
            identities=identities,
            platform_purchases=int(totals["purchases"]),
        )
        salla_summary = dict(salla.get("summary") or {})
        salla_available = salla_summary.get("coverage_status") == "complete"
    except Exception as exc:  # noqa: BLE001
        salla_available = False
        salla = {"orders": [], "orders_total": 0, "orders_returned": 0, "truncated": False}
        salla_summary = {
            "coverage_status": "partial",
            "reason": str(type(exc).__name__)[:96],
            "platform_attributed_purchases": int(totals["purchases"]),
        }
    totals["salla_results"] = {
        "status": "complete" if salla_available else "partial",
        "orders": (
            int(salla_summary.get("campaign_matched_orders") or 0)
            if salla_available
            else None
        ),
        "sales_sar": (
            float(salla_summary.get("campaign_matched_financial_sales_sar") or 0)
            if salla_available
            else None
        ),
        "roas": None,
    }
    if (
        salla_available
        and totals["spend_sar"] is not None
        and totals["spend_sar"] > 0
    ):
        totals["salla_results"]["roas"] = round(
            totals["salla_results"]["sales_sar"] / totals["spend_sar"],
            6,
        )

    report = build_snapchat_v2_unified_report(
        account_value=account,
        period_value={
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "timezone": timezone_name,
            "action_report_time": "conversion",
        },
        entity_type="account",
        rows=[totals],
        totals=totals,
        sync_status=sync_status,
        orders=list(salla.get("orders") or []),
        order_summary={
            **salla_summary,
            "orders_total": int(salla.get("orders_total") or 0),
            "orders_returned": int(salla.get("orders_returned") or 0),
            "truncated": bool(salla.get("truncated")),
        },
    )
    report["decision_eligibility"] = {
        "eligible": False,
        "reason": "dashboard_shadow_not_accepted",
    }
    return report


__all__ = ["load_snapchat_v2_account_report"]
