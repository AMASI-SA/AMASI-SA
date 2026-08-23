from backend.autonomous_decision_intelligence import build_decision_recommendation


def test_recommendation_is_read_only():
    result = build_decision_recommendation({"confidence": "high"})
    assert result["execution_allowed"] is False
    assert result["evidence_required"] is True
