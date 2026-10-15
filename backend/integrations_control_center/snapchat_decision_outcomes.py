"""Conservative, append-only outcome measurements for Snapchat decisions.

The evaluator deliberately measures association, not causation.  It compares
equal-length snapshots around the effective decision time and keeps Salla's
manual source as manual.  Sales explicitly assigned to another advertising
platform are never counted as Snapchat contribution.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import snapchat_decision_ledger as decision_ledger
from .snapchat_decision_metrics import (
    capture_decision_baseline,
    resolve_decision_campaign_id,
)


OUTCOME_SOURCE = "snapchat_decision_outcome_evaluator_v1"
OUTCOME_WORKER_STATE_COLLECTION = "mezan_ad_decision_outcome_worker_state_v1"
EVALUATION_HORIZONS = (
    ("1d", 24, 1),
    ("3d", 72, 3),
    ("7d", 168, 7),
)
OUTCOME_STATUSES = {
    "successful",
    "failed",
    "mixed",
    "inconclusive",
    "pending",
}
_DIRECTION_UP = {"increase", "increased", "higher", "up", "improve", "improved"}
_DIRECTION_DOWN = {"decrease", "decreased", "lower", "down", "reduce", "reduced"}
_DIRECTION_STABLE = {"stable", "maintain", "maintained", "unchanged", "flat"}
_OTHER_AD_PLATFORMS = {"meta", "facebook", "instagram", "tiktok", "google"}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _text(value)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now(value: datetime | None) -> datetime:
    parsed = _utc(value) if value is not None else datetime.now(timezone.utc)
    if parsed is None:
        raise ValueError("now must be a valid datetime")
    return parsed


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except (KeyError, TypeError, AttributeError):
        return getattr(db, name)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        output = dump(mode="json")
        if isinstance(output, dict):
            return output
    raise ValueError("decision must be an object")


def _window(snapshot: Any, days: int) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    windows = snapshot.get("windows")
    if isinstance(windows, list):
        for row in windows:
            if isinstance(row, dict) and int(_number(row.get("days")) or 0) == days:
                return deepcopy(row)
    if isinstance(windows, dict):
        for key in (days, str(days), f"d{days}", f"{days}d", f"day_{days}"):
            row = windows.get(key)
            if isinstance(row, dict):
                return deepcopy(row)
    if int(_number(snapshot.get("days")) or 0) == days:
        return deepcopy(snapshot)
    return None


def _coverage_complete(snapshot: Any, window: Any) -> bool:
    """Require explicit proof for the exact comparison window.

    Legacy snapshots and synthetic zero rows without a durable provider-range
    proof fail closed. Top-level 14-day coverage cannot stand in for a selected
    1/3/7-day window (or vice versa).
    """

    if not isinstance(snapshot, dict) or not isinstance(window, dict):
        return False
    coverage = window.get("coverage")
    return isinstance(coverage, dict) and coverage.get("complete") is True


def _scope_complete(scope: Any, *, store: bool = False) -> bool:
    if not isinstance(scope, dict):
        return False
    if scope.get("cost_complete") is False:
        return False
    required = (
        ("orders", "sales_sar")
        if store
        else (
            "orders",
            "sales_sar",
            "spend_sar",
        )
    )
    return all(_number(scope.get(key)) is not None for key in required)


def _data_completeness(
    baseline_snapshot: dict[str, Any] | None,
    post_snapshot: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    post: dict[str, Any] | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if baseline is None:
        reasons.append("baseline_window_missing")
    if post is None:
        reasons.append("post_window_missing")
    if not _coverage_complete(baseline_snapshot, baseline):
        reasons.append("baseline_coverage_incomplete")
    if not _coverage_complete(post_snapshot, post):
        reasons.append("post_coverage_incomplete")
    for label, window in (("baseline", baseline), ("post", post)):
        if not isinstance(window, dict):
            continue
        if not _scope_complete(window.get("campaign")):
            reasons.append(f"{label}_campaign_metrics_incomplete")
        if not _scope_complete(window.get("account")):
            reasons.append(f"{label}_account_metrics_incomplete")
        if not _scope_complete(window.get("store"), store=True):
            reasons.append(f"{label}_store_metrics_incomplete")
    return {
        "complete": not reasons,
        "reasons": sorted(set(reasons)),
        "policy": "all_comparable_scopes_and_product_costs_must_be_complete",
    }


def _metric_delta(before: Any, after: Any) -> dict[str, Any] | None:
    old = _number(before)
    new = _number(after)
    if old is None or new is None:
        return None
    change = new - old
    percent = change / abs(old) * 100 if old != 0 else None
    tolerance = max(abs(old) * 0.001, 0.005)
    direction = "stable"
    if change > tolerance:
        direction = "increase"
    elif change < -tolerance:
        direction = "decrease"
    return {
        "baseline": round(old, 4),
        "actual": round(new, 4),
        "absolute_delta": round(change, 4),
        "delta_pct": round(percent, 2) if percent is not None else None,
        "direction": direction,
    }


def _scope_deltas(
    baseline: dict[str, Any] | None,
    post: dict[str, Any] | None,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    old = baseline if isinstance(baseline, dict) else {}
    new = post if isinstance(post, dict) else {}
    return {
        key: delta
        for key in keys
        if (delta := _metric_delta(old.get(key), new.get(key))) is not None
    }


def _all_deltas(
    baseline: dict[str, Any] | None,
    post: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    baseline = baseline if isinstance(baseline, dict) else {}
    post = post if isinstance(post, dict) else {}
    return {
        "campaign": _scope_deltas(
            baseline.get("campaign"),
            post.get("campaign"),
            (
                "orders",
                "sales_sar",
                "spend_sar",
                "product_cost_sar",
                "contribution_profit_sar",
                "profit_margin_pct",
                "roas",
                "cpa_sar",
            ),
        ),
        "account": _scope_deltas(
            baseline.get("account"),
            post.get("account"),
            (
                "orders",
                "sales_sar",
                "spend_sar",
                "product_cost_sar",
                "contribution_profit_sar",
                "profit_margin_pct",
                "roas",
                "cpa_sar",
            ),
        ),
        "store": _scope_deltas(
            baseline.get("store"),
            post.get("store"),
            (
                "orders",
                "sales_sar",
                "product_cost_sar",
                "gross_profit_before_marketing_sar",
                "gross_margin_before_marketing_pct",
            ),
        ),
    }


def _source_units(row: dict[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    for source in row.get("observed_order_sources") or []:
        if not isinstance(source, dict):
            continue
        name = _text(source.get("source")).lower() or "unknown"
        units = _number(source.get("units")) or 0.0
        output[name] = output.get(name, 0.0) + units
    return output


def _product_attribution(window: dict[str, Any] | None) -> dict[str, Any]:
    products: list[dict[str, Any]] = []
    total_campaign = total_store = total_cross_platform = 0.0
    total_manual = total_whatsapp = total_unresolved = 0.0
    for raw in (window or {}).get("product_sales_comparison") or []:
        if not isinstance(raw, dict):
            continue
        sources = _source_units(raw)
        campaign_units = _number(raw.get("campaign_attributed_units")) or 0.0
        store_units = _number(raw.get("whole_store_product_units")) or 0.0
        cross_platform = _number(raw.get("verified_other_ad_platform_units"))
        if cross_platform is None:
            cross_platform = sum(
                units
                for source, units in sources.items()
                if source in _OTHER_AD_PLATFORMS
            )
        manual = sum(
            units
            for source, units in sources.items()
            if source in {"manual", "salla", "salla_manual", "manual_entry"}
        )
        if not manual:
            manual = _number(raw.get("salla_manual_entry_units")) or 0.0
        whatsapp = sources.get("whatsapp", 0.0)
        if not whatsapp:
            whatsapp = _number(raw.get("explicit_whatsapp_units")) or 0.0
        unresolved = max(store_units - campaign_units - cross_platform, 0.0)
        total_campaign += campaign_units
        total_store += store_units
        total_cross_platform += cross_platform
        total_manual += manual
        total_whatsapp += whatsapp
        total_unresolved += unresolved
        products.append(
            {
                "identity": raw.get("identity"),
                "name": raw.get("name"),
                "campaign_attributed_units": round(campaign_units, 2),
                "whole_store_product_units": round(store_units, 2),
                "verified_cross_platform_units_excluded": round(cross_platform, 2),
                "manual_or_salla_units_unresolved": round(manual, 2),
                "explicit_whatsapp_units": round(whatsapp, 2),
                "units_unresolved_for_snapchat_decision": round(unresolved, 2),
                "snapchat_contribution_units": round(campaign_units, 2),
                "source_policy": (
                    "manual_and_salla_remain_manual; whatsapp_only_when_explicit; "
                    "explicit_other_ad_platforms_are_excluded"
                ),
            }
        )
    return {
        "products": products,
        "campaign_attributed_units": round(total_campaign, 2),
        "whole_store_product_units": round(total_store, 2),
        "verified_cross_platform_units_excluded": round(total_cross_platform, 2),
        "manual_or_salla_units_unresolved": round(total_manual, 2),
        "explicit_whatsapp_units": round(total_whatsapp, 2),
        "units_unresolved_for_snapchat_decision": round(total_unresolved, 2),
        "snapchat_contribution_rule": "exact_campaign_attribution_only",
        "manual_source_rule": "manual_is_not_assumed_to_be_whatsapp",
        "cross_platform_rule": (
            "explicit_meta_tiktok_google_sales_are_excluded_from_snapchat_contribution"
        ),
    }


def _canonical_metric(path: str) -> tuple[str, str] | None:
    normalized = path.lower().replace("-", "_").replace(" ", "_")
    scope = "campaign"
    if "store" in normalized or "whole_store" in normalized:
        scope = "store"
    elif "account" in normalized:
        scope = "account"
    candidates = (
        ("profit_margin", "profit_margin_pct"),
        ("gross_margin", "gross_margin_before_marketing_pct"),
        ("gross_profit", "gross_profit_before_marketing_sar"),
        ("contribution_profit", "contribution_profit_sar"),
        (
            "profit",
            (
                "contribution_profit_sar"
                if scope != "store"
                else "gross_profit_before_marketing_sar"
            ),
        ),
        ("revenue", "sales_sar"),
        ("sales", "sales_sar"),
        ("orders", "orders"),
        ("purchases", "orders"),
        ("spend", "spend_sar"),
        ("roas", "roas"),
        ("cpa", "cpa_sar"),
    )
    for token, metric in candidates:
        if token in normalized:
            return scope, metric
    return None


def _direction(value: Any) -> str | None:
    normalized = _text(value).lower()
    if normalized in _DIRECTION_UP:
        return "increase"
    if normalized in _DIRECTION_DOWN:
        return "decrease"
    if normalized in _DIRECTION_STABLE:
        return "stable"
    return None


def _expected_checks(expected: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, path)
            return
        if not isinstance(value, dict):
            return
        metric_name = value.get("metric")
        direction = _direction(value.get("direction"))
        if metric_name:
            explicit_scope = _text(value.get("scope")).lower()
            metric_path = "_".join((*path, explicit_scope, _text(metric_name)))
            target = _canonical_metric(metric_path)
            if target:
                check = {
                    "scope": target[0],
                    "metric": target[1],
                    "direction": direction,
                    "value_basis": _text(value.get("value_basis") or "actual").lower(),
                }
                for public_key, aliases in {
                    "minimum": ("minimum", "min", "expected_min"),
                    "maximum": ("maximum", "max", "expected_max"),
                }.items():
                    raw = next(
                        (
                            value.get(alias)
                            for alias in aliases
                            if value.get(alias) is not None
                        ),
                        None,
                    )
                    parsed = _number(raw)
                    if parsed is not None:
                        check[public_key] = parsed
                if direction or "minimum" in check or "maximum" in check:
                    checks.append(check)
        for key, item in value.items():
            key_text = _text(key)
            current_path = (*path, key_text)
            target = _canonical_metric("_".join(current_path))
            item_direction = _direction(item)
            if target and item_direction:
                checks.append(
                    {
                        "scope": target[0],
                        "metric": target[1],
                        "direction": item_direction,
                    }
                )
            elif isinstance(item, (dict, list)):
                visit(item, current_path)

    visit(expected, ())
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for check in checks:
        unique[(check["scope"], check["metric"])] = check
    return list(unique.values())


def _explicit_assessment(
    expected: Any,
    deltas: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    requested = _expected_checks(expected)
    measured: list[dict[str, Any]] = []
    for check in requested:
        delta = (deltas.get(check["scope"]) or {}).get(check["metric"])
        if not isinstance(delta, dict):
            measured.append({**check, "met": None, "reason": "metric_unavailable"})
            continue
        direction = check.get("direction")
        actual_direction = delta.get("direction")
        direction_met = actual_direction == direction if direction else None
        value_basis = check.get("value_basis")
        if value_basis not in {"actual", "absolute_delta", "delta_pct"}:
            value_basis = "actual"
        compared_value = _number(delta.get(value_basis))
        minimum = _number(check.get("minimum"))
        maximum = _number(check.get("maximum"))
        range_met: bool | None = None
        if minimum is not None or maximum is not None:
            range_met = bool(
                compared_value is not None
                and (minimum is None or compared_value >= minimum)
                and (maximum is None or compared_value <= maximum)
            )
        requested_results = [
            result for result in (direction_met, range_met) if result is not None
        ]
        met = all(requested_results) if requested_results else None
        measured.append(
            {
                **check,
                "baseline": delta.get("baseline"),
                "actual": delta.get("actual"),
                "absolute_delta": delta.get("absolute_delta"),
                "delta_pct": delta.get("delta_pct"),
                "actual_direction": actual_direction,
                "direction_met": direction_met,
                "range_met": range_met,
                "compared_value": compared_value,
                "met": met,
            }
        )
    comparable = [row for row in measured if row.get("met") is not None]
    unavailable = [row for row in measured if row.get("met") is None]
    met_count = sum(row.get("met") is True for row in comparable)
    if not requested or not comparable or unavailable:
        status = "inconclusive"
    elif met_count == len(comparable):
        status = "successful"
    elif met_count == 0:
        status = "failed"
    else:
        status = "mixed"
    return status, {
        "basis": "explicit_expected_outcome",
        "expected": deepcopy(expected),
        "checks": measured,
        "requested_checks": len(requested),
        "comparable_checks": len(comparable),
        "unavailable_checks": len(unavailable),
        "heuristic_used": False,
    }


def _action_semantics(decision: dict[str, Any]) -> str | None:
    before = decision.get("before") if isinstance(decision.get("before"), dict) else {}
    after = decision.get("after") if isinstance(decision.get("after"), dict) else {}
    planned = (
        decision.get("planned_changes")
        if isinstance(decision.get("planned_changes"), dict)
        else {}
    )
    new_status = _text(after.get("status") or planned.get("status")).upper()
    if new_status == "PAUSED":
        return "pause_or_decrease"
    if new_status == "ACTIVE" and _text(before.get("status")).upper() != "ACTIVE":
        return "increase_or_activate"
    old_budget = _number(before.get("daily_budget_micro"))
    new_budget = _number(after.get("daily_budget_micro"))
    if new_budget is None:
        new_budget = _number(planned.get("daily_budget_micro"))
    if old_budget is not None and new_budget is not None:
        if new_budget < old_budget:
            return "pause_or_decrease"
        if new_budget > old_budget:
            return "increase_or_activate"
    action = _text(decision.get("action")).lower()
    if "pause" in action or "decrease" in action or "reduce" in action:
        return "pause_or_decrease"
    if "activate" in action or "increase" in action:
        return "increase_or_activate"
    return None


def _unscored_observation(
    decision: dict[str, Any],
    deltas: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Return measured observations without inventing a fixed success rule.

    A pause, increase, or activation can be correct for different reasons in
    different campaigns.  If the decision did not record its own expected
    outcome, later measurement must not retrofit a universal 80/90 percent
    threshold and call the decision successful or failed.
    """
    semantics = _action_semantics(decision)
    campaign = deltas.get("campaign") or {}
    observations = []
    for metric in (
        "sales_sar",
        "orders",
        "contribution_profit_sar",
        "spend_sar",
        "roas",
        "cpa_sar",
    ):
        delta = campaign.get(metric)
        if isinstance(delta, dict):
            observations.append({"scope": "campaign", "metric": metric, **delta})
    return "inconclusive", {
        "basis": "no_explicit_expected_outcome",
        "semantics": semantics,
        "heuristic_used": False,
        "objective": "grow_sales_while_protecting_contribution_profit",
        "observations": observations,
        "checks": [],
        "reason": (
            "the decision did not record an adaptive expected outcome; measured "
            "deltas are retained without imposing a universal success threshold"
        ),
    }


def _summary(status: str, label: str) -> str:
    labels = {
        "successful": "النتيجة المقاسة وافقت الهدف المسجل أو التقدير المحافظ",
        "failed": "النتيجة المقاسة لم توافق الهدف المسجل أو التقدير المحافظ",
        "mixed": "النتيجة المقاسة جمعت إشارات إيجابية وأخرى سلبية",
        "inconclusive": "الأدلة المقاسة المتاحة لا تكفي للحكم",
        "pending": "لم تكتمل نافذة القياس بعد",
    }
    return f"{labels[status]} عند نافذة {label}؛ هذا ارتباط مقاس وليس إثبات سببية."


async def _capture_snapshot(
    db: Any,
    user_id: str,
    decision: dict[str, Any],
    *,
    campaign_id: str | None,
    captured_at: datetime,
) -> dict[str, Any]:
    baseline = decision.get("baseline")
    timezone_name = (
        _text((baseline or {}).get("account_timezone"))
        if isinstance(baseline, dict)
        else ""
    ) or "Asia/Riyadh"
    entity_type = _text(decision.get("entity_type"))
    entity_id = _text(decision.get("entity_id")) or None
    before = decision.get("before") if isinstance(decision.get("before"), dict) else {}
    after = decision.get("after") if isinstance(decision.get("after"), dict) else {}
    evidence = (
        decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    )
    parent_id = (
        _text(
            evidence.get("parent_id")
            or after.get("ad_squad_id")
            or before.get("ad_squad_id")
        )
        or None
    )
    ad_squad_id = (
        entity_id
        if entity_type == "ad_squad"
        else parent_id if entity_type == "ad" else None
    )
    ad_id = entity_id if entity_type == "ad" else None
    return await capture_decision_baseline(
        db,
        user_id,
        account_id=_text(decision.get("account_id")),
        campaign_id=campaign_id,
        ad_squad_id=ad_squad_id,
        ad_id=ad_id,
        account_timezone=timezone_name,
        captured_at=captured_at,
        completed_days_only=True,
    )


async def _campaign_id(
    db: Any,
    user_id: str,
    decision: dict[str, Any],
) -> str | None:
    baseline = decision.get("baseline")
    if isinstance(baseline, dict) and _text(baseline.get("campaign_id")):
        return _text(baseline.get("campaign_id"))
    entity_type = _text(decision.get("entity_type"))
    entity_id = _text(decision.get("entity_id")) or None
    if entity_type == "campaign":
        return entity_id
    parent_id = None
    evidence = decision.get("evidence")
    if isinstance(evidence, dict):
        parent_id = _text(evidence.get("parent_id")) or None
    return await resolve_decision_campaign_id(
        db,
        user_id,
        account_id=_text(decision.get("account_id")),
        entity_type=entity_type,
        entity_id=entity_id,
        parent_id=parent_id,
    )


def _horizon_label(evaluation: dict[str, Any]) -> str:
    label = _text(evaluation.get("horizon"))
    if label:
        return label
    hours = int(_number(evaluation.get("horizon_hours")) or 0)
    return next(
        (item[0] for item in EVALUATION_HORIZONS if item[1] == hours),
        "",
    )


def _existing_horizons(decision: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for evaluation in decision.get("evaluations") or []:
        if not isinstance(evaluation, dict):
            continue
        if _text(evaluation.get("source")) != OUTCOME_SOURCE:
            continue
        label = _horizon_label(evaluation)
        if label:
            output[label] = evaluation
    return output


def _recorded_horizons_by_decision(
    entries: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Read every persisted evaluator horizon, not merely the latest one."""
    output: dict[str, set[str]] = {}
    for row in entries:
        if row.get("entry_type") != "evaluation":
            continue
        if _text(row.get("source")) != OUTCOME_SOURCE:
            continue
        payload = row.get("evaluation")
        if not isinstance(payload, dict):
            continue
        label = _horizon_label(payload)
        decision_id = _text(row.get("decision_id"))
        if label and decision_id:
            output.setdefault(decision_id, set()).add(label)
    return output


def _summary_recorded_horizons(summary: dict[str, Any]) -> set[str]:
    """Compatibility fallback for callers supplying already-aggregated rows."""
    evaluations = list(summary.get("evaluations") or [])
    latest = summary.get("latest_evaluation")
    if isinstance(latest, dict):
        evaluations.append(latest)
    return set(_existing_horizons({"evaluations": evaluations}))


def _rotation_state_id(tenant: str) -> str:
    tenant_digest = hashlib.sha256(tenant.encode("utf-8")).hexdigest()
    return f"snapchat-outcome-worker:{tenant_digest}"


async def _rotation_cursor(db: Any, tenant: str) -> str:
    state = await _collection(db, OUTCOME_WORKER_STATE_COLLECTION).find_one(
        {"_id": _rotation_state_id(tenant), "user_id": tenant},
        {"_id": 0, "cursor_decision_id": 1},
    )
    return _text((state or {}).get("cursor_decision_id"))


async def _persist_rotation_cursor(
    db: Any,
    tenant: str,
    *,
    decision_id: str,
    updated_at: datetime,
) -> None:
    await _collection(db, OUTCOME_WORKER_STATE_COLLECTION).update_one(
        {"_id": _rotation_state_id(tenant), "user_id": tenant},
        {
            "$set": {
                "cursor_decision_id": decision_id,
                "source": OUTCOME_SOURCE,
                "updated_at": updated_at.isoformat(),
                "schema_version": 1,
            }
        },
        upsert=True,
    )


async def _bounded_rotated_due(
    db: Any,
    tenant: str,
    due_summaries: list[tuple[datetime, dict[str, Any]]],
    *,
    limit: int,
    updated_at: datetime,
) -> list[tuple[datetime, dict[str, Any]]]:
    """Select a durable tenant-scoped round-robin slice before evaluation."""
    if len(due_summaries) <= limit:
        return due_summaries
    cursor = await _rotation_cursor(db, tenant)
    identities = [_text(pair[1].get("decision_id")) for pair in due_summaries]
    start = 0
    if cursor in identities:
        start = (identities.index(cursor) + 1) % len(due_summaries)
    rotated = due_summaries[start:] + due_summaries[:start]
    selected = rotated[:limit]
    # Advance before doing remote measurements.  A crash can delay a selected
    # row for one cycle, but cannot pin every later decision behind it forever.
    await _persist_rotation_cursor(
        db,
        tenant,
        decision_id=_text(selected[-1][1].get("decision_id")),
        updated_at=updated_at,
    )
    return selected


async def _measure_horizon(
    db: Any,
    user_id: str,
    decision: dict[str, Any],
    *,
    now: datetime,
    effective_at: datetime,
    label: str,
    hours: int,
    days: int,
    campaign_id: str | None,
) -> dict[str, Any]:
    timezone_name = (
        _text(
            ((decision.get("baseline") or {}).get("account_timezone"))
            if isinstance(decision.get("baseline"), dict)
            else None
        )
        or "Asia/Riyadh"
    )
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "Asia/Riyadh"
        local_tz = ZoneInfo(timezone_name)
    effective_local = effective_at.astimezone(local_tz)
    decision_day_start = datetime.combine(
        effective_local.date(), datetime.min.time(), tzinfo=local_tz
    )
    baseline_capture_at = decision_day_start.astimezone(timezone.utc)
    # Exclude the decision's partial local day.  The post window begins on the
    # next local day and is evaluated only after `days` complete days exist.
    window_end = (decision_day_start + timedelta(days=days + 1)).astimezone(
        timezone.utc
    )
    if now < window_end:
        return {
            "horizon": label,
            "horizon_hours": hours,
            "outcome_status": "pending",
            "due_at": window_end.isoformat(),
            "summary": _summary("pending", label),
            "appended": False,
        }
    baseline_snapshot = (
        decision.get("baseline") if isinstance(decision.get("baseline"), dict) else None
    )
    baseline_window = None
    if baseline_snapshot is not None:
        expected_baseline_date_to = (
            decision_day_start.date() - timedelta(days=1)
        ).isoformat()
        candidate_completed = _window(
            {"windows": baseline_snapshot.get("completed_windows") or []}, days
        )
        if (
            _text((candidate_completed or {}).get("date_to"))
            == expected_baseline_date_to
        ):
            baseline_window = candidate_completed
        if baseline_window is None:
            candidate = _window(baseline_snapshot, days)
            if (candidate or {}).get("includes_partial_current_day") is False and _text(
                (candidate or {}).get("date_to")
            ) == expected_baseline_date_to:
                baseline_window = candidate
    baseline_source = "decision_time_immutable_completed_local_days"
    capture_caveats: list[str] = []
    if baseline_window is None:
        baseline_source = "historical_completed_local_days_before_decision"
        try:
            baseline_snapshot = await _capture_snapshot(
                db,
                user_id,
                decision,
                campaign_id=campaign_id,
                captured_at=baseline_capture_at,
            )
            baseline_window = _window(baseline_snapshot, days)
        except Exception as exc:
            baseline_snapshot = None
            capture_caveats.append(f"baseline_capture_failed:{type(exc).__name__}")
    try:
        post_snapshot = await _capture_snapshot(
            db,
            user_id,
            decision,
            campaign_id=campaign_id,
            captured_at=window_end,
        )
        post_window = _window(post_snapshot, days)
    except Exception as exc:
        post_snapshot = None
        post_window = None
        capture_caveats.append(f"post_capture_failed:{type(exc).__name__}")
    completeness = _data_completeness(
        baseline_snapshot,
        post_snapshot,
        baseline_window,
        post_window,
    )
    time_alignment_complete = not (
        (baseline_window or {}).get("includes_partial_current_day") is True
        or (post_window or {}).get("includes_partial_current_day") is True
    )
    if not time_alignment_complete:
        completeness["complete"] = False
        completeness.setdefault("reasons", []).append(
            "partial_calendar_day_windows_are_not_time_aligned"
        )
    deltas = _all_deltas(baseline_window, post_window)
    product_attribution = _product_attribution(post_window)
    caveats = list(capture_caveats)
    if (baseline_window or {}).get("includes_partial_current_day") is True:
        caveats.append("baseline_includes_partial_calendar_day")
    if (post_window or {}).get("includes_partial_current_day") is True:
        caveats.append("post_window_includes_partial_calendar_day")
    if (baseline_window or {}).get("attribution_caution") is True or (
        post_window or {}
    ).get("attribution_caution") is True:
        caveats.append("product_attribution_has_a_large_unresolved_share")
    caveats.append("baseline_uses_completed_local_calendar_days")
    caveats.extend(completeness["reasons"])
    caveats.extend(
        [
            "daily_source_boundaries_may_include_a_partial_calendar_day",
            "manual_or_salla_source_is_not_assumed_to_be_whatsapp",
            "explicit_other_platform_product_sales_are_excluded_from_snapchat_contribution",
        ]
    )
    if not completeness["complete"]:
        status = "inconclusive"
        expected_vs_actual = {
            "basis": "data_completeness_gate",
            "expected": deepcopy(decision.get("expected")),
            "checks": [],
            "heuristic_used": decision.get("expected") is None,
        }
    elif decision.get("expected") is not None:
        status, expected_vs_actual = _explicit_assessment(
            decision.get("expected"),
            deltas,
        )
    else:
        status, expected_vs_actual = _unscored_observation(decision, deltas)
        caveats.append(
            "no_fixed_success_rule_was_applied_without_recorded_expectations"
        )
    if status not in OUTCOME_STATUSES:  # pragma: no cover - defensive invariant
        status = "inconclusive"
    primary = []
    for metric in (
        "contribution_profit_sar",
        "sales_sar",
        "orders",
        "spend_sar",
        "roas",
    ):
        delta = (deltas.get("campaign") or {}).get(metric)
        if delta:
            primary.append({"scope": "campaign", "metric": metric, **delta})
    current_day_partial = {
        "baseline": (baseline_window or {}).get("includes_partial_current_day") is True,
        "post": (post_window or {}).get("includes_partial_current_day") is True,
        "treatment": (
            "retained_as_a_caveat; equal-length windows use corresponding "
            "calendar-day cutoffs"
        ),
    }
    result = {
        "horizon": label,
        "horizon_hours": hours,
        "window": {
            "decision_effective_at": effective_at.isoformat(),
            "post_window_ends_at": window_end.isoformat(),
            "length_hours": hours,
            "comparison": (
                "equal_length_immediately_prior_baseline"
                if time_alignment_complete
                else "not_comparable_partial_calendar_day_windows"
            ),
            "baseline_source": baseline_source,
            "decision_partial_local_day_excluded": True,
            "baseline_date_from": (baseline_window or {}).get("date_from"),
            "baseline_date_to": (baseline_window or {}).get("date_to"),
            "post_date_from": (post_window or {}).get("date_from"),
            "post_date_to": (post_window or {}).get("date_to"),
        },
        "outcome_status": status,
        "summary": _summary(status, label),
        "expected_vs_actual": expected_vs_actual,
        "deltas": deltas,
        "campaign_delta": deltas["campaign"],
        "account_delta": deltas["account"],
        "store_delta": deltas["store"],
        "attribution_product_comparison": product_attribution,
        "data_completeness": completeness,
        "current_day_partial": current_day_partial,
        "primary_measured_evidence": primary,
        "association_statement": (
            "These are observed before/after associations; they do not establish "
            "that the advertising decision caused the changes."
        ),
        "caveats": sorted(set(caveats)),
        "evaluated_at": now.isoformat(),
        "appended": False,
    }
    payload = {key: value for key, value in result.items() if key != "appended"}
    payload["evidence"] = {
        "window": result["window"],
        "deltas": deltas,
        "attribution_product_comparison": product_attribution,
        "data_completeness": completeness,
        "current_day_partial": current_day_partial,
        "primary_measured_evidence": primary,
        "association_statement": result["association_statement"],
        "caveats": result["caveats"],
    }
    # Missing/partial sources are a retryable measurement attempt, not the
    # permanent business verdict for this horizon.
    if not completeness["complete"]:
        result["retryable"] = True
        result["appended"] = False
        return result
    source_key = f"snapchat-decision-outcome:v1:{decision['decision_id']}:{hours}h"
    await decision_ledger.append_decision_evaluation(
        db,
        user_id,
        _text(decision.get("decision_id")),
        payload,
        actor_kind="mezan_ai",
        outcome_status=status,
        source=OUTCOME_SOURCE,
        source_event_key=source_key,
        evaluated_at=now,
    )
    result["source_event_key"] = source_key
    result["appended"] = True
    return result


async def evaluate_ad_decision(
    db: Any,
    user_id: str,
    decision: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate every elapsed, not-yet-recorded horizon for one decision."""
    tenant = _text(user_id)
    if not tenant:
        raise ValueError("user_id is required")
    item = _as_dict(decision)
    decision_id = _text(item.get("decision_id"))
    if not decision_id:
        raise ValueError("decision_id is required")
    # Refresh before checking recorded horizons.  This makes a replay with a
    # caller's stale decision detail idempotent and enforces tenant ownership
    # before any measurement query runs.
    stored = await decision_ledger.get_ad_decision(db, tenant, decision_id)
    if stored is None:
        raise ValueError("decision not found")
    item = stored
    current = _now(now)
    effective_at = _utc(item.get("effective_at"))
    if effective_at is None:
        return {
            "decision_id": decision_id,
            "outcome_status": "inconclusive",
            "evaluated": 0,
            "pending": 0,
            "evaluations": [],
            "caveats": ["decision_effective_at_is_invalid"],
        }
    if _text(item.get("execution_status")).lower() not in {"completed", "observed"}:
        return {
            "decision_id": decision_id,
            "outcome_status": "inconclusive",
            "evaluated": 0,
            "pending": 0,
            "evaluations": [],
            "caveats": ["decision_was_not_confirmed_or_observed_as_effective"],
        }
    existing = _existing_horizons(item)
    campaign_id = await _campaign_id(db, tenant, item)
    results: list[dict[str, Any]] = []
    for label, hours, days in EVALUATION_HORIZONS:
        if label in existing:
            row = existing[label]
            results.append(
                {
                    "horizon": label,
                    "horizon_hours": hours,
                    "outcome_status": _text(row.get("outcome_status"))
                    or "inconclusive",
                    "summary": row.get("summary"),
                    "evaluated_at": row.get("evaluated_at"),
                    "appended": False,
                    "already_recorded": True,
                }
            )
            continue
        results.append(
            await _measure_horizon(
                db,
                tenant,
                item,
                now=current,
                effective_at=effective_at,
                label=label,
                hours=hours,
                days=days,
                campaign_id=campaign_id,
            )
        )
    measured = [row for row in results if row["outcome_status"] != "pending"]
    latest_status = measured[-1]["outcome_status"] if measured else "pending"
    return {
        "decision_id": decision_id,
        "account_id": item.get("account_id"),
        "campaign_id": campaign_id,
        "outcome_status": latest_status,
        "evaluated": sum(row.get("appended") is True for row in results),
        "already_recorded": sum(row.get("already_recorded") is True for row in results),
        "pending": sum(row["outcome_status"] == "pending" for row in results),
        "evaluations": results,
        "association_statement": (
            "Outcome labels describe measured association and never claim causality."
        ),
    }


async def evaluate_due_ad_decisions(
    db: Any,
    user_id: str,
    *,
    now: datetime | None = None,
    decision_id: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Evaluate elapsed horizons for tenant-scoped confirmed decisions."""
    tenant = _text(user_id)
    if not tenant:
        raise ValueError("user_id is required")
    bounded_limit = max(1, min(int(limit or 25), 100))
    if decision_id is not None:
        detail = await decision_ledger.get_ad_decision(db, tenant, decision_id)
        if detail is None:
            raise ValueError("decision not found")
        decisions = [detail]
    else:
        entries = await decision_ledger._tenant_entries(db, tenant)
        summaries = decision_ledger._aggregate_decisions(entries)
        current = _now(now)
        recorded_by_decision = _recorded_horizons_by_decision(entries)
        due_summaries: list[tuple[datetime, dict[str, Any]]] = []
        for summary in summaries.values():
            if _text(summary.get("execution_status")).lower() not in {
                "completed",
                "observed",
            }:
                continue
            effective_at = _utc(summary.get("effective_at"))
            if effective_at is None:
                continue
            timezone_name = _text(
                ((summary.get("baseline") or {}).get("account_timezone"))
                if isinstance(summary.get("baseline"), dict)
                else None
            ) or "Asia/Riyadh"
            try:
                local_tz = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                local_tz = ZoneInfo("Asia/Riyadh")
            decision_day_start = datetime.combine(
                effective_at.astimezone(local_tz).date(),
                datetime.min.time(),
                tzinfo=local_tz,
            )
            identity = _text(summary.get("decision_id"))
            recorded = set(recorded_by_decision.get(identity) or set())
            recorded.update(_summary_recorded_horizons(summary))
            missing_due_at = [
                (decision_day_start + timedelta(days=days + 1)).astimezone(
                    timezone.utc
                )
                for label, _, days in EVALUATION_HORIZONS
                if label not in recorded
                and (
                    decision_day_start + timedelta(days=days + 1)
                ).astimezone(timezone.utc)
                <= current
            ]
            if missing_due_at:
                due_summaries.append((min(missing_due_at), summary))
        due_summaries.sort(
            key=lambda pair: (
                pair[0],
                _text(pair[1].get("effective_at")),
                _text(pair[1].get("decision_id")),
            )
        )
        selected_summaries = await _bounded_rotated_due(
            db,
            tenant,
            due_summaries,
            limit=bounded_limit,
            updated_at=current,
        )
        decisions = []
        for _, summary in selected_summaries:
            identity = _text(summary.get("decision_id"))
            detail = await decision_ledger.get_ad_decision(db, tenant, identity)
            if detail is not None:
                decisions.append(detail)
    results = [
        await evaluate_ad_decision(db, tenant, decision, now=now)
        for decision in decisions
    ]
    return {
        "provider": "snapchat_ads",
        "source": OUTCOME_SOURCE,
        "limit": bounded_limit,
        "eligible_due": len(decisions) if decision_id is not None else len(due_summaries),
        "deferred_due": (
            0
            if decision_id is not None
            else max(0, len(due_summaries) - len(decisions))
        ),
        "scanned": len(decisions),
        "evaluated": sum(int(row.get("evaluated") or 0) for row in results),
        "already_recorded": sum(
            int(row.get("already_recorded") or 0) for row in results
        ),
        "pending": sum(int(row.get("pending") or 0) for row in results),
        "results": results,
    }


__all__ = [
    "EVALUATION_HORIZONS",
    "OUTCOME_SOURCE",
    "OUTCOME_WORKER_STATE_COLLECTION",
    "evaluate_ad_decision",
    "evaluate_due_ad_decisions",
]
