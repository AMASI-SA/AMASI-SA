"""Recommendation-only autonomous decision intelligence foundation."""


def build_decision_recommendation(signals: dict) -> dict:
    return {
        "action": "review",
        "confidence": signals.get("confidence", "unknown"),
        "evidence_required": True,
        "execution_allowed": False,
    }
