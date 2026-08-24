"""Snapchat V2 adapter for the provider-neutral marketing contract."""
from __future__ import annotations

from typing import Any, Literal

from unified_marketing.contract import (
    MoneyValue,
    UnifiedAccount,
    UnifiedCommerceOrderSummary,
    UnifiedCommerceOutcomes,
    UnifiedDeliveryMetrics,
    UnifiedEntityIdentity,
    UnifiedLineage,
    UnifiedMarketingReport,
    UnifiedMarketingRow,
    UnifiedPeriod,
    UnifiedPlatformOutcomes,
    UnifiedQuality,
)

SnapchatEntityType = Literal["account", "campaign", "ad_squad", "ad"]

LEVELS = {
    "account": "account",
    "campaign": "campaign",
    "ad_squad": "ad_group",
    "ad": "ad",
}

PROVIDER_METRIC_MAPPING = {
    "delivery.clicks": "swipes",
    "delivery.views": "video_views",
    "delivery.impressions": "impressions",
    "delivery.spend": "spend_native",
    "platform_outcomes.conversions": "purchases",
    "platform_outcomes.revenue": "purchase_value_native",
    "commerce_outcomes.orders": "salla_results.orders",
    "commerce_outcomes.revenue": "salla_results.sales_sar",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _integer(value: Any) -> int:
    parsed = _number(value)
    return max(0, int(parsed or 0))


def _known_integer(value: Any, *, known: bool) -> int | None:
    return _integer(value) if known else None


def _account(value: dict[str, Any]) -> UnifiedAccount:
    account_id = str(value.get("ad_account_id") or value.get("id") or "").strip()
    return UnifiedAccount(
        id=account_id,
        name=str(value.get("display_name") or value.get("name") or account_id),
        currency=str(value.get("currency") or "").upper(),
        timezone=str(value.get("timezone") or ""),
    )


def _period(value: dict[str, Any]) -> UnifiedPeriod:
    return UnifiedPeriod(
        date_from=str(value["date_from"]),
        date_to=str(value["date_to"]),
        timezone=str(value["timezone"]),
        action_report_time=str(value.get("action_report_time") or "conversion"),
    )


def _identity(
    row: dict[str, Any],
    *,
    entity_type: SnapchatEntityType,
    account: UnifiedAccount,
) -> UnifiedEntityIdentity:
    if entity_type == "account":
        return UnifiedEntityIdentity(
            level="account",
            provider_level="ad_account",
            id=account.id,
            name=account.name,
            status=row.get("status"),
            active=True,
        )
    external_id = str(
        row.get("external_id")
        or row.get(f"{entity_type}_id")
        or ""
    )
    return UnifiedEntityIdentity(
        level=LEVELS[entity_type],
        provider_level=entity_type,
        id=external_id,
        name=str(row.get("name") or row.get(f"{entity_type}_name") or external_id),
        status=row.get("status"),
        active=row.get("active") if isinstance(row.get("active"), bool) else None,
        campaign_id=(
            external_id if entity_type == "campaign" else row.get("campaign_id")
        ),
        ad_group_id=(
            external_id if entity_type == "ad_squad" else row.get("ad_squad_id")
        ),
    )


def _commerce(
    row: dict[str, Any],
    *,
    entity_type: SnapchatEntityType,
) -> UnifiedCommerceOutcomes:
    if entity_type not in {"account", "campaign"}:
        return UnifiedCommerceOutcomes(
            status="unavailable",
            orders=None,
            revenue=MoneyValue(amount=None, currency="SAR"),
            roas=None,
            attribution_scope="campaign_only",
        )
    value = row.get("salla_results")
    if not isinstance(value, dict):
        return UnifiedCommerceOutcomes(
            status="partial",
            orders=None,
            revenue=MoneyValue(amount=None, currency="SAR"),
            roas=None,
            attribution_scope="exact_campaign_match",
        )
    status = str(value.get("status") or "complete")
    if status != "complete":
        return UnifiedCommerceOutcomes(
            status="partial",
            orders=None,
            revenue=MoneyValue(amount=None, currency="SAR"),
            roas=None,
            attribution_scope="exact_campaign_match",
        )
    return UnifiedCommerceOutcomes(
        status="complete",
        orders=_integer(value.get("orders")),
        revenue=MoneyValue(
            amount=_number(value.get("sales_sar")) or 0.0,
            currency="SAR",
        ),
        roas=_number(value.get("roas")),
        attribution_scope=(
            "account_sum_of_exact_campaign_matches"
            if entity_type == "account"
            else "exact_campaign_match"
        ),
    )


def _commerce_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "order_number": str(row.get("order_number") or ""),
                "local_created_at": row.get("local_created_at"),
                "local_date": row.get("local_date"),
                "date_source": row.get("date_source"),
                "timezone": row.get("timezone"),
                "status": row.get("status"),
                "amount": {
                    "amount": _number(row.get("amount_sar")),
                    "currency": "SAR",
                },
                "financially_included": row.get("financially_included"),
                "source_label": row.get("source_label"),
                "classification": row.get("classification"),
                "match_method": row.get("match_method"),
                "campaign_id": row.get("campaign_id"),
                "campaign_name": row.get("campaign_name"),
            }
        )
    return output


def _commerce_order_summary(value: dict[str, Any]) -> UnifiedCommerceOrderSummary:
    status = str(value.get("coverage_status") or "unavailable")
    available = status == "complete"
    platform_conversions = value.get("platform_attributed_purchases")
    return UnifiedCommerceOrderSummary(
        status=(
            "complete"
            if available
            else "partial"
            if status == "partial"
            else "unavailable"
        ),
        source="salla",
        created_orders=(
            _integer(value.get("total_salla_created_orders")) if available else None
        ),
        financial_orders=(
            _integer(value.get("total_financial_orders")) if available else None
        ),
        financial_revenue=MoneyValue(
            amount=_number(value.get("total_financial_sales_sar")) if available else None,
            currency="SAR",
        ),
        matched_orders=(
            _integer(value.get("campaign_matched_orders")) if available else None
        ),
        matched_financial_orders=(
            _integer(value.get("campaign_matched_financial_orders"))
            if available
            else None
        ),
        matched_financial_revenue=MoneyValue(
            amount=(
                _number(value.get("campaign_matched_financial_sales_sar"))
                if available
                else None
            ),
            currency="SAR",
        ),
        unmatched_orders=(
            _integer(value.get("non_campaign_orders")) if available else None
        ),
        ambiguous_orders=(
            _integer(value.get("ambiguous_orders")) if available else None
        ),
        platform_attributed_conversions=(
            _integer(platform_conversions)
            if platform_conversions is not None
            else None
        ),
        platform_minus_matched_financial_orders=(
            int(value["platform_minus_confirmed_campaign_orders"])
            if available
            and value.get("platform_minus_confirmed_campaign_orders") is not None
            else None
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


def adapt_snapchat_v2_row(
    row: dict[str, Any],
    *,
    account_value: dict[str, Any],
    period_value: dict[str, Any],
    entity_type: SnapchatEntityType,
    default_sync_status: str,
) -> UnifiedMarketingRow:
    account = _account(account_value)
    period = _period(period_value)
    fact_count = _integer(row.get("source_fact_count"))
    row_sync_status = str(row.get("performance_sync_status") or default_sync_status)
    metrics_known = fact_count > 0 or (
        entity_type == "account" and row_sync_status == "complete"
    )
    spend = (_number(row.get("spend_native")) or 0.0) if metrics_known else None
    spend_sar = _number(row.get("spend_sar"))
    purchase_value = (
        (_number(row.get("purchase_value_native")) or 0.0)
        if metrics_known
        else None
    )
    coverage_status = "complete" if row_sync_status == "complete" else "partial"
    return UnifiedMarketingRow(
        provider="snapchat_ads",
        account=account,
        period=period,
        entity=_identity(row, entity_type=entity_type, account=account),
        delivery=UnifiedDeliveryMetrics(
            spend=MoneyValue(amount=spend, currency=account.currency),
            spend_sar=MoneyValue(amount=spend_sar, currency="SAR"),
            impressions=_known_integer(row.get("impressions"), known=metrics_known),
            clicks=_known_integer(row.get("swipes"), known=metrics_known),
            views=_known_integer(row.get("video_views"), known=metrics_known),
            ctr_pct=_number(row.get("ctr_pct")) if metrics_known else None,
        ),
        platform_outcomes=UnifiedPlatformOutcomes(
            conversions=_known_integer(row.get("purchases"), known=metrics_known),
            revenue=MoneyValue(amount=purchase_value, currency=account.currency),
            roas=_number(row.get("roas")) if metrics_known else None,
        ),
        commerce_outcomes=_commerce(row, entity_type=entity_type),
        quality=UnifiedQuality(
            sync_status=row_sync_status,
            coverage_status=coverage_status,
            source_fact_count=fact_count,
            amount_complete=row.get("amount_complete"),
            reconciliation_status=row.get("reconciliation_status"),
            reason=row.get("performance_reason"),
        ),
        lineage=UnifiedLineage(
            adapter="snapchat_v2",
            source_version="v2",
            source_collection="mezan_snapchat_hourly_facts_v2",
            provider_metric_mapping=dict(PROVIDER_METRIC_MAPPING),
        ),
    )


def build_snapchat_v2_unified_report(
    *,
    account_value: dict[str, Any],
    period_value: dict[str, Any],
    entity_type: SnapchatEntityType,
    rows: list[dict[str, Any]],
    totals: dict[str, Any],
    sync_status: str,
    orders: list[dict[str, Any]] | None = None,
    order_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account = _account(account_value)
    period = _period(period_value)
    total_value = {
        **totals,
        "external_id": account.id,
        "name": account.name,
        "status": account_value.get("account_status"),
        "active": account_value.get("active"),
        "performance_sync_status": sync_status,
    }
    report = UnifiedMarketingReport(
        provider="snapchat_ads",
        entity_level=LEVELS[entity_type],
        account=account,
        period=period,
        totals=adapt_snapchat_v2_row(
            total_value,
            account_value=account_value,
            period_value=period_value,
            entity_type="account",
            default_sync_status=sync_status,
        ),
        rows=[
            adapt_snapchat_v2_row(
                row,
                account_value=account_value,
                period_value=period_value,
                entity_type=entity_type,
                default_sync_status=sync_status,
            )
            for row in rows
        ],
        orders=_commerce_orders(list(orders or [])),
        order_summary=_commerce_order_summary(dict(order_summary or {})),
    )
    return report.model_dump(mode="json")


__all__ = [
    "PROVIDER_METRIC_MAPPING",
    "adapt_snapchat_v2_row",
    "build_snapchat_v2_unified_report",
]
