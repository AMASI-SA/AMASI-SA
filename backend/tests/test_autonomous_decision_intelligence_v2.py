from autonomous_decision_intelligence_v2 import DecisionSignal, rank_decision_signals


def test_rank_decision_signals_by_impact():
    result = rank_decision_signals([
        DecisionSignal("low", 0.9, 3, 1),
        DecisionSignal("high", 0.8, 2, 10),
    ])
    assert result[0].name == "high"
