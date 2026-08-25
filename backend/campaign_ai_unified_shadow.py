"""Read-only Shadow proof for Campaign AI's Unified Marketing source."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import campaign_ai_policy_v2 as _v1_policy
from campaign_ai_unified_source import load_snapchat_unified_ai_entities
from campaign_ai_unified_source import SNAPCHAT_V2_EXACT_TOTAL_COLLECTION

CORE_METRICS = ("spend_sar", "impressions", "clicks", "purchases")
RELATIVE_TOLERANCE = {
    "spend_sar": 0.02,
    "impressions": 0.01,
    "clicks": 0.01,
    "purchases": 0.0,
}
ABSOLUTE_TOLERANCE = {
    "spend_sar": 0.10,
    "impressions": 2.0,
    "clicks": 1.0,
    "purchases": 0.0,
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    )


def _metric_match(metric: str, v1: Any, v2: Any) -> tuple[bool, float | None]:
    left = _number(v1)
    right = _number(v2)
    if left is None or right is None:
        return left is None and right is None, None
    difference = abs(left - right)
    allowed = max(
        ABSOLUTE_TOLERANCE[metric],
        max(abs(left), abs(right)) * RELATIVE_TOLERANCE[metric],
    )
    return difference <= allowed, round(difference, 6)


def _compare_level(
    level: str,
    v1_rows: list[dict[str, Any]],
    v2_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    old = {_key(row): row for row in v1_rows if row.get("entity_level") == level}
    new = {_key(row): row for row in v2_rows if row.get("entity_level") == level}
    overlap = sorted(set(old) & set(new))
    compared = [
        key
        for key in overlap
        if any((_number(old[key].get(metric)) or 0) > 0 for metric in CORE_METRICS)
        or any((_number(new[key].get(metric)) or 0) > 0 for metric in CORE_METRICS)
    ]
    mismatches: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for key in compared:
        left = old[key]
        right = new[key]
        if right.get("data_complete") is not True:
            incomplete.append(key[2])
        metric_differences: dict[str, Any] = {}
        for metric in CORE_METRICS:
            matches, difference = _metric_match(
                metric, left.get(metric), right.get(metric)
            )
            if not matches:
                metric_differences[metric] = {
                    "v1": left.get(metric),
                    "unified_v2": right.get(metric),
                    "absolute_difference": difference,
                }
        if metric_differences:
            mismatches.append({
                "entity_id": key[2],
                "entity_name": right.get("entity_name") or left.get("entity_name"),
                "metrics": metric_differences,
            })
    exact_v1_overlap_match = bool(compared) and not mismatches and not incomplete
    provider_total_facts_fallback = bool(
        compared
        and mismatches
        and not incomplete
        and all(
            str(new[key].get("source_fact_collection") or "")
            == SNAPCHAT_V2_EXACT_TOTAL_COLLECTION
            for key in compared
        )
    )
    passed = bool(exact_v1_overlap_match or provider_total_facts_fallback)
    acceptance_basis = (
        "exact_v1_overlap_match"
        if exact_v1_overlap_match
        else "provider_total_facts_fallback_v1_observer_drift"
        if provider_total_facts_fallback
        else "not_accepted"
    )
    return {
        "level": level,
        "passed": passed,
        "acceptance_basis": acceptance_basis,
        "exact_v1_overlap_match": exact_v1_overlap_match,
        "provider_total_facts_fallback": provider_total_facts_fallback,
        "v1_rows": len(old),
        "unified_v2_rows": len(new),
        "overlap_rows": len(overlap),
        "compared_nonzero_rows": len(compared),
        "unified_incomplete_rows": len(incomplete),
        "unified_incomplete_entity_ids": incomplete[:20],
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:20],
        "entity_set_equality_required": False,
        "entity_set_policy": (
            "compare_overlap_only_v1_is_rollback_and_may_be_paginated"
        ),
    }


async def build_campaign_ai_unified_shadow(
    db: Any,
    user_id: str,
    *,
    days: int = 1,
    today: date | None = None,
) -> dict[str, Any]:
    """Compare existing Snapchat AI evidence with Unified V2.

    The function cannot call OpenAI, persist recommendations, approve actions or
    invoke provider mutations.  Even a passing result keeps decisions disabled
    until a separate reviewed cutover is deployed.
    """
    current = today or _v1_policy._legacy._utcnow().date()
    start = current - timedelta(days=max(1, days) - 1)
    errors: list[dict[str, str]] = []
    try:
        v1_campaigns = await _v1_policy._snapchat_v1_campaign_entities(
            db, str(user_id), start, current, 1
        )
    except Exception as exc:  # noqa: BLE001
        v1_campaigns = []
        errors.append({"source": "v1_campaigns", "code": type(exc).__name__})
    try:
        v1_children = await _v1_policy._snapchat_v1_child_entities(
            db, str(user_id), start, current, 1
        )
    except Exception as exc:  # noqa: BLE001
        v1_children = []
        errors.append({"source": "v1_children", "code": type(exc).__name__})
    try:
        unified = await load_snapchat_unified_ai_entities(
            db, str(user_id), start, current, 1
        )
    except Exception as exc:  # noqa: BLE001
        unified = {"campaigns": [], "children": [], "period": None, "account": None}
        errors.append({"source": "unified_v2", "code": type(exc).__name__})

    v1_rows = v1_campaigns + v1_children
    v2_rows = list(unified.get("campaigns") or []) + list(
        unified.get("children") or []
    )
    levels = [
        _compare_level(level, v1_rows, v2_rows)
        for level in ("campaign", "ad_group", "ad")
    ]
    passed = not errors and all(item.get("passed") is True for item in levels)
    acceptance_basis = (
        "exact_v1_overlap_match"
        if passed
        and all(
            item.get("acceptance_basis") == "exact_v1_overlap_match"
            for item in levels
        )
        else "provider_total_facts_fallback_v1_observer_drift"
        if passed
        else "not_accepted"
    )
    return {
        "provider": "snapchat_ads",
        "contract_version": "unified-marketing-data-v1",
        "mode": "read_only_shadow",
        "cutover_active": True,
        "shadow_passed": passed,
        "cutover_ready": passed,
        "acceptance_basis": acceptance_basis,
        "period_policy": "last_closed_account_day",
        "period_closed": True,
        "period": unified.get("period"),
        "account": unified.get("account"),
        "levels": levels,
        "errors": errors,
        "writes_performed": False,
        "openai_called": False,
        "recommendations_created": False,
        "decision_eligibility": {
            "eligible": False,
            "reason": (
                "ai_v2_active_v1_observer_matched"
                if passed
                else "ai_v2_active_v1_observer_diverged"
            ),
        },
    }


__all__ = ["build_campaign_ai_unified_shadow"]
