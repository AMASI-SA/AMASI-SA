"""Fail-closed store-level profit accounting gate for Campaign AI scaling."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException

from mezan_profit_engine import build_mezan_profit_envelope

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
    """Compatibility parser for old snapshots; unknown stays unknown, never zero."""
    source = totals if isinstance(totals, dict) else {}
    missing = _count(source.get("missing_product_cost_count"))
    incomplete = _count(source.get("incomplete_profit_orders_count"))
    known = missing is not None and incomplete is not None
    complete = bool(known and missing == 0 and incomplete == 0)
    return {
        "known": known,
        "complete": complete,
        "scale_safe": complete,
        "missing_product_cost_count": missing,
        "incomplete_profit_orders_count": incomplete,
        "source": source.get("profit_source") or "mezan_profit_engine_v2_read_only",
        "unknown_is_zero": False,
    }


def accounting_quality_from_envelope(envelope: dict[str, Any] | None) -> dict[str, Any]:
    source = envelope if isinstance(envelope, dict) else {}
    quality = source.get("quality") if isinstance(source.get("quality"), dict) else {}
    known = quality.get("known") is True
    complete = bool(known and quality.get("complete") is True and quality.get("scale_safe") is True)
    return {
        **quality,
        "known": known,
        "complete": complete,
        "scale_safe": complete,
        "source": source.get("source") or "mezan_profit_engine_v2_read_only",
        "contract_version": source.get("contract_version"),
        "unknown_is_zero": False,
    }


async def require_profit_accounting_complete_for_scale(
    db: Any,
    user_id: str,
    action: str,
) -> dict[str, Any]:
    """Allow defensive actions, but block spend expansion unless envelope proves completeness."""
    if str(action or "").strip().lower() != "scale":
        return {"complete": True, "scale_gate_applied": False}
    today = datetime.now(RIYADH).date()
    envelope = await build_mezan_profit_envelope(
        db,
        user_id,
        from_date=today.replace(day=1).isoformat(),
        to_date=today.isoformat(),
    )
    quality = accounting_quality_from_envelope(envelope)
    if not quality["complete"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "campaign_ai_profit_accounting_incomplete",
                "message": (
                    "صافي الربح الحالي غير مثبت كمحاسبة مكتملة؛ أُوقفت زيادة الإنفاق "
                    "حتى يثبت عقد ربح ميزان اكتمال كل مكونات الربح المطلوبة."
                ),
                **quality,
                "recovery_action": "complete_missing_profit_inputs_then_refresh_recommendation",
            },
        )
    return {**quality, "scale_gate_applied": True}


__all__ = [
    "accounting_quality_from_envelope",
    "accounting_quality_from_totals",
    "require_profit_accounting_complete_for_scale",
]
