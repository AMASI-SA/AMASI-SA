from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

module = r'''"""Deterministic Saudi Trend Score and product lifecycle detector.

Read-only evidence interpreter for Saudi Product Radar. It measures momentum,
acceleration, freshness, evidence depth, and source diversity. It never treats a
single external-market signal as proof of Saudi demand and performs no writes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

CONTRACT_VERSION = "saudi_trend_lifecycle_v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def trend_score_and_lifecycle(
    observations: list[dict[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    """Return a bounded trend score and lifecycle state from Saudi observations."""
    rows: list[dict[str, Any]] = []
    for raw in observations:
        observed_on = raw.get("observed_on")
        score = _number(raw.get("score"))
        if not isinstance(observed_on, date) or score is None or observed_on > as_of:
            continue
        rows.append({
            "observed_on": observed_on,
            "score": _clamp(score),
            "source": str(raw.get("source") or "unknown"),
        })
    rows.sort(key=lambda item: item["observed_on"])
    if not rows:
        return {
            "contract_version": CONTRACT_VERSION,
            "trend_score": None,
            "state": "insufficient_evidence",
            "confidence": "low",
            "momentum": None,
            "acceleration": None,
            "days_since_latest_signal": None,
            "estimated_wave_stage": "unknown",
            "risk": "unknown",
            "evidence": {"observations": 0, "sources": 0},
        }

    latest_day = rows[-1]["observed_on"]
    age_days = max(0, (as_of - latest_day).days)
    last_3 = [r["score"] for r in rows if r["observed_on"] >= as_of - timedelta(days=2)]
    last_7 = [r["score"] for r in rows if r["observed_on"] >= as_of - timedelta(days=6)]
    prev_7 = [r["score"] for r in rows if as_of - timedelta(days=13) <= r["observed_on"] < as_of - timedelta(days=6)]
    prev_14 = [r["score"] for r in rows if as_of - timedelta(days=27) <= r["observed_on"] < as_of - timedelta(days=13)]

    recent = _avg(last_7) if last_7 else rows[-1]["score"]
    prior = _avg(prev_7)
    older = _avg(prev_14)
    momentum = (recent - prior) if prior is not None else (recent - rows[0]["score"] if len(rows) > 1 else 0.0)
    prior_momentum = (prior - older) if prior is not None and older is not None else 0.0
    acceleration = momentum - prior_momentum

    source_count = len({r["source"] for r in rows})
    observation_count = len(rows)
    evidence_depth = min(20.0, observation_count * 2.0)
    diversity = min(10.0, source_count * 2.5)
    freshness = max(0.0, 15.0 - age_days * 3.0)
    momentum_component = _clamp(25.0 + momentum * 1.25, 0.0, 40.0)
    acceleration_component = _clamp(7.5 + acceleration * 0.5, 0.0, 15.0)
    level_component = _clamp(recent * 0.20, 0.0, 20.0)
    score = _clamp(
        level_component + momentum_component + acceleration_component
        + freshness + evidence_depth * 0.5 + diversity * 0.5
    )

    if age_days >= 7 or recent < 20:
        state = "trend_ended"
        stage = "ended"
    elif recent >= 70 and momentum <= -8:
        state = "falling"
        stage = "cooling"
    elif recent >= 75 and abs(momentum) < 8:
        state = "stable"
        stage = "peak_or_plateau"
    elif momentum >= 12 and acceleration >= 3:
        state = "rising"
        stage = "accelerating"
    elif momentum >= 8:
        state = "rising"
        stage = "emerging"
    elif momentum <= -10:
        state = "falling"
        stage = "declining"
    else:
        state = "stable"
        stage = "developing"

    confidence = (
        "high" if observation_count >= 8 and source_count >= 2 and age_days <= 2
        else "medium" if observation_count >= 4 and age_days <= 4
        else "low"
    )
    risk = (
        "late_entry" if stage in {"peak_or_plateau", "cooling"}
        else "trend_decay" if stage in {"declining", "ended"}
        else "early_uncertainty" if confidence == "low"
        else "normal"
    )
    wave_age_days = max(0, (latest_day - rows[0]["observed_on"]).days)
    return {
        "contract_version": CONTRACT_VERSION,
        "trend_score": round(score, 2),
        "state": state,
        "confidence": confidence,
        "momentum": round(momentum, 2),
        "acceleration": round(acceleration, 2),
        "days_since_latest_signal": age_days,
        "observed_wave_age_days": wave_age_days,
        "estimated_wave_stage": stage,
        "risk": risk,
        "evidence": {
            "observations": observation_count,
            "sources": source_count,
            "recent_7d_avg": round(recent, 2),
            "prior_7d_avg": round(prior, 2) if prior is not None else None,
            "last_3d_avg": round(_avg(last_3), 2) if last_3 else None,
        },
    }


def rank_products_by_trend(
    grouped_observations: dict[str, list[dict[str, Any]]],
    *,
    as_of: date,
) -> list[dict[str, Any]]:
    ranked = []
    for product_key, observations in grouped_observations.items():
        result = trend_score_and_lifecycle(observations, as_of=as_of)
        ranked.append({"product_key": product_key, **result})
    ranked.sort(key=lambda item: (item.get("trend_score") is not None, item.get("trend_score") or -1), reverse=True)
    return ranked


__all__ = ["CONTRACT_VERSION", "rank_products_by_trend", "trend_score_and_lifecycle"]
'''

(ROOT / "backend/campaign_ai_saudi_trend_lifecycle.py").write_text(module, encoding="utf-8")

radar_path = ROOT / "backend/campaign_ai_saudi_product_radar.py"
radar = radar_path.read_text(encoding="utf-8")
old = '''        lifecycle = classify_lifecycle(\n            [(item["observed_on"], item["score"]) for item in rows], as_of=as_of\n        )\n        recent = [item["score"] for item in rows if item["observed_on"] >= as_of - timedelta(days=6)]\n        score = round(sum(recent) / len(recent), 2) if recent else round(latest["score"], 2)\n'''
new = '''        from campaign_ai_saudi_trend_lifecycle import trend_score_and_lifecycle\n        trend = trend_score_and_lifecycle(rows, as_of=as_of)\n        lifecycle = {\n            "state": trend["state"],\n            "delta": trend["momentum"],\n            "confidence": trend["confidence"],\n        }\n        recent = [item["score"] for item in rows if item["observed_on"] >= as_of - timedelta(days=6)]\n        score = round(sum(recent) / len(recent), 2) if recent else round(latest["score"], 2)\n'''
if old not in radar:
    raise SystemExit("radar lifecycle block not found")
radar = radar.replace(old, new, 1)
old_entry = '''            "saudi_opportunity_score": score,\n            "lifecycle": lifecycle,\n            "latest_price_sar": latest.get("price_sar"),\n'''
new_entry = '''            "saudi_opportunity_score": score,\n            "saudi_trend_score": trend.get("trend_score"),\n            "lifecycle": lifecycle,\n            "trend_lifecycle": trend,\n            "latest_price_sar": latest.get("price_sar"),\n'''
if old_entry not in radar:
    raise SystemExit("radar entry block not found")
radar = radar.replace(old_entry, new_entry, 1)
old_sort = '''            item["lifecycle"]["state"] == "rising",\n            item["saudi_opportunity_score"],\n'''
new_sort = '''            item["lifecycle"]["state"] == "rising",\n            item.get("saudi_trend_score") or -1,\n            item["saudi_opportunity_score"],\n'''
if old_sort not in radar:
    raise SystemExit("radar sort block not found")
radar = radar.replace(old_sort, new_sort, 1)
radar_path.write_text(radar, encoding="utf-8")

tests = r'''from datetime import date, timedelta

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
'''
(ROOT / "backend/tests/test_campaign_ai_saudi_trend_lifecycle.py").write_text(tests, encoding="utf-8")

print("wrote backend/campaign_ai_saudi_trend_lifecycle.py")
print("patched backend/campaign_ai_saudi_product_radar.py")
print("wrote backend/tests/test_campaign_ai_saudi_trend_lifecycle.py")
