"""Decision Intelligence adapter for the Unified Marketing Data Contract.

The adapter is deliberately read-only and provider-neutral at its boundary.
It translates the stable marketing contract into the established Campaign AI
candidate shape; recommendation creation, approval and provider writes remain
owned by their existing guarded modules.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import campaign_ai_monitor_legacy as _legacy
from unified_marketing.contract import CONTRACT_VERSION
from unified_marketing.gateway import (
    load_unified_marketing_account_identity,
    load_unified_marketing_entity_report,
)

UNIFIED_AI_PROVIDER = "snapchat_ads"
UNIFIED_AI_SOURCE = f"{CONTRACT_VERSION}:snapchat-v2"
UNIFIED_AI_SOURCE_MODE = "unified_marketing_entity_report"
SNAPCHAT_V2_EXACT_TOTAL_COLLECTION = "mezan_snapchat_daily_total_facts_v2"


def _amount(value: Any) -> float | None:
    if not isinstance(value, dict) or value.get("amount") is None:
        return None
    try:
        parsed = float(value.get("amount"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _local_range(
    timezone_name: str,
    requested_start: date,
    requested_end: date,
    period_end_offset_days: int = 0,
) -> tuple[date, date]:
    days = max(1, (requested_end - requested_start).days + 1)
    now = _legacy._utcnow()
    try:
        local_end = now.astimezone(ZoneInfo(timezone_name)).date() - timedelta(
            days=max(0, int(period_end_offset_days or 0))
        )
    except Exception:  # noqa: BLE001 - invalid timezone must fail safely below
        return requested_start, requested_end
    return local_end - timedelta(days=days - 1), local_end


def _legacy_products(value: Any) -> list[dict[str, Any]]:
    products = value if isinstance(value, list) else []
    output: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        output.append({
            "identity": product.get("identity"),
            "salla_product_id": product.get("salla_product_id"),
            "mezan_product_id": product.get("mezan_product_id"),
            "name": product.get("name"),
            "sku": product.get("sku"),
            "units": product.get("units"),
            "orders": product.get("orders"),
            "sales_sar": _amount(product.get("sales")),
            "product_cost_sar": _amount(product.get("product_cost")),
            "allocated_ad_spend_sar": _amount(
                product.get("allocated_ad_spend")
            ),
            "contribution_profit_sar": _amount(
                product.get("contribution_profit")
            ),
            "profit_margin_pct": product.get("profit_margin_pct"),
            "cost_status": product.get("cost_status"),
        })
    return output


def _profitability(value: Any, commerce: Any) -> dict[str, Any]:
    profit = value if isinstance(value, dict) else {}
    salla = commerce if isinstance(commerce, dict) else {}
    orders = int(salla.get("orders") or 0)
    sales = _amount(salla.get("revenue")) or 0.0
    status = str(profit.get("status") or "unavailable")
    contribution = _amount(profit.get("contribution_profit"))
    verified = status in {"complete", "partial"} and int(
        profit.get("orders") or 0
    ) == orders and abs((_amount(profit.get("sales")) or 0.0) - sales) <= 0.05
    return {
        "source": "unified_marketing_contract:mezan_exact_campaign_attribution",
        "legacy_source": "salla_exact_campaign_match",
        "finance_authority": "mezan",
        "commercial_outcomes_authority": "mezan_attribution",
        "provider_finance_authority": False,
        "provider_sales_used_as_profit": False,
        "profit_metric": "contribution_profit",
        "contribution_profit_available": verified and contribution is not None,
        "net_profit_available": False,
        "net_profit_sar": None,
        "net_profit_unavailable_reason": (
            "campaign_level_full_cost_allocation_not_implemented"
        ),
        "mezan_attributed_orders": orders,
        "mezan_attributed_sales_sar": round(sales, 2),
        "page_salla_orders": orders,
        "page_salla_sales_sar": round(sales, 2),
        "engine_orders": int(profit.get("orders") or 0),
        "engine_sales_sar": round(_amount(profit.get("sales")) or 0.0, 2),
        "verified_against_page_salla": verified,
        "verified_against_mezan_attribution": verified,
        "product_count": int(profit.get("product_count") or 0),
        "products": _legacy_products(profit.get("products")),
        "product_cost_sar": _amount(profit.get("product_cost")) if verified else None,
        "known_product_cost_sar": _amount(profit.get("known_product_cost")),
        "ad_spend_sar": _amount(profit.get("ad_spend")),
        "gross_profit_before_ads_sar": None,
        "contribution_profit_sar": contribution if verified else None,
        "profit_margin_pct": profit.get("profit_margin_pct") if verified else None,
        "break_even_roas": None,
        "cost_status": (
            profit.get("cost_status") if verified else "contract_window_mismatch"
        ),
        "missing_cost_orders": profit.get("missing_cost_orders"),
        "profit_scope": profit.get("profit_scope"),
        "allocation_method": profit.get("allocation_method"),
    }


def _platform_revenue_sar(row: dict[str, Any]) -> float | None:
    native_revenue = _amount((row.get("platform_outcomes") or {}).get("revenue"))
    delivery = row.get("delivery") or {}
    native_spend = _amount(delivery.get("spend"))
    spend_sar = _amount(delivery.get("spend_sar"))
    if native_revenue is None:
        return None
    if native_spend and spend_sar is not None:
        return round(native_revenue * (spend_sar / native_spend), 2)
    currency = str(((row.get("account") or {}).get("currency") or "")).upper()
    return round(native_revenue, 2) if currency == "SAR" else None


def _row_complete(row: dict[str, Any]) -> bool:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    delivery = row.get("delivery") if isinstance(row.get("delivery"), dict) else {}
    lineage = row.get("lineage") if isinstance(row.get("lineage"), dict) else {}
    exact_provider_total = (
        lineage.get("source_collection") == SNAPCHAT_V2_EXACT_TOTAL_COLLECTION
    )
    return bool(
        int(quality.get("source_fact_count") or 0) > 0
        and _amount(delivery.get("spend_sar")) is not None
        and (
            (
                quality.get("sync_status") == "complete"
                and quality.get("coverage_status") == "complete"
            )
            or exact_provider_total
        )
    )


def _to_candidate(
    row: dict[str, Any],
    *,
    account: dict[str, Any],
    local_start: date,
    local_end: date,
    management: dict[str, Any],
    campaign_names: dict[str, str],
    ad_group_names: dict[str, str],
) -> dict[str, Any] | None:
    entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
    delivery = row.get("delivery") if isinstance(row.get("delivery"), dict) else {}
    platform = (
        row.get("platform_outcomes")
        if isinstance(row.get("platform_outcomes"), dict)
        else {}
    )
    identity = str(entity.get("id") or "")
    level = str(entity.get("level") or "")
    campaign_id = str(entity.get("campaign_id") or "") or (
        identity if level == "campaign" else ""
    )
    ad_group_id = str(entity.get("ad_group_id") or "") or (
        identity if level == "ad_group" else ""
    )
    parent_id = campaign_id if level == "ad_group" else ad_group_id if level == "ad" else None
    parent_name = (
        campaign_names.get(campaign_id)
        if level == "ad_group"
        else ad_group_names.get(ad_group_id) or campaign_names.get(campaign_id)
        if level == "ad"
        else None
    )
    status = management.get("status") or entity.get("status")
    candidate = _legacy._entity(
        provider="snapchat",
        level=level,
        entity_id=identity,
        entity_name=entity.get("name"),
        parent_name=parent_name,
        status=status,
        spend_sar=_amount(delivery.get("spend_sar")),
        revenue_sar=_platform_revenue_sar(row),
        purchases=platform.get("conversions"),
        impressions=delivery.get("impressions"),
        clicks=delivery.get("clicks"),
        observed_days=(local_end - local_start).days + 1,
        data_complete=_row_complete(row),
        account_id=account.get("id"),
        account_name=account.get("name"),
        parent_id=parent_id,
        current_daily_budget_native=management.get("daily_budget_native"),
        configured_status=status,
        effective_status=status,
        status_updated_at=management.get("updated_at"),
        campaign_id=campaign_id,
        campaign_name=campaign_names.get(campaign_id),
        campaign_status=(
            status if level == "campaign" else None
        ),
        ad_group_id=ad_group_id or None,
        ad_group_name=ad_group_names.get(ad_group_id),
        ad_group_status=(status if level == "ad_group" else None),
        currency_native=account.get("currency"),
        fx_rate_to_sar=(
            round(
                (_amount(delivery.get("spend_sar")) or 0)
                / (_amount(delivery.get("spend")) or 1),
                6,
            )
            if _amount(delivery.get("spend"))
            and _amount(delivery.get("spend_sar")) is not None
            else None
        ),
        fx_source="unified_marketing_cost_contract",
        provider_result_source=UNIFIED_AI_SOURCE,
        action_report_time="conversion",
        result_source="platform",
        source_date_from=local_start.isoformat(),
        source_date_to=local_end.isoformat(),
        source_observed_at=account.get("last_sync_at"),
        account_timezone=account.get("timezone"),
        pagination_complete=True,
        source_mode=UNIFIED_AI_SOURCE_MODE,
        source_fact_collection=(row.get("lineage") or {}).get("source_collection"),
    )
    if candidate is None:
        return None
    candidate["unified_contract_version"] = CONTRACT_VERSION
    candidate["decision_eligibility"] = {
        "eligible": True,
        "reason": "ai_unified_v2_cutover_deployed",
    }
    return candidate


async def load_snapchat_unified_ai_entities(
    db: Any,
    user_id: str,
    start: date,
    end: date,
    period_end_offset_days: int = 0,
) -> dict[str, Any]:
    account = await load_unified_marketing_account_identity(
        db,
        str(user_id),
        provider=UNIFIED_AI_PROVIDER,
    )
    if not account:
        return {
            "campaigns": [],
            "children": [],
            "account": None,
            "period": None,
            "decision_eligibility": {
                "eligible": False,
                "reason": "selected_account_missing",
            },
        }
    local_start, local_end = _local_range(
        str(account.get("timezone") or ""),
        start,
        end,
        period_end_offset_days,
    )
    reports = {
        level: await load_unified_marketing_entity_report(
            db,
            str(user_id),
            provider=UNIFIED_AI_PROVIDER,
            entity_level=level,
            date_from=local_start,
            date_to=local_end,
            timezone_name=str(account.get("timezone") or ""),
            include_stale=True,
        )
        for level in ("campaign", "ad_group", "ad")
    }
    campaign_names = {
        str((row.get("entity") or {}).get("id") or ""): str(
            (row.get("entity") or {}).get("name") or ""
        )
        for row in reports["campaign"].get("rows") or []
    }
    ad_group_names = {
        str((row.get("entity") or {}).get("id") or ""): str(
            (row.get("entity") or {}).get("name") or ""
        )
        for row in reports["ad_group"].get("rows") or []
    }
    converted: dict[str, list[dict[str, Any]]] = {
        "campaign": [],
        "ad_group": [],
        "ad": [],
    }
    for level, report in reports.items():
        management = report.get("management_context") or {}
        for row in report.get("rows") or []:
            entity_id = str((row.get("entity") or {}).get("id") or "")
            candidate = _to_candidate(
                row,
                account=account,
                local_start=local_start,
                local_end=local_end,
                management=(management.get(entity_id) or {}),
                campaign_names=campaign_names,
                ad_group_names=ad_group_names,
            )
            if candidate is None:
                continue
            if level == "campaign":
                commerce = row.get("commerce_outcomes") or {}
                salla_results = {
                    "orders": int(commerce.get("orders") or 0),
                    "sales_sar": round(_amount(commerce.get("revenue")) or 0.0, 2),
                }
                candidate.update({
                    "salla_attribution_applied_to_entity_metrics": False,
                    "mezan_campaign_results": {
                        "source": "unified_marketing_contract:exact_account_campaign_match",
                        **salla_results,
                    },
                    "salla_campaign_results": {
                        "source": "unified_marketing_contract:exact_account_campaign_match",
                        **salla_results,
                    },
                    "campaign_profitability": _profitability(
                        row.get("commerce_profitability"), commerce
                    ),
                    "profitability_coverage": {
                        "available": str(
                            (row.get("commerce_profitability") or {}).get("status")
                        ) in {"complete", "partial"},
                        "source": "unified_marketing_contract",
                    },
                    "finance_semantics": {
                        "finance_authority": "mezan",
                        "provider_role": "ad_delivery_performance_only",
                        "provider_sales_used_as_profit": False,
                        "campaign_profit_metric": "contribution_profit",
                        "campaign_net_profit_available": False,
                    },
                    "abandoned_cart_outcomes": row.get(
                        "abandoned_cart_outcomes"
                    ),
                })
            converted[level].append(candidate)

    campaign_context = {
        str(row.get("entity_id") or ""): row for row in converted["campaign"]
    }
    for level in ("ad_group", "ad"):
        for candidate in converted[level]:
            parent = campaign_context.get(str(candidate.get("campaign_id") or "")) or {}
            candidate.update({
                "salla_attribution_applied_to_entity_metrics": False,
                "parent_campaign_salla_results": parent.get(
                    "mezan_campaign_results"
                ),
                "parent_campaign_profitability": parent.get(
                    "campaign_profitability"
                ),
                "commercial_context_scope": "parent_campaign_only",
            })
    return {
        "campaigns": converted["campaign"],
        "children": converted["ad_group"] + converted["ad"],
        "levels": converted,
        "account": account,
        "period": {
            "date_from": local_start.isoformat(),
            "date_to": local_end.isoformat(),
            "timezone": account.get("timezone"),
            "action_report_time": "conversion",
        },
        "decision_eligibility": {
            "eligible": True,
            "reason": "ai_unified_v2_cutover_deployed",
        },
    }


__all__ = [
    "UNIFIED_AI_SOURCE",
    "load_snapchat_unified_ai_entities",
]
