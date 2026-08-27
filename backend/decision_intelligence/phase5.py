"""Decision Intelligence Phase 5 recommendation-only shadow pipeline."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime
from typing import Any

from .action_simulator import simulate_action
from .approval_workflow import create_approval_request
from .decision_engine import rank_decisions
from .evidence_adapter import load_decision_evidence
from .impact_predictor import choose_best_simulation
from .models import DecisionRecommendation, DecisionSignal

PHASE5_VERSION = "decision-intelligence-phase5-v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _shadow_simulation(candidate: dict[str, Any]):
    metrics = candidate.get("metrics") or {}
    current_profit = _number(metrics.get("contribution_profit_sar"))
    commerce_roas = _number(metrics.get("salla_roas"))
    conversions = _number(metrics.get("conversions")) or 0.0
    if current_profit is None:
        return None

    if current_profit > 0 and commerce_roas is not None and commerce_roas >= 1.5 and conversions >= 3:
        scenario = "bounded_budget_increase_5pct_shadow"
        expected_profit = current_profit + (current_profit * 0.03)
        confidence = 0.65
        assumptions = [
            "simulation_only_no_platform_write",
            "budget_change_capped_at_5pct",
            "60pct_of_current_profit_efficiency_carries_to_incremental_spend",
            "no_saturation_or_competition_shift_inside_the_window",
        ]
    elif current_profit < 0 or (commerce_roas is not None and commerce_roas < 1.0):
        scenario = "bounded_budget_decrease_5pct_shadow"
        expected_profit = current_profit + (abs(current_profit) * 0.025)
        confidence = 0.6
        assumptions = [
            "simulation_only_no_platform_write",
            "budget_change_capped_at_5pct",
            "50pct_of_avoided_loss_is_recoverable",
            "no_cross_campaign_cannibalization_modelled",
        ]
    else:
        scenario = "hold_and_collect_more_evidence"
        expected_profit = current_profit
        confidence = 0.75
        assumptions = [
            "simulation_only_no_platform_write",
            "current_budget_and_status_remain_unchanged",
        ]
    return simulate_action(
        scenario=scenario,
        current_profit_sar=current_profit,
        expected_profit_sar=expected_profit,
        confidence=confidence,
        assumptions=assumptions,
    )


def _signal(candidate: dict[str, Any], predicted_delta: float | None) -> DecisionSignal:
    metrics = candidate.get("metrics") or {}
    evidence_count = sum(
        value is not None
        for value in (
            metrics.get("spend_sar"),
            metrics.get("conversions"),
            metrics.get("salla_orders"),
            metrics.get("salla_revenue_sar"),
            metrics.get("contribution_profit_sar"),
        )
    )
    contribution = _number(metrics.get("contribution_profit_sar")) or 0.0
    urgency = 0.8 if contribution < 0 else 0.45
    return DecisionSignal(
        signal_id=str(candidate.get("evidence_id") or "unknown"),
        source="decision_evidence_adapter",
        title=str((candidate.get("entity") or {}).get("name") or "marketing entity"),
        evidence_count=evidence_count,
        confidence=0.65,
        expected_profit_delta_sar=predicted_delta,
        urgency=urgency,
        effort=0.2,
        evidence=(
            {
                "contract_version": "unified-marketing-data-v1",
                "metrics": metrics,
                "quality": candidate.get("quality") or {},
                "lineage": candidate.get("lineage") or {},
            },
        ),
    )


def _enforce_shadow_mode(
    recommendation: DecisionRecommendation,
) -> DecisionRecommendation:
    if recommendation.action != "EXECUTE_NOW":
        return recommendation
    return replace(
        recommendation,
        action="TEST",
        reason=(
            "Shadow mode downgraded EXECUTE_NOW to a bounded TEST proposal; "
            "owner approval cannot execute a platform write in Phase 5."
        ),
    )


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
        prediction = choose_best_simulation([simulation] if simulation else [])
        if prediction is None:
            item = {
                "decision_id": candidate.get("evidence_id"),
                "entity": candidate.get("entity") or {},
                "status": "BLOCKED",
                "blocked_by": ["impact_prediction_unavailable"],
                "simulation": asdict(simulation) if simulation else None,
                "impact_prediction": None,
                "approval": None,
            }
            blocked.append(item)
            decisions.append(item)
            continue

        recommendation = _enforce_shadow_mode(
            rank_decisions([
                _signal(candidate, prediction.expected_profit_delta_sar)
            ])[0]
        )
        approval = (
            asdict(create_approval_request(recommendation))
            if recommendation.action == "TEST"
            else {
                "state": "NOT_REQUESTED",
                "requires_owner_approval": True,
                "execution_performed": False,
            }
        )
        decisions.append({
            "decision_id": candidate.get("evidence_id"),
            "entity": candidate.get("entity") or {},
            "status": "RECOMMENDATION_SHADOW",
            "blocked_by": [],
            "recommendation": asdict(recommendation),
            "simulation": asdict(simulation),
            "impact_prediction": asdict(prediction),
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
        "source": evidence.get("source"),
        "account": evidence.get("account"),
        "period": evidence.get("period"),
        "gates": evidence.get("gates"),
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
    )
    return run_phase5_shadow_from_evidence(
        evidence,
        max_candidates=max_candidates,
    )


__all__ = [
    "PHASE5_VERSION",
    "run_phase5_shadow",
    "run_phase5_shadow_from_evidence",
]
