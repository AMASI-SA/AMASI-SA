from campaign_ai_decision_policy_v2 import (
    CONTRACT_VERSION,
    POLICY_MODE,
    build_observation_plan,
    evaluate_decision_readiness,
    historical_context_windows,
)


def test_low_exposure_waits_in_hours_not_fixed_day_buckets():
    result = evaluate_decision_readiness(
        spend=25,
        conversions=0,
        target_cpa=100,
        hours_since_material_change=4,
        data_complete=True,
    )

    assert result["contract_version"] == CONTRACT_VERSION
    assert result["policy_mode"] == POLICY_MODE
    assert result["decision_ready"] is False
    assert result["recommended_wait_hours"] == 6
    assert result["fixed_day_buckets_used"] is False
    assert result["historical_context_days"] == []


def test_same_age_campaign_can_be_ready_when_evidence_is_strong():
    result = evaluate_decision_readiness(
        spend=220,
        conversions=0,
        target_cpa=100,
        hours_since_material_change=4,
        data_complete=True,
    )

    assert result["decision_ready"] is True
    assert result["recommended_wait_hours"] is None
    assert "spend_exposure_sufficient" in result["reasons"]


def test_conversion_signal_can_make_decision_ready_without_calendar_wait():
    result = evaluate_decision_readiness(
        spend=80,
        conversions=3,
        target_cpa=100,
        hours_since_material_change=3,
        data_complete=True,
    )

    assert result["decision_ready"] is True
    assert "conversion_signal_sufficient" in result["reasons"]


def test_recent_material_change_gets_short_stabilization_window():
    result = evaluate_decision_readiness(
        spend=500,
        conversions=8,
        target_cpa=100,
        hours_since_material_change=0.5,
        data_complete=True,
    )

    assert result["decision_ready"] is False
    assert result["recommended_wait_hours"] == 2
    assert result["reasons"] == ["recent_material_change_needs_stabilization"]


def test_unknown_evidence_stays_unknown_and_blocks_decisive_recommendation():
    result = evaluate_decision_readiness(
        spend=None,
        conversions=None,
        target_cpa=None,
        hours_since_material_change=None,
        data_complete=False,
    )

    assert result["decision_ready"] is False
    assert result["recommended_wait_hours"] is None
    assert result["metrics"]["spend"] is None
    assert result["metrics"]["conversions"] is None
    assert set(result["evidence_gaps"]) == {
        "spend",
        "conversions",
        "target_cpa",
        "hours_since_material_change",
        "data_quality",
    }


def test_seven_and_thirty_day_windows_are_context_only():
    assert historical_context_windows(enabled=False) == []
    assert historical_context_windows(enabled=True) == [7, 30]

    result = evaluate_decision_readiness(
        spend=250,
        conversions=4,
        target_cpa=100,
        hours_since_material_change=5,
        data_complete=True,
        historical_context=True,
    )

    assert result["decision_ready"] is True
    assert result["historical_context_days"] == [7, 30]
    assert result["historical_context_is_decision_gate"] is False


def test_policy_is_recommendation_only_and_never_executes_provider_action():
    readiness = evaluate_decision_readiness(
        spend=250,
        conversions=4,
        target_cpa=100,
        hours_since_material_change=5,
        data_complete=True,
    )
    plan = build_observation_plan(readiness)

    assert readiness["read_only"] is True
    assert readiness["execution_allowed"] is False
    assert plan["read_only"] is True
    assert plan["execution_allowed"] is False
    assert plan["calendar_day_gate"] is None
    assert plan["status"] == "ready_for_recommendation"


def test_wait_cadence_adapts_to_evidence_strength():
    weak = evaluate_decision_readiness(
        spend=20,
        conversions=0,
        target_cpa=100,
        hours_since_material_change=5,
        data_complete=True,
    )
    medium = evaluate_decision_readiness(
        spend=60,
        conversions=0,
        target_cpa=100,
        hours_since_material_change=5,
        data_complete=True,
    )
    near = evaluate_decision_readiness(
        spend=120,
        conversions=0,
        target_cpa=100,
        hours_since_material_change=5,
        data_complete=True,
    )

    assert weak["recommended_wait_hours"] == 6
    assert medium["recommended_wait_hours"] == 4
    assert near["recommended_wait_hours"] == 2
