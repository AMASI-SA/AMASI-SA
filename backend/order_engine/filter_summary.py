"""Read-only filter summaries for the Mezan OS orders screen.

The module reads Salla Direct orders only.  Qoyod counters reuse the existing
eligible-orders classifier and never call or write to Qoyod.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from integrations.qoyod.eligible_orders import build_eligible_orders_report

QOYOD_DEFAULT_FROM_DATE = date(2026, 7, 1)


def _status_key(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


def _status_group(value: Any) -> str:
    key = _status_key(value)
    if any(token in key for token in ("under review", "in review", "review", "انتظار المراجعة", "بإنتظار المراجعة", "بانتظار المراجعة")):
        return "under_review"
    if any(token in key for token in ("processing", "in progress", "قيد التنفيذ", "جاري التنفيذ")):
        return "processing"
    if any(token in key for token in ("completed", "delivered", "تم التنفيذ", "تم التوصيل")):
        return "completed"
    if any(token in key for token in ("shipping", "shipped", "جاري التوصيل", "تم الشحن")):
        return "shipping"
    if any(token in key for token in ("cancel", "ملغ", "محذوف")):
        return "cancelled"
    if any(token in key for token in ("refund", "مسترج")):
        return "refunded"
    return "other"


async def build_order_filter_summary(db: Any, *, user_id: str) -> dict[str, Any]:
    """Return full-dataset card counts for Salla Direct orders."""
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
                {"$ifNull": ["$order_status", "$raw_by_source.salla_direct.status.slug"]},
            ]},
        }},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    async for row in db.unified_orders.aggregate(pipeline):
        group = _status_group(row.get("_id"))
        status_counts[group] = status_counts.get(group, 0) + int(row.get("count") or 0)

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
        "status_counts": status_counts,
        "qoyod": {
            "from_date": QOYOD_DEFAULT_FROM_DATE.isoformat(),
            "sent": int(counts.get("already_sent") or 0),
            "eligible_not_sent": eligible_not_sent,
        },
    }
