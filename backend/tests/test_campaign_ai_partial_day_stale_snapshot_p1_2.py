from datetime import date, datetime, timezone

import campaign_ai_monitor_legacy as legacy
import campaign_ai_time_window_quality as quality


def test_p1_2_current_local_day_is_marked_partial_and_not_safe_for_scale():
    row = {
        "source_date_from": "2026-08-19",
        "source_date_to": "2026-08-21",
        "account_timezone": "Asia/Riyadh",
    }
    result = quality.window_quality(
        row, now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    )
    assert result["contains_open_current_day"] is True
    assert result["completed_days"] == 2
    assert result["safe_for_scale_comparison"] is False
    assert 0 < result["open_day_elapsed_fraction"] < 1


def test_p1_2_completed_historical_window_is_safe_for_scale():
    row = {
        "source_date_from": "2026-08-18",
        "source_date_to": "2026-08-20",
        "account_timezone": "Asia/Riyadh",
    }
    result = quality.window_quality(
        row, now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    )
    assert result["contains_open_current_day"] is False
    assert result["completed_days"] == 3
    assert result["safe_for_scale_comparison"] is True


def test_p1_2_history_uses_completed_days_only():
    start, end = quality.completed_history_window(date(2026, 8, 21), 7)
    assert start == date(2026, 8, 14)
    assert end == date(2026, 8, 20)


def test_p1_2_scale_snapshot_is_stricter_than_defensive_action():
    assert quality.snapshot_max_age_minutes("scale") == 90
    assert quality.snapshot_max_age_minutes("reduce") == 300
    assert quality.snapshot_max_age_minutes("pause") == 300


def test_p1_2_govern_downgrades_scale_when_window_contains_open_day():
    candidate = {
        "provider": "meta",
        "entity_level": "campaign",
        "entity_id": "c1",
        "entity_name": "Campaign",
        "account_id": "a1",
        "account_name": "Account",
        "active": True,
        "data_complete": True,
        "purchases": 5,
        "scale_comparison_safe": False,
    }
    item = legacy.RecommendationItem(
        recommendation_id="r1", provider="meta", entity_level="campaign",
        entity_id="c1", entity_name="Campaign", account_id="a1",
        account_name="Account", parent_name=None, action="scale",
        change_percent=15, priority="high", confidence="high", title="Scale",
        rationale="r", evidence=[], why_now="n", recommended_wait_hours=5,
        observation_plan="o", success_criteria=[], risk_if_ignored="risk",
        guardrail="g", next_check_at="2026-08-21T20:00:00+00:00",
    )
    output = legacy.RecommendationOutput(summary="s", recommendations=[item], limitations=[])
    governed = legacy._govern_output(
        output, [candidate], next_check_at="2026-08-21T20:00:00+00:00"
    )
    assert governed.recommendations[0].action == "monitor"
    assert governed.recommendations[0].change_percent is None
