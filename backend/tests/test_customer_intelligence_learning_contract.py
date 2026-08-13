"""Evidence and progress contracts for channel-neutral customer learning."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from customer_intelligence.foundation import (
    CUSTOMER_INTELLIGENCE_ANALYSIS_TARGETS,
    EMPLOYEE_RESPONSE_ANALYSIS_TARGETS,
    ConversationMessageRecord,
)
from customer_intelligence.learning_contract import (
    CustomerDecisionRecord,
    CustomerProblemRecord,
    CustomerSignalRecord,
)


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


def _message(**updates):
    data = {
        "user_id": "owner-learning",
        "merchant_id": "merchant-learning",
        "message_id": "message-learning",
        "conversation_id": "conversation-learning",
        "channel_id": "channel-learning",
        "customer_id": "customer-learning",
        "external_message_key": "external:v1:message-learning",
        "direction": "inbound",
        "sender_type": "customer",
        "content_type": "text",
        "content_ciphertext": b"encrypted-content",
        "source_event": "instagram.comments.comment",
        "occurred_at": NOW,
        "received_at": NOW,
        "analysis_status": "pending",
        "delivery_state": "received",
        "created_at": NOW,
    }
    data.update(updates)
    return ConversationMessageRecord(**data)


def test_every_inbound_message_is_queued_for_every_intelligence_target():
    message = _message()

    assert message.analysis_contract_version == 1
    assert message.analysis_requested_at == NOW
    assert tuple(message.analysis_targets) == CUSTOMER_INTELLIGENCE_ANALYSIS_TARGETS


def test_employee_echo_is_queued_only_for_response_quality_learning():
    message = _message(
        direction="outbound",
        sender_type="employee",
        analysis_status="pending",
        delivery_state="sent",
        analysis_targets=list(EMPLOYEE_RESPONSE_ANALYSIS_TARGETS),
    )

    assert tuple(message.analysis_targets) == EMPLOYEE_RESPONSE_ANALYSIS_TARGETS
    assert "customer_signal" not in message.analysis_targets
    assert message.analysis_requested_at == NOW


def test_signal_must_cite_its_immutable_source_message():
    with pytest.raises(ValueError, match="source_message_id"):
        CustomerSignalRecord(
            user_id="owner-learning",
            merchant_id="merchant-learning",
            signal_id="signal-learning",
            customer_id="customer-learning",
            conversation_id="conversation-learning",
            source_message_id="message-learning",
            channel_id="channel-learning",
            provider="instagram",
            surface="comment",
            signal_type="complaint",
            commercial_impact="retention_risk",
            urgency="high",
            confidence=0.91,
            analysis_ciphertext=b"encrypted-analysis",
            evidence_refs=["another-message"],
            created_at=NOW,
            updated_at=NOW,
        )


def test_problem_cannot_be_marked_resolved_without_baseline_and_outcome():
    with pytest.raises(ValueError, match="baseline"):
        CustomerProblemRecord(
            user_id="owner-learning",
            merchant_id="merchant-learning",
            problem_id="problem-learning",
            problem_type="fulfillment",
            status="resolved",
            severity="high",
            first_detected_at=NOW,
            last_detected_at=NOW,
            occurrence_count=3,
            affected_customer_count=2,
            evidence_refs=["message-learning"],
            problem_ciphertext=b"encrypted-problem",
            progress_state="complete",
            baseline_recorded=False,
            outcome_recorded=False,
            confidence=0.88,
            created_at=NOW,
            updated_at=NOW,
        )


def test_decision_outcome_requires_approval_and_measured_evidence():
    with pytest.raises(ValueError, match="approving actor"):
        CustomerDecisionRecord(
            user_id="owner-learning",
            merchant_id="merchant-learning",
            decision_id="decision-learning",
            decision_type="problem_resolution",
            status="successful",
            evidence_refs=["message-learning"],
            problem_refs=["problem-learning"],
            recommendation_ciphertext=b"encrypted-recommendation",
            measurement_ciphertext=b"encrypted-measurement",
            confidence=0.8,
            risk="medium",
            required_approval="owner",
            measured_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
