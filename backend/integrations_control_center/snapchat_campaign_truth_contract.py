"""Final read-only truth contract for the Snapchat Campaign Manager report."""
from __future__ import annotations

from typing import Any

from . import snapchat_account_timezone_manager as manager

STATUS_VALUES = {"complete", "partial", "stale", "failed"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    return manager._number(value)


def _status(value: Any, fallback: str) -> str:
    candidate = _text(value).lower()
    return candidate if candidate in STATUS_VALUES else fallback


def _ratio(numerator: Any, denominator: Any, *, eligible: bool) -> float | None:
    if not eligible:
        return None
    return manager._ratio(_number(numerator), _number(denominator))


def _salla_campaign_values(campaign: dict[str, Any]) -> tuple[int | None, float | None]:
    salla = campaign.get("salla_results")
    salla = salla if isinstance(salla, dict) else {}
    raw_orders = campaign.get("salla_orders", salla.get("created_orders", salla.get("orders")))
    raw_sales = campaign.get("salla_sales_sar", salla.get("sales_sar"))
    orders = int(raw_orders) if _number(raw_orders) is not None else None
    sales = _number(raw_sales)
    return orders, round(sales, 2) if sales is not None else None


def _snapchat_campaign_values(
    campaign: dict[str, Any],
) -> tuple[int | None, float | None, float | None]:
    platform = campaign.get("platform_results")
    platform = platform if isinstance(platform, dict) else {}
    raw_purchases = campaign.get("snapchat_purchases", platform.get("orders"))
    raw_value = campaign.get("snapchat_purchase_value_sar", platform.get("sales_sar"))
    raw_spend = campaign.get("snapchat_spend_sar", campaign.get("spend_sar"))
    purchases = int(raw_purchases) if _number(raw_purchases) is not None else None
    value = _number(raw_value)
    spend = _number(raw_spend)
    return (
        purchases,
        round(value, 2) if value is not None else None,
        round(spend, 6) if spend is not None else None,
    )


def apply_campaign_truth_contract(result: dict[str, Any]) -> dict[str, Any]:
    """Expose source-specific facts and fail closed on cross-source ratios."""
    source = result.setdefault("source", {})
    coverage = source.get("salla_attribution")
    coverage = coverage if isinstance(coverage, dict) else {}
    totals = result.setdefault("totals", {})

    salla_status = _status(result.get("salla_status"), "complete")
    platform_status = source.get("platform_source_status")
    snapchat_status = _status(
        platform_status or result.get("snapchat_status"),
        "partial",
    )
    if source.get("platform_total_snapshot_ready") is False and snapchat_status == "complete":
        snapchat_status = "partial"

    matching_status = _text(
        result.get("matching_status") or coverage.get("matching_status")
    ) or "complete"
    salla_total = coverage.get("salla_total_orders", totals.get("salla_total_orders"))
    salla_matched = coverage.get(
        "salla_matched_orders", totals.get("salla_matched_orders")
    )
    total_number = int(salla_total) if _number(salla_total) is not None else None
    matched_number = int(salla_matched) if _number(salla_matched) is not None else None
    counts_available = total_number is not None and matched_number is not None
    invariant_ok = counts_available and matched_number <= total_number
    if not counts_available and matching_status == "complete":
        matching_status = "partial"
    elif not invariant_ok:
        matching_status = "failed"
        salla_status = "failed"

    reconciliation_reasons: list[str] = []
    if salla_status != "complete":
        reconciliation_reasons.append(f"salla_{salla_status}")
    if snapchat_status != "complete":
        reconciliation_reasons.append(f"snapchat_{snapchat_status}")
    if matching_status != "complete":
        reconciliation_reasons.append("matching_not_complete")
    if result.get("business_timezone") != manager.BUSINESS_TIMEZONE:
        reconciliation_reasons.append("salla_business_timezone_mismatch")
    platform_account_spend = _number(source.get("platform_account_spend_sar"))
    platform_campaign_spend = _number(source.get("platform_campaign_spend_sar"))
    platform_spend_delta = (
        round(platform_account_spend - platform_campaign_spend, 6)
        if platform_account_spend is not None and platform_campaign_spend is not None
        else None
    )
    if platform_spend_delta is not None and abs(platform_spend_delta) > 0.01:
        reconciliation_reasons.append("snapchat_account_campaign_spend_mismatch")
    salla_campaign_matched = _number(source.get("salla_campaign_matched_orders"))
    salla_campaign_delta = (
        int(matched_number - salla_campaign_matched)
        if matched_number is not None and salla_campaign_matched is not None
        else None
    )
    if salla_campaign_delta not in (None, 0):
        reconciliation_reasons.append("salla_matched_campaign_sum_mismatch")
    reconciled = not reconciliation_reasons

    salla_sales = _number(totals.get("salla_sales_sar"))
    snap_purchases = totals.get("snapchat_purchases")
    snap_value = _number(totals.get("snapchat_purchase_value_sar"))
    snap_spend = _number(totals.get("snapchat_spend_sar"))
    salla_profitability = totals.get("salla_profitability") or totals.get("profitability")

    totals.update({
        "salla_total_orders": total_number,
        "salla_matched_orders": matched_number if invariant_ok else None,
        "salla_unmatched_orders": (
            max(total_number - matched_number, 0)
            if invariant_ok and total_number is not None and matched_number is not None
            else None
        ),
        "salla_sales_sar": round(salla_sales, 2) if salla_sales is not None else None,
        "snapchat_purchases": (
            int(snap_purchases) if _number(snap_purchases) is not None else None
        ),
        "snapchat_purchase_value_sar": (
            round(snap_value, 2) if snap_value is not None else None
        ),
        "snapchat_spend_sar": (
            round(snap_spend, 6) if snap_spend is not None else None
        ),
        "salla_roas": _ratio(salla_sales, snap_spend, eligible=reconciled),
        "snapchat_roas": _ratio(snap_value, snap_spend, eligible=snapchat_status == "complete"),
        "salla_cpa_sar": _ratio(snap_spend, matched_number, eligible=reconciled),
        "snapchat_cpa_sar": _ratio(snap_spend, snap_purchases, eligible=snapchat_status == "complete"),
        "salla_profitability": salla_profitability,
        # Ambiguous commercial aliases are intentionally unusable on this
        # report.  Consumers must name the source they display.
        "orders": None,
        "sales_sar": None,
        "roas": None,
        "cpa_sar": None,
    })
    for account in result.get("accounts") or []:
        account.update({
            key: totals.get(key)
            for key in (
                "salla_total_orders",
                "salla_matched_orders",
                "salla_unmatched_orders",
                "salla_sales_sar",
                "snapchat_purchases",
                "snapchat_purchase_value_sar",
                "snapchat_spend_sar",
                "salla_roas",
                "snapchat_roas",
                "salla_cpa_sar",
                "snapchat_cpa_sar",
                "salla_profitability",
            )
        })
        account.update({"orders": None, "sales_sar": None, "roas": None, "cpa_sar": None})

    for campaign in result.get("campaigns") or []:
        salla_orders, campaign_salla_sales = _salla_campaign_values(campaign)
        snap_orders, snap_sales, campaign_spend = _snapchat_campaign_values(campaign)
        profitability = campaign.get("salla_profitability") or campaign.get("profitability")
        cost_complete = (
            salla_orders == 0
            or (
                isinstance(profitability, dict)
                and profitability.get("product_cost_sar") is not None
            )
        )
        campaign.update({
            "salla_orders": salla_orders if salla_status != "failed" else None,
            "salla_sales_sar": (
                campaign_salla_sales if salla_status != "failed" else None
            ),
            "snapchat_purchases": snap_orders,
            "snapchat_purchase_value_sar": snap_sales,
            "snapchat_spend_sar": campaign_spend,
            "salla_roas": _ratio(campaign_salla_sales, campaign_spend, eligible=reconciled),
            "snapchat_roas": _ratio(snap_sales, campaign_spend, eligible=snapchat_status == "complete"),
            "salla_cpa_sar": _ratio(campaign_spend, salla_orders, eligible=reconciled),
            "snapchat_cpa_sar": _ratio(campaign_spend, snap_orders, eligible=snapchat_status == "complete"),
            "salla_profitability": profitability,
            "cost_status": (
                "not_applicable" if salla_orders == 0
                else "complete" if cost_complete
                else "cost_incomplete"
            ),
            "orders": None,
            "sales_sar": None,
            "roas": None,
            "cpa_sar": None,
        })

    for day in result.get("daily") or []:
        day_spend = _number(day.get("snapchat_spend_sar"))
        day_salla_sales = _number(day.get("salla_sales_sar"))
        day_snap_value = _number(day.get("snapchat_purchase_value_sar"))
        day.update({
            "salla_roas": _ratio(day_salla_sales, day_spend, eligible=reconciled),
            "snapchat_roas": _ratio(
                day_snap_value,
                day_spend,
                eligible=snapchat_status == "complete",
            ),
            "orders": None,
            "sales_sar": None,
            "roas": None,
            "cpa_sar": None,
        })

    result.update({
        "effective_timezone": manager.BUSINESS_TIMEZONE,
        "salla_attribution_timezone": manager.BUSINESS_TIMEZONE,
        "salla_status": salla_status,
        "snapchat_status": snapchat_status,
        "matching_status": matching_status,
        "reconciliation_status": "reconciled" if reconciled else "unreconciled",
        "reconciliation_reasons": reconciliation_reasons,
        "reconciliation": {
            "status": "reconciled" if reconciled else "unreconciled",
            "reasons": reconciliation_reasons,
            "salla_total_orders": total_number,
            "salla_matched_orders": matched_number if invariant_ok else None,
            "salla_campaign_matched_orders": (
                int(salla_campaign_matched)
                if salla_campaign_matched is not None else None
            ),
            "salla_matched_campaign_delta": salla_campaign_delta,
            "salla_matched_le_total": invariant_ok if counts_available else None,
            "snapchat_account_spend_sar": platform_account_spend,
            "snapchat_campaign_spend_sar": platform_campaign_spend,
            "snapchat_account_campaign_spend_delta_sar": platform_spend_delta,
        },
    })
    result.setdefault("coverage_reasons", {}).update({
        "salla": source.get("salla_source_reason") or coverage.get("matching_reason"),
        "snapchat": source.get("platform_source_reason") or (
            result.get("coverage_reasons") or {}
        ).get("snapchat"),
        "reconciliation": reconciliation_reasons or ["source_windows_and_statuses_coherent"],
    })
    result.setdefault("ai_readiness", {}).update({
        "report_ready": salla_status == "complete" and snapchat_status == "complete",
        "spend_ready": snapchat_status == "complete" and snap_spend is not None,
        "orders_ready": salla_status == "complete" and matched_number is not None,
        "sales_ready": salla_status == "complete" and salla_sales is not None,
        "ratios_ready": reconciled and (
            totals.get("salla_roas") is not None
            or totals.get("salla_cpa_sar") is not None
        ),
        "ai_analysis_ready": reconciled,
    })
    result.setdefault("policy", {}).update({
        "source_specific_financial_fields_required": True,
        "generic_commercial_aliases_disabled": True,
        "literal_utm_campaign_id_only": True,
        "financial_ratios_fail_closed": True,
        "financial_cache_status": "disabled_for_source_coherence",
        "provider_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    })
    return result


def install_snapchat_campaign_truth_contract() -> None:
    current_report = manager.build_account_timezone_campaign_report
    if getattr(current_report, "_mezan_campaign_truth_contract", False):
        return

    async def report(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return apply_campaign_truth_contract(
            dict(await current_report(*args, **kwargs) or {})
        )

    report._mezan_campaign_truth_contract = True  # type: ignore[attr-defined]
    report._mezan_campaign_truth_base = current_report  # type: ignore[attr-defined]
    manager.build_account_timezone_campaign_report = report


__all__ = [
    "STATUS_VALUES",
    "apply_campaign_truth_contract",
    "install_snapchat_campaign_truth_contract",
]
