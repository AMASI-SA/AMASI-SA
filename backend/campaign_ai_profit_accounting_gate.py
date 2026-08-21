"""Fail-closed store-level profit accounting gate for Campaign AI scaling."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException

from mezan_campaign_profit_loader import build_mezan_profit_totals

RIYADH = timezone(timedelta(hours=3))


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def accounting_quality_from_totals(totals: dict[str, Any] | None) -> dict[str, Any]:
    source = totals if isinstance(totals, dict) else {}
    missing = _count(source.get("missing_product_cost_count"))
    incomplete = _count(source.get("incomplete_profit_orders_count"))
    complete = missing == 0 and incomplete == 0
    return {
        "complete": complete,
        "missing_product_cost_count": missing,
        "incomplete_profit_orders_count": incomplete,
        "source": source.get("profit_source") or "mezan_profit_engine_v2_read_only",
    }


async def require_profit_accounting_complete_for_scale(
    db: Any,
    user_id: str,
    action: str,
) -> dict[str, Any]:
    """Allow defensive actions, but block spend expansion on incomplete P&L."""
    if str(action or "").strip().lower() != "scale":
        return {"complete": True, "scale_gate_applied": False}
    today = datetime.now(RIYADH).date()
    totals = await build_mezan_profit_totals(
        db,
        user_id,
        from_date=today.replace(day=1).isoformat(),
        to_date=today.isoformat(),
    )
    quality = accounting_quality_from_totals(totals)
    if not quality["complete"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "campaign_ai_profit_accounting_incomplete",
                "message": (
                    "صافي الربح الحالي غير مكتمل محاسبيًا لبعض الطلبات؛ "
                    "أُوقفت زيادة الإنفاق حتى تكتمل تكاليف المنتجات والطلبات."
                ),
                **quality,
                "recovery_action": "complete_missing_profit_inputs_then_refresh_recommendation",
            },
        )
    return {**quality, "scale_gate_applied": True}


__all__ = [
    "accounting_quality_from_totals",
    "require_profit_accounting_complete_for_scale",
]
