"""Meta V2 adapter for the existing provider-neutral marketing contract.

The adapter is pure: it accepts persisted native projection rows and never
opens a provider client or writes storage.
"""

from __future__ import annotations

from typing import Any, Literal

from unified_marketing.contract import (
    MoneyValue,
    UnifiedAbandonedCartOutcomes,
    UnifiedAccount,
    UnifiedCommerceOrder,
    UnifiedCommerceOrderSummary,
    UnifiedCommerceOutcomes,
    UnifiedCommerceProfitability,
    UnifiedDeliveryMetrics,
    UnifiedEntityIdentity,
    UnifiedLineage,
    UnifiedMarketingReport,
    UnifiedMarketingRow,
    UnifiedPeriod,
    UnifiedPlatformOutcomes,
    UnifiedProductProfitability,
    UnifiedQuality,
)

MetaEntityType = Literal["ad_account", "campaign", "adset", "ad"]

LEVELS = {
    "ad_account": "account",
    "campaign": "campaign",
    "adset": "ad_group",
    "ad": "ad",
}

PROVIDER_METRIC_MAPPING = {
    "delivery.clicks": "clicks",
    "delivery.impressions": "impressions",
    "delivery.spend": "spend_native",
    "platform_outcomes.conversions": "purchases",
    "platform_outcomes.revenue": "purchase_value_native",
    "commerce_outcomes.orders": "salla_results.orders",
    "commerce_outcomes.revenue": "salla_results.sales_sar",
    "commerce_profitability": "salla_results.profitability",
    "entity.status": "status/effective_status",
    "entity.budget_bid_settings": "mezan_meta_entity_snapshots_v2",
}

SETTINGS_FIELDS = {
    "ad_account": {"account_status"},
    "campaign": {"status", "effective_status", "daily_budget", "lifetime_budget"},
    "adset": {
        "status",
        "effective_status",
        "daily_budget",
        "lifetime_budget",
        "bid_amount",
        "bid_strategy",
        "billing_event",
        "optimization_goal",
    },
    "ad": {"status", "effective_status"},
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _integer(value: Any) -> int:
    return max(0, int(_number(value) or 0))


def _money(amount: Any, currency: str) -> MoneyValue:
    return MoneyValue(amount=_number(amount), currency=currency)


def _account(value: dict[str, Any]) -> UnifiedAccount:
    account_id = str(
        value.get("ad_account_id")
        or value.get("external_account_id")
        or value.get("id")
        or ""
    ).strip()
    return UnifiedAccount(
        id=account_id,
        name=str(value.get("display_name") or value.get("name") or account_id),
        currency=str(
            value.get("currency") or value.get("currency_native") or ""
        ).upper(),
        timezone=str(value.get("timezone") or value.get("account_timezone") or ""),
    )


def _period(value: dict[str, Any]) -> UnifiedPeriod:
    return UnifiedPeriod(
        date_from=str(value["date_from"]),
        date_to=str(value["date_to"]),
        timezone=str(value["timezone"]),
        action_report_time="conversion",
    )


def _identity(
    row: dict[str, Any],
    *,
    entity_type: MetaEntityType,
    account: UnifiedAccount,
) -> UnifiedEntityIdentity:
    if entity_type == "ad_account":
        return UnifiedEntityIdentity(
            level="account",
            provider_level="ad_account",
            id=account.id,
            name=account.name,
            status=str(row.get("status") or row.get("account_status") or "") or None,
            active=row.get("active") if isinstance(row.get("active"), bool) else None,
        )
    external_id = str(
        row.get("external_id") or row.get(f"{entity_type}_id") or ""
    ).strip()
    campaign_id = external_id if entity_type == "campaign" else row.get("campaign_id")
    ad_group_id = (
        external_id
        if entity_type == "adset"
        else row.get("ad_group_id") or row.get("adset_id")
    )
    return UnifiedEntityIdentity(
        level=LEVELS[entity_type],
        provider_level=entity_type,
        id=external_id,
        name=str(row.get("name") or row.get(f"{entity_type}_name") or external_id),
        status=str(row.get("effective_status") or row.get("status") or "") or None,
        active=row.get("active") if isinstance(row.get("active"), bool) else None,
        campaign_id=str(campaign_id or "") or None,
        ad_group_id=str(ad_group_id or "") or None,
    )


def _commerce(
    row: dict[str, Any], entity_type: MetaEntityType
) -> UnifiedCommerceOutcomes:
    if entity_type not in {"ad_account", "campaign"}:
        return UnifiedCommerceOutcomes(
            status="unavailable",
            revenue=_money(None, "SAR"),
            attribution_scope="campaign_only",
        )
    value = (
        row.get("salla_results") if isinstance(row.get("salla_results"), dict) else {}
    )
    if value.get("status") != "complete":
        return UnifiedCommerceOutcomes(
            status="partial",
            revenue=_money(None, "SAR"),
            attribution_scope=str(
                value.get("attribution_scope") or "exact_campaign_match"
            ),
        )
    return UnifiedCommerceOutcomes(
        status="complete",
        orders=_integer(value.get("orders")),
        revenue=_money(value.get("sales_sar"), "SAR"),
        roas=_number(value.get("roas")),
        attribution_scope=str(
            value.get("attribution_scope")
            or (
                "account_sum_of_exact_campaign_matches"
                if entity_type == "ad_account"
                else "exact_campaign_match"
            )
        ),
    )


def _profitability(row: dict[str, Any]) -> UnifiedCommerceProfitability:
    value = (row.get("salla_results") or {}).get("profitability")
    if not isinstance(value, dict) or value.get("status") != "complete":
        return UnifiedCommerceProfitability(
            status="unavailable",
            sales=_money(None, "SAR"),
            product_cost=_money(None, "SAR"),
            known_product_cost=_money(None, "SAR"),
            ad_spend=_money(row.get("spend_sar"), "SAR"),
            contribution_profit=_money(None, "SAR"),
            cost_status="unavailable",
            profit_scope="campaign_exact_match_only",
        )
    products = [
        UnifiedProductProfitability(
            identity=str(item.get("identity") or "unknown"),
            salla_product_id=item.get("salla_product_id") or None,
            mezan_product_id=item.get("mezan_product_id") or None,
            name=str(item.get("name") or "منتج بدون اسم"),
            sku=item.get("sku") or None,
            image_url=item.get("image_url") or None,
            units=max(0.0, _number(item.get("units")) or 0.0),
            orders=_integer(item.get("orders")),
            sales=_money(item.get("sales_sar"), "SAR"),
            product_cost=_money(item.get("product_cost_sar"), "SAR"),
            allocated_ad_spend=_money(item.get("allocated_ad_spend_sar"), "SAR"),
            contribution_profit=_money(item.get("contribution_profit_sar"), "SAR"),
            profit_margin_pct=_number(item.get("profit_margin_pct")),
            cost_status=str(item.get("cost_status") or "unavailable"),
            cost_sources=list(item.get("cost_sources") or []),
        )
        for item in list(value.get("products") or [])
        if isinstance(item, dict)
    ]
    missing = _integer(value.get("missing_cost_orders"))
    return UnifiedCommerceProfitability(
        status="complete" if missing == 0 else "partial",
        orders=_integer(value.get("orders")),
        sales=_money(value.get("sales_sar"), "SAR"),
        product_cost=_money(value.get("product_cost_sar"), "SAR"),
        known_product_cost=_money(value.get("known_product_cost_sar"), "SAR"),
        ad_spend=_money(value.get("ad_spend_sar"), "SAR"),
        contribution_profit=_money(value.get("contribution_profit_sar"), "SAR"),
        profit_margin_pct=_number(value.get("profit_margin_pct")),
        cost_status=str(value.get("cost_status") or "complete"),
        missing_cost_orders=missing,
        product_count=_integer(value.get("product_count")),
        products=products,
        profit_scope=str(value.get("profit_scope") or "campaign_exact_match_only"),
        allocation_method=value.get("allocation_method"),
    )


def _order_summary(value: dict[str, Any]) -> UnifiedCommerceOrderSummary:
    available = value.get("coverage_status") == "complete"
    return UnifiedCommerceOrderSummary(
        status="complete" if available else "partial" if value else "unavailable",
        source="salla",
        created_orders=(
            _integer(value.get("total_salla_created_orders")) if available else None
        ),
        financial_orders=(
            _integer(value.get("total_financial_orders")) if available else None
        ),
        financial_revenue=_money(
            value.get("total_financial_sales_sar") if available else None, "SAR"
        ),
        matched_orders=(
            _integer(value.get("campaign_matched_orders")) if available else None
        ),
        matched_financial_orders=(
            _integer(value.get("campaign_matched_financial_orders"))
            if available
            else None
        ),
        matched_financial_revenue=_money(
            value.get("campaign_matched_financial_sales_sar") if available else None,
            "SAR",
        ),
        unmatched_orders=(
            _integer(value.get("non_campaign_orders")) if available else None
        ),
        ambiguous_orders=_integer(value.get("ambiguous_orders")) if available else None,
        platform_attributed_conversions=(
            _integer(value.get("platform_attributed_purchases"))
            if value.get("platform_attributed_purchases") is not None
            else None
        ),
        platform_minus_matched_financial_orders=value.get(
            "platform_minus_confirmed_campaign_orders"
        ),
        attribution_policy=value.get("campaign_attribution_policy"),
        timezone=value.get("date_timezone"),
        orders_total=(
            _integer(value.get("orders_total"))
            if value.get("orders_total") is not None
            else None
        ),
        orders_returned=(
            _integer(value.get("orders_returned"))
            if value.get("orders_returned") is not None
            else None
        ),
        truncated=bool(value.get("truncated")),
        reason=value.get("reason"),
    )


def _commerce_orders(rows: list[dict[str, Any]]) -> list[UnifiedCommerceOrder]:
    return [
        UnifiedCommerceOrder(
            order_number=str(row.get("order_number") or ""),
            local_created_at=row.get("local_created_at"),
            local_date=row.get("local_date"),
            date_source=row.get("date_source"),
            timezone=row.get("timezone"),
            status=row.get("status"),
            amount=_money(row.get("amount_sar"), "SAR"),
            financially_included=row.get("financially_included"),
            source_label=row.get("source_label"),
            classification=row.get("classification"),
            match_method=row.get("match_method"),
            campaign_id=row.get("campaign_id"),
            campaign_name=row.get("campaign_name"),
        )
        for row in rows
        if isinstance(row, dict)
    ]


def _management_context(
    rows: list[dict[str, Any]], entity_type: MetaEntityType
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    required = SETTINGS_FIELDS[entity_type]
    for row in rows:
        entity_id = str(row.get("external_id") or "").strip()
        if not entity_id:
            continue
        present = {str(item) for item in row.get("settings_fields_present") or []}
        output[entity_id] = {
            "status": row.get("status"),
            "effective_status": row.get("effective_status"),
            "active": row.get("active"),
            "campaign_id": row.get("campaign_id"),
            "ad_group_id": (
                entity_id
                if entity_type == "adset"
                else row.get("ad_group_id") or row.get("adset_id")
            ),
            "daily_budget_native": _number(row.get("daily_budget_native")),
            "lifetime_budget_native": _number(row.get("lifetime_budget_native")),
            "bid_amount_native": _number(row.get("bid_amount_native")),
            "bid_strategy": row.get("bid_strategy"),
            "billing_event": row.get("billing_event"),
            "optimization_goal": row.get("optimization_goal"),
            "currency_scope": "account_native",
            "settings_evidence_status": (
                "complete" if required.issubset(present) else "partial"
            ),
            "source": "mezan_meta_entity_snapshots_v2",
        }
    return output


def adapt_meta_v2_row(
    row: dict[str, Any],
    *,
    account_value: dict[str, Any],
    period_value: dict[str, Any],
    entity_type: MetaEntityType,
    default_sync_status: str,
) -> UnifiedMarketingRow:
    account = _account(account_value)
    fact_count = _integer(row.get("source_fact_count"))
    sync_status = str(row.get("performance_sync_status") or default_sync_status)
    known = fact_count > 0 or entity_type == "ad_account"
    spend = _number(row.get("spend_native")) if known else None
    revenue = _number(row.get("purchase_value_native")) if known else None
    return UnifiedMarketingRow(
        provider="meta_ads",
        account=account,
        period=_period(period_value),
        entity=_identity(row, entity_type=entity_type, account=account),
        delivery=UnifiedDeliveryMetrics(
            spend=_money(spend, account.currency),
            spend_sar=_money(row.get("spend_sar"), "SAR"),
            impressions=_integer(row.get("impressions")) if known else None,
            clicks=_integer(row.get("clicks")) if known else None,
            views=None,
            ctr_pct=(
                round(
                    (_integer(row.get("clicks")) / _integer(row.get("impressions")))
                    * 100,
                    6,
                )
                if known and _integer(row.get("impressions")) > 0
                else None
            ),
        ),
        platform_outcomes=UnifiedPlatformOutcomes(
            conversions=_integer(row.get("purchases")) if known else None,
            revenue=_money(revenue, account.currency),
            roas=(
                round(revenue / spend, 6)
                if revenue is not None and spend and spend > 0
                else None
            ),
        ),
        commerce_outcomes=_commerce(row, entity_type),
        commerce_profitability=_profitability(row),
        abandoned_cart_outcomes=UnifiedAbandonedCartOutcomes(
            status="unavailable",
            scope="campaign_only",
            abandoned_value=_money(None, "SAR"),
            is_campaign_attributed=False,
            causality_guard="unattributed_store_carts_are_not_campaign_revenue",
        ),
        quality=UnifiedQuality(
            sync_status=sync_status,
            coverage_status="complete" if sync_status == "complete" else "partial",
            source_fact_count=fact_count,
            amount_complete=row.get("amount_complete"),
            reconciliation_status=row.get("reconciliation_status"),
            reason=row.get("performance_reason"),
        ),
        lineage=UnifiedLineage(
            adapter="meta_v2",
            source_version="v2",
            source_collection=str(
                row.get("source_collection") or "mezan_meta_entity_performance_daily_v2"
            ),
            provider_metric_mapping=dict(PROVIDER_METRIC_MAPPING),
        ),
    )


def _native_metrics(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "spend_native": _number(row.get("spend_native")),
        "spend_sar": _number(row.get("spend_sar")),
        "impressions": _integer(row.get("impressions")),
        "clicks": _integer(row.get("clicks")),
        "purchases": _integer(row.get("purchases")),
        "purchase_value_native": _number(row.get("purchase_value_native")),
    }


def build_meta_v2_unified_report(
    *,
    account_value: dict[str, Any],
    period_value: dict[str, Any],
    entity_type: MetaEntityType,
    rows: list[dict[str, Any]],
    totals: dict[str, Any],
    sync_status: str,
    orders: list[dict[str, Any]] | None = None,
    order_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account = _account(account_value)
    total_value = {
        **totals,
        "external_id": account.id,
        "name": account.name,
        "performance_sync_status": sync_status,
    }
    report = UnifiedMarketingReport(
        provider="meta_ads",
        entity_level=LEVELS[entity_type],
        account=account,
        period=_period(period_value),
        totals=adapt_meta_v2_row(
            total_value,
            account_value=account_value,
            period_value=period_value,
            entity_type="ad_account",
            default_sync_status=sync_status,
        ),
        rows=[
            adapt_meta_v2_row(
                row,
                account_value=account_value,
                period_value=period_value,
                entity_type=entity_type,
                default_sync_status=sync_status,
            )
            for row in rows
        ],
        orders=_commerce_orders(list(orders or [])),
        order_summary=_order_summary(dict(order_summary or {})),
    ).model_dump(mode="json")
    report["management_context"] = _management_context(rows, entity_type)
    report["native_evidence"] = {
        "provider": "meta_ads",
        "account_id": account.id,
        "timezone": account.timezone,
        "entity_level": LEVELS[entity_type],
        "hierarchy": sorted(
            [
                {
                    "id": str(row.get("external_id") or account.id),
                    "campaign_id": (
                        str(row.get("external_id"))
                        if entity_type == "campaign"
                        else str(row.get("campaign_id") or "") or None
                    ),
                    "ad_group_id": (
                        str(row.get("external_id"))
                        if entity_type == "adset"
                        else str(row.get("ad_group_id") or row.get("adset_id") or "")
                        or None
                    ),
                }
                for row in rows
            ],
            key=lambda item: item["id"],
        ),
        "metric_totals": _native_metrics(totals),
        "observed_dates": sorted(
            {str(item) for item in totals.get("observed_dates") or []}
        ),
        "expected_dates": sorted(
            {str(item) for item in totals.get("expected_dates") or []}
        ),
        "source_collection": str(
            totals.get("source_collection") or "mezan_meta_entity_performance_daily_v2"
        ),
    }
    report["decision_eligibility"] = {
        "eligible": False,
        "reason": "meta_shadow_not_accepted",
    }
    return report


__all__ = [
    "LEVELS",
    "PROVIDER_METRIC_MAPPING",
    "SETTINGS_FIELDS",
    "adapt_meta_v2_row",
    "build_meta_v2_unified_report",
]
