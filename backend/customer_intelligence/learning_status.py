"""Content-free health view for the customer-learning pipeline."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from .foundation import CONVERSATION_MESSAGES_COLLECTION
from .learning_contract import (
    CUSTOMER_DECISIONS_COLLECTION,
    CUSTOMER_MESSAGE_ANALYSES_COLLECTION,
    CUSTOMER_PROBLEMS_COLLECTION,
    CUSTOMER_SIGNALS_COLLECTION,
)
from .learning_worker import LEARNING_ENABLED_ENV


class CustomerLearningStatusPublic(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    state: Literal[
        "not_configured", "no_data", "healthy", "processing", "attention_required"
    ]
    runtime_configured: bool
    worker_enabled: bool
    inbound_customer_messages: int = Field(ge=0)
    employee_responses: int = Field(ge=0)
    total_evidence_events: int = Field(ge=0)
    queued_for_analysis: int = Field(ge=0)
    analyzed_messages: int = Field(ge=0)
    pending_messages: int = Field(ge=0)
    failed_messages: int = Field(ge=0)
    queue_coverage_percent: float = Field(ge=0, le=100)
    analysis_completion_percent: float = Field(ge=0, le=100)
    signals_detected: int = Field(ge=0)
    open_problems: int = Field(ge=0)
    proposed_decisions: int = Field(ge=0)
    metadata_only_media_events: int = Field(ge=0)
    customer_content_exposed: Literal[False] = False
    automatic_execution_allowed: Literal[False] = False


def _enabled() -> bool:
    return os.environ.get(LEARNING_ENABLED_ENV, "true").strip().casefold() in {
        "1", "true", "yes", "on"
    }


class CustomerLearningStatusService:
    def __init__(self, db: Any):
        self.db = db

    async def status(self, *, owner_user_id: str) -> CustomerLearningStatusPublic:
        customer_scope = {
            "user_id": owner_user_id,
            "direction": "inbound",
            "sender_type": "customer",
        }
        employee_scope = {
            "user_id": owner_user_id,
            "direction": "outbound",
            "sender_type": "employee",
        }
        scope = {"$or": [customer_scope, employee_scope]}
        messages = getattr(self.db, CONVERSATION_MESSAGES_COLLECTION)
        customer_total = await messages.count_documents(customer_scope)
        employee_total = await messages.count_documents(employee_scope)
        total = customer_total + employee_total
        pending = await messages.count_documents({**scope, "analysis_status": "pending"})
        ready = await messages.count_documents({**scope, "analysis_status": "ready"})
        failed = await messages.count_documents({**scope, "analysis_status": "failed"})
        queued = pending + ready + failed

        tenant = {"user_id": owner_user_id}
        signals = await getattr(self.db, CUSTOMER_SIGNALS_COLLECTION).count_documents(tenant)
        open_problems = await getattr(self.db, CUSTOMER_PROBLEMS_COLLECTION).count_documents(
            {**tenant, "status": {"$nin": ["resolved", "dismissed"]}}
        )
        proposed_decisions = await getattr(
            self.db, CUSTOMER_DECISIONS_COLLECTION
        ).count_documents({**tenant, "status": "proposed"})
        metadata_only = await getattr(
            self.db, CUSTOMER_MESSAGE_ANALYSES_COLLECTION
        ).count_documents({**tenant, "content_mode": "metadata_only"})

        configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        enabled = _enabled()
        if not configured or not enabled:
            state = "not_configured"
        elif total == 0:
            state = "no_data"
        elif failed:
            state = "attention_required"
        elif pending:
            state = "processing"
        else:
            state = "healthy"

        return CustomerLearningStatusPublic(
            generated_at=datetime.now(timezone.utc),
            state=state,
            runtime_configured=configured,
            worker_enabled=enabled,
            inbound_customer_messages=customer_total,
            employee_responses=employee_total,
            total_evidence_events=total,
            queued_for_analysis=queued,
            analyzed_messages=ready,
            pending_messages=pending,
            failed_messages=failed,
            queue_coverage_percent=round((queued / total * 100) if total else 100.0, 2),
            analysis_completion_percent=round((ready / total * 100) if total else 100.0, 2),
            signals_detected=signals,
            open_problems=open_problems,
            proposed_decisions=proposed_decisions,
            metadata_only_media_events=metadata_only,
        )


__all__ = ["CustomerLearningStatusPublic", "CustomerLearningStatusService"]
