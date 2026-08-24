"""Stable provider-neutral read contract for paid marketing data.

The contract is intentionally read-only. Provider mutation workflows and
Decision Intelligence remain separate consumers with their own safety gates.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "unified-marketing-data-v1"

UnifiedEntityLevel = Literal["account", "campaign", "ad_group", "ad"]
CoverageStatus = Literal["complete", "partial", "unavailable"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoneyValue(StrictModel):
    amount: float | None = None
    currency: str


class UnifiedAccount(StrictModel):
    id: str
    name: str
    currency: str
    timezone: str


class UnifiedPeriod(StrictModel):
    date_from: str
    date_to: str
    timezone: str
    action_report_time: Literal["conversion", "impression"]


class UnifiedEntityIdentity(StrictModel):
    level: UnifiedEntityLevel
    provider_level: str
    id: str
    name: str
    status: str | None = None
    active: bool | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None


class UnifiedDeliveryMetrics(StrictModel):
    spend: MoneyValue
    spend_sar: MoneyValue
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    ctr_pct: float | None = None


class UnifiedPlatformOutcomes(StrictModel):
    conversions: int | None = Field(default=None, ge=0)
    revenue: MoneyValue
    roas: float | None = None


class UnifiedCommerceOutcomes(StrictModel):
    status: CoverageStatus
    orders: int | None = Field(default=None, ge=0)
    revenue: MoneyValue
    roas: float | None = None
    attribution_scope: str


class UnifiedProductProfitability(StrictModel):
    identity: str
    salla_product_id: str | None = None
    mezan_product_id: str | None = None
    name: str
    sku: str | None = None
    image_url: str | None = None
    units: float = Field(default=0, ge=0)
    orders: int = Field(default=0, ge=0)
    sales: MoneyValue
    product_cost: MoneyValue
    allocated_ad_spend: MoneyValue
    contribution_profit: MoneyValue
    profit_margin_pct: float | None = None
    cost_status: str
    cost_sources: list[str] = Field(default_factory=list)


class UnifiedCommerceProfitability(StrictModel):
    status: CoverageStatus
    orders: int | None = Field(default=None, ge=0)
    sales: MoneyValue
    product_cost: MoneyValue
    known_product_cost: MoneyValue
    ad_spend: MoneyValue
    contribution_profit: MoneyValue
    profit_margin_pct: float | None = None
    cost_status: str
    missing_cost_orders: int = Field(default=0, ge=0)
    product_count: int = Field(default=0, ge=0)
    products: list[UnifiedProductProfitability] = Field(default_factory=list)
    profit_scope: str
    allocation_method: str | None = None


class UnifiedQuality(StrictModel):
    sync_status: str
    coverage_status: CoverageStatus
    source_fact_count: int = Field(default=0, ge=0)
    amount_complete: bool | None = None
    reconciliation_status: str | None = None
    reason: str | None = None


class UnifiedLineage(StrictModel):
    adapter: str
    source_version: str
    source_collection: str
    provider_metric_mapping: dict[str, str]


class UnifiedMarketingRow(StrictModel):
    provider: str
    account: UnifiedAccount
    period: UnifiedPeriod
    entity: UnifiedEntityIdentity
    delivery: UnifiedDeliveryMetrics
    platform_outcomes: UnifiedPlatformOutcomes
    commerce_outcomes: UnifiedCommerceOutcomes
    commerce_profitability: UnifiedCommerceProfitability
    quality: UnifiedQuality
    lineage: UnifiedLineage


class UnifiedDecisionEligibility(StrictModel):
    eligible: bool = False
    reason: str = "shadow_sync_not_accepted"


class UnifiedCommerceOrder(StrictModel):
    """Whitelisted order audit fields exposed by a marketing adapter."""

    order_number: str = ""
    local_created_at: str | None = None
    local_date: str | None = None
    date_source: str | None = None
    timezone: str | None = None
    status: str | None = None
    amount: MoneyValue
    financially_included: bool | None = None
    source_label: str | None = None
    classification: str | None = None
    match_method: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None


class UnifiedCommerceOrderSummary(StrictModel):
    status: CoverageStatus
    source: str
    created_orders: int | None = Field(default=None, ge=0)
    financial_orders: int | None = Field(default=None, ge=0)
    financial_revenue: MoneyValue
    matched_orders: int | None = Field(default=None, ge=0)
    matched_financial_orders: int | None = Field(default=None, ge=0)
    matched_financial_revenue: MoneyValue
    unmatched_orders: int | None = Field(default=None, ge=0)
    ambiguous_orders: int | None = Field(default=None, ge=0)
    platform_attributed_conversions: int | None = None
    platform_minus_matched_financial_orders: int | None = None
    attribution_policy: str | None = None
    timezone: str | None = None
    orders_total: int | None = Field(default=None, ge=0)
    orders_returned: int | None = Field(default=None, ge=0)
    truncated: bool = False
    reason: str | None = None


class UnifiedMarketingReport(StrictModel):
    contract_version: Literal["unified-marketing-data-v1"] = CONTRACT_VERSION
    provider: str
    entity_level: UnifiedEntityLevel
    account: UnifiedAccount
    period: UnifiedPeriod
    totals: UnifiedMarketingRow
    rows: list[UnifiedMarketingRow]
    orders: list[UnifiedCommerceOrder] = Field(default_factory=list)
    order_summary: UnifiedCommerceOrderSummary
    decision_eligibility: UnifiedDecisionEligibility = Field(
        default_factory=UnifiedDecisionEligibility
    )


__all__ = [
    "CONTRACT_VERSION",
    "MoneyValue",
    "UnifiedAccount",
    "UnifiedCommerceOutcomes",
    "UnifiedCommerceProfitability",
    "UnifiedCommerceOrder",
    "UnifiedCommerceOrderSummary",
    "UnifiedDecisionEligibility",
    "UnifiedDeliveryMetrics",
    "UnifiedEntityIdentity",
    "UnifiedLineage",
    "UnifiedMarketingReport",
    "UnifiedMarketingRow",
    "UnifiedPeriod",
    "UnifiedProductProfitability",
    "UnifiedPlatformOutcomes",
    "UnifiedQuality",
]
