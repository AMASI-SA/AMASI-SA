from decision_intelligence.action_simulator import simulate_action


def test_simulator_is_read_only_and_keeps_unknown_unknown():
    result = simulate_action(
        scenario="raise-budget",
        current_profit_sar=1000,
        expected_profit_sar=None,
        confidence=0.8,
    )
    assert result.expected_profit_delta_sar is None
    assert result.downside_sar is None
    assert result.upside_sar is None
    assert result.read_only is True


def test_simulator_calculates_profit_delta_envelope():
    result = simulate_action(
        scenario="creative-test",
        current_profit_sar=1000,
        expected_profit_sar=1300,
        confidence=0.8,
        assumptions=["same product cost"],
    )
    assert result.expected_profit_delta_sar == 300
    assert result.downside_sar == 240
    assert result.upside_sar == 360
