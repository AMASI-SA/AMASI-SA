"""Recommendation-only autonomous decision intelligence v2 foundation.

Collects decision signals and ranks opportunities without executing changes.
"""

from dataclasses import dataclass


@dataclass
class DecisionSignal:
    name: str
    confidence: float
    evidence_count: int
    impact_score: float


def rank_decision_signals(signals: list[DecisionSignal]) -> list[DecisionSignal]:
    return sorted(
        signals,
        key=lambda item: (item.impact_score, item.confidence, item.evidence_count),
        reverse=True,
    )
