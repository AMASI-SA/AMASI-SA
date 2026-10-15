"""Owner-approval workflow contracts. No external action execution occurs here."""
from __future__ import annotations

from .models import ApprovalRequest, DecisionRecommendation


def create_approval_request(recommendation: DecisionRecommendation) -> ApprovalRequest:
    return ApprovalRequest(
        decision_id=recommendation.signal_id,
        state="PENDING",
        requested_action=recommendation.action,
        rationale=recommendation.reason,
    )


def resolve_approval(request: ApprovalRequest, *, approved: bool) -> ApprovalRequest:
    return ApprovalRequest(
        decision_id=request.decision_id,
        state="APPROVED" if approved else "REJECTED",
        requested_action=request.requested_action,
        rationale=request.rationale,
        requires_owner_approval=True,
        execution_performed=False,
    )
