"""Deterministic, recommendation-only decision ranking."""
from __future__ import annotations

from .models import DecisionRecommendation, DecisionSignal


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def rank_decisions(signals: list[DecisionSignal]) -> list[DecisionRecommendation]:
    """Rank signals without inventing missing profit or evidence."""
    recommendations: list[DecisionRecommendation] = []
    for signal in signals:
        confidence = _clamp(signal.confidence)
        urgency = _clamp(signal.urgency)
        effort = _clamp(signal.effort)
        evidence_ratio = min(1.0, max(0, signal.evidence_count) / 3.0)
        profit = signal.expected_profit_delta_sar

        if signal.evidence_count <= 0 or confidence < 0.5:
            action = "WATCH"
            score = 0.0
            reason = "Insufficient evidence or confidence; collect more evidence before action."
        elif profit is None:
            action = "TEST"
            score = round((confidence * 35) + (evidence_ratio * 25) + (urgency * 20) - (effort * 10), 2)
            reason = "Evidence exists, but expected profit impact is still unknown; run a bounded test."
        elif profit <= 0:
            action = "DO_NOT_INTERVENE"
            score = round(max(0.0, (confidence * 20) + (evidence_ratio * 15) - (effort * 10)), 2)
            reason = "Measured expected profit impact is non-positive."
        else:
            profit_component = min(40.0, profit / 500.0)
            score = round(min(100.0, (confidence * 30) + (evidence_ratio * 20) + (urgency * 15) + profit_component - (effort * 10)), 2)
            action = "EXECUTE_NOW" if score >= 70 else "TEST"
            reason = "Positive measured profit impact with sufficient evidence; owner approval is still required."

        recommendations.append(
            DecisionRecommendation(
                signal_id=signal.signal_id,
                action=action,
                priority_score=score,
                confidence=confidence,
                expected_profit_delta_sar=profit,
                reason=reason,
                evidence=signal.evidence,
            )
        )

    return sorted(recommendations, key=lambda item: item.priority_score, reverse=True)
