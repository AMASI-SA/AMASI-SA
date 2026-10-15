from decision_intelligence.approval_workflow import create_approval_request, resolve_approval
from decision_intelligence.models import DecisionRecommendation


def _recommendation():
    return DecisionRecommendation(
        signal_id="decision-1",
        action="TEST",
        priority_score=55,
        confidence=0.8,
        expected_profit_delta_sar=500,
        reason="bounded test",
        evidence=(),
    )


def test_approval_never_executes_action():
    request = create_approval_request(_recommendation())
    assert request.state == "PENDING"
    assert request.execution_performed is False

    approved = resolve_approval(request, approved=True)
    assert approved.state == "APPROVED"
    assert approved.execution_performed is False
    assert approved.requires_owner_approval is True
