"""Campaign AI source-of-truth policy for Snapchat and Meta.

The established scheduler, approval and execution machinery remains in
``campaign_ai_monitor_legacy``.  This module replaces only the recommendation
evidence/policy boundary:

* Snapchat Campaign / Ad Squad / Ad metrics use the same selected-account,
  account-timezone reports as the Snapchat page and are pinned to
  ``action_report_time=conversion`` (the page's recommended conversion-time
  mode).
* Salla attribution and Mezan V2 product costs are commercial context at
  campaign grain only.  Salla is never fabricated at Ad Squad or Ad grain.
* Old Mezan recommendations are never sent to OpenAI.  Only owner-approved
  changes that actually reached execution can become experiment memory.
* If OpenAI is unavailable, Mezan creates no replacement marketing decision.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import campaign_ai_monitor_legacy as _legacy
import campaign_ai_execution_quality_gate as _execution_quality
from integrations_control_center.snapchat_account_timezone_manager import (
    ACCOUNT_LOCAL_SOURCE_MODE,
    SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
    account_local_today,
    build_account_timezone_campaign_report,
)
from integrations_control_center.snapchat_ad_performance import (
    AD_SOURCE_MODE,
    build_account_timezone_ad_report,
)
from integrations_control_center.snapchat_adsquad_performance import (
    ADSQUAD_SOURCE_MODE,
    build_account_timezone_adsquad_report,
)
from integrations_control_center.snapchat_campaign_profitability import (
    build_campaign_profitability,
)
from integrations_control_center.snapchat_campaign_result_source_routes import (
    RESULT_SOURCE_PLATFORM,
    RESULT_SOURCE_SALLA,
)

logger = logging.getLogger(__name__)

SNAPCHAT_AI_ACTION_REPORT_TIME = "conversion"
SNAPCHAT_AI_PLATFORM_SOURCE = "snapchat_ads_manager_conversion_reporting"
SNAPCHAT_AI_SALLA_SOURCE = "salla_exact_campaign_match"
EXPERIMENT_MAX_ROWS = 30

# Public types/constants used by existing tests and callers.
RecommendationItem = _legacy.RecommendationItem
RecommendationOutput = _legacy.RecommendationOutput
RecommendationApprovalInput = _legacy.RecommendationApprovalInput
CampaignOpenAIError = _legacy.CampaignOpenAIError
DEFAULT_INITIAL_DELAY_SECONDS = _legacy.DEFAULT_INITIAL_DELAY_SECONDS
DEFAULT_INTERVAL_SECONDS = _legacy.DEFAULT_INTERVAL_SECONDS
MONITOR_TIMEOUT_SECONDS = _legacy.MONITOR_TIMEOUT_SECONDS
MAX_ENTITY_ROWS = _legacy.MAX_ENTITY_ROWS
MAX_AI_CANDIDATES = _legacy.MAX_AI_CANDIDATES
MAX_RECOMMENDATIONS = _legacy.MAX_RECOMMENDATIONS
RECOMMENDATION_COLLECTION = _legacy.RECOMMENDATION_COLLECTION
RUN_COLLECTION = _legacy.RUN_COLLECTION
EXECUTION_COLLECTION = _legacy.EXECUTION_COLLECTION
TARGET_CPA_SAR = _legacy.TARGET_CPA_SAR
TARGET_ROAS = _legacy.TARGET_ROAS

_ORIGINAL_CAMPAIGN_ENTITIES = _legacy._campaign_entities
_ORIGINAL_RECOMMENDATION_EXPLANATION = _legacy._recommendation_explanation


def _account_range(
    account: dict[str, Any],
    requested_start: date,
    requested_end: date,
) -> tuple[date, date]:
    """Use the same valid local calendar window as the Snapchat account page.

    Riyadh may already be one date ahead of America/Los_Angeles.  Passing the
    Riyadh ``to_date`` to an account-local Snapchat report can therefore make
    the AI branch fail as a future date although the Snapchat page is healthy.
    Preserve the requested span, but anchor it to that account's local today.
    """
    requested_days = max(1, (requested_end - requested_start).days + 1)
    timezone_name = str(account.get("timezone") or "").strip()
    if not timezone_name:
        return requested_start, requested_end
    local_end = account_local_today(timezone_name, now=_legacy._utcnow())
    return local_end - timedelta(days=requested_days - 1), local_end


def _compact_products(value: Any, limit: int = 8) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    compact: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        compact.append({key: row.get(key) for key in (
            "salla_product_id",
            "mezan_product_id",
            "name",
            "sku",
            "units",
            "orders",
            "sales_sar",
            "product_cost_sar",
            "allocated_ad_spend_sar",
            "contribution_profit_sar",
            "profit_margin_pct",
            "sales_share_pct",
            "cost_status",
        )})
    return compact


def _page_aligned_profitability(
    raw: dict[str, Any] | None,
    salla_results: dict[str, Any] | None,
) -> dict[str, Any]:
    """Expose exact profit only when it matches the Salla cohort on the page."""
    source = raw if isinstance(raw, dict) else {}
    salla = salla_results if isinstance(salla_results, dict) else {}
    page_orders = int(salla.get("orders") or 0)
    page_sales = float(salla.get("sales_sar") or 0.0)
    engine_orders = int(source.get("orders") or 0)
    engine_sales = float(source.get("sales_sar") or 0.0)
    aligned = (
        page_orders == engine_orders
        and abs(page_sales - engine_sales) <= 0.05
    )
    contribution = source.get("contribution_profit_sar")
    contribution_available = aligned and contribution is not None
    base = {
        # P0-2 finance contract: ad platforms are performance evidence only.
        # Mezan attribution + cost engines own commercial outcomes. This value
        # is contribution profit, never full store net profit.
        "source": "mezan_exact_campaign_attribution",
        "legacy_source": SNAPCHAT_AI_SALLA_SOURCE,
        "finance_authority": "mezan",
        "commercial_outcomes_authority": "mezan_attribution",
        "provider_finance_authority": False,
        "provider_sales_used_as_profit": False,
        "profit_metric": "contribution_profit",
        "contribution_profit_available": contribution_available,
        "net_profit_available": False,
        "net_profit_sar": None,
        "net_profit_unavailable_reason": (
            "campaign_level_full_cost_allocation_not_implemented"
        ),
        "mezan_attributed_orders": page_orders,
        "mezan_attributed_sales_sar": round(page_sales, 2),
        # Compatibility fields retained until the UI/API migration is complete.
        "page_salla_orders": page_orders,
        "page_salla_sales_sar": round(page_sales, 2),
        "engine_orders": engine_orders,
        "engine_sales_sar": round(engine_sales, 2),
        "verified_against_page_salla": aligned,
        "verified_against_mezan_attribution": aligned,
        "product_count": int(source.get("product_count") or 0),
        "products": _compact_products(source.get("products")),
        "profit_scope": source.get("profit_scope"),
        "allocation_method": source.get("allocation_method"),
    }
    if not aligned:
        return {
            **base,
            "product_cost_sar": None,
            "known_product_cost_sar": source.get("known_product_cost_sar"),
            "ad_spend_sar": source.get("ad_spend_sar"),
            "gross_profit_before_ads_sar": None,
            "contribution_profit_sar": None,
            "profit_margin_pct": None,
            "break_even_roas": None,
            "cost_status": "page_window_mismatch",
            "missing_cost_orders": source.get("missing_cost_orders"),
        }
    return {
        **base,
        **{key: source.get(key) for key in (
            "product_cost_sar",
            "known_product_cost_sar",
            "ad_spend_sar",
            "gross_profit_before_ads_sar",
            "contribution_profit_sar",
            "gross_margin_pct",
            "profit_margin_pct",
            "break_even_roas",
            "cost_status",
            "missing_cost_orders",
            "fallback_cost_orders",
            "no_products_orders",
        )},
    }


def _salla_campaign_results_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in report.get("campaigns") or []:
        campaign_id = _legacy._text(item.get("campaign_id"), limit=120)
        if not campaign_id:
            continue
        value = item.get("salla_results")
        if not isinstance(value, dict):
            value = {
                "orders": item.get("orders") or 0,
                "sales_sar": item.get("sales_sar") or 0.0,
            }
        output[campaign_id] = {
            "orders": int(value.get("orders") or 0),
            "sales_sar": round(float(value.get("sales_sar") or 0.0), 2),
        }
    return output


async def _account_salla_campaign_results(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    local_start: date,
    local_end: date,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read the same Salla result mode exposed by the Snapchat page."""
    report = await build_account_timezone_campaign_report(
        db=db,
        user_id=user_id,
        account_id=account_id,
        from_date=local_start.isoformat(),
        to_date=local_end.isoformat(),
        campaign_query=None,
        page=1,
        limit=100,
        action_report_time=SNAPCHAT_AI_ACTION_REPORT_TIME,
        active_campaigns_only=False,
        result_source=RESULT_SOURCE_SALLA,
    )
    return _salla_campaign_results_map(report), report


async def _snapchat_campaign_entities(
    db: Any,
    user_id: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    accounts = await _legacy._snapchat_accounts(db, user_id)
    for account in accounts:
        account_id = _legacy._text(account.get("ad_account_id"), limit=120)
        account_name = _legacy._text(
            account.get("display_name") or account.get("name"), limit=180
        )
        local_start, local_end = _account_range(account, start, end)
        platform_report = await build_account_timezone_campaign_report(
            db=db,
            user_id=user_id,
            account_id=account_id,
            from_date=local_start.isoformat(),
            to_date=local_end.isoformat(),
            campaign_query=None,
            page=1,
            limit=100,
            action_report_time=SNAPCHAT_AI_ACTION_REPORT_TIME,
            active_campaigns_only=False,
            result_source=RESULT_SOURCE_PLATFORM,
        )
        salla_by_id, _salla_report = await _account_salla_campaign_results(
            db,
            user_id,
            account_id=account_id,
            local_start=local_start,
            local_end=local_end,
        )
        try:
            profitability = await build_campaign_profitability(
                db,
                user_id,
                date_from=local_start.isoformat(),
                date_to=local_end.isoformat(),
            )
            profit_by_campaign = profitability.get("by_campaign") or {}
            profitability_coverage = profitability.get("coverage") or {}
        except Exception as exc:  # Provider evidence remains usable if profit fails.
            logger.warning(
                "Snapchat AI campaign profitability unavailable for account %s: %s",
                account_id,
                type(exc).__name__,
            )
            profit_by_campaign = {}
            profitability_coverage = {
                "available": False,
                "error": type(exc).__name__,
            }

        observed_days = int(
            ((platform_report.get("totals") or {}).get("observed_days") or 0)
        )
        report_source = platform_report.get("source") or {}
        report_pagination = platform_report.get("campaign_pagination") or {}
        report_complete = bool(
            (platform_report.get("ai_readiness") or {}).get("report_ready")
            and (platform_report.get("ai_readiness") or {}).get("campaign_details_ready")
            and observed_days >= (local_end - local_start).days + 1
            and report_source.get("row_limit_reached") is False
            and report_source.get("entity_limit_reached") is False
            and int(report_pagination.get("page") or 0) == 1
            and int(report_pagination.get("pages") or 0) == 1
            and int(report_pagination.get("total") or 0) > 0
        )
        for item in platform_report.get("campaigns") or []:
            campaign_id = _legacy._text(item.get("campaign_id"), limit=120)
            if not campaign_id:
                continue
            salla_results = salla_by_id.get(campaign_id) or {
                "orders": 0,
                "sales_sar": 0.0,
            }
            raw_profit = profit_by_campaign.get((account_id, campaign_id)) or {}
            profit = _page_aligned_profitability(raw_profit, salla_results)
            row = _legacy._entity(
                provider="snapchat",
                level="campaign",
                entity_id=campaign_id,
                entity_name=item.get("campaign_name"),
                parent_name=None,
                status=item.get("delivery_status") or item.get("status"),
                spend_sar=item.get("spend_sar"),
                revenue_sar=item.get("sales_sar"),
                purchases=item.get("orders"),
                impressions=item.get("impressions"),
                clicks=item.get("swipes"),
                observed_days=item.get("observed_days") or observed_days,
                data_complete=bool(
                    item.get("data_complete", report_complete) and report_complete
                ),
                account_id=account_id,
                account_name=item.get("account_name") or account_name,
                current_daily_budget_native=(item.get("budget") or {}).get("daily_native"),
                campaign_id=campaign_id,
                campaign_name=item.get("campaign_name"),
                campaign_status=item.get("status"),
                currency_native=(
                    item.get("display_currency")
                    or (item.get("budget") or {}).get("currency")
                    or account.get("currency")
                ),
                fx_rate_to_sar=(
                    item.get("exchange_rate_to_sar")
                    or (platform_report.get("accounts") or [{}])[0].get(
                        "exchange_rate_to_sar"
                    )
                ),
                fx_source="provider_currency_identity" if str(
                    item.get("display_currency")
                    or (item.get("budget") or {}).get("currency")
                    or account.get("currency")
                    or ""
                ).upper() == "SAR" else "account_cost_setting_required",
                provider_result_source=SNAPCHAT_AI_PLATFORM_SOURCE,
                action_report_time=SNAPCHAT_AI_ACTION_REPORT_TIME,
                result_source=RESULT_SOURCE_PLATFORM,
                source_date_from=local_start.isoformat(),
                source_date_to=local_end.isoformat(),
                source_observed_at=account.get("last_sync_at"),
                account_timezone=platform_report.get("account_timezone"),
                pagination_complete=report_complete,
                source_mode=ACCOUNT_LOCAL_SOURCE_MODE,
                source_fact_collection=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            )
            if not row:
                continue
            row.update({
                "salla_attribution_applied_to_entity_metrics": False,
                "mezan_campaign_results": {
                    "source": "mezan_attribution:unified_orders:exact_account_campaign_match",
                    **salla_results,
                },
                # Legacy compatibility only; do not treat this key name as the
                # finance authority. Mezan is authoritative for commercial truth.
                "salla_campaign_results": {
                    "source": "unified_orders:salla_exact_account_campaign_match",
                    **salla_results,
                },
                "finance_semantics": {
                    "finance_authority": "mezan",
                    "provider_role": "ad_delivery_performance_only",
                    "provider_sales_used_as_profit": False,
                    "campaign_profit_metric": "contribution_profit",
                    "campaign_net_profit_available": False,
                },
                "campaign_profitability": profit,
                "profitability_coverage": profitability_coverage,
            })
            rows.append(row)
    return rows


async def _campaign_entities(
    db: Any,
    user_id: str,
    provider: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    if provider == "snapchat":
        return await _snapchat_campaign_entities(db, user_id, start, end)
    return await _ORIGINAL_CAMPAIGN_ENTITIES(db, user_id, provider, start, end)


async def _snapchat_child_entities(
    db: Any,
    user_id: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Read child results only from Snapchat conversion-time platform facts.

    Salla is loaded only to verify the *parent campaign* commercial context.
    It is never copied into an Ad Squad or Ad's provider result fields.
    """
    rows: list[dict[str, Any]] = []
    accounts = await _legacy._snapchat_accounts(db, user_id)
    for account in accounts:
        account_id = _legacy._text(account.get("ad_account_id"), limit=120)
        account_name = _legacy._text(
            account.get("display_name") or account.get("name"), limit=180
        )
        local_start, local_end = _account_range(account, start, end)
        salla_by_id, _salla_report = await _account_salla_campaign_results(
            db,
            user_id,
            account_id=account_id,
            local_start=local_start,
            local_end=local_end,
        )
        try:
            profitability = await build_campaign_profitability(
                db,
                user_id,
                date_from=local_start.isoformat(),
                date_to=local_end.isoformat(),
            )
            profit_by_campaign = profitability.get("by_campaign") or {}
        except Exception as exc:
            logger.warning(
                "Snapchat AI parent profitability unavailable for account %s: %s",
                account_id,
                type(exc).__name__,
            )
            profit_by_campaign = {}

        group_report = await build_account_timezone_adsquad_report(
            db,
            user_id,
            account_id=account_id,
            from_date=local_start.isoformat(),
            to_date=local_end.isoformat(),
            query=None,
            page=1,
            limit=100,
            active_campaigns_only=False,
            sort_by="spend",
            action_report_time=SNAPCHAT_AI_ACTION_REPORT_TIME,
        )
        ad_report = await build_account_timezone_ad_report(
            db,
            user_id,
            account_id=account_id,
            from_date=local_start.isoformat(),
            to_date=local_end.isoformat(),
            query=None,
            page=1,
            limit=100,
            active_campaigns_only=False,
            sort_by="spend",
            action_report_time=SNAPCHAT_AI_ACTION_REPORT_TIME,
        )
        group_source = group_report.get("source") or {}
        group_pagination = group_report.get("pagination") or {}
        group_report_complete = bool(
            group_source.get("row_limit_reached") is False
            and group_source.get("entity_limit_reached") is False
            and int(group_pagination.get("page") or 0) == 1
            and int(group_pagination.get("pages") or 0) == 1
            and int(group_pagination.get("total") or 0) > 0
        )
        ad_source = ad_report.get("source") or {}
        ad_pagination = ad_report.get("pagination") or {}
        ad_report_complete = bool(
            ad_source.get("row_limit_reached") is False
            and ad_source.get("entity_limit_reached") is False
            and int(ad_pagination.get("page") or 0) == 1
            and int(ad_pagination.get("pages") or 0) == 1
            and int(ad_pagination.get("total") or 0) > 0
        )

        def parent_profit(campaign_id: str) -> dict[str, Any]:
            return _page_aligned_profitability(
                profit_by_campaign.get((account_id, campaign_id)) or {},
                salla_by_id.get(campaign_id) or {"orders": 0, "sales_sar": 0.0},
            )

        for item in group_report.get("ad_squads") or []:
            campaign_id = _legacy._text(item.get("campaign_id"), limit=120)
            row = _legacy._entity(
                provider="snapchat",
                level="ad_group",
                entity_id=item.get("ad_squad_id"),
                entity_name=item.get("ad_squad_name"),
                parent_name=item.get("campaign_name"),
                status=item.get("delivery_status") or item.get("status"),
                spend_sar=item.get("spend_sar"),
                revenue_sar=item.get("sales_sar"),
                purchases=item.get("orders"),
                impressions=item.get("impressions"),
                clicks=item.get("swipes"),
                observed_days=item.get("observed_days"),
                data_complete=bool(
                    item.get("data_complete") and group_report_complete
                ),
                account_id=account_id,
                account_name=account_name,
                parent_id=campaign_id,
                current_daily_budget_native=(item.get("budget") or {}).get("daily_native"),
                campaign_id=campaign_id,
                campaign_name=item.get("campaign_name"),
                campaign_status=item.get("campaign_status"),
                ad_group_id=item.get("ad_squad_id"),
                ad_group_name=item.get("ad_squad_name"),
                ad_group_status=item.get("status"),
                currency_native=(
                    item.get("display_currency") or account.get("currency")
                ),
                fx_rate_to_sar=item.get("exchange_rate_to_sar"),
                fx_source="provider_currency_identity" if str(
                    item.get("display_currency") or account.get("currency") or ""
                ).upper() == "SAR" else "account_cost_setting_required",
                provider_result_source=SNAPCHAT_AI_PLATFORM_SOURCE,
                action_report_time=SNAPCHAT_AI_ACTION_REPORT_TIME,
                result_source=RESULT_SOURCE_PLATFORM,
                source_date_from=local_start.isoformat(),
                source_date_to=local_end.isoformat(),
                source_observed_at=account.get("last_sync_at"),
                account_timezone=group_report.get("account_timezone"),
                pagination_complete=group_report_complete,
                source_mode=ADSQUAD_SOURCE_MODE,
                source_fact_collection=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            )
            if row:
                row.update({
                    "salla_attribution_applied_to_entity_metrics": False,
                    "parent_campaign_salla_results": salla_by_id.get(campaign_id),
                    "parent_campaign_profitability": parent_profit(campaign_id),
                    "commercial_context_scope": "parent_campaign_only",
                })
                rows.append(row)

        for item in ad_report.get("ads") or []:
            campaign_id = _legacy._text(item.get("campaign_id"), limit=120)
            row = _legacy._entity(
                provider="snapchat",
                level="ad",
                entity_id=item.get("ad_id"),
                entity_name=item.get("ad_name"),
                parent_name=item.get("ad_squad_name") or item.get("campaign_name"),
                status=(
                    item.get("delivery_status")
                    or item.get("delivery_state")
                    or item.get("status")
                ),
                spend_sar=item.get("spend_sar"),
                revenue_sar=item.get("sales_sar"),
                purchases=item.get("orders"),
                impressions=item.get("impressions"),
                clicks=item.get("swipes"),
                observed_days=item.get("observed_days"),
                data_complete=bool(
                    item.get("data_complete") and ad_report_complete
                ),
                account_id=account_id,
                account_name=account_name,
                parent_id=item.get("ad_squad_id"),
                configured_status=item.get("configured_status") or item.get("status"),
                effective_status=item.get("delivery_state"),
                status_updated_at=item.get("updated_at_provider"),
                campaign_id=campaign_id,
                campaign_name=item.get("campaign_name"),
                campaign_status=item.get("campaign_status"),
                ad_group_id=item.get("ad_squad_id"),
                ad_group_name=item.get("ad_squad_name"),
                ad_group_status=item.get("ad_squad_status"),
                currency_native=(
                    item.get("display_currency") or account.get("currency")
                ),
                fx_rate_to_sar=item.get("exchange_rate_to_sar"),
                fx_source="provider_currency_identity" if str(
                    item.get("display_currency") or account.get("currency") or ""
                ).upper() == "SAR" else "account_cost_setting_required",
                provider_result_source=SNAPCHAT_AI_PLATFORM_SOURCE,
                action_report_time=SNAPCHAT_AI_ACTION_REPORT_TIME,
                result_source=RESULT_SOURCE_PLATFORM,
                source_date_from=local_start.isoformat(),
                source_date_to=local_end.isoformat(),
                source_observed_at=account.get("last_sync_at"),
                account_timezone=ad_report.get("account_timezone"),
                pagination_complete=ad_report_complete,
                source_mode=AD_SOURCE_MODE,
                source_fact_collection=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            )
            if row:
                row.update({
                    "salla_attribution_applied_to_entity_metrics": False,
                    "parent_campaign_salla_results": salla_by_id.get(campaign_id),
                    "parent_campaign_profitability": parent_profit(campaign_id),
                    "commercial_context_scope": "parent_campaign_only",
                })
                rows.append(row)
    return rows


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    )


def _metric_snapshot(row: dict[str, Any] | None) -> dict[str, Any]:
    source = row or {}
    return {key: source.get(key) for key in (
        "spend_sar",
        "revenue_sar",
        "purchases",
        "roas",
        "cpa_sar",
        "observed_days",
        "spend_per_day_sar",
        "data_complete",
        "campaign_profitability",
        "parent_campaign_profitability",
    )}


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _experiment_outcomes_context(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    current: datetime,
) -> dict[str, Any]:
    """Only owner-approved changes that actually reached execution become memory."""
    cursor = db[EXECUTION_COLLECTION].find(
        {
            "user_id": user_id,
            "status": {"$in": ["completed", "verification_required"]},
            "writes_performed": True,
        },
        {
            "_id": 0,
            "execution_id": 1,
            "snapshot_id": 1,
            "recommendation_id": 1,
            "provider": 1,
            "action": 1,
            "status": 1,
            "approved_at": 1,
            "finished_at": 1,
            "result": 1,
        },
    ).sort("approved_at", -1).limit(EXPERIMENT_MAX_ROWS)
    executions = await cursor.to_list(length=EXPERIMENT_MAX_ROWS)
    candidate_by_key = {_candidate_key(row): row for row in candidates}
    experiments: list[dict[str, Any]] = []
    for execution in executions:
        snapshot_id = execution.get("snapshot_id")
        recommendation_id = execution.get("recommendation_id")
        snapshot = await db[RECOMMENDATION_COLLECTION].find_one(
            {"user_id": user_id, "snapshot_id": snapshot_id},
            {
                "_id": 0,
                "generated_at": 1,
                "execution_targets": 1,
                "recommendations.recommendation_id": 1,
                "recommendations.change_percent": 1,
            },
        ) or {}
        target = (snapshot.get("execution_targets") or {}).get(recommendation_id) or {}
        recommendation = next(
            (
                item for item in (snapshot.get("recommendations") or [])
                if item.get("recommendation_id") == recommendation_id
            ),
            {},
        )
        key = (
            str(target.get("provider") or execution.get("provider") or ""),
            str(target.get("entity_level") or ""),
            str(target.get("account_id") or ""),
            str(target.get("entity_id") or ""),
        )
        current_row = candidate_by_key.get(key)
        approved_at = _parse_datetime(execution.get("approved_at"))
        elapsed_hours = (
            round((current - approved_at).total_seconds() / 3600, 2)
            if approved_at else None
        )
        result = (
            execution.get("result")
            if isinstance(execution.get("result"), dict)
            else {}
        )
        experiments.append({
            "execution_id": execution.get("execution_id"),
            "provider": key[0],
            "entity_level": key[1],
            "account_id": key[2] or None,
            "entity_id": key[3] or None,
            "action": execution.get("action"),
            "change_percent": recommendation.get("change_percent"),
            "execution_status": execution.get("status"),
            "approved_at": execution.get("approved_at"),
            "finished_at": execution.get("finished_at"),
            "elapsed_hours": elapsed_hours,
            "market_context_at_execution": (
                _legacy._saudi_calendar_context(approved_at)
                if approved_at else None
            ),
            "baseline_snapshot": {
                "spend_sar": target.get("spend_sar"),
                "purchases": target.get("purchases"),
                "data_complete": target.get("data_complete"),
                "current_daily_budget_native": target.get("current_daily_budget_native"),
                "revenue_sar": target.get("revenue_sar"),
                "roas": target.get("roas"),
                "cpa_sar": target.get("cpa_sar"),
                "campaign_profitability": target.get("campaign_profitability"),
                "parent_campaign_profitability": target.get("parent_campaign_profitability"),
            },
            "current_followup_snapshot": _metric_snapshot(current_row),
            "followup_scope": (
                "rolling_window_observation_not_causal_proof"
                if current_row
                else "entity_not_in_current_active_candidates"
            ),
            "provider_execution": {
                "status": result.get("status"),
                "before": result.get("before"),
                "requested_change": result.get("requested_change"),
                "verification": result.get("verification"),
            },
        })
    return {
        "source": "owner_approved_executed_changes_only",
        "contains_unexecuted_recommendations": False,
        "interpretation": (
            "Experiments are observational evidence. Similar timing can raise or lower "
            "the probability of success, but does not prove causality or create a fixed rule."
        ),
        "experiments": experiments,
    }


def _recommendation_explanation(
    item: RecommendationItem,
    row: dict[str, Any],
) -> dict[str, Any]:
    brief = _ORIGINAL_RECOMMENDATION_EXPLANATION(item, row)
    direct = row.get("campaign_profitability") if item.entity_level == "campaign" else None
    parent = (
        row.get("parent_campaign_profitability")
        if item.entity_level != "campaign"
        else None
    )
    profit = (
        direct
        if isinstance(direct, dict)
        else parent
        if isinstance(parent, dict)
        else None
    )
    if not profit:
        return brief
    facts = list(brief.get("decision_facts") or [])
    if profit.get("verified_against_page_salla"):
        contribution = profit.get("contribution_profit_sar")
        contribution_text = (
            f"{float(contribution):.2f}"
            if contribution is not None
            else "غير محسوم"
        )
        facts.append(
            "مبيعات سلة للحملة %.2f ر.س وصافي مساهمة الحملة %s ر.س"
            % (float(profit.get("page_salla_sales_sar") or 0), contribution_text)
        )
    brief["decision_facts"] = facts
    financial = dict(brief.get("financial_impact") or {})
    financial.update({
        "provider_metrics_basis": SNAPCHAT_AI_PLATFORM_SOURCE,
        "action_report_time": SNAPCHAT_AI_ACTION_REPORT_TIME,
        "campaign_commercial_source": SNAPCHAT_AI_SALLA_SOURCE,
        "campaign_salla_sales_sar": profit.get("page_salla_sales_sar"),
        "campaign_product_cost_sar": profit.get("product_cost_sar"),
        "campaign_ad_spend_sar": profit.get("ad_spend_sar"),
        "campaign_contribution_profit_sar": profit.get("contribution_profit_sar"),
        "campaign_profit_margin_pct": profit.get("profit_margin_pct"),
        "campaign_cost_status": profit.get("cost_status"),
        "campaign_profit_verified_against_page_salla": profit.get(
            "verified_against_page_salla"
        ),
        "child_salla_attribution_claimed": False,
    })
    if (
        item.entity_level == "campaign"
        and profit.get("contribution_profit_sar") is not None
    ):
        financial["basis"] = (
            "snapchat_conversion_metrics_plus_salla_exact_campaign_profitability"
        )
        financial["period_exact_profit_available"] = True
    elif item.entity_level != "campaign":
        financial["basis"] = (
            "snapchat_child_conversion_metrics_with_parent_campaign_salla_profit_context"
        )
        financial["period_exact_profit_available"] = False
        financial["limitation"] = (
            "سلة لا تنسب الربح للمجموعة أو الإعلان؛ ربحية الحملة الأم سياق فقط، "
            "وقرار المجموعة/الإعلان يعتمد على نتائج Snapchat وقت التحويل."
        )
    brief["financial_impact"] = financial
    return brief


def _openai_unavailable_summary(error_code: str) -> str:
    labels = {
        "openai_api_key_missing": "مفتاح OpenAI غير مهيأ.",
        "openai_invalid_api_key": "مفتاح OpenAI غير صالح.",
        "openai_insufficient_quota": "رصيد أو حد إنفاق OpenAI API غير متاح.",
        "openai_rate_limited": "OpenAI مزدحم مؤقتًا.",
        "openai_model_access_denied": "مشروع OpenAI لا يملك صلاحية النموذج المحدد.",
    }
    detail = labels.get(error_code, "تعذر إكمال تحليل OpenAI في هذه الدورة.")
    return (
        f"{detail} لم ينشئ ميزان أي توصية بديلة؛ ستعاد المحاولة في الدورة التالية."
    )


async def _ask_openai(
    candidates: list[dict[str, Any]],
    *,
    now: datetime,
    campaign_history: dict[str, Any],
    prior_decisions: dict[str, Any],
    business_profit: dict[str, Any],
) -> RecommendationOutput:
    if _legacy.AsyncOpenAI is None:
        raise RuntimeError("openai_sdk_missing")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("openai_api_key_missing")
    next_check = _legacy._iso(now + timedelta(hours=5))
    safe_rows = [
        {key: row.get(key) for key in (
            "provider",
            "entity_level",
            "account_id",
            "account_name",
            "entity_id",
            "entity_name",
            "parent_name",
            "status",
            "configured_status",
            "effective_status",
            "status_updated_at",
            "active",
            "campaign_id",
            "campaign_name",
            "campaign_status",
            "ad_group_id",
            "ad_group_name",
            "ad_group_status",
            "campaign_ad_group_count",
            "campaign_ad_count",
            "entity_period_spend_sar",
            "entity_period_purchases",
            "ad_group_period_spend_sar",
            "ad_group_period_purchases",
            "campaign_period_spend_sar",
            "campaign_period_purchases",
            "spend_sar",
            "revenue_sar",
            "purchases",
            "impressions",
            "clicks",
            "roas",
            "cpa_sar",
            "observed_days",
            "spend_per_day_sar",
            "ctr_pct",
            "data_complete",
            "data_quality",
            "account_benchmark",
            "provider_result_source",
            "action_report_time",
            "result_source",
            "source_date_from",
            "source_date_to",
            "account_timezone",
            "salla_attribution_applied_to_entity_metrics",
            "salla_campaign_results",
            "campaign_profitability",
            "parent_campaign_salla_results",
            "parent_campaign_profitability",
            "commercial_context_scope",
        )}
        for row in candidates
    ]
    client = _legacy.AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=_legacy.OPENAI_TIMEOUT_SECONDS,
    )
    try:
        response = await client.responses.create(
            model=os.environ.get(
                "MEZAN_CAMPAIGN_AI_MODEL",
                os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"),
            ),
            instructions=(
                "أنت مدير أداء مستقل لمتجر أماسي. أنت صاحب الحكم التسويقي الوحيد؛ "
                "لا تستخدم قواعد قرار أو توصيات قديمة صادرة من كود ميزان. "
                "بالنسبة إلى Snapchat، اعتبر نتائج campaign/ad_group/ad المرسلة حقائق "
                "من نفس صفحة مدير سناب بوضع وقت التحويل conversion. لا تستبدلها بنتائج سلة. "
                "سلة وMezan V2 يقدمان فقط مبيعات وربحية الحملة المطابقة بدقة؛ لا تنسب مبيعات "
                "أو ربح سلة إلى المجموعة أو الإعلان لأن سلة لا توفر ذلك المستوى. عند تحليل "
                "مجموعة أو إعلان استخدم نتائج Snapchat الخاصة به، واجعل ربحية الحملة الأم سياقًا فقط. "
                "executed_experiments يحتوي فقط تغييرات وافق عليها المالك ووصلت فعلًا إلى التنفيذ. "
                "تعلم من نتائجها اللاحقة كدليل احتمالي لا كقانون سببي. نفس الحملة قد تنجح عند الراتب "
                "وتفشل في منتصف الشهر بسبب اختلاف السيولة؛ تقويم السعودية ويوم الشهر والراتب ونهاية "
                "الأسبوع عوامل سياقية واحتمالات فقط. لا تقل إن الراتب أو يومًا معينًا سبب للرفع إلا "
                "إذا دعمت التجارب أو البيانات التاريخية المشابهة ذلك. ميّز بين ضعف السوق وضعف الإعلان "
                "أو الاستهداف، وبين تذبذب قصير وفشل مستمر. CPA %.2f ر.س وROAS %.2f× مراجع اقتصادية "
                "وليست قواعد قرار. افحص الحملة والمجموعة والإعلان معًا قبل إيقاف الحملة الأم، ولا توقف "
                "عنصرًا رابحًا بسبب عنصر فرعي ضعيف. حلل كل حساب إعلاني مستقلًا واحتفظ حرفيًا بـ "
                "account_id وaccount_name. اشرح لماذا الآن، وما الاحتمال/الثقة، ومدة الانتظار، وما الذي "
                "يؤكد نجاح التجربة أو فشلها. لا تدّع تنفيذ شيء؛ التنفيذ بعد موافقة المالك فقط. "
                "recommendation_id بصيغة provider:level:account_id:id، واكتب بالعربية بأرقام إنجليزية، "
                "واجعل next_check_at مساويًا للقيمة المرسلة."
            ) % (TARGET_CPA_SAR, TARGET_ROAS),
            input=json.dumps({
                "next_check_at": next_check,
                "saudi_market_timing_context": _legacy._saudi_calendar_context(now),
                "active_entities_last_3_days": safe_rows,
                "campaign_history": campaign_history,
                "overall_store_profit_context": business_profit,
                "executed_experiments": prior_decisions,
                "source_contract": {
                    "snapchat_entity_metrics": SNAPCHAT_AI_PLATFORM_SOURCE,
                    "snapchat_action_report_time": SNAPCHAT_AI_ACTION_REPORT_TIME,
                    "campaign_salla_profit": SNAPCHAT_AI_SALLA_SOURCE,
                    "salla_child_attribution_allowed": False,
                    "mezan_previous_recommendations_allowed": False,
                    "mezan_fallback_decisions_allowed": False,
                },
            }, ensure_ascii=False, default=str),
            max_output_tokens=_legacy.OPENAI_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
            store=False,
            text={"format": {
                "type": "json_schema",
                "name": "campaign_monitor_recommendations",
                "strict": True,
                "schema": _legacy.AI_SCHEMA,
            }},
        )
        if getattr(response, "status", None) == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = _legacy._text(
                getattr(details, "reason", "unknown"), limit=80
            ) or "unknown"
            raise CampaignOpenAIError(f"openai_response_incomplete_{reason}")
        output = _legacy._normalize_openai_output(
            response.output_text,
            candidates,
            next_check_at=next_check,
        )
        return _legacy._govern_output(
            output,
            candidates,
            next_check_at=next_check,
        )
    finally:
        await client.close()


async def run_campaign_ai_monitor(
    db: Any,
    user_id: str,
    *,
    now: Callable[[], datetime] = _legacy._utcnow,
    refresh_meta: bool = True,
    business_context_loader: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    current = now().astimezone(timezone.utc)
    end = current.astimezone(_legacy.RIYADH_OFFSET).date()
    start = end - timedelta(days=2)
    run_id = str(_legacy.uuid.uuid4())
    started_at = _legacy._iso(current)
    await db[RUN_COLLECTION].insert_one({
        "run_id": run_id,
        "user_id": user_id,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "expires_at": current + timedelta(days=14),
    })
    errors: list[dict[str, str]] = []
    meta_refresh = None
    try:
        if refresh_meta:
            try:
                meta_refresh = await _legacy._refresh_meta_entities(
                    db,
                    user_id,
                    start=start,
                    end=end,
                    now=now,
                )
            except Exception as exc:
                errors.append({
                    "source": "meta_entity_refresh",
                    "code": _legacy._text(
                        getattr(exc, "code", type(exc).__name__), limit=100
                    ),
                })

        entities: list[dict[str, Any]] = []
        for provider in ("snapchat", "meta"):
            try:
                entities.extend(
                    await _campaign_entities(db, user_id, provider, start, end)
                )
            except Exception as exc:
                errors.append({
                    "source": f"{provider}_campaigns",
                    "code": _legacy._text(
                        getattr(exc, "code", type(exc).__name__), limit=100
                    ),
                })
        try:
            entities.extend(await _snapchat_child_entities(db, user_id, start, end))
        except Exception as exc:
            errors.append({
                "source": "snapchat_children",
                "code": _legacy._text(
                    getattr(exc, "code", type(exc).__name__), limit=100
                ),
            })
        try:
            entities.extend(
                await _legacy._meta_child_entities(db, user_id, start, end)
            )
        except Exception as exc:
            errors.append({
                "source": "meta_children",
                "code": _legacy._text(type(exc).__name__, limit=100),
            })

        entities = _legacy._bounded_account_sample(entities, MAX_ENTITY_ROWS)
        candidates = _legacy.deterministic_candidates(entities)
        fingerprint = _legacy._fingerprint(candidates)
        campaign_history = await _legacy._campaign_history_context(db, user_id, end)
        experiments = await _experiment_outcomes_context(
            db,
            user_id,
            candidates,
            current,
        )
        try:
            business_profit = await _legacy._business_profit_context(
                business_context_loader,
                user_id,
                end,
            )
        except Exception as exc:
            errors.append({
                "source": "mezan_business_profit",
                "code": _legacy._text(type(exc).__name__, limit=100),
            })
            business_profit = {
                "available": False,
                "reason": "dashboard_profit_context_failed",
            }

        if not candidates:
            recommendation_source = "none"
            result = RecommendationOutput(
                summary=(
                    "لا توجد كيانات نشطة ذات صرف يمكن لـ OpenAI تحليلها في الفترة الحالية."
                ),
                recommendations=[],
                limitations=[item["source"] for item in errors],
            )
        else:
            try:
                result = await _ask_openai(
                    candidates,
                    now=current,
                    campaign_history=campaign_history,
                    prior_decisions=experiments,
                    business_profit=business_profit,
                )
                recommendation_source = "openai"
            except Exception as exc:
                error_code = _legacy._openai_error_code(exc)
                logger.warning(
                    "Campaign AI unavailable for user %s (%s); no Mezan fallback decision will be created",
                    user_id,
                    error_code,
                )
                errors.append({
                    "source": "openai_recommendation",
                    "code": error_code,
                })
                result = RecommendationOutput(
                    summary=_openai_unavailable_summary(error_code),
                    recommendations=[],
                    limitations=[f"openai_recommendation:{error_code}"],
                )
                recommendation_source = "openai_unavailable"

        candidate_by_key = {_candidate_key(row): row for row in candidates}
        recommendation_rows: list[dict[str, Any]] = []
        execution_targets: dict[str, dict[str, Any]] = {}
        snapshot_id = str(_legacy.uuid.uuid4())
        snapshot_range = {"from": start.isoformat(), "to": end.isoformat()}
        # Provider refresh and the model call can take minutes.  Quality is
        # evaluated against a fresh capture clock, not the monitor's start
        # time, so facts observed during this same run are not misclassified as
        # future data.  The analytical date window itself remains unchanged.
        snapshot_captured_at = now().astimezone(timezone.utc)
        snapshot_generated_at = _legacy._iso(snapshot_captured_at)
        for item in result.recommendations:
            public_item = item.model_dump()
            target = candidate_by_key.get((
                item.provider,
                item.entity_level,
                str(item.account_id or ""),
                item.entity_id,
            )) or {}
            public_item.update(_recommendation_explanation(item, target))
            public_item["generated_at"] = snapshot_generated_at
            public_item["recommendation_source"] = recommendation_source
            public_item["decision_score"] = None
            public_item.update({key: target.get(key) for key in (
                "status",
                "configured_status",
                "effective_status",
                "status_updated_at",
                "campaign_id",
                "campaign_name",
                "campaign_status",
                "ad_group_id",
                "ad_group_name",
                "ad_group_status",
                "campaign_ad_group_count",
                "campaign_ad_count",
                "entity_period_spend_sar",
                "entity_period_purchases",
                "ad_group_period_spend_sar",
                "ad_group_period_purchases",
                "campaign_period_spend_sar",
                "campaign_period_purchases",
                "provider_result_source",
                "action_report_time",
                "result_source",
                "source_date_from",
                "source_date_to",
                "account_timezone",
                "salla_campaign_results",
                "campaign_profitability",
                "parent_campaign_salla_results",
                "parent_campaign_profitability",
                "commercial_context_scope",
            )})
            capability_executable = bool(
                item.action in {"pause", "reduce", "scale"}
                and target.get("account_id")
                and target.get("active")
                and (item.entity_level != "ad" or item.action == "pause")
            )
            execution_target = {
                key: target.get(key) for key in (
                    "provider",
                    "entity_level",
                    "entity_id",
                    "account_id",
                    "parent_id",
                    "status",
                    "configured_status",
                    "effective_status",
                    "active",
                    "current_daily_budget_native",
                    "spend_sar",
                    "revenue_sar",
                    "purchases",
                    "impressions",
                    "clicks",
                    "roas",
                    "cpa_sar",
                    "observed_days",
                    "data_complete",
                    "currency_native",
                    "fx_rate_to_sar",
                    "fx_source",
                    "provider_result_source",
                    "action_report_time",
                    "result_source",
                    "source_date_from",
                    "source_date_to",
                    "source_observed_at",
                    "account_timezone",
                    "pagination_complete",
                    "source_mode",
                    "source_fact_collection",
                    "campaign_profitability",
                    "parent_campaign_profitability",
                )
            }
            quality_evidence: dict[str, Any] | None = None
            quality_decision = {
                "allowed": False,
                "status": "blocked",
                "blockers": ["execution_capability_unavailable"],
            }
            if capability_executable:
                try:
                    quality_evidence = (
                        await _execution_quality.collect_execution_quality_evidence(
                            db,
                            user_id,
                            execution_target,
                            snapshot_generated_at=snapshot_generated_at,
                            snapshot_range=snapshot_range,
                            now=now,
                            source_context={
                                "monitor_errors": errors,
                                "meta_refresh": meta_refresh,
                            },
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Campaign AI execution-quality evidence failed closed for %s/%s: %s",
                        item.provider,
                        item.entity_id,
                        type(exc).__name__,
                    )
                    quality_evidence = None
                quality_decision = _execution_quality.evaluate_execution_quality(
                    quality_evidence,
                    action=item.action,
                )
            executable = bool(capability_executable and quality_decision["allowed"])
            public_item["approval_available"] = executable
            public_item["execution_status"] = (
                "awaiting_approval" if executable else "recommendation_only"
            )
            public_item["execution_quality_status"] = quality_decision["status"]
            public_item["execution_quality_blockers"] = quality_decision["blockers"]
            recommendation_rows.append(public_item)
            if capability_executable:
                execution_target["execution_quality"] = quality_evidence
                execution_targets[item.recommendation_id] = execution_target

        document = {
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "user_id": user_id,
            "generated_at": snapshot_generated_at,
            "next_run_at": _legacy._iso(snapshot_captured_at + timedelta(hours=5)),
            "range": snapshot_range,
            "summary": result.summary,
            "recommendations": recommendation_rows,
            "execution_targets": execution_targets,
            "limitations": list(dict.fromkeys([
                *result.limitations,
                *[item["source"] for item in errors],
            ])),
            "fingerprint": fingerprint,
            "entities_scanned": len(entities),
            "candidates_scanned": len(candidates),
            "providers": ["snapchat", "meta"],
            "mode": "recommend_then_approve",
            "decision_authority": recommendation_source,
            "recommendation_source": recommendation_source,
            "decision_interval_hours": 5,
            "context_windows_days": [3, 7, 30],
            "writes_performed": False,
            "meta_refresh": meta_refresh,
            "business_profit_context_available": bool(
                business_profit.get("available")
            ),
            "experiment_context_count": len(experiments.get("experiments") or []),
            "source_contract": {
                "snapchat_entity_metrics": SNAPCHAT_AI_PLATFORM_SOURCE,
                "snapchat_action_report_time": SNAPCHAT_AI_ACTION_REPORT_TIME,
                "campaign_salla_profit": SNAPCHAT_AI_SALLA_SOURCE,
                "salla_child_attribution_allowed": False,
                "previous_mezan_recommendations_used": False,
                "mezan_fallback_decisions_enabled": False,
                "experiment_memory": "owner_approved_executed_changes_only",
            },
        }
        await db[RECOMMENDATION_COLLECTION].insert_one(document)
        await db[RUN_COLLECTION].update_one(
            {"run_id": run_id, "user_id": user_id},
            {"$set": {
                "status": "complete",
                "finished_at": _legacy._iso(),
                "snapshot_id": document["snapshot_id"],
                "recommendations": len(document["recommendations"]),
                "recommendation_source": recommendation_source,
            }},
        )
        return {
            key: value
            for key, value in document.items()
            if key != "user_id"
        }
    except Exception as exc:
        logger.exception("Campaign AI monitor failed for user %s", user_id)
        await db[RUN_COLLECTION].update_one(
            {"run_id": run_id, "user_id": user_id},
            {"$set": {
                "status": "failed",
                "finished_at": _legacy._iso(),
                "error_code": _legacy._text(
                    getattr(exc, "code", type(exc).__name__), limit=100
                ),
            }},
        )
        raise


# Legacy routes/scheduler resolve their own module globals.  Patch those globals
# so their established lifecycle executes this policy without duplicating route
# or worker code.
_legacy._campaign_entities = _campaign_entities
_legacy._snapchat_child_entities = _snapchat_child_entities
_legacy._prior_ai_context = lambda db, user_id: _experiment_outcomes_context(
    db,
    user_id,
    [],
    _legacy._utcnow(),
)
_legacy._ask_openai = _ask_openai
_legacy._recommendation_explanation = _recommendation_explanation
_legacy.run_campaign_ai_monitor = run_campaign_ai_monitor

# Preserve the established public API.  Unknown/private attributes delegate to
# the legacy implementation so existing imports and regression tests stay valid.
__all__ = list(getattr(_legacy, "__all__", []))


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)
