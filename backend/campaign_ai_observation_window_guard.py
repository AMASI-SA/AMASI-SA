"""P1-4 deterministic observation-window guard for Campaign AI recommendations.

A fresh model pass may reason again every scheduler cycle, but it must not churn
financial actions on the same entity before the previous recommendation's
observation window has elapsed.  The only initial emergency override is a
fail-safe pause for severe no-purchase spend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _blocking_prior(
    prior_decisions: dict[str, Any] | None,
    recommendation_id: str,
    current: datetime,
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    context = prior_decisions if isinstance(prior_decisions, dict) else {}
    for snapshot in context.get("recent_recommendations") or []:
        if not isinstance(snapshot, dict):
            continue
        generated_at = _dt(snapshot.get("generated_at"))
        if generated_at is None or generated_at > current + timedelta(minutes=5):
            continue
        for item in snapshot.get("recommendations") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("recommendation_id") or "") != recommendation_id:
                continue
            next_check = _dt(item.get("next_check_at"))
            wait = _number(item.get("recommended_wait_hours"))
            if next_check is None:
                wait = min(24.0, max(1.0, wait if wait is not None else 5.0))
                next_check = generated_at + timedelta(hours=wait)
            if next_check <= current:
                continue
            matches.append({
                "generated_at": generated_at,
                "blocked_until": next_check,
                "prior_action": str(item.get("action") or ""),
                "prior_execution_status": item.get("execution_status"),
                "prior_change_percent": item.get("change_percent"),
                "prior_period_spend_sar": (
                    (item.get("financial_impact") or {}).get("period_spend_sar")
                    if isinstance(item.get("financial_impact"), dict)
                    else None
                ),
            })
    if not matches:
        return None
    return max(matches, key=lambda row: row["generated_at"])


def observation_window_decision(
    recommendation: dict[str, Any],
    row: dict[str, Any] | None,
    prior_decisions: dict[str, Any] | None,
    *,
    now: datetime,
    target_cpa_sar: float,
) -> dict[str, Any]:
    """Return a deterministic suppression/override decision for one entity."""
    current = now.astimezone(timezone.utc)
    recommendation_id = str(recommendation.get("recommendation_id") or "")
    prior = _blocking_prior(prior_decisions, recommendation_id, current)
    if prior is None:
        return {
            "status": "clear",
            "blocked": False,
            "emergency_override": False,
            "replacement_action": None,
            "blocked_until": None,
        }

    evidence = row if isinstance(row, dict) else {}
    spend = _number(evidence.get("spend_sar"))
    purchases = _number(evidence.get("purchases"))
    proposed = str(recommendation.get("action") or "").strip().lower()
    emergency_threshold = max(0.0, float(target_cpa_sar)) * 3.0
    emergency_pause = bool(
        proposed == "pause"
        and spend is not None
        and spend >= emergency_threshold
        and purchases is not None
        and purchases == 0
    )
    base = {
        "blocked_until": prior["blocked_until"].isoformat(),
        "prior_action": prior["prior_action"],
        "prior_execution_status": prior.get("prior_execution_status"),
        "prior_change_percent": prior.get("prior_change_percent"),
        "proposed_action": proposed or None,
        "emergency_threshold_spend_sar": round(emergency_threshold, 2),
        "observed_spend_sar": spend,
        "observed_purchases": purchases,
    }
    if emergency_pause:
        return {
            **base,
            "status": "emergency_override",
            "blocked": False,
            "emergency_override": True,
            "replacement_action": None,
            "reason": "critical_no_purchase_spend",
        }
    return {
        **base,
        "status": "observation_window_active",
        "blocked": True,
        "emergency_override": False,
        "replacement_action": "monitor",
        "reason": "prior_recommendation_observation_window_not_elapsed",
    }


__all__ = ["observation_window_decision"]
