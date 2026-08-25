"""Read-only Dashboard comparison for the Unified Marketing cutover."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ABSOLUTE_SPEND_TOLERANCE_SAR = 0.05
RELATIVE_SPEND_TOLERANCE = 0.001
DASHBOARD_UNIFIED_SHADOW_COLLECTION = "mezan_unified_marketing_dashboard_shadow_v1"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def build_dashboard_unified_shadow(
    legacy_snapchat: dict[str, Any],
    unified_report: dict[str, Any],
) -> dict[str, Any]:
    legacy_quality = dict(legacy_snapchat.get("quality") or {})
    totals = dict(unified_report.get("totals") or {})
    delivery = dict(totals.get("delivery") or {})
    platform = dict(totals.get("platform_outcomes") or {})
    commerce = dict(totals.get("commerce_outcomes") or {})
    quality = dict(totals.get("quality") or {})
    legacy_spend = _number(legacy_snapchat.get("total_sar"))
    unified_spend = _number((delivery.get("spend_sar") or {}).get("amount"))
    delta = (
        round(unified_spend - legacy_spend, 2)
        if legacy_spend is not None and unified_spend is not None
        else None
    )
    tolerance = max(
        ABSOLUTE_SPEND_TOLERANCE_SAR,
        abs(legacy_spend or 0) * RELATIVE_SPEND_TOLERANCE,
    )
    spend_match = delta is not None and abs(delta) <= tolerance
    coverage_complete = (
        legacy_quality.get("amount_complete") is True
        and quality.get("coverage_status") == "complete"
        and quality.get("amount_complete") is True
    )
    shadow_passed = bool(spend_match and coverage_complete)
    return {
        "mode": "shadow",
        "provider": "snapchat_ads",
        "contract_version": unified_report.get("contract_version"),
        "period": unified_report.get("period"),
        "shadow_passed": shadow_passed,
        "cutover_ready": False,
        "comparison": {
            "spend_sar": {
                "legacy": legacy_spend,
                "unified": unified_spend,
                "delta": delta,
                "tolerance": round(tolerance, 4),
                "match": spend_match,
            },
            "coverage_complete": coverage_complete,
        },
        "unified_summary": {
            "platform_conversions": platform.get("conversions"),
            "platform_revenue": (platform.get("revenue") or {}).get("amount"),
            "salla_orders": commerce.get("orders"),
            "salla_revenue_sar": (commerce.get("revenue") or {}).get("amount"),
            "salla_status": commerce.get("status"),
            "quality": quality,
            "order_summary": unified_report.get("order_summary") or {},
        },
        "decision_eligibility": {
            "eligible": False,
            "reason": "dashboard_shadow_not_accepted",
        },
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def ensure_dashboard_shadow_indexes(db: Any) -> None:
    await db[DASHBOARD_UNIFIED_SHADOW_COLLECTION].create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("date_from", 1),
            ("date_to", 1),
        ],
        unique=True,
        name="unified_marketing_dashboard_shadow_range_unique",
    )


async def persist_dashboard_unified_shadow(
    db: Any,
    *,
    user_id: str,
    date_from: str,
    date_to: str,
    shadow: dict[str, Any],
) -> None:
    await ensure_dashboard_shadow_indexes(db)
    now = datetime.now(timezone.utc)
    identity = {
        "user_id": str(user_id),
        "provider": "snapchat_ads",
        "date_from": str(date_from),
        "date_to": str(date_to),
    }
    await db[DASHBOARD_UNIFIED_SHADOW_COLLECTION].update_one(
        identity,
        {
            "$set": {**identity, "shadow": dict(shadow), "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def load_dashboard_unified_shadow(
    db: Any,
    *,
    user_id: str,
    date_from: str,
    date_to: str,
) -> dict[str, Any] | None:
    row = await db[DASHBOARD_UNIFIED_SHADOW_COLLECTION].find_one(
        {
            "user_id": str(user_id),
            "provider": "snapchat_ads",
            "date_from": str(date_from),
            "date_to": str(date_to),
        },
        {"_id": 0, "shadow": 1, "updated_at": 1},
    )
    if not row:
        return None
    return {**dict(row.get("shadow") or {}), "shadow_updated_at": row.get("updated_at")}


__all__ = [
    "DASHBOARD_UNIFIED_SHADOW_COLLECTION",
    "build_dashboard_unified_shadow",
    "ensure_dashboard_shadow_indexes",
    "load_dashboard_unified_shadow",
    "persist_dashboard_unified_shadow",
]
