from datetime import date, timedelta

from campaign_ai_saudi_trend_lifecycle import trend_score_and_lifecycle


def _rows(scores, *, start=date(2026, 8, 1), source="saudi_market"):
    return [
        {"observed_on": start + timedelta(days=i), "score": score, "source": source}
        for i, score in enumerate(scores)
    ]


def test_accelerating_trend_is_rising():
    rows = _rows([20, 24, 28, 34, 42, 52, 65, 78, 88])
    result = trend_score_and_lifecycle(rows, as_of=date(2026, 8, 9))
    assert result["state"] == "rising"
    assert result["estimated_wave_stage"] in {"accelerating", "emerging"}
    assert result["trend_score"] >= 60


def test_high_but_flat_trend_is_peak_or_plateau():
    rows = _rows([78, 80, 79, 81, 80, 82, 79, 80])
    result = trend_score_and_lifecycle(rows, as_of=date(2026, 8, 8))
    assert result["state"] == "stable"
    assert result["estimated_wave_stage"] == "peak_or_plateau"
    assert result["risk"] == "late_entry"


def test_falling_trend_detected_before_it_hits_zero():
    rows = _rows([90, 88, 84, 80, 72, 60, 48, 38, 30])
    result = trend_score_and_lifecycle(rows, as_of=date(2026, 8, 9))
    assert result["state"] == "falling"
    assert result["estimated_wave_stage"] in {"cooling", "declining"}


def test_stale_signal_is_trend_ended():
    rows = _rows([60, 68, 72])
    result = trend_score_and_lifecycle(rows, as_of=date(2026, 8, 20))
    assert result["state"] == "trend_ended"
    assert result["risk"] == "trend_decay"


def test_single_signal_remains_low_confidence():
    rows = _rows([85])
    result = trend_score_and_lifecycle(rows, as_of=date(2026, 8, 1))
    assert result["confidence"] == "low"
    assert result["risk"] == "early_uncertainty"


def test_multi_source_evidence_increases_confidence():
    rows = _rows([35, 42, 50, 58], source="saudi_search")
    rows += [
        {"observed_on": date(2026, 8, 2) + timedelta(days=i), "score": score, "source": "saudi_competitor"}
        for i, score in enumerate([40, 48, 56, 64])
    ]
    result = trend_score_and_lifecycle(rows, as_of=date(2026, 8, 5))
    assert result["evidence"]["sources"] == 2
    assert result["confidence"] in {"medium", "high"}


def test_contract_is_read_only_metric_interpretation():
    result = trend_score_and_lifecycle([], as_of=date(2026, 8, 22))
    assert result["contract_version"] == "saudi_trend_lifecycle_v1"
    assert "action" not in result
