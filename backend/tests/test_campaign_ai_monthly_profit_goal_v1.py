from datetime import date

from campaign_ai_monthly_profit_goal_v1 import _derive_goal_progress


def _goal(target=100_000.0):
    return {
        "minimum_net_profit_sar": target,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-08-20T00:00:00+00:00",
    }


def test_monthly_goal_marks_behind_target_and_calculates_required_daily_profit():
    result = _derive_goal_progress(
        goal=_goal(),
        month_to_date={"available": True, "net_profit": 50_000.0},
        end=date(2026, 8, 20),
    )

    assert result["status"] == "behind_target"
    assert result["phase"] == "recover_profit_gap"
    assert result["days_remaining"] == 11
    assert result["remaining_to_target_sar"] == 50_000.0
    assert result["required_daily_net_profit_sar"] == 4545.45
    assert result["projected_month_end_net_profit_sar"] == 77_500.0
    assert result["projected_gap_sar"] == -22_500.0


def test_monthly_goal_marks_on_track_without_claiming_target_is_already_covered():
    result = _derive_goal_progress(
        goal=_goal(),
        month_to_date={"available": True, "net_profit": 70_000.0},
        end=date(2026, 8, 20),
    )

    assert result["status"] == "on_track"
    assert result["phase"] == "protect_target_path"
    assert result["net_profit_to_date_sar"] == 70_000.0
    assert result["projected_month_end_net_profit_sar"] == 108_500.0
    assert result["remaining_to_target_sar"] == 30_000.0


def test_monthly_goal_allows_expansion_only_after_minimum_is_covered():
    result = _derive_goal_progress(
        goal=_goal(),
        month_to_date={"available": True, "net_profit": 120_000.0},
        end=date(2026, 8, 20),
    )

    assert result["status"] == "minimum_target_covered"
    assert result["phase"] == "expand_above_floor"
    assert result["remaining_to_target_sar"] == 0.0


def test_monthly_goal_fails_closed_when_profit_data_is_missing():
    result = _derive_goal_progress(
        goal=_goal(),
        month_to_date={"available": False, "net_profit": None},
        end=date(2026, 8, 20),
    )

    assert result["progress_available"] is False
    assert result["status"] == "profit_data_unavailable"
    assert result["phase"] == "protect_data_quality"
    assert result["projected_month_end_net_profit_sar"] is None
