"""Evidence-first learning contracts for Mezan Customer Intelligence.

Every inbound customer message is queued by the channel gateway against all
intelligence targets. Analysis outputs use the records below so opinions,
objections, problems, proposed solutions, decisions and measured progress stay
tenant-scoped and traceable to immutable source-message evidence.

This module deliberately contains no provider client and no mutation executor.
Derived text is encrypted; recommendations never become facts or actions merely
because a model proposed them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from .foundation import FoundationRecord


CUSTOMER_SIGNALS_COLLECTION = "mezan_customer_signals_v1"
CUSTOMER_PROBLEMS_COLLECTION = "mezan_customer_problems_v1"
CUSTOMER_DECISIONS_COLLECTION = "mezan_customer_decisions_v1"
CUSTOMER_MESSAGE_ANALYSES_COLLECTION = "mezan_customer_message_analyses_v1"


class CustomerMessageAnalysisRecord(FoundationRecord):
    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    source_message_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    provider: Literal["whatsapp", "instagram", "tiktok"]
    surface: Literal["direct_message", "comment", "unknown"]
    source_role: Literal["customer", "employee"] = "customer"
    content_mode: Literal["text", "caption", "metadata_only"] = "text"
    model: str = Field(min_length=1)
    analysis_targets: list[
        Literal[
            "reply_context",
            "customer_signal",
            "problem_detection",
            "sales_opportunity",
            "product_feedback",
            "service_quality",
            "decision_support",
        ]
    ] = Field(min_length=1)
    sentiment: Literal["positive", "neutral", "negative", "mixed"]
    urgency: Literal["low", "medium", "high", "critical"]
    result_ciphertext: bytes = Field(min_length=1)
    generated_at: datetime
    created_at: datetime
    plaintext_derived_content_stored: Literal[False] = False


class CustomerSignalRecord(FoundationRecord):
    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    signal_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    source_message_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    provider: Literal["whatsapp", "instagram", "tiktok"]
    surface: Literal["direct_message", "comment", "unknown"]
    source_role: Literal["customer", "employee"] = "customer"
    signal_type: Literal[
        "intent",
        "inquiry",
        "opinion",
        "objection",
        "complaint",
        "product_request",
        "praise",
        "spam",
        "abuse",
        "response_quality",
        "other",
    ]
    commercial_impact: Literal[
        "sales_opportunity",
        "retention_risk",
        "product_feedback",
        "service_quality",
        "none",
    ] = "none"
    urgency: Literal["low", "medium", "high", "critical"] = "low"
    confidence: float = Field(ge=0, le=1)
    analysis_ciphertext: bytes = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    review_status: Literal["proposed", "confirmed", "dismissed", "superseded"] = (
        "proposed"
    )
    human_review_required: Literal[True] = True
    action_allowed: Literal[False] = False
    created_at: datetime
    updated_at: datetime
    plaintext_derived_content_stored: Literal[False] = False

    @model_validator(mode="after")
    def source_is_evidence(self) -> "CustomerSignalRecord":
        if self.source_message_id not in self.evidence_refs:
            raise ValueError("source_message_id must be included in evidence_refs")
        return self


class CustomerProblemRecord(FoundationRecord):
    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    problem_id: str = Field(min_length=1)
    problem_type: Literal[
        "customer_experience",
        "product",
        "pricing",
        "inventory",
        "fulfillment",
        "support",
        "marketing",
        "technical",
        "other",
    ]
    status: Literal[
        "detected",
        "confirmed",
        "solution_proposed",
        "approved",
        "implemented",
        "monitoring",
        "resolved",
        "reopened",
        "dismissed",
    ] = "detected"
    severity: Literal["low", "medium", "high", "critical"]
    first_detected_at: datetime
    last_detected_at: datetime
    occurrence_count: int = Field(ge=1)
    affected_customer_count: int = Field(ge=1)
    evidence_refs: list[str] = Field(min_length=1)
    problem_ciphertext: bytes = Field(min_length=1)
    proposed_solution_ciphertext: bytes | None = Field(default=None, min_length=1)
    measurement_plan_ciphertext: bytes | None = Field(default=None, min_length=1)
    progress_note_ciphertext: bytes | None = Field(default=None, min_length=1)
    progress_state: Literal[
        "not_started", "in_progress", "waiting_for_measurement", "on_track", "at_risk", "complete"
    ] = "not_started"
    baseline_recorded: bool = False
    outcome_recorded: bool = False
    last_measured_at: datetime | None = None
    next_review_at: datetime | None = None
    confidence: float = Field(ge=0, le=1)
    human_review_required: Literal[True] = True
    external_mutation_allowed: Literal[False] = False
    created_at: datetime
    updated_at: datetime
    plaintext_derived_content_stored: Literal[False] = False

    @model_validator(mode="after")
    def progress_requires_measurement(self) -> "CustomerProblemRecord":
        if self.status in {"monitoring", "resolved"} and not self.baseline_recorded:
            raise ValueError("monitoring and resolution require a recorded baseline")
        if self.status == "resolved" and not self.outcome_recorded:
            raise ValueError("resolution requires a recorded outcome")
        return self


class CustomerDecisionRecord(FoundationRecord):
    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision_type: Literal[
        "reply_guidance",
        "service_improvement",
        "product_improvement",
        "sales_action",
        "marketing_action",
        "problem_resolution",
        "other",
    ]
    status: Literal[
        "proposed",
        "approved",
        "rejected",
        "implemented",
        "monitoring",
        "successful",
        "unsuccessful",
        "rolled_back",
    ] = "proposed"
    evidence_refs: list[str] = Field(min_length=1)
    problem_refs: list[str] = Field(default_factory=list)
    recommendation_ciphertext: bytes = Field(min_length=1)
    measurement_ciphertext: bytes | None = Field(default=None, min_length=1)
    confidence: float = Field(ge=0, le=1)
    risk: Literal["low", "medium", "high"]
    required_approval: Literal["employee", "owner"]
    approved_by: str | None = None
    approved_at: datetime | None = None
    measured_at: datetime | None = None
    human_review_required: Literal[True] = True
    automatic_execution_allowed: Literal[False] = False
    created_at: datetime
    updated_at: datetime
    plaintext_derived_content_stored: Literal[False] = False

    @model_validator(mode="after")
    def approval_is_attributable(self) -> "CustomerDecisionRecord":
        approved_states = {
            "approved", "implemented", "monitoring", "successful", "unsuccessful", "rolled_back"
        }
        if self.status in approved_states and (not self.approved_by or not self.approved_at):
            raise ValueError("approved decisions require an approving actor and time")
        if self.status in {"successful", "unsuccessful"} and (
            self.measurement_ciphertext is None or self.measured_at is None
        ):
            raise ValueError("decision outcomes require measured evidence")
        return self


async def ensure_customer_learning_indexes(db: Any) -> None:
    analyses = getattr(db, CUSTOMER_MESSAGE_ANALYSES_COLLECTION)
    await analyses.create_index(
        [("user_id", 1), ("merchant_id", 1), ("analysis_id", 1)],
        unique=True,
        name="mezan_customer_message_analysis_identity_unique",
    )
    await analyses.create_index(
        [("user_id", 1), ("merchant_id", 1), ("source_message_id", 1)],
        unique=True,
        name="mezan_customer_message_analysis_source_unique",
    )
    await analyses.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("provider", 1),
            ("generated_at", -1),
        ],
        name="mezan_customer_message_analysis_provider_recent",
    )

    signals = getattr(db, CUSTOMER_SIGNALS_COLLECTION)
    await signals.create_index(
        [("user_id", 1), ("merchant_id", 1), ("signal_id", 1)],
        unique=True,
        name="mezan_customer_signals_identity_unique",
    )
    await signals.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("source_message_id", 1),
            ("signal_type", 1),
        ],
        unique=True,
        name="mezan_customer_signals_message_type_unique",
    )
    await signals.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("commercial_impact", 1),
            ("review_status", 1),
            ("updated_at", -1),
        ],
        name="mezan_customer_signals_commercial_queue",
    )

    problems = getattr(db, CUSTOMER_PROBLEMS_COLLECTION)
    await problems.create_index(
        [("user_id", 1), ("merchant_id", 1), ("problem_id", 1)],
        unique=True,
        name="mezan_customer_problems_identity_unique",
    )
    await problems.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("status", 1),
            ("severity", 1),
            ("updated_at", -1),
        ],
        name="mezan_customer_problems_progress_queue",
    )
    await problems.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("next_review_at", 1),
            ("progress_state", 1),
        ],
        name="mezan_customer_problems_review_due",
    )

    decisions = getattr(db, CUSTOMER_DECISIONS_COLLECTION)
    await decisions.create_index(
        [("user_id", 1), ("merchant_id", 1), ("decision_id", 1)],
        unique=True,
        name="mezan_customer_decisions_identity_unique",
    )
    await decisions.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("status", 1),
            ("updated_at", -1),
        ],
        name="mezan_customer_decisions_lifecycle",
    )


__all__ = [
    "CUSTOMER_DECISIONS_COLLECTION",
    "CUSTOMER_MESSAGE_ANALYSES_COLLECTION",
    "CUSTOMER_PROBLEMS_COLLECTION",
    "CUSTOMER_SIGNALS_COLLECTION",
    "CustomerDecisionRecord",
    "CustomerMessageAnalysisRecord",
    "CustomerProblemRecord",
    "CustomerSignalRecord",
    "ensure_customer_learning_indexes",
]
