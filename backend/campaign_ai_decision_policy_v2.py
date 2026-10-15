"""Adaptive, read-only decision policy for Campaign AI.

The policy deliberately does not use fixed 1/3/7/30-day buckets as decision
rules.  Decision readiness is derived from evidence sufficiency, spend exposure,
conversion signal, data quality, and the time elapsed since the last material
change.  Seven- and thirty-day windows may be requested as historical context,
but they never gate a recommendation.
"""
from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "campaign_ai_decision_policy_v2"
POLICY_MODE = "adaptive_evidence_window"
HISTORICAL_CONTEXT_DAYS = (7, 30)


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return parsed


def _optional_nonnegative(value: Any) -> float | None:
    parsed = _optional_number(value)
    if parsed is None or parsed < 0:
        return None
    return parsed


def historical_context_windows(*, enabled: bool = False) -> list[int]:
    """Return optional trend windows; these values are never decision gates."""
    return list(HISTORICAL_CONTEXT_DAYS) if enabled else []


def evaluate_decision_readiness(
    *,
    spend: Any,
    conversions: Any,
    target_cpa: Any,
    hours_since_material_change: Any,
    data_complete: bool,
    historical_context: bool = False,
) -> dict[str, Any]:
    """Evaluate whether Campaign AI has enough evidence to recommend an action.

    The policy is intentionally conservative:
    - incomplete/unknown evidence blocks decisive recommendations;
    - low exposure waits for more spend or conversion evidence;
    - material changes receive a short stabilization window;
    - sufficient exposure can be evaluated within hours rather than waiting for
      an arbitrary calendar-day bucket.

    This function is pure and read-only.  It never executes an advertising
    provider action.
    """
    spend_value = _optional_nonnegative(spend)
    conversions_value = _optional_nonnegative(conversions)
    target_cpa_value = _optional_nonnegative(target_cpa)
    hours_value = _optional_nonnegative(hours_since_material_change)

    gaps: list[str] = []
    if spend_value is None:
        gaps.append("spend")
    if conversions_value is None:
        gaps.append("conversions")
    if target_cpa_value is None or target_cpa_value == 0:
        gaps.append("target_cpa")
    if hours_value is None:
        gaps.append("hours_since_material_change")
    if not data_complete:
        gaps.append("data_quality")

    reasons: list[str] = []
    decision_ready = False
    recommended_wait_hours: int | None = None
    exposure_multiple: float | None = None

    if not gaps:
        assert spend_value is not None
        assert conversions_value is not None
        assert target_cpa_value is not None
        assert hours_value is not None

        exposure_multiple = round(spend_value / target_cpa_value, 3)

        if hours_value < 2:
            recommended_wait_hours = max(1, int(round(2 - hours_value)))
            reasons.append("recent_material_change_needs_stabilization")
        elif conversions_value >= 3:
            decision_ready = True
            reasons.append("conversion_signal_sufficient")
        elif exposure_multiple >= 2.0:
            decision_ready = True
            reasons.append("spend_exposure_sufficient")
        elif conversions_value >= 1 and exposure_multiple >= 1.0:
            decision_ready = True
            reasons.append("mixed_conversion_and_spend_signal_sufficient")
        else:
            # Re-check cadence adapts to how close the campaign is to useful
            # exposure.  It is intentionally measured in hours, not day buckets.
            if exposure_multiple >= 1.0:
                recommended_wait_hours = 2
            elif exposure_multiple >= 0.5:
                recommended_wait_hours = 4
            else:
                recommended_wait_hours = 6
            reasons.append("more_evidence_required")
    else:
        reasons.append("evidence_incomplete")

    return {
        "contract_version": CONTRACT_VERSION,
        "policy_mode": POLICY_MODE,
        "decision_ready": decision_ready,
        "recommended_wait_hours": recommended_wait_hours,
        "evidence_gaps": gaps,
        "reasons": reasons,
        "metrics": {
            "spend": spend_value,
            "conversions": conversions_value,
            "target_cpa": target_cpa_value,
            "hours_since_material_change": hours_value,
            "spend_to_target_cpa_multiple": exposure_multiple,
        },
        "historical_context_days": historical_context_windows(enabled=historical_context),
        "historical_context_is_decision_gate": False,
        "fixed_day_buckets_used": False,
        "read_only": True,
        "execution_allowed": False,
    }


def build_observation_plan(readiness: dict[str, Any]) -> dict[str, Any]:
    """Translate a readiness result into an operator-safe observation plan."""
    ready = readiness.get("decision_ready") is True
    wait = readiness.get("recommended_wait_hours")
    return {
        "status": "ready_for_recommendation" if ready else "observe_more_evidence",
        "recommended_wait_hours": None if ready else wait,
        "re_evaluate_on_new_conversion": not ready,
        "re_evaluate_on_material_spend_change": not ready,
        "calendar_day_gate": None,
        "read_only": True,
        "execution_allowed": False,
    }


__all__ = [
    "CONTRACT_VERSION",
    "POLICY_MODE",
    "HISTORICAL_CONTEXT_DAYS",
    "historical_context_windows",
    "evaluate_decision_readiness",
    "build_observation_plan",
]
