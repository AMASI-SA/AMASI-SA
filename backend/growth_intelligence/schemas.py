"""Governed schemas for Growth Intelligence V1.

The models intentionally separate observed evidence from AI judgment. No score,
country fit, trend claim, supplier recommendation, or seasonal plan is allowed to
pretend to be an authoritative fact merely because a model produced it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


GCCCountryCode = Literal["SA", "AE", "QA", "KW", "BH", "OM"]
Confidence = Literal["high", "medium", "low"]
Reliability = Literal["official", "first_party", "high", "contextual"]


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal[
        "first_party_store",
        "official_calendar",
        "official_statistics",
        "marketplace",
        "supplier_marketplace",
        "retailer",
        "search_trend",
        "social_trend",
        "competitor_store",
        "manual_review",
    ]
    source_name: str
    url: str | None
    country: GCCCountryCode | None
    observed_at: str
    published_at: str | None
    reliability: Reliability
    signal: str
    limitations: list[str] = Field(default_factory=list)


class LiquidityEvent(BaseModel):
    """A verified or explicitly uncertain consumer-liquidity event/window."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    country: GCCCountryCode
    name: str
    category: Literal[
        "government_salary",
        "private_payroll_window",
        "citizen_support",
        "pension",
        "social_support",
        "holiday_spend",
        "other",
    ]
    starts_at: str
    ends_at: str
    recommended_preparation_start: str
    confidence: Confidence
    evidence: list[EvidenceRef]
    commercial_notes: list[str]
    limitations: list[str]


class SeasonalOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    country: GCCCountryCode
    season_name: str
    event_date: str | None
    recommended_preparation_start: str | None
    preparation_horizon_days: int | None = Field(default=None, ge=0, le=365)
    product_themes: list[str]
    audience_fit_hypotheses: list[str]
    merchandising_plan: list[str]
    creative_plan: list[str]
    inventory_plan: list[str]
    confidence: Confidence
    evidence: list[EvidenceRef]
    what_would_disprove_it: list[str]
    limitations: list[str]


class GCCCountryOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    country: GCCCountryCode
    product_id: str | None
    product_name: str
    category: str
    recommendation: Literal["TEST", "EXPAND", "WATCH", "DO_NOT_EXPAND_YET"]
    confidence: Confidence
    audience_similarity_reasons: list[str]
    first_party_market_evidence: list[str]
    external_market_evidence: list[str]
    price_and_offer_considerations: list[str]
    shipping_and_payment_considerations: list[str]
    localization_requirements: list[str]
    risks: list[str]
    evidence: list[EvidenceRef]
    what_would_change_the_decision: list[str]


class ProductDiscoveryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    product_name: str
    category: str
    discovery_action: Literal[
        "WATCH",
        "SOURCE_SAMPLE",
        "ORDER_TEST_STOCK",
        "PREPARE_PRODUCT_DRAFT",
        "REJECT_FOR_NOW",
    ]
    confidence: Confidence
    target_countries: list[GCCCountryCode]
    why_it_matches_amasi_audience: list[str]
    trend_signals: list[str]
    supplier_or_source_options: list[EvidenceRef]
    comparable_retail_signals: list[EvidenceRef]
    expected_customer_use_cases: list[str]
    unit_economics_known: bool
    unit_economics_notes: list[str]
    minimum_viable_test: str
    initial_stock_logic: str
    photography_plan: list[str]
    hero_image_plan: str
    product_title_draft: str
    product_description_outline: list[str]
    offer_and_price_test_plan: list[str]
    creative_test_plan: list[str]
    risks: list[str]
    what_would_disprove_it: list[str]
    requires_owner_approval: bool = True


class GrowthIntelligenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["growth_intelligence_v1"] = "growth_intelligence_v1"
    generated_at: str
    horizon_start: str
    horizon_end: str
    liquidity_events: list[LiquidityEvent]
    seasonal_opportunities: list[SeasonalOpportunity]
    gcc_market_opportunities: list[GCCCountryOpportunity]
    product_candidates: list[ProductDiscoveryCandidate]
    limitations: list[str]
    contracts: dict[str, bool]


__all__ = [
    "Confidence",
    "EvidenceRef",
    "GCCCountryCode",
    "GCCCountryOpportunity",
    "GrowthIntelligenceSnapshot",
    "LiquidityEvent",
    "ProductDiscoveryCandidate",
    "Reliability",
    "SeasonalOpportunity",
]
