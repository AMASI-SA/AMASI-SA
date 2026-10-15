from decision_intelligence.decision_engine import rank_decisions
from decision_intelligence.models import DecisionSignal


def test_unknown_evidence_stays_watch():
    result = rank_decisions([
        DecisionSignal(
            signal_id="s1",
            source="snapchat_v2",
            title="candidate",
            evidence_count=0,
            confidence=0.9,
        )
    ])[0]
    assert result.action == "WATCH"
    assert result.priority_score == 0
    assert result.read_only is True


def test_positive_measured_profit_can_rank_higher():
    ranked = rank_decisions([
        DecisionSignal("low", "store", "low", 3, 0.8, 100, 0.2, 0.5),
        DecisionSignal("high", "store", "high", 3, 0.9, 10000, 0.9, 0.1),
    ])
    assert ranked[0].signal_id == "high"
    assert ranked[0].expected_profit_delta_sar == 10000
    assert ranked[0].requires_owner_approval is True
