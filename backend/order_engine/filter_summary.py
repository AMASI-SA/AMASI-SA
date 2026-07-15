"""Read-only filter summaries for the Mezan OS orders screen.

Salla status counters must remain fast and exact. Qoyod classification is an
independent, potentially expensive diagnostic and must never block the order
screen or cause Cloudflare origin timeouts.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any

from integrations.qoyod.eligible_orders import build_eligible_orders_report

QOYOD_DEFAULT_FROM_DATE = date(2026, 7, 1)
QOYOD_SUMMARY_TIMEOUT_SECONDS = 4.0


def _status_key(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


_STATUS_VALUES: dict[str, set[str]] = {
    "under_review": {
        "under review",
        "waiting review",
        "pending review",
        "بإنتظار المراجعة",
        "بانتظار المراجعة",
        "انتظار المراجعة",
    },
    "processing": {
        "processing",
        "in progress",
        "قيد التنفيذ",
        "جاري التنفيذ",
    },
    "completed": {
        "completed",
        "delivered",
        "تم التنفيذ",
        "تم التوصيل",
    },
    "shipping": {
        "shipping",
        "shipped",
        "out for delivery",
        "جاري التوصيل",
        "تم الشحن",
    },
    "cancelled": {
        "cancelled",
        "canceled",
        "deleted",
        "ملغي",
        "ملغى",
        "محذوف",
    },
    "refunded": {
        "refunded",
        "returned",
        "مسترجع",
        "تم الاسترجاع",
    },
}


def _status_group(value: Any) -> str:
    key = _status_key(value)
    for group, values in _STATUS_VALUES.items():
        if key in values:
            return group
    return "other"


async def _qoyod_summary(db: Any, *, user_id: str) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    since_days = max(1, (today - QOYOD_DEFAULT_FROM_DATE).days + 1)
    report = await build_eligible_orders_report(
        db,
        user_id=str(user_id),
        since_days=since_days,
        limit=1,
        show_already_sent=False,
        debug=False,
    )
    counts = dict(report.get("counts") or {})
    eligible_not_sent = int(counts.get("ready_for_preview") or 0) + int(
        counts.get("ready_for_manual_approval") or 0
    )
    return {
        "from_date": QOYOD_DEFAULT_FROM_DATE.isoformat(),
        "sent": int(counts.get("already_sent") or 0),
        "eligible_not_sent": eligible_not_sent,
        "available": True,
        "error": None,
    }


async def build_order_filter_summary(db: Any, *, user_id: str) -> dict[str, Any]:
    """Return exact full-dataset Salla card counts without slow-page failure."""
    base_query = {
        "user_id": str(user_id),
        "raw_by_source.salla_direct": {"$exists": True},
    }

    status_counts = {
        "all": await db.unified_orders.count_documents(base_query),
        "under_review": 0,
        "processing": 0,
        "completed": 0,
        "shipping": 0,
        "cancelled": 0,
        "refunded": 0,
        "other": 0,
    }

    pipeline = [
        {"$match": base_query},
        {"$project": {
            "status": {"$ifNull": [
                "$order_status_slug",
                {"$ifNull": [
                    "$order_status",
                    {"$ifNull": [
                        "$raw_by_source.salla_direct.status.slug",
                        "$raw_by_source.salla_direct.status.name",
                    ]},
                ]},
            ]},
        }},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    async for row in db.unified_orders.aggregate(pipeline):
        group = _status_group(row.get("_id"))
        status_counts[group] = status_counts.get(group, 0) + int(row.get("count") or 0)

    try:
        qoyod = await asyncio.wait_for(
            _qoyod_summary(db, user_id=str(user_id)),
            timeout=QOYOD_SUMMARY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        qoyod = {
            "from_date": QOYOD_DEFAULT_FROM_DATE.isoformat(),
            "sent": None,
            "eligible_not_sent": None,
            "available": False,
            "error": "qoyod_summary_timeout",
        }
    except Exception as exc:  # Salla cards must remain available.
        qoyod = {
            "from_date": QOYOD_DEFAULT_FROM_DATE.isoformat(),
            "sent": None,
            "eligible_not_sent": None,
            "available": False,
            "error": f"qoyod_summary_failed:{type(exc).__name__}",
        }

    return {
        "status_counts": status_counts,
        "qoyod": qoyod,
    }
