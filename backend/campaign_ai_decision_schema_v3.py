"""Decision Intelligence V3 schema for campaign recommendations.

This schema is deliberately additive to the established V2 execution contract.
``recommended_action`` describes the business action OpenAI believes is correct.
The legacy ``action`` field remains an internal compatibility projection used
only by the existing Ads API approval/execution path.

No action in this module is selected by thresholds.  It only defines the output
contract and a deterministic *translation* from an already-made OpenAI decision
to the old execution vocabulary.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RootCauseCategory = Literal[
    "CAMPAIGN",
    "CREATIVE",
    "AUDIENCE",
    "PRODUCT",
    "OFFER",
    "LANDING_PAGE",
    "ADD_TO_CART",
    "CHECKOUT",
    "SHIPPING",
    "PAYMENT",
    "WEBSITE",
    "TRACKING",
    "ATTRIBUTION",
    "SEASONALITY",
    "INVENTORY",
    "PRODUCT_VISIBILITY",
    "PRODUCT_URL",
    "NORMAL_VARIANCE",
    "INSUFFICIENT_DATA",
    "UNKNOWN",
]

RecommendedAction = Literal[
    "CONTINUE",
    "MONITOR",
    "PAUSE_AD",
    "PAUSE_ADSET",
    "PAUSE_CAMPAIGN",
    "DECREASE_BUDGET",
    "INCREASE_BUDGET",
    "TEST_NEW_CREATIVE",
    "REFRESH_CREATIVE",
    "TEST_NEW_HOOK",
    "SHORTEN_VIDEO",
    "LONGER_DEMO_VIDEO",
    "PRODUCT_DEMO",
    "PROBLEM_SOLUTION_VIDEO",
    "UGC_STYLE_VIDEO",
    "TESTIMONIAL_VIDEO",
    "BEFORE_AFTER",
    "STORYTELLING_VIDEO",
    "FAQ_VIDEO",
    "OBJECTION_HANDLING_VIDEO",
    "PRICE_OFFER_VIDEO",
    "UNBOXING_VIDEO",
    "PRODUCT_CLOSEUP",
    "LIFESTYLE_VIDEO",
    "COMPARISON_VIDEO",
    "STORY_AD",
    "STATIC_IMAGE_TEST",
    "CAROUSEL_TEST",
    "REVIEW_AUDIENCE",
    "REVIEW_PRODUCT",
    "REVIEW_OFFER",
    "REVIEW_PRODUCT_PAGE",
    "CHANGE_PRODUCT_TITLE",
    "CHANGE_PRODUCT_DESCRIPTION",
    "CHANGE_HERO_IMAGE",
    "REORDER_PRODUCT_IMAGES",
    "REVIEW_PRICE",
    "REVIEW_SHIPPING_COST",
    "REVIEW_CHECKOUT",
    "REVIEW_PAYMENT",
    "INVESTIGATE_ABANDONED_CARTS",
    "INVESTIGATE_WEBSITE",
    "INVESTIGATE_TRACKING",
    "FIX_TRACKING",
    "FIX_DESTINATION_URL",
    "RESTORE_PRODUCT_VISIBILITY",
    "REVIEW_INVENTORY",
    "NO_ACTION_INSUFFICIENT_DATA",
    "CHANGE_VALUE_PROPOSITION",
    "ADD_STRONGER_CTA",
    "SHOW_PRODUCT_EARLIER",
    "SHOW_PRICE_OR_OFFER",
]

ActionType = Literal[
    "ads_write",
    "diagnostic",
    "creative",
    "product_change",
    "operational_alert",
    "no_action",
]

RecommendationType = Literal[
    "media_buying",
    "diagnostic",
    "creative_strategy",
    "product_page",
    "inventory",
    "operational",
    "funnel",
    "tracking",
    "no_action",
]


class MetricEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | None
    numeric_value: float | None
    unit: str | None
    source: str
    quality: Literal["complete", "partial", "unavailable", "estimated"]


class AnalysisBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "GOOD",
        "BAD",
        "MIXED",
        "NORMAL_VARIANCE",
        "INSUFFICIENT_DATA",
        "NOT_APPLICABLE",
        "UNKNOWN",
    ]
    summary: str
    metrics: list[MetricEvidence]
    signals: list[str]
    limitations: list[str]


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RootCauseCategory
    statement: str
    confidence: Literal["high", "medium", "low"]
    evidence_for: list[str]
    evidence_against: list[str]


class ProductPageAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_health: str
    url_health: str
    visibility: str
    inventory_status: str
    estimated_days_to_stockout: float | None
    estimated_stockout_at: str | None
    promoted_variant_status: str
    product_title_analysis: str
    product_description_analysis: str
    hero_image_analysis: str
    gallery_analysis: str
    pricing_analysis: str
    competitor_price_context: str
    internal_price_context: str
    offer_analysis: str
    reviews_analysis: str
    shipping_analysis: str
    ad_page_consistency: str
    detected_issues: list[str]
    recommendations: list[str]
    priority: Literal["critical", "high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]


class KnowledgeReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str
    source_tier: Literal[1, 2, 3]
    published_at: str | None
    last_reviewed_at: str
    reliability: Literal["official", "high", "contextual"]
    topics: list[str]
    insight_summary: str


class CreativeBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str
    problem_to_solve: str
    audience: str
    creative_angle: str
    hook: str
    duration_seconds: int | None
    shot_list: list[str]
    scene_order: list[str]
    first_two_seconds: str
    on_screen_text: list[str]
    voiceover_idea: str
    cta: str
    format: str
    avoid_repeating: list[str]
    hypothesis: str
    success_metrics: list[str]


class ProposedProductChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal[
        "title",
        "description",
        "hero_image",
        "gallery_order",
        "price",
        "offer",
        "visibility",
        "destination_url",
        "inventory",
        "other",
    ]
    current: str | None
    proposed: str
    reason: str
    requires_owner_approval: bool


class DecisionRecommendationV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    provider: Literal["snapchat", "meta"]
    entity_level: Literal["campaign", "ad_group", "ad"]
    entity_id: str
    entity_name: str
    account_id: str | None
    account_name: str | None
    parent_name: str | None
    product_id: str | None
    destination_url: str | None

    recommendation_type: RecommendationType
    action_type: ActionType
    executable: bool
    recommended_action: RecommendedAction
    change_percent: int | None = Field(ge=5, le=30)
    priority: Literal["critical", "high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    title: str
    diagnosis: str
    root_cause_category: RootCauseCategory
    primary_hypothesis: Hypothesis | None
    secondary_hypotheses: list[Hypothesis]
    evidence_for: list[str]
    evidence_against: list[str]

    today_analysis: AnalysisBlock
    yesterday_analysis: AnalysisBlock
    day_minus_2_analysis: AnalysisBlock
    baseline_7d: AnalysisBlock
    baseline_30d: AnalysisBlock
    funnel_analysis: AnalysisBlock
    video_analysis: AnalysisBlock
    creative_analysis: AnalysisBlock
    product_page_analysis: ProductPageAnalysis
    inventory_analysis: AnalysisBlock
    abandoned_cart_analysis: AnalysisBlock
    cross_campaign_analysis: AnalysisBlock
    cross_platform_analysis: AnalysisBlock
    business_context: AnalysisBlock
    external_knowledge_used: list[KnowledgeReference]

    creative_brief: CreativeBrief | None
    proposed_product_changes: list[ProposedProductChange]
    why: str
    expected_effect: str
    risks: list[str]
    what_would_change_the_decision: list[str]

    recommended_wait_hours: int = Field(ge=1, le=48)
    observation_plan: str
    success_criteria: list[str]
    guardrail: str
    next_check_at: str


class DecisionOutputV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    recommendations: list[DecisionRecommendationV3]
    limitations: list[str]


PAUSE_BY_LEVEL: dict[str, str] = {
    "PAUSE_CAMPAIGN": "campaign",
    "PAUSE_ADSET": "ad_group",
    "PAUSE_AD": "ad",
}
ADS_WRITE_ACTIONS = {
    "PAUSE_AD",
    "PAUSE_ADSET",
    "PAUSE_CAMPAIGN",
    "DECREASE_BUDGET",
    "INCREASE_BUDGET",
}


def legacy_execution_action(
    recommended_action: str,
    *,
    entity_level: str,
) -> Literal["pause", "reduce", "monitor", "maintain", "scale"]:
    """Translate an OpenAI business decision into the existing write vocabulary.

    This function does not choose the recommendation.  Invalid write/target
    combinations fail closed to ``monitor`` and are later marked non-executable.
    """
    if recommended_action == "CONTINUE":
        return "maintain"
    if recommended_action == "MONITOR" or recommended_action == "NO_ACTION_INSUFFICIENT_DATA":
        return "monitor"
    if recommended_action in PAUSE_BY_LEVEL:
        return "pause" if PAUSE_BY_LEVEL[recommended_action] == entity_level else "monitor"
    if recommended_action == "DECREASE_BUDGET":
        return "reduce" if entity_level in {"campaign", "ad_group"} else "monitor"
    if recommended_action == "INCREASE_BUDGET":
        return "scale" if entity_level in {"campaign", "ad_group"} else "monitor"
    return "monitor"


def v3_json_schema() -> dict[str, Any]:
    """Return the strict structured-output schema used by the Responses API."""
    return DecisionOutputV3.model_json_schema()


__all__ = [
    "ADS_WRITE_ACTIONS",
    "ActionType",
    "AnalysisBlock",
    "CreativeBrief",
    "DecisionOutputV3",
    "DecisionRecommendationV3",
    "Hypothesis",
    "KnowledgeReference",
    "ProductPageAnalysis",
    "ProposedProductChange",
    "RecommendedAction",
    "RootCauseCategory",
    "legacy_execution_action",
    "v3_json_schema",
]
