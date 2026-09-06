"""Monthly net-profit objective for Campaign AI.

The owner supplies one commercial objective: the minimum monthly net profit that
must be protected before discretionary expansion.  This module does not choose
marketing actions.  It stores the objective, derives deterministic progress from
Mezan dashboard profit totals, and injects that progress into the OpenAI evidence
context.
"""
from __future__ import annotations

import calendar
import hashlib
import json
from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field


COLLECTION = "mezan_campaign_ai_monthly_profit_goals_v1"
SNAPSHOT_COLLECTION = "mezan_campaign_ai_recommendations_v1"
DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR = 100_000.0
CONTRACT_VERSION = "campaign_ai_monthly_profit_goal_v2"
ACCOUNT_TIMEZONE = "Asia/Riyadh"
DEFAULT_GOAL_CONFIG_VERSION = "default_owner_safety_floor_v1"
# The scheduler's established cadence is five hours.  One hour of delivery
# tolerance keeps a normally delayed run usable without making next_run_at the
# sole proof that the underlying calculation is recent.
GOAL_EVIDENCE_MAX_AGE_SECONDS = 6 * 60 * 60
GOAL_EVIDENCE_FUTURE_SKEW_SECONDS = 5 * 60

_CURRENT_GOAL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "campaign_ai_monthly_profit_goal_context",
    default=None,
)


class MonthlyProfitGoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_net_profit_sar: float = Field(ge=1_000, le=100_000_000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def with_goal_config_identity(goal: dict[str, Any]) -> dict[str, Any]:
    """Attach a stable identity without adding a new storage schema."""
    result = dict(goal)
    target = _number(result.get("minimum_net_profit_sar"))
    configured = result.get("configured")
    updated_at = result.get("updated_at")
    if target is None or configured not in {True, False}:
        return {
            **result,
            "goal_config_version": None,
            "goal_config_id": None,
        }
    version = (
        str(updated_at)
        if configured is True and updated_at
        else DEFAULT_GOAL_CONFIG_VERSION
    )
    identity = {
        "configured": configured,
        "minimum_net_profit_sar": round(target, 2),
        "source": result.get("source"),
        "version": version,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **result,
        "minimum_net_profit_sar": round(target, 2),
        "goal_config_version": version,
        "goal_config_id": f"goal-config-{fingerprint}",
    }


async def ensure_indexes(db: Any) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1)],
        unique=True,
        name="campaign_ai_monthly_profit_goal_user_unique",
    )


async def load_goal(
    db: Any,
    user_id: str,
    *,
    ensure_index: bool = True,
) -> dict[str, Any]:
    if ensure_index:
        await ensure_indexes(db)
    row = await db[COLLECTION].find_one({"user_id": user_id}, {"_id": 0})
    if not row:
        return with_goal_config_identity({
            "minimum_net_profit_sar": DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR,
            "configured": False,
            "source": "default_owner_safety_floor",
            "updated_at": None,
        })
    stored_target = _number(row.get("minimum_net_profit_sar"))
    if stored_target is None:
        raise RuntimeError("monthly_profit_goal_config_invalid")
    return with_goal_config_identity({
        "minimum_net_profit_sar": stored_target,
        "configured": True,
        "source": "owner_configured",
        "updated_at": row.get("updated_at"),
    })


async def save_goal(
    db: Any,
    user_id: str,
    payload: MonthlyProfitGoalInput,
) -> dict[str, Any]:
    await ensure_indexes(db)
    now_iso = _now()
    document = {
        "user_id": user_id,
        "minimum_net_profit_sar": round(float(payload.minimum_net_profit_sar), 2),
        "updated_at": now_iso,
    }
    write = await db[COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    if (
        getattr(write, "matched_count", 0) == 0
        and getattr(write, "upserted_id", None) is None
    ):
        raise RuntimeError("monthly_profit_goal_save_not_persisted")
    return with_goal_config_identity({
        "minimum_net_profit_sar": document["minimum_net_profit_sar"],
        "configured": True,
        "source": "owner_configured",
        "updated_at": now_iso,
    })


def _source_data_through(profit_envelope: dict[str, Any]) -> tuple[str | None, str]:
    value = profit_envelope.get("data_through")
    if not isinstance(value, str) or not value.strip():
        return None, "source_watermark_unavailable"
    candidate = value.strip()
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            date.fromisoformat(candidate)
        except ValueError:
            return None, "source_watermark_invalid"
    return candidate, "source_watermark"


async def _month_to_date_totals(
    loader: Callable[..., Awaitable[Any]] | None,
    user_id: str,
    end: date,
) -> dict[str, Any]:
    start = end.replace(day=1)
    if loader is None:
        return {
            "available": False,
            "reason": "dashboard_profit_loader_unavailable",
            "from": start.isoformat(),
            "to": end.isoformat(),
            "timezone": ACCOUNT_TIMEZONE,
            "calculated_at": None,
            "data_through": None,
            "data_through_status": "source_watermark_unavailable",
        }
    payload = await loader(
        user={"id": user_id},
        from_date=start.isoformat(),
        to_date=end.isoformat(),
        payment_methods=None,
        shipping_companies=None,
        include_legacy_analyses=False,
        allow_self_heal=False,
    )
    totals = (payload or {}).get("totals") or {}
    profit_envelope = (payload or {}).get("profit_envelope") or {}
    data_through, data_through_status = _source_data_through(profit_envelope)
    return {
        "available": _number(totals.get("net_profit")) is not None,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timezone": ACCOUNT_TIMEZONE,
        "calculated_at": _now(),
        "data_through": data_through,
        "data_through_status": data_through_status,
        "net_profit": _number(totals.get("net_profit")),
        "total_sales": _number(totals.get("total_sales")),
        "total_orders": _number(totals.get("total_orders")),
        "total_ads_cost": _number(totals.get("total_ads_cost")),
        "total_product_cost": _number(totals.get("total_product_cost")),
        "total_payment_fees": _number(totals.get("total_payment_fees")),
        "total_shipping_cost": _number(totals.get("total_shipping_cost")),
        "operating_expenses_total": _number(totals.get("operating_expenses_total")),
        "missing_product_cost_count": totals.get("missing_product_cost_count"),
        "incomplete_profit_orders_count": totals.get("incomplete_profit_orders_count"),
        "profit_accounting": profit_envelope.get("quality"),
        "profit_contract_version": profit_envelope.get("contract_version"),
    }


def _derive_goal_progress(
    *,
    goal: dict[str, Any],
    month_to_date: dict[str, Any],
    end: date,
) -> dict[str, Any]:
    target = round(float(goal["minimum_net_profit_sar"]), 2)
    days_in_month = calendar.monthrange(end.year, end.month)[1]
    elapsed_days = max(1, end.day)
    remaining_days = max(0, days_in_month - elapsed_days)
    net_profit = _number(month_to_date.get("net_profit"))
    base = {
        **goal,
        "contract_version": CONTRACT_VERSION,
        "month": f"{end.year:04d}-{end.month:02d}",
        "currency": "SAR",
        "timezone": ACCOUNT_TIMEZONE,
        "period": {
            "kind": "calendar_month_to_date",
            "month": f"{end.year:04d}-{end.month:02d}",
            "from": month_to_date.get("from") or end.replace(day=1).isoformat(),
            "to_requested": month_to_date.get("to") or end.isoformat(),
            "timezone": ACCOUNT_TIMEZONE,
        },
        "calculated_at": month_to_date.get("calculated_at"),
        "calculation_attempted_at": month_to_date.get("calculation_attempted_at"),
        "data_through": month_to_date.get("data_through"),
        "data_through_status": (
            month_to_date.get("data_through_status")
            or "source_watermark_unavailable"
        ),
        "days_in_month": days_in_month,
        "days_elapsed": elapsed_days,
        "days_remaining": remaining_days,
        "month_to_date": month_to_date,
        "objective": (
            "تحقيق صافي ربح شهري لا يقل عن الهدف، وحماية الحد الأدنى قبل التوسع الاختياري."
        ),
    }
    envelope_quality = month_to_date.get("profit_accounting")
    if isinstance(envelope_quality, dict):
        accounting_quality_known = envelope_quality.get("known") is True
        accounting_complete = bool(
            accounting_quality_known
            and envelope_quality.get("complete") is True
            and envelope_quality.get("scale_safe") is True
        )
        missing_costs = envelope_quality.get("missing_product_cost_count")
        incomplete_orders = envelope_quality.get("incomplete_profit_orders_count")
    else:
        missing_costs = month_to_date.get("missing_product_cost_count")
        incomplete_orders = month_to_date.get("incomplete_profit_orders_count")
        try:
            missing_costs = int(missing_costs)
        except (TypeError, ValueError, OverflowError):
            missing_costs = None
        try:
            incomplete_orders = int(incomplete_orders)
        except (TypeError, ValueError, OverflowError):
            incomplete_orders = None
        accounting_quality_known = (
            missing_costs is not None and incomplete_orders is not None
        )
        accounting_complete = bool(
            accounting_quality_known
            and missing_costs == 0
            and incomplete_orders == 0
        )
    accounting_incomplete = bool(accounting_quality_known and not accounting_complete)
    base = {
        **base,
        "profit_accounting_complete": (
            accounting_complete if accounting_quality_known else None
        ),
        "profit_accounting_quality_known": accounting_quality_known,
        # Execution remains fail-closed when completeness is unknown.
        "scale_execution_allowed_by_profit_accounting": accounting_complete,
        "profit_accounting_quality": (
            dict(envelope_quality)
            if isinstance(envelope_quality, dict)
            else {
                "missing_product_cost_count": missing_costs,
                "incomplete_profit_orders_count": incomplete_orders,
            }
        ),
    }
    if net_profit is None:
        reason = month_to_date.get("reason")
        issues = (
            envelope_quality.get("issues")
            if isinstance(envelope_quality, dict)
            else None
        )
        if not reason and issues:
            reason = f"profit_accounting:{','.join(str(item) for item in issues)}"
        progress_state = (
            "calculation_failed"
            if str(reason or "").startswith("month_to_date_profit_failed:")
            else "quality_incomplete"
            if isinstance(envelope_quality, dict) and not accounting_complete
            else "missing"
        )
        return {
            **base,
            "progress_available": False,
            "progress_state": progress_state,
            "progress_unavailable_reason": reason or "net_profit_unavailable",
            "status": "profit_data_unavailable",
            "phase": "protect_data_quality",
            "net_profit_to_date_sar": None,
            "remaining_to_target_sar": None,
            "required_daily_net_profit_sar": None,
            "projected_month_end_net_profit_sar": None,
            "projected_gap_sar": None,
            "target_coverage_pct": None,
        }

    remaining = max(0.0, target - net_profit)
    required_daily = remaining / remaining_days if remaining_days else remaining
    projected = net_profit / elapsed_days * days_in_month
    projected_gap = projected - target
    coverage = (net_profit / target * 100.0) if target else 100.0
    if accounting_incomplete:
        status = "profit_accounting_incomplete"
        phase = "protect_data_quality"
    elif net_profit >= target:
        status = "minimum_target_covered"
        phase = "expand_above_floor"
    elif projected >= target:
        status = "on_track"
        phase = "protect_target_path"
    else:
        status = "behind_target"
        phase = "recover_profit_gap"
    return {
        **base,
        "progress_available": True,
        "progress_state": (
            "quality_incomplete" if accounting_incomplete else "available"
        ),
        "progress_unavailable_reason": None,
        "status": status,
        "phase": phase,
        "net_profit_to_date_sar": round(net_profit, 2),
        "remaining_to_target_sar": round(remaining, 2),
        "required_daily_net_profit_sar": round(required_daily, 2),
        "projected_month_end_net_profit_sar": round(projected, 2),
        "projected_gap_sar": round(projected_gap, 2),
        "target_coverage_pct": round(coverage, 2),
        "expansion_policy": (
            "لا تطارد المبيعات أو ROAS كهدف مستقل. زد الإنفاق فقط عندما يتوقع أن يرفع صافي الربح "
            "مع حماية مسار الحد الأدنى. إذا كان المسار دون الهدف، أعط الأولوية لسد فجوة الربح."
        ),
    }


async def build_goal_context(
    db: Any,
    user_id: str,
    *,
    loader: Callable[..., Awaitable[Any]] | None,
    end: date,
) -> dict[str, Any]:
    goal = await load_goal(db, user_id)
    try:
        month_to_date = await _month_to_date_totals(loader, user_id, end)
    except Exception as exc:
        month_to_date = {
            "available": False,
            "reason": f"month_to_date_profit_failed:{type(exc).__name__}",
            "from": end.replace(day=1).isoformat(),
            "to": end.isoformat(),
            "timezone": ACCOUNT_TIMEZONE,
            "calculated_at": None,
            "calculation_attempted_at": _now(),
            "data_through": None,
            "data_through_status": "source_watermark_unavailable",
        }
    return _derive_goal_progress(goal=goal, month_to_date=month_to_date, end=end)


def goal_context_unavailable(*, end: date, reason: str) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "minimum_net_profit_sar": None,
        "configured": None,
        "source": "goal_config_unavailable",
        "updated_at": None,
        "goal_config_version": None,
        "goal_config_id": None,
        "month": f"{end.year:04d}-{end.month:02d}",
        "currency": "SAR",
        "timezone": ACCOUNT_TIMEZONE,
        "period": {
            "kind": "calendar_month_to_date",
            "month": f"{end.year:04d}-{end.month:02d}",
            "from": end.replace(day=1).isoformat(),
            "to_requested": end.isoformat(),
            "timezone": ACCOUNT_TIMEZONE,
        },
        "calculated_at": None,
        "data_through": None,
        "data_through_status": "source_watermark_unavailable",
        "progress_available": False,
        "progress_state": "calculation_failed",
        "progress_unavailable_reason": reason,
        "status": "goal_context_unavailable",
        "phase": "protect_data_quality",
        "net_profit_to_date_sar": None,
        "remaining_to_target_sar": None,
        "required_daily_net_profit_sar": None,
        "projected_month_end_net_profit_sar": None,
        "projected_gap_sar": None,
        "target_coverage_pct": None,
        "profit_accounting_complete": None,
        "profit_accounting_quality_known": False,
        "scale_execution_allowed_by_profit_accounting": False,
    }


def with_snapshot_provenance(
    goal_context: dict[str, Any],
    *,
    run_id: str,
    snapshot_id: str,
    snapshot_generated_at: str,
) -> dict[str, Any]:
    return {
        **goal_context,
        "historical_recommendation_authority_renewed": False,
        "provenance": {
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "snapshot_generated_at": snapshot_generated_at,
        },
    }


def _display_unavailable(
    *,
    current_goal: dict[str, Any],
    current_month: date,
    state: str,
    reason: str,
    snapshot_goal: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preserved = snapshot_goal if isinstance(snapshot_goal, dict) else {}
    return {
        **current_goal,
        "contract_version": CONTRACT_VERSION,
        "month": f"{current_month.year:04d}-{current_month.month:02d}",
        "currency": "SAR",
        "timezone": ACCOUNT_TIMEZONE,
        "period": {
            "kind": "calendar_month_to_date",
            "month": f"{current_month.year:04d}-{current_month.month:02d}",
            "from": current_month.replace(day=1).isoformat(),
            "to_requested": current_month.isoformat(),
            "timezone": ACCOUNT_TIMEZONE,
        },
        "calculated_at": preserved.get("calculated_at"),
        "data_through": preserved.get("data_through"),
        "data_through_status": (
            preserved.get("data_through_status")
            or "source_watermark_unavailable"
        ),
        "progress_available": False,
        "progress_state": state,
        "progress_unavailable_reason": reason,
        "status": "profit_data_unavailable",
        "phase": "protect_data_quality",
        "net_profit_to_date_sar": None,
        "remaining_to_target_sar": None,
        "required_daily_net_profit_sar": None,
        "projected_month_end_net_profit_sar": None,
        "projected_gap_sar": None,
        "target_coverage_pct": None,
        "scale_execution_allowed_by_profit_accounting": False,
        "provenance": preserved.get("provenance"),
        "historical_recommendation_authority_renewed": False,
        "evidence": evidence,
    }


def _aware_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _evidence_quality(snapshot_goal: dict[str, Any]) -> str:
    if snapshot_goal.get("profit_accounting_quality_known") is not True:
        return "unknown"
    return (
        "complete"
        if snapshot_goal.get("profit_accounting_complete") is True
        else "incomplete"
    )


def _goal_evidence_for_display(
    *,
    snapshot_goal: dict[str, Any],
    current_month: date,
    current_time: datetime,
    snapshot_next_run_at: Any,
) -> tuple[dict[str, Any], str | None]:
    """Validate stored profit evidence without manufacturing legacy metadata."""
    month_to_date = snapshot_goal.get("month_to_date")
    raw_period = snapshot_goal.get("period")
    evidence_period = None
    if isinstance(month_to_date, dict):
        evidence_period = {
            "kind": raw_period.get("kind") if isinstance(raw_period, dict) else None,
            "month": raw_period.get("month") if isinstance(raw_period, dict) else None,
            "from": month_to_date.get("from"),
            "to": month_to_date.get("to"),
            "timezone": month_to_date.get("timezone"),
        }
    calculated_raw = snapshot_goal.get("calculated_at")
    data_through = snapshot_goal.get("data_through")
    data_through_status = snapshot_goal.get("data_through_status")
    if not isinstance(data_through_status, str) or not data_through_status:
        data_through_status = "source_watermark_unavailable"
    base = {
        "valid": False,
        "freshness_status": "invalid",
        "validation_reason": None,
        "age_seconds": None,
        "max_age_seconds": GOAL_EVIDENCE_MAX_AGE_SECONDS,
        "calculated_at": calculated_raw,
        "next_run_at": snapshot_next_run_at,
        "data_through": data_through,
        "data_through_status": data_through_status,
        "period": evidence_period,
        "accounting_quality": _evidence_quality(snapshot_goal),
    }

    def invalid(reason: str, freshness_status: str = "invalid"):
        return {
            **base,
            "freshness_status": freshness_status,
            "validation_reason": reason,
        }, reason

    if snapshot_next_run_at is None or (
        isinstance(snapshot_next_run_at, str) and not snapshot_next_run_at.strip()
    ):
        return invalid("monthly_goal_snapshot_next_run_at_missing", "unknown")
    next_run = _aware_timestamp(snapshot_next_run_at)
    if next_run is None:
        return invalid("monthly_goal_snapshot_next_run_at_invalid", "unknown")
    base["next_run_at"] = next_run.isoformat()
    if snapshot_goal.get("contract_version") != CONTRACT_VERSION:
        return invalid("monthly_goal_snapshot_contract_missing_or_unsupported")
    if not isinstance(month_to_date, dict) or not isinstance(raw_period, dict):
        return invalid("monthly_goal_snapshot_mtd_metadata_missing")

    calculated = _aware_timestamp(calculated_raw)
    if calculated is None:
        return invalid("monthly_goal_snapshot_calculated_at_missing_or_invalid", "unknown")
    base["calculated_at"] = calculated.isoformat()
    mtd_calculated = _aware_timestamp(month_to_date.get("calculated_at"))
    if mtd_calculated is None or mtd_calculated != calculated:
        return invalid("monthly_goal_snapshot_calculated_at_mismatch")

    current_utc = (
        current_time.replace(tzinfo=timezone.utc)
        if current_time.tzinfo is None
        else current_time.astimezone(timezone.utc)
    )
    age_seconds = int((current_utc - calculated).total_seconds())
    base["age_seconds"] = age_seconds
    if age_seconds < -GOAL_EVIDENCE_FUTURE_SKEW_SECONDS:
        return invalid("monthly_goal_snapshot_calculated_at_in_future", "invalid")
    if current_utc > next_run:
        return invalid("monthly_goal_snapshot_past_next_run_at", "stale")
    if next_run < calculated:
        return invalid("monthly_goal_snapshot_next_run_before_calculation")
    if age_seconds > GOAL_EVIDENCE_MAX_AGE_SECONDS:
        return invalid("monthly_goal_snapshot_calculated_at_too_old", "stale")

    try:
        mtd_start = date.fromisoformat(str(month_to_date.get("from") or ""))
        mtd_end = date.fromisoformat(str(month_to_date.get("to") or ""))
    except ValueError:
        return invalid("monthly_goal_snapshot_mtd_period_invalid")
    expected_month = f"{current_month.year:04d}-{current_month.month:02d}"
    if (mtd_start.year, mtd_start.month) != (
        current_month.year,
        current_month.month,
    ) or (mtd_end.year, mtd_end.month) != (
        current_month.year,
        current_month.month,
    ):
        return invalid("monthly_goal_snapshot_mtd_month_mismatch")
    if mtd_start != current_month.replace(day=1):
        return invalid("monthly_goal_snapshot_mtd_start_not_month_start")
    if month_to_date.get("timezone") != ACCOUNT_TIMEZONE:
        return invalid("monthly_goal_snapshot_mtd_timezone_mismatch")
    if mtd_end != current_month:
        return invalid("monthly_goal_snapshot_mtd_end_not_current_date", "stale")
    if (
        raw_period.get("kind") != "calendar_month_to_date"
        or raw_period.get("month") != expected_month
        or raw_period.get("from") != mtd_start.isoformat()
        or raw_period.get("to_requested") != mtd_end.isoformat()
        or raw_period.get("timezone") != ACCOUNT_TIMEZONE
    ):
        return invalid("monthly_goal_snapshot_period_contract_mismatch")
    return {
        **base,
        "valid": True,
        "freshness_status": "fresh",
        "validation_reason": None,
    }, None


def reconcile_goal_for_display(
    *,
    current_goal: dict[str, Any],
    snapshot_goal: dict[str, Any] | None,
    current_month: date,
    current_time: datetime | None = None,
    snapshot_next_run_at: str | None = None,
) -> dict[str, Any]:
    """Build a current read view without mutating or refreshing snapshot facts."""
    current = with_goal_config_identity(current_goal)
    if current.get("goal_config_id") is None:
        return goal_context_unavailable(
            end=current_month,
            reason="goal_config_read_failed",
        )
    if not isinstance(snapshot_goal, dict):
        return _display_unavailable(
            current_goal=current,
            current_month=current_month,
            state="missing",
            reason="monthly_goal_snapshot_missing",
        )
    expected_month = f"{current_month.year:04d}-{current_month.month:02d}"
    if snapshot_goal.get("month") != expected_month:
        return _display_unavailable(
            current_goal=current,
            current_month=current_month,
            state="stale",
            reason="monthly_goal_snapshot_month_mismatch",
            snapshot_goal=snapshot_goal,
        )
    evidence, invalid_reason = _goal_evidence_for_display(
        snapshot_goal=snapshot_goal,
        current_month=current_month,
        current_time=current_time or _utcnow(),
        snapshot_next_run_at=snapshot_next_run_at,
    )
    if invalid_reason is not None:
        return _display_unavailable(
            current_goal=current,
            current_month=current_month,
            state="stale",
            reason=invalid_reason,
            snapshot_goal=snapshot_goal,
            evidence=evidence,
        )

    config_matches = snapshot_goal.get("goal_config_id") == current.get("goal_config_id")
    if config_matches:
        return {
            **snapshot_goal,
            **current,
            "historical_recommendation_authority_renewed": False,
            "evidence": evidence,
        }

    month_to_date = snapshot_goal.get("month_to_date")
    # The shared validation above proves this is the current Riyadh calendar
    # MTD and that its timestamps are fresh before the stored fact is reused.
    evidence_end = date.fromisoformat(str(month_to_date["to"]))
    derived = _derive_goal_progress(
        goal=current,
        month_to_date=dict(month_to_date),
        end=evidence_end,
    )
    return {
        **derived,
        "progress_state": "config_mismatch",
        "underlying_progress_state": derived.get("progress_state"),
        "display_derivation": "current_goal_from_stored_month_to_date",
        "provenance": snapshot_goal.get("provenance"),
        "historical_recommendation_authority_renewed": False,
        "evidence": evidence,
    }


def wrap_business_profit_context(
    base: Callable[..., Awaitable[dict[str, Any]]],
    get_runtime_context: Callable[[], Any],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def goal_aware_context(
        loader: Callable[..., Awaitable[Any]] | None,
        user_id: str,
        end: date,
    ) -> dict[str, Any]:
        clear_goal_context()
        try:
            try:
                runtime = get_runtime_context()
                goal_context = await build_goal_context(
                    runtime.db,
                    user_id,
                    loader=loader,
                    end=end,
                )
            except Exception as exc:
                goal_context = goal_context_unavailable(
                    end=end,
                    reason=f"goal_context_failed:{type(exc).__name__}",
                )
            _CURRENT_GOAL_CONTEXT.set(goal_context)
            try:
                business = await base(loader, user_id, end)
            except Exception as exc:
                return {
                    "available": False,
                    "reason": "business_profit_windows_failed",
                    "analysis_status": "failed",
                    "analysis_error_code": type(exc).__name__,
                    "monthly_profit_goal": goal_context,
                }
            return {
                **business,
                "analysis_status": business.get("analysis_status") or (
                    "complete" if business.get("available") is True else "unavailable"
                ),
                "monthly_profit_goal": goal_context,
            }
        finally:
            clear_goal_context()

    return goal_aware_context


def current_goal_context() -> dict[str, Any] | None:
    value = _CURRENT_GOAL_CONTEXT.get()
    return dict(value) if isinstance(value, dict) else None


def clear_goal_context() -> None:
    _CURRENT_GOAL_CONTEXT.set(None)


def attach_monthly_profit_goal_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/ai-monitor/monthly-profit-goal")
    async def get_monthly_profit_goal(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await load_goal(db, str(owner["id"]))

    @router.put("/ai-monitor/monthly-profit-goal")
    async def put_monthly_profit_goal(
        payload: MonthlyProfitGoalInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        saved = await save_goal(db, user_id, payload)
        current_time = _utcnow()
        current_month = current_time.astimezone(ZoneInfo(ACCOUNT_TIMEZONE)).date()
        try:
            snapshot = await db[SNAPSHOT_COLLECTION].find_one(
                {"user_id": user_id},
                {"_id": 0, "monthly_profit_goal": 1, "next_run_at": 1},
                sort=[("generated_at", -1)],
            )
        except Exception as exc:
            display = _display_unavailable(
                current_goal=saved,
                current_month=current_month,
                state="calculation_failed",
                reason=f"snapshot_read_failed_after_goal_save:{type(exc).__name__}",
            )
        else:
            display = reconcile_goal_for_display(
                current_goal=saved,
                snapshot_goal=(snapshot or {}).get("monthly_profit_goal"),
                current_month=current_month,
                current_time=current_time,
                snapshot_next_run_at=(snapshot or {}).get("next_run_at"),
            )
        return {
            **display,
            "goal_config_saved": True,
            "goal_config_save_status": "saved",
        }


__all__ = [
    "COLLECTION",
    "CONTRACT_VERSION",
    "DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR",
    "GOAL_EVIDENCE_MAX_AGE_SECONDS",
    "MonthlyProfitGoalInput",
    "attach_monthly_profit_goal_routes",
    "build_goal_context",
    "clear_goal_context",
    "current_goal_context",
    "goal_context_unavailable",
    "load_goal",
    "reconcile_goal_for_display",
    "save_goal",
    "wrap_business_profit_context",
    "with_goal_config_identity",
    "with_snapshot_provenance",
]
