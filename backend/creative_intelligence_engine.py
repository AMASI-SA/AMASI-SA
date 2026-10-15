"""Creative Intelligence Engine foundation.

Recommendation-only analysis layer. No ad, product, or campaign writes.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class CreativeSignal:
    source: str
    metric: str
    value: float


@dataclass(frozen=True)
class CreativeRecommendation:
    area: str
    reason: str
    action: str


class CreativeIntelligenceEngine:
    def analyze(self, signals: List[CreativeSignal]) -> List[CreativeRecommendation]:
        recommendations: List[CreativeRecommendation] = []
        for signal in signals:
            if signal.metric == "low_video_completion":
                recommendations.append(
                    CreativeRecommendation(
                        area="video",
                        reason="Low completion signal detected",
                        action="Test a different creative structure",
                    )
                )
            if signal.metric == "high_click_low_conversion":
                recommendations.append(
                    CreativeRecommendation(
                        area="landing_page",
                        reason="Traffic converts below expectation",
                        action="Review product page friction",
                    )
                )
        return recommendations
