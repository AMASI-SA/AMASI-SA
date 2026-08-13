"""Learning health exposes coverage counts, never customer content."""
from __future__ import annotations

import pytest

from customer_intelligence.foundation import CONVERSATION_MESSAGES_COLLECTION
from customer_intelligence.learning_contract import (
    CUSTOMER_DECISIONS_COLLECTION,
    CUSTOMER_MESSAGE_ANALYSES_COLLECTION,
    CUSTOMER_PROBLEMS_COLLECTION,
    CUSTOMER_SIGNALS_COLLECTION,
)
from customer_intelligence.learning_status import CustomerLearningStatusService


class CountCollection:
    def __init__(self, counts):
        self.counts = counts

    async def count_documents(self, query):
        if query.get("content_mode") == "metadata_only":
            return self.counts.get("metadata_only", 0)
        if query.get("direction") == "inbound":
            return self.counts.get("customer_total", 0)
        if query.get("direction") == "outbound":
            return self.counts.get("employee_total", 0)
        status = query.get("analysis_status") or query.get("status")
        if isinstance(status, dict):
            return self.counts.get("open", 0)
        return self.counts.get(status or "total", 0)


class FakeDb:
    pass


@pytest.mark.asyncio
async def test_learning_status_reports_measurable_analysis_coverage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "configured-but-never-returned")
    db = FakeDb()
    setattr(db, CONVERSATION_MESSAGES_COLLECTION, CountCollection({
        "customer_total": 8,
        "employee_total": 2,
        "pending": 2,
        "ready": 7,
        "failed": 1,
    }))
    setattr(db, CUSTOMER_SIGNALS_COLLECTION, CountCollection({"total": 13}))
    setattr(db, CUSTOMER_PROBLEMS_COLLECTION, CountCollection({"open": 3}))
    setattr(db, CUSTOMER_DECISIONS_COLLECTION, CountCollection({"proposed": 4}))
    setattr(db, CUSTOMER_MESSAGE_ANALYSES_COLLECTION, CountCollection({"metadata_only": 2}))

    result = await CustomerLearningStatusService(db).status(owner_user_id="owner-1")

    assert result.state == "attention_required"
    assert result.inbound_customer_messages == 8
    assert result.employee_responses == 2
    assert result.total_evidence_events == 10
    assert result.queue_coverage_percent == 100
    assert result.analysis_completion_percent == 70
    assert result.signals_detected == 13
    assert result.open_problems == 3
    assert result.proposed_decisions == 4
    assert result.metadata_only_media_events == 2
    assert "configured-but-never-returned" not in result.model_dump_json()
    assert result.customer_content_exposed is False
    assert result.automatic_execution_allowed is False
