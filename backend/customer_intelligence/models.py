"""Strict public contracts for the Customer Intelligence Phase 1 preview.

Phase 1 is deliberately synthetic and observe-only.  The Literal safety fields
make it impossible for this response contract to advertise a real mutation as
enabled by accident.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class WorkspaceTab(StrictResponseModel):
    key: str
    label_ar: str
    count: int = Field(default=0, ge=0)
    state: Literal["preview", "planned"] = "preview"


class ObjectionPreview(StrictResponseModel):
    id: str
    label: str
    count: int = Field(ge=0)
    trend: str
    evidence: str
    recommendation: str


class CampaignImpactPreview(StrictResponseModel):
    id: str
    campaign_name: str
    source: str
    conversations: int = Field(ge=0)
    qualified: int = Field(ge=0)
    paid_orders: Literal[0] = 0
    top_objection: str
    data_quality: Literal["preview_only"] = "preview_only"


class IntegrationStatusPreview(StrictResponseModel):
    id: str
    name: str
    status: Literal["mock_provider", "not_connected_here", "simulation"]
    detail: str
    reads_allowed: Literal[False] = False
    writes_allowed: Literal[False] = False


class WorkspaceDefinition(StrictResponseModel):
    title_ar: str
    title_en: str
    description_ar: str
    preview_notice_ar: str
    owner_preview: Literal[True] = True
    operating_level: Literal[1] = 1
    operating_level_label: str
    tabs: list[WorkspaceTab]
    objections: list[ObjectionPreview]
    campaign_impact: list[CampaignImpactPreview]
    integrations: list[IntegrationStatusPreview]


class OverviewPreview(StrictResponseModel):
    open_conversations: int = Field(ge=0)
    needs_human_review: int = Field(ge=0)
    follow_ups_due: int = Field(ge=0)
    sales_opportunities: int = Field(ge=0)
    product_opportunities: int = Field(ge=0)
    potential_revenue_sar: float = Field(ge=0)


class MediaAnalysisPreview(StrictResponseModel):
    media_type: Literal["audio", "image"]
    fixture_asset_key: str
    transcript: str | None = None
    summary_ar: str
    confidence: float = Field(ge=0, le=1)
    is_fixture: Literal[True] = True


class ConversationMessagePreview(StrictResponseModel):
    message_id: str
    sender: Literal["customer", "assistant", "employee"]
    kind: Literal["text", "audio", "image"]
    text: str | None = None
    occurred_at: datetime
    media_analysis: MediaAnalysisPreview | None = None


class ConversationPreview(StrictResponseModel):
    conversation_id: str
    customer_label: str
    channel: Literal["whatsapp_mock"] = "whatsapp_mock"
    state: Literal["needs_reply", "follow_up", "human_review", "resolved"]
    intent: str
    objection: str | None = None
    sentiment: Literal["positive", "neutral", "hesitant", "negative"]
    confidence: float = Field(ge=0, le=1)
    assigned_to: str
    last_message: str
    last_message_at: datetime
    unread_count: int = Field(default=0, ge=0)
    messages: list[ConversationMessagePreview] = Field(default_factory=list)


class CustomerProfilePreview(StrictResponseModel):
    customer_id: str
    customer_label: str
    lifecycle_stage: str
    classifications: list[str]
    preferred_products: list[str]
    preferred_colors: list[str]
    budget_range_sar: str
    purchase_probability: float = Field(ge=0, le=1)
    contact_consent: Literal["unknown", "allowed", "declined"]
    next_best_action: str
    facts_are_synthetic: Literal[True] = True


class FollowUpPreview(StrictResponseModel):
    follow_up_id: str
    conversation_id: str
    due_at: datetime
    reason: str
    proposed_message: str
    status: Literal["suggested_preview"] = "suggested_preview"
    execution_allowed: Literal[False] = False


class SalesOpportunityPreview(StrictResponseModel):
    opportunity_id: str
    conversation_id: str
    title: str
    stage: Literal["detected", "collecting", "suggested"]
    score: int = Field(ge=0, le=100)
    reason: str
    next_best_action: str


class ProductOpportunityPreview(StrictResponseModel):
    opportunity_id: str
    title: str
    stage: Literal["detected", "collecting", "suggested"]
    request_count: int = Field(ge=0)
    unique_customers: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    evidence_examples: list[str]
    closest_store_products: list[str]
    recommendation: Literal["ignore", "monitor", "request_sample", "review_for_store"]
    product_creation_allowed: Literal[False] = False


class CompetitorSignalPreview(StrictResponseModel):
    signal_id: str
    name: str
    status: Literal["mentioned_once", "potential_repeated", "review_required"]
    mention_count: int = Field(ge=0)
    linked_product: str
    confidence: float = Field(ge=0, le=1)
    external_research_allowed: Literal[False] = False


class ApprovedOfferPreview(StrictResponseModel):
    offer_id: str
    label_ar: str
    offer_type: Literal["percentage", "fixed", "free_shipping", "bundle"]
    value: str
    reason: str
    expected_margin_impact_sar: float
    approval_state: Literal["demo_approved"] = "demo_approved"
    application_allowed: Literal[False] = False


class CartItemPreview(StrictResponseModel):
    product_id: str
    title: str
    variant: str
    quantity: int = Field(ge=1)
    unit_price_sar: float = Field(ge=0)
    source_verification: Literal["synthetic_unverified"] = "synthetic_unverified"


class PaymentLinkPreview(StrictResponseModel):
    url: str
    label_ar: str
    is_real: Literal[False] = False
    creation_allowed: Literal[False] = False


class ConversationCartPreview(StrictResponseModel):
    draft_id: str
    conversation_id: str
    status: Literal["preview_only"] = "preview_only"
    items: list[CartItemPreview]
    subtotal_sar: float = Field(ge=0)
    shipping_sar: float = Field(ge=0)
    discount_sar: float = Field(ge=0)
    total_sar: float = Field(ge=0)
    price_verified_from_source: Literal[False] = False
    inventory_verified_from_source: Literal[False] = False
    customer_confirmed: Literal[False] = False
    create_order_allowed: Literal[False] = False
    payment_link: PaymentLinkPreview


class KnowledgePreview(StrictResponseModel):
    status: Literal["proposal_only"] = "proposal_only"
    suggested_articles: list[str]
    publication_allowed: Literal[False] = False


class QualityPreview(StrictResponseModel):
    measurement_mode: Literal["synthetic"] = "synthetic"
    suggested_reply_acceptance_pct: float = Field(ge=0, le=100)
    human_escalation_pct: float = Field(ge=0, le=100)
    paid_order_conversion_pct: float = Field(ge=0, le=100)
    detected_policy_violations: int = Field(ge=0)


class DecisionLogPreview(StrictResponseModel):
    decision_id: str
    observation: str
    evidence_refs: list[str]
    confidence: float = Field(ge=0, le=1)
    expected_impact: str
    risk: Literal["low", "medium", "high"]
    proposed_action: str
    required_approval: Literal["employee", "owner"]
    approval_status: Literal["not_requested"] = "not_requested"
    execution_status: Literal["not_executed"] = "not_executed"
    measured_outcome: Literal["not_available"] = "not_available"
    rollback_status: Literal["not_applicable"] = "not_applicable"


class SafetyPolicy(StrictResponseModel):
    mode: Literal["observe_only"] = "observe_only"
    preview_only: Literal[True] = True
    fixtures_are_synthetic: Literal[True] = True
    writes_allowed: Literal[False] = False
    external_calls_allowed: Literal[False] = False
    whatsapp_send_allowed: Literal[False] = False
    order_creation_allowed: Literal[False] = False
    discount_creation_allowed: Literal[False] = False
    payment_link_creation_allowed: Literal[False] = False
    product_mutation_allowed: Literal[False] = False
    campaign_mutation_allowed: Literal[False] = False
    ai_execution_allowed: Literal[False] = False
    lifecycle_required_for_future_writes: list[str]
    blocked_reason_ar: str


class CustomerIntelligenceWorkspaceResponse(StrictResponseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    mode: Literal["preview_fixture"] = "preview_fixture"
    data_origin: Literal["synthetic"] = "synthetic"
    workspace: WorkspaceDefinition
    overview: OverviewPreview
    conversations: list[ConversationPreview]
    customer_profile: CustomerProfilePreview
    follow_ups: list[FollowUpPreview]
    sales_opportunities: list[SalesOpportunityPreview]
    product_opportunities: list[ProductOpportunityPreview]
    competitor_signals: list[CompetitorSignalPreview]
    approved_offers: list[ApprovedOfferPreview]
    conversation_cart: ConversationCartPreview
    knowledge: KnowledgePreview
    quality: QualityPreview
    audit_preview: list[DecisionLogPreview]
    safety_policy: SafetyPolicy
