from __future__ import annotations

import json

import pytest

from integrations_control_center.snapchat_adaptive_decision_ai import (
    judge_adaptive_snapchat_decision,
    judge_adaptive_snapchat_decisions,
)


class Responses:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output_text": json.dumps(self.payload)})()


class Client:
    def __init__(self, payload):
        self.responses = Responses(payload)


def judgment(action="observe", safe=False):
    return {
        "recommended_action": action,
        "entity_type": "campaign",
        "entity_id": "campaign-1",
        "confidence": 0.74,
        "reason_ar": "المبيعات تتحسن والدليل الحالي لا يبرر تغييرًا الآن.",
        "primary_objective": "grow_sales_while_protecting_contribution_profit",
        "expected_outcome": [],
        "evidence_used": ["entity_evidence.metrics"],
        "evidence_not_used": ["user_suggestion:payday"],
        "uncertainties": ["manual_orders_source_unresolved"],
        "recent_improvement_treatment": "تمت مراعاته كدليل مساند.",
        "safe_to_prepare_proposal": safe,
    }


@pytest.mark.asyncio
async def test_adaptive_judgment_uses_strict_schema_and_never_writes():
    client = Client(judgment())
    result = await judge_adaptive_snapchat_decision(
        {"entity_evidence": {"entity_id": "campaign-1"}},
        client_factory=lambda: client,
    )

    assert result["judgment"]["recommended_action"] == "observe"
    assert result["judgment"]["safe_to_prepare_proposal"] is False
    assert result["provider_write_reached"] is False
    assert result["proposal_created"] is False
    request = client.responses.calls[0]
    assert request["text"]["format"]["strict"] is True
    assert "لا تستخدم قاعدة ثابتة" in request["instructions"]


@pytest.mark.asyncio
async def test_observe_cannot_be_marked_safe_for_proposal_even_if_model_says_so():
    client = Client(judgment(safe=True))
    result = await judge_adaptive_snapchat_decision(
        {"entity_evidence": {"entity_id": "campaign-1"}},
        client_factory=lambda: client,
    )

    assert result["judgment"]["safe_to_prepare_proposal"] is False


@pytest.mark.asyncio
async def test_batch_review_uses_one_provider_call_and_discards_invented_entities():
    second = {**judgment("investigate", safe=True), "entity_id": "campaign-2"}
    invented = {**judgment("pause", safe=True), "entity_id": "invented"}
    client = Client({"judgments": [judgment(safe=True), second, invented]})

    results = await judge_adaptive_snapchat_decisions(
        [
            {
                "entity_evidence": {
                    "entity_type": "campaign",
                    "entity_id": "campaign-1",
                }
            },
            {
                "entity_evidence": {
                    "entity_type": "campaign",
                    "entity_id": "campaign-2",
                }
            },
        ],
        client_factory=lambda: client,
    )

    assert len(client.responses.calls) == 1
    assert [row["judgment"]["entity_id"] for row in results] == [
        "campaign-1",
        "campaign-2",
    ]
    assert all(row["judgment"]["safe_to_prepare_proposal"] is False for row in results)
    assert all(row["proposal_created"] is False for row in results)
