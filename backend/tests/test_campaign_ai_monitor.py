import asyncio

from campaign_ai_monitor import (
    DEFAULT_INITIAL_DELAY_SECONDS,
    RecommendationItem,
    RecommendationOutput,
    _deterministic_recommendations,
    _govern_output,
    _monitored_user_ids,
    _recommendation_explanation,
    deterministic_candidates,
)


def entity(**overrides):
    value = {
        "provider": "snapchat",
        "entity_level": "ad",
        "entity_id": "ad-1",
        "entity_name": "إعلان المنتج",
        "spend_sar": 120.0,
        "revenue_sar": 0.0,
        "purchases": 0,
        "roas": 0.0,
        "cpa_sar": None,
        "data_complete": True,
    }
    value.update(overrides)
    return value


def test_waste_without_purchase_is_highest_priority_signal():
    rows = deterministic_candidates([
        entity(entity_id="waste", spend_sar=140, purchases=0),
        entity(entity_id="watch", spend_sar=80, purchases=2, cpa_sar=40, roas=1.2),
    ])

    assert rows[0]["entity_id"] == "waste"
    assert rows[0]["screening_signal"] == "rapid_spend_without_results"
    assert rows[0]["spend_per_day_sar"] == 140


def test_slower_accumulated_spend_is_waste_but_not_rapid_spend():
    rows = deterministic_candidates([
        entity(entity_id="slow-waste", spend_sar=140, purchases=0, observed_days=3),
    ])

    assert rows[0]["screening_signal"] == "waste_without_purchase"


def test_profitable_entity_is_selected_as_scale_candidate_only_with_volume():
    rows = deterministic_candidates([
        entity(entity_id="scale", spend_sar=150, revenue_sar=600, purchases=4, roas=4, cpa_sar=37.5),
        entity(entity_id="too-early", spend_sar=40, revenue_sar=160, purchases=2, roas=4, cpa_sar=20),
    ])

    assert [row["entity_id"] for row in rows] == ["scale"]
    assert rows[0]["screening_signal"] == "scale_candidate"


def test_zero_spend_entities_never_reach_openai_candidates():
    assert deterministic_candidates([entity(spend_sar=0, purchases=0)]) == []


def test_paused_entity_is_not_recommended_for_another_change():
    assert deterministic_candidates([entity(status="PAUSED", active=False)]) == []


def test_model_cannot_scale_an_entity_without_scale_evidence():
    candidate = entity(entity_id="waste", spend_sar=140, purchases=0)
    candidate.update(screening_signal="rapid_spend_without_results")
    output = RecommendationOutput(
        summary="اختبار",
        recommendations=[RecommendationItem(
            recommendation_id="invented",
            provider="snapchat",
            entity_level="ad",
            entity_id="waste",
            entity_name="اسم غير موثوق",
            action="scale",
            change_percent=30,
            priority="high",
            confidence="high",
            title="اختبار",
            rationale="اختبار الحماية",
            evidence=["اختبار"],
            guardrail="مراجعة",
            next_check_at="wrong",
        )],
    )

    governed = _govern_output(output, [candidate], next_check_at="2026-08-16T03:00:00+00:00")

    assert governed.recommendations[0].action == "monitor"
    assert governed.recommendations[0].change_percent is None
    assert governed.recommendations[0].entity_name == "إعلان المنتج"


def test_scheduler_discovers_connected_v2_ad_account_owner():
    class Accounts:
        async def distinct(self, field, query):
            assert field == "user_id"
            assert query == {
                "provider": {"$in": ["snapchat_ads", "meta_ads"]},
                "connection_status": {"$in": ["connected", "needs_reauth"]},
            }
            return ["owner-1"]

    class DB:
        mezan_integration_accounts_v2 = Accounts()

    assert asyncio.run(_monitored_user_ids(DB())) == ["owner-1"]


def test_first_monitor_pass_starts_promptly_after_boot():
    assert DEFAULT_INITIAL_DELAY_SECONDS <= 10


def test_invalid_model_output_can_fall_back_to_safe_waste_recommendation():
    candidate = deterministic_candidates([
        entity(entity_id="fast-waste", spend_sar=180, purchases=0),
    ])[0]

    result = _deterministic_recommendations(
        [candidate],
        next_check_at="2026-08-16T15:00:00+00:00",
        limitation="openai_recommendation:ValidationError",
    )

    assert result.recommendations[0].action == "reduce"
    assert result.recommendations[0].change_percent == 20
    assert result.recommendations[0].recommendation_id == "snapchat:ad:fast-waste"
    assert result.recommendations[0].next_check_at == "2026-08-16T15:00:00+00:00"
    assert result.limitations == ["openai_recommendation:ValidationError"]


def test_fallback_never_scales_incomplete_data():
    candidate = entity(
        entity_id="scale-incomplete",
        spend_sar=150,
        revenue_sar=600,
        purchases=4,
        roas=4,
        cpa_sar=37.5,
        data_complete=False,
    )
    candidate["screening_signal"] = "scale_candidate"

    result = _deterministic_recommendations(
        [candidate],
        next_check_at="2026-08-16T15:00:00+00:00",
        limitation="fallback",
    )

    assert result.recommendations[0].action == "monitor"
    assert result.recommendations[0].change_percent is None


def test_recommendation_explanation_states_reason_wait_and_success_criteria():
    candidate = deterministic_candidates([
        entity(entity_id="fast-waste", spend_sar=180, purchases=0),
    ])[0]
    item = _deterministic_recommendations(
        [candidate],
        next_check_at="2026-08-16T15:00:00+00:00",
        limitation="fallback",
    ).recommendations[0]

    explanation = _recommendation_explanation(item, candidate)

    assert explanation["decision_signal"] == "rapid_spend_without_results"
    assert explanation["recommended_wait_hours"] == 2
    assert "اصبر 2 ساعات" in explanation["observation_plan"]
    assert "صرف 180.00 ر.س" in explanation["decision_facts"]
    assert len(explanation["success_criteria"]) == 3
    assert explanation["financial_impact"]["period_estimated_contribution_sar"] == -180
    assert explanation["financial_impact"]["forecast_delta_sar"] > 0
    assert explanation["financial_impact"]["is_estimate"] is True
