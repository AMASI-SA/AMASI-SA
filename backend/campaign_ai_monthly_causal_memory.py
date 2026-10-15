"""Monthly causal memory for Mezan Store Profit Manager.

The memory separates observed facts from causal claims. Execution success is an
operational fact, not proof that the action caused profit to improve. Causal
claims stay empty unless a future evidence contract can prove pre/post linkage.
"""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any

COLLECTION = "mezan_campaign_ai_monthly_causal_memory_v1"
RECOMMENDATION_COLLECTION = "mezan_campaign_ai_recommendations_v1"
EXECUTION_COLLECTION = "mezan_campaign_ai_executions_v1"
CONTRACT_VERSION = "monthly_causal_memory_v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _compact_action(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "recommendation_id", "provider", "entity_level", "entity_id",
            "entity_name", "account_id", "action", "change_percent",
            "priority", "confidence", "execution_status", "generated_at",
        )
    }


def _execution_action(execution: dict[str, Any]) -> str | None:
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    requested = result.get("requested_change") if isinstance(result.get("requested_change"), dict) else {}
    action = execution.get("action") or result.get("action") or requested.get("action")
    if action:
        return str(action)
    if "status" in requested:
        return "pause" if str(requested.get("status") or "").upper() == "PAUSED" else "status_change"
    if "daily_budget" in requested or "daily_budget_micro" in requested:
        return "budget_change"
    return None


def derive_monthly_causal_memory(
    *,
    month_end: date,
    goal_context: dict[str, Any] | None,
    recommendation_snapshots: list[dict[str, Any]],
    executions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one bounded monthly memory document from auditable stored facts."""
    goal = goal_context if isinstance(goal_context, dict) else {}
    month = _month_key(month_end)
    recommendations: list[dict[str, Any]] = []
    for snapshot in recommendation_snapshots:
        generated_at = snapshot.get("generated_at")
        for raw in snapshot.get("recommendations") or []:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("generated_at", generated_at)
            recommendations.append(row)

    recommendation_counts = Counter(str(row.get("action") or "unknown") for row in recommendations)
    provider_counts = Counter(str(row.get("provider") or "unknown") for row in recommendations)
    recommendation_id_counts = Counter(
        str(row.get("recommendation_id") or "")
        for row in recommendations
        if row.get("recommendation_id")
    )

    execution_counts = Counter(str(row.get("status") or "unknown") for row in executions)
    completed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    failed_by_recommendation: defaultdict[str, int] = defaultdict(int)
    for execution in executions:
        status = str(execution.get("status") or "unknown")
        compact = {
            "execution_id": execution.get("execution_id"),
            "recommendation_id": execution.get("recommendation_id"),
            "provider": execution.get("provider"),
            "entity_id": execution.get("entity_id") or (execution.get("result") or {}).get("entity_id") if isinstance(execution.get("result") or {}, dict) else execution.get("entity_id"),
            "action": _execution_action(execution),
            "status": status,
            "started_at": execution.get("started_at"),
            "finished_at": execution.get("finished_at"),
            "error_code": execution.get("error_code"),
        }
        if status == "completed":
            completed.append(compact)
        elif status in {"provider_state_uncertain", "verification_required"}:
            uncertain.append(compact)
            if compact.get("recommendation_id"):
                failed_by_recommendation[str(compact["recommendation_id"])] += 1
        elif status in {"failed", "error", "rejected"}:
            failed.append(compact)
            if compact.get("recommendation_id"):
                failed_by_recommendation[str(compact["recommendation_id"])] += 1

    repeated_entities = [
        {"recommendation_id": key, "observations": count}
        for key, count in recommendation_id_counts.most_common(20)
        if count >= 2
    ]
    repeated_failures = [
        {"recommendation_id": key, "failed_or_uncertain_executions": count}
        for key, count in sorted(failed_by_recommendation.items(), key=lambda row: (-row[1], row[0]))[:20]
        if count >= 2
    ]

    accounting_known = goal.get("profit_accounting_quality_known") is True
    accounting_complete = goal.get("profit_accounting_complete") is True
    status = str(goal.get("status") or "unknown")
    target = _number(goal.get("minimum_net_profit_sar"))
    net_profit = _number(goal.get("net_profit_to_date_sar"))
    projected = _number(goal.get("projected_month_end_net_profit_sar"))
    gap = _number(goal.get("remaining_to_target_sar"))

    guardrails: list[str] = []
    if not accounting_known or not accounting_complete:
        guardrails.append("Do not learn profit causality or scale from incomplete/unknown accounting evidence.")
    if status == "behind_target":
        guardrails.append("Carry the unresolved monthly profit gap forward as a planning constraint; do not chase sales alone.")
    if repeated_failures:
        guardrails.append("Do not repeat failed/uncertain actions on the same recommendation identity until the failure mechanism is resolved.")
    if not guardrails:
        guardrails.append("Preserve the monthly profit floor and require measurable evidence before changing a proven pattern.")

    last_day = calendar.monthrange(month_end.year, month_end.month)[1]
    finalized = month_end.day == last_day
    return {
        "contract_version": CONTRACT_VERSION,
        "month": month,
        "currency": "SAR",
        "finalized": finalized,
        "observed_facts": {
            "goal": {
                "target_net_profit_sar": target,
                "net_profit_sar": net_profit,
                "remaining_gap_sar": gap,
                "projected_month_end_net_profit_sar": projected,
                "status": status,
                "phase": goal.get("phase"),
                "accounting_quality_known": accounting_known,
                "accounting_complete": accounting_complete,
                "profit_contract_version": (goal.get("month_to_date") or {}).get("profit_contract_version") if isinstance(goal.get("month_to_date"), dict) else None,
            },
            "recommendations": {
                "total": len(recommendations),
                "by_action": dict(recommendation_counts),
                "by_provider": dict(provider_counts),
            },
            "executions": {
                "total": len(executions),
                "by_status": dict(execution_counts),
            },
        },
        "operational_outcomes": {
            "completed": completed[:30],
            "failed": failed[:30],
            "uncertain": uncertain[:30],
            "note": "completed means provider execution completed; it does not prove positive profit causality",
        },
        "repeated_patterns": {
            "repeated_recommendation_entities": repeated_entities,
            "repeated_failed_or_uncertain": repeated_failures,
        },
        "causal_inference": {
            "claims": [],
            "root_cause_candidates": [],
            "confidence": "not_established",
            "rule": "Never convert correlation, execution completion, provider revenue, or missing data into a causal profit claim.",
        },
        "what_worked": {
            "operationally_completed_actions": completed[:12],
            "profit_effect_proven": False,
        },
        "what_failed": {
            "failed_actions": failed[:12],
            "uncertain_actions": uncertain[:12],
        },
        "repeated_mistakes": repeated_failures,
        "next_month_plan": {
            "carry_forward_guardrails": guardrails,
            "profit_gap_to_revisit_sar": gap,
            "requires_new_evidence": True,
        },
    }


async def ensure_indexes(db: Any) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1), ("month", 1)],
        unique=True,
        name="campaign_ai_monthly_causal_memory_user_month_unique",
    )
    await db[COLLECTION].create_index(
        [("user_id", 1), ("updated_at", -1)],
        name="campaign_ai_monthly_causal_memory_recent",
    )


async def refresh_monthly_causal_memory(
    db: Any,
    user_id: str,
    *,
    month_end: date,
    goal_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Refresh current-month memory from tenant-scoped recommendation/execution facts."""
    await ensure_indexes(db)
    month = _month_key(month_end)
    prefix = month + "-"
    snapshots = await db[RECOMMENDATION_COLLECTION].find(
        {"user_id": user_id, "generated_at": {"$gte": prefix + "01", "$lt": month + "-32"}},
        {"_id": 0, "generated_at": 1, "recommendations": 1},
    ).sort("generated_at", 1).limit(200).to_list(length=200)
    executions = await db[EXECUTION_COLLECTION].find(
        {"user_id": user_id, "started_at": {"$gte": prefix + "01", "$lt": month + "-32"}},
        {"_id": 0},
    ).sort("started_at", 1).limit(500).to_list(length=500)
    memory = derive_monthly_causal_memory(
        month_end=month_end,
        goal_context=goal_context,
        recommendation_snapshots=snapshots,
        executions=executions,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    document = {
        **memory,
        "user_id": user_id,
        "updated_at": now_iso,
    }
    await db[COLLECTION].update_one(
        {"user_id": user_id, "month": month},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return {key: value for key, value in document.items() if key != "user_id"}


async def load_monthly_causal_memory_context(
    db: Any,
    user_id: str,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return bounded recent memory for OpenAI context, scoped to one merchant."""
    await ensure_indexes(db)
    rows = await db[COLLECTION].find(
        {"user_id": user_id},
        {"_id": 0, "user_id": 0},
    ).sort("month", -1).limit(max(1, min(12, int(limit)))).to_list(length=max(1, min(12, int(limit))))
    return rows


__all__ = [
    "COLLECTION",
    "CONTRACT_VERSION",
    "derive_monthly_causal_memory",
    "ensure_indexes",
    "load_monthly_causal_memory_context",
    "refresh_monthly_causal_memory",
]
