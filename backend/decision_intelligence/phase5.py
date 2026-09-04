"""Decision Intelligence Phase 5 recommendation-only shadow pipeline."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .evidence_adapter import (
    load_decision_evidence,
    load_decision_evidence_for_latest_closed_day,
)

PHASE5_VERSION = "decision-intelligence-phase5-v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _shadow_simulation(candidate: dict[str, Any]) -> dict[str, Any]:
    """Describe a bounded scenario without forecasting an unmeasured effect."""
    metrics = candidate.get("metrics") or {}
    current_profit = _number(metrics.get("contribution_profit_sar"))
    commerce_roas = _number(metrics.get("salla_roas"))
    conversions = _number(metrics.get("conversions")) or 0.0

    if (
        current_profit is not None
        and current_profit > 0
        and commerce_roas is not None
        and commerce_roas >= 1.5
        and conversions >= 3
    ):
        scenario = "bounded_budget_increase_5pct_shadow"
        proposed_change = {"budget_change_pct": 5.0}
        rationale = "Observed reconciled outcomes qualify this campaign for a bounded shadow test."
    elif (
        (current_profit is not None and current_profit < 0)
        or (commerce_roas is not None and commerce_roas < 1.0)
    ):
        scenario = "bounded_budget_decrease_5pct_shadow"
        proposed_change = {"budget_change_pct": -5.0}
        rationale = "Observed reconciled outcomes qualify this campaign for a bounded shadow test."
    else:
        scenario = "hold_and_collect_more_evidence"
        proposed_change = {"budget_change_pct": 0.0}
        rationale = "Keep current state and collect measured response evidence."
    return {
        "status": "SHADOW_SCENARIO_ONLY",
        "scenario": scenario,
        "proposed_change": proposed_change,
        "rationale": rationale,
        "forecast_used": False,
        "execution_performed": False,
    }


def _unknown_impact_prediction() -> dict[str, Any]:
    return {
        "status": "unknown",
        "expected_profit_delta_sar": None,
        "downside_sar": None,
        "upside_sar": None,
        "confidence": None,
        "evidence_basis": None,
        "reason": "measured_elasticity_experiment_or_validated_model_evidence_unavailable",
        "evidence_required": [
            "measured_elasticity",
            "controlled_experiment",
            "validated_model_output",
        ],
    }


def _shadow_recommendation(
    candidate: dict[str, Any],
    simulation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "signal_id": str(candidate.get("evidence_id") or "unknown"),
        "action": "TEST",
        "priority_score": None,
        "confidence": None,
        "expected_profit_delta_sar": None,
        "reason": (
            "Reconciled observed evidence supports a shadow scenario only; "
            "financial impact remains unknown until measured impact evidence exists."
        ),
        "scenario": simulation["scenario"],
        "requires_owner_approval": True,
        "read_only": True,
    }


def _pending_approval(recommendation: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_id": recommendation["signal_id"],
        "state": "PENDING",
        "requested_action": recommendation["action"],
        "rationale": recommendation["reason"],
        "requires_owner_approval": True,
        "execution_performed": False,
    }


def _lineage(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "reader": (evidence.get("source") or {}).get("reader"),
        "contract_only": bool((evidence.get("source") or {}).get("contract_only")),
        "contract_version": evidence.get("contract_version"),
        "evidence_adapter": evidence.get("adapter"),
        "provider": evidence.get("provider"),
        "account_id": (evidence.get("account") or {}).get("id"),
        "period": evidence.get("period"),
        "entities": [
            {
                "evidence_id": candidate.get("evidence_id"),
                "entity": candidate.get("entity") or {},
                "source": candidate.get("lineage") or {},
            }
            for candidate in evidence.get("candidates") or []
        ],
    }


def run_phase5_shadow_from_evidence(
    evidence: dict[str, Any],
    *,
    max_candidates: int = 25,
) -> dict[str, Any]:
    """Run the deterministic shadow chain on an already adapted bundle."""
    decisions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for candidate in list(evidence.get("candidates") or [])[: max(1, max_candidates)]:
        if not candidate.get("decision_eligible"):
            item = {
                "decision_id": candidate.get("evidence_id"),
                "entity": candidate.get("entity") or {},
                "status": "BLOCKED",
                "blocked_by": list(candidate.get("blocked_by") or []),
                "simulation": None,
                "impact_prediction": None,
                "approval": None,
            }
            blocked.append(item)
            decisions.append(item)
            continue

        simulation = _shadow_simulation(candidate)
        prediction = _unknown_impact_prediction()
        recommendation = _shadow_recommendation(candidate, simulation)
        approval = _pending_approval(recommendation)
        decisions.append({
            "decision_id": candidate.get("evidence_id"),
            "entity": candidate.get("entity") or {},
            "status": "RECOMMENDATION_SHADOW",
            "blocked_by": [],
            "recommendation": recommendation,
            "simulation": simulation,
            "impact_prediction": prediction,
            "approval": approval,
            "execution_allowed": False,
        })

    if not decisions and evidence.get("blocked_by"):
        item = {
            "decision_id": "account-window",
            "entity": {"level": "account", "id": (evidence.get("account") or {}).get("id")},
            "status": "BLOCKED",
            "blocked_by": list(evidence.get("blocked_by") or []),
            "simulation": None,
            "impact_prediction": None,
            "approval": None,
        }
        blocked.append(item)
        decisions.append(item)

    return {
        "phase": PHASE5_VERSION,
        "mode": "recommendation_shadow",
        "contract_version": evidence.get("contract_version"),
        "provider": evidence.get("provider"),
        "source": evidence.get("source"),
        "account": evidence.get("account"),
        "period": evidence.get("period"),
        "evidence_timestamp": evidence.get("evaluated_at"),
        "gates": evidence.get("gates"),
        "lineage": _lineage(evidence),
        "candidate_selection": evidence.get("candidate_selection"),
        "decision_ready": bool(evidence.get("decision_ready")),
        "decisions": decisions,
        "summary": {
            "candidates_evaluated": len(decisions),
            "recommendations": sum(
                item.get("status") == "RECOMMENDATION_SHADOW" for item in decisions
            ),
            "blocked": len(blocked),
            "blocked_reasons": sorted({
                reason for item in blocked for reason in item.get("blocked_by") or []
            }),
        },
        "approval_workflow": {
            "mode": "manual_owner_review",
            "auto_approval_enabled": False,
            "approval_can_execute": False,
        },
        "scheduler_integration": {
            "campaign_ai_scheduler_connected": False,
            "automatic_execution_connected": False,
        },
        "write_policy": {
            "platform_writes_enabled": False,
            "platform_writes_performed": False,
            "database_writes_performed": False,
        },
    }


async def run_phase5_shadow(
    db: Any,
    user_id: str,
    *,
    provider: str,
    date_from: date,
    date_to: date,
    now: datetime | None = None,
    max_freshness_hours: float = 36.0,
    max_candidates: int = 25,
) -> dict[str, Any]:
    evidence = await load_decision_evidence(
        db,
        str(user_id),
        provider=provider,
        date_from=date_from,
        date_to=date_to,
        now=now,
        max_freshness_hours=max_freshness_hours,
        max_candidates=max_candidates,
    )
    return run_phase5_shadow_from_evidence(
        evidence,
        max_candidates=max_candidates,
    )


async def run_phase5_shadow_for_latest_closed_day(
    db: Any,
    user_id: str,
    *,
    provider: str,
    now: datetime | None = None,
    max_freshness_hours: float = 36.0,
    max_candidates: int = 25,
) -> dict[str, Any]:
    """Run the shadow chain for the provider account's latest closed day."""
    evidence = await load_decision_evidence_for_latest_closed_day(
        db,
        str(user_id),
        provider=provider,
        now=now,
        max_freshness_hours=max_freshness_hours,
        max_candidates=max_candidates,
    )
    return run_phase5_shadow_from_evidence(
        evidence,
        max_candidates=max_candidates,
    )


__all__ = [
    "PHASE5_VERSION",
    "run_phase5_shadow",
    "run_phase5_shadow_for_latest_closed_day",
    "run_phase5_shadow_from_evidence",
]
