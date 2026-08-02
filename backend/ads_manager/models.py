"""Response models for the read-only unified advertising manager."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricSet(StrictResponseModel):
    provider_reported_spend_sar: float | None = None
    booked_ad_expense_sar: float | None = None
    platform_attributed_revenue_sar: float | None = None
    platform_reported_purchases: int | None = None
    platform_reported_impressions: int | None = None
    platform_reported_clicks: int | None = None
    platform_roas: float | None = None
    platform_cpa_sar: float | None = None
    platform_cpc_sar: float | None = None
    platform_cpm_sar: float | None = None
    platform_ctr_pct: float | None = None


class Freshness(StrictResponseModel):
    last_observed_at: str | None = None
    data_delay_minutes: float | None = None
    observed_days: int = 0
    requested_days: int = 0
    status: Literal["fresh", "delayed", "stale", "unavailable", "unknown"]


class CampaignCoverage(StrictResponseModel):
    status: Literal["available", "aggregate_only", "unavailable"]
    campaign_count: int = 0
    source_rows: int = 0
    detail: str


class PerformanceCoverage(StrictResponseModel):
    status: Literal["complete", "partial", "stale", "unavailable"]
    eligible_for_ratios: bool = False
    observed_days: int = 0
    requested_days: int = 0
    coverage_pct: float | None = None
    missing_spend_dates: list[str] = Field(default_factory=list)
    reasons: list[
        Literal[
            "source_unavailable",
            "source_truncated",
            "invalid_source_dates",
            "incomplete_spend",
            "missing_performance_dates",
            "stale_performance",
            "incomplete_revenue",
            "incomplete_conversions",
            "unverified_zero_performance",
        ]
    ] = Field(default_factory=list)
    detail: str


class AccountPerformanceCoverage(StrictResponseModel):
    account_id: str
    account_name: str
    status: Literal["complete", "partial", "unavailable"]
    spend_sar: float | None = None
    spend_days: int = 0
    conversion_complete_days: int = 0
    requested_days: int = 0
    missing_spend_dates: list[str] = Field(default_factory=list)
    missing_conversion_dates: list[str] = Field(default_factory=list)
    current_day_lag_allowed: bool = False
    last_observed_date: str | None = None
    detail: str


class Reconciliation(StrictResponseModel):
    status: Literal["matched", "drift", "not_comparable", "no_data"]
    comparison_basis: Literal[
        "account_day_aligned",
        "aggregate_period_only",
        "unavailable",
    ]
    severity: Literal["none", "info", "warning"]
    action_required: bool = False
    provider_reported_spend_sar: float | None = None
    booked_ad_expense_sar: float | None = None
    gap_sar: float | None = None
    gap_pct: float | None = None
    detail: str


class ProviderSummary(StrictResponseModel):
    provider: Literal["snapchat", "tiktok", "meta"]
    provider_label: str
    integration_provider: str
    connection_status: str
    connection_provenance: str
    health_status: str
    health_score: float | None = None
    last_sync_at: str | None = None
    metrics: MetricSet
    freshness: Freshness
    performance_coverage: PerformanceCoverage
    account_performance_coverage: list[AccountPerformanceCoverage] = Field(
        default_factory=list
    )
    campaign_coverage: CampaignCoverage
    reconciliation: Reconciliation
    metric_availability: dict[str, bool] = Field(default_factory=dict)


class DailySpendPoint(StrictResponseModel):
    date: str
    snapchat: float | None = None
    tiktok: float | None = None
    meta: float | None = None
    booked_ad_expense_sar: float | None = None


class CampaignBudget(StrictResponseModel):
    currency: str | None = None
    daily_native: float | None = None
    lifetime_native: float | None = None


class CampaignRow(StrictResponseModel):
    provider: Literal["tiktok", "meta"]
    provider_label: str
    account_id: str | None = None
    campaign_id: str
    campaign_name: str
    status: str | None = None
    delivery_status: str | None = None
    objective: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    budget: CampaignBudget = Field(default_factory=CampaignBudget)
    spend_reported: float | None = None
    spend_currency: str | None = None
    spend_sar_equivalent: float | None = None
    revenue_reported: float | None = None
    revenue_sar_equivalent: float | None = None
    purchases: int | None = None
    impressions: int | None = None
    clicks: int | None = None
    roas: float | None = None
    cpa_reported: float | None = None
    cpc_reported: float | None = None
    cpm_reported: float | None = None
    ctr_pct: float | None = None
    spend_share_pct: float | None = None
    last_observed_date: str | None = None
    data_source: str
    currency_evidence: str


class Insight(StrictResponseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    title: str
    detail: str
    confidence: Literal["high", "medium", "low"]
    evidence: dict[str, Any] = Field(default_factory=dict)


class SourceDefinition(StrictResponseModel):
    key: str
    role: str
    grain: str
    authoritative_for: list[str]


class ObserveOnlyPolicy(StrictResponseModel):
    mode: Literal["observe_only"] = "observe_only"
    mutations_allowed: Literal[False] = False
    advertising_mutations_enabled: Literal[False] = False
    ai_can: list[str]
    ai_cannot: list[str]
    lifecycle_required_for_future_writes: list[str]


class CoverageSummary(StrictResponseModel):
    revenue_is_partial: bool = True
    provider_spend_is_partial: bool = True
    booked_expense_is_partial: bool = True
    providers_with_performance_data: int = 0
    providers_total: int = 3
    campaign_detail_providers: int = 0
    revenue_providers: int = 0
    conversion_providers: int = 0
    click_providers: int = 0
    impression_providers: int = 0
    ratio_eligible_providers: int = 0
    provider_spend_providers: int = 0
    booked_expense_providers: int = 0
    unscoped_booked_expense_sar: float | None = None
    source_row_limit_reached: list[str] = Field(default_factory=list)
    source_warnings: list[str] = Field(default_factory=list)


class CampaignPagination(StrictResponseModel):
    page: int = 1
    limit: int = 50
    total: int = 0
    pages: int = 0


class AdsManagerOverview(StrictResponseModel):
    generated_at: str
    range: dict[str, str]
    metrics: MetricSet
    coverage: CoverageSummary
    providers: list[ProviderSummary]
    daily_spend: list[DailySpendPoint]
    campaigns: list[CampaignRow]
    campaign_pagination: CampaignPagination
    insights: list[Insight]
    sources: list[SourceDefinition]
    policy: ObserveOnlyPolicy
