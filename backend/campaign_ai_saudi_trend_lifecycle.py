"""Deterministic Saudi Trend Score and product lifecycle detector.

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
    elif observation_count >= 2 and recent >= 75 and abs(momentum) < 8:
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
        "trend_decay" if stage in {"declining", "ended"}
        else "early_uncertainty" if confidence == "low"
        else "late_entry" if stage in {"peak_or_plateau", "cooling"}
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
