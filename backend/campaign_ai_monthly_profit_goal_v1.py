"""Monthly net-profit objective for Campaign AI.

The owner supplies one commercial objective: the minimum monthly net profit that
must be protected before discretionary expansion.  This module does not choose
marketing actions.  It stores the objective, derives deterministic progress from
Mezan dashboard profit totals, and injects that progress into the OpenAI evidence
context.
"""
from __future__ import annotations

import calendar
from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field


COLLECTION = "mezan_campaign_ai_monthly_profit_goals_v1"
DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR = 100_000.0

_CURRENT_GOAL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "campaign_ai_monthly_profit_goal_context",
    default=None,
)


class MonthlyProfitGoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_net_profit_sar: float = Field(ge=1_000, le=100_000_000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


async def ensure_indexes(db: Any) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1)],
        unique=True,
        name="campaign_ai_monthly_profit_goal_user_unique",
    )


async def load_goal(db: Any, user_id: str) -> dict[str, Any]:
    await ensure_indexes(db)
    row = await db[COLLECTION].find_one({"user_id": user_id}, {"_id": 0})
    if not row:
        return {
            "minimum_net_profit_sar": DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR,
            "configured": False,
            "source": "default_owner_safety_floor",
            "updated_at": None,
        }
    return {
        "minimum_net_profit_sar": float(
            row.get("minimum_net_profit_sar") or DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR
        ),
        "configured": True,
        "source": "owner_configured",
        "updated_at": row.get("updated_at"),
    }


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
    await db[COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return {
        "minimum_net_profit_sar": document["minimum_net_profit_sar"],
        "configured": True,
        "source": "owner_configured",
        "updated_at": now_iso,
    }


async def _month_to_date_totals(
    loader: Callable[..., Awaitable[Any]] | None,
    user_id: str,
    end: date,
) -> dict[str, Any]:
    if loader is None:
        return {
            "available": False,
            "reason": "dashboard_profit_loader_unavailable",
        }
    start = end.replace(day=1)
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
    return {
        "available": _number(totals.get("net_profit")) is not None,
        "from": start.isoformat(),
        "to": end.isoformat(),
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
        "month": f"{end.year:04d}-{end.month:02d}",
        "currency": "SAR",
        "days_in_month": days_in_month,
        "days_elapsed": elapsed_days,
        "days_remaining": remaining_days,
        "month_to_date": month_to_date,
        "objective": (
            "تحقيق صافي ربح شهري لا يقل عن الهدف، وحماية الحد الأدنى قبل التوسع الاختياري."
        ),
    }
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
    accounting_complete = missing_costs == 0 and incomplete_orders == 0
    base = {
        **base,
        "profit_accounting_complete": accounting_complete,
        "scale_execution_allowed_by_profit_accounting": accounting_complete,
        "profit_accounting_quality": {
            "missing_product_cost_count": missing_costs,
            "incomplete_profit_orders_count": incomplete_orders,
        },
    }
    if net_profit is None:
        return {
            **base,
            "progress_available": False,
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
    if not accounting_complete:
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
        }
    return _derive_goal_progress(goal=goal, month_to_date=month_to_date, end=end)


def wrap_business_profit_context(
    base: Callable[..., Awaitable[dict[str, Any]]],
    get_runtime_context: Callable[[], Any],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def goal_aware_context(
        loader: Callable[..., Awaitable[Any]] | None,
        user_id: str,
        end: date,
    ) -> dict[str, Any]:
        business = await base(loader, user_id, end)
        try:
            runtime = get_runtime_context()
            goal_context = await build_goal_context(
                runtime.db,
                user_id,
                loader=loader,
                end=end,
            )
        except Exception as exc:
            goal_context = {
                "minimum_net_profit_sar": DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR,
                "progress_available": False,
                "status": "goal_context_unavailable",
                "reason": type(exc).__name__,
            }
        _CURRENT_GOAL_CONTEXT.set(goal_context)
        return {
            **business,
            "monthly_profit_goal": goal_context,
        }

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
        return await save_goal(db, str(owner["id"]), payload)


__all__ = [
    "COLLECTION",
    "DEFAULT_MONTHLY_NET_PROFIT_TARGET_SAR",
    "MonthlyProfitGoalInput",
    "attach_monthly_profit_goal_routes",
    "build_goal_context",
    "clear_goal_context",
    "current_goal_context",
    "load_goal",
    "save_goal",
    "wrap_business_profit_context",
]
