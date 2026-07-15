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
        "delivering",
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
        "restored",
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


def _effective_status_expression() -> dict[str, Any]:
    """Use authoritative Salla status first and stale canonical slug last.

    Production diagnostics proved historical rows may contain:
    ``order_status_slug=under_review`` while the current provider/native status
    is already completed, delivering or delivered. Raw Salla Direct status is
    therefore authoritative for cards and filters. Canonical native name is the
    next fallback; canonical slug is used only when no newer fact exists.
    """

    return {
        "$ifNull": [
            "$raw_by_source.salla_direct.status.slug",
            {
                "$ifNull": [
                    "$raw_by_source.salla_direct.status.name",
                    {"$ifNull": ["$order_status", "$order_status_slug"]},
                ]
            },
        ]
    }


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
        {"$project": {"status": _effective_status_expression()}},
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


async def build_order_status_diagnostic(
    db: Any,
    *,
    user_id: str,
    sample_limit: int = 100,
) -> dict[str, Any]:
    """Explain exactly why a status card has its current count.

    Read-only and owner-only through the route. It exposes no payment payloads,
    customer contacts or Qoyod calls.
    """
    base_query = {
        "user_id": str(user_id),
        "raw_by_source.salla_direct": {"$exists": True},
    }

    distribution_pipeline = [
        {"$match": base_query},
        {
            "$project": {
                "effective_status": _effective_status_expression(),
                "top_slug": "$order_status_slug",
                "top_name": "$order_status",
                "raw_slug": "$raw_by_source.salla_direct.status.slug",
                "raw_name": "$raw_by_source.salla_direct.status.name",
                "order_date": 1,
                "updated_at": 1,
                "last_salla_update": {
                    "$ifNull": [
                        "$last_salla_direct_update_at",
                        {
                            "$ifNull": [
                                "$raw_by_source.salla_direct.updated_at",
                                "$updated_at",
                            ]
                        },
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": {
                    "effective_status": "$effective_status",
                    "top_slug": "$top_slug",
                    "top_name": "$top_name",
                    "raw_slug": "$raw_slug",
                    "raw_name": "$raw_name",
                },
                "count": {"$sum": 1},
                "oldest_order_date": {"$min": "$order_date"},
                "newest_order_date": {"$max": "$order_date"},
                "oldest_update": {"$min": "$last_salla_update"},
                "newest_update": {"$max": "$last_salla_update"},
            }
        },
        {"$sort": {"count": -1}},
    ]

    distribution: list[dict[str, Any]] = []
    async for row in db.unified_orders.aggregate(distribution_pipeline):
        identity = dict(row.get("_id") or {})
        effective = identity.get("effective_status")
        distribution.append(
            {
                **identity,
                "group": _status_group(effective),
                "count": int(row.get("count") or 0),
                "oldest_order_date": row.get("oldest_order_date"),
                "newest_order_date": row.get("newest_order_date"),
                "oldest_update": row.get("oldest_update"),
                "newest_update": row.get("newest_update"),
            }
        )

    target_values = list(_STATUS_VALUES["under_review"])
    sample_query = {
        **base_query,
        "$expr": {
            "$in": [
                {
                    "$toLower": {
                        "$trim": {
                            "input": {
                                "$replaceAll": {
                                    "input": {"$toString": _effective_status_expression()},
                                    "find": "_",
                                    "replacement": " ",
                                }
                            }
                        }
                    }
                },
                target_values,
            ]
        },
    }

    sample_projection = {
        "_id": 0,
        "order_number": 1,
        "order_date": 1,
        "updated_at": 1,
        "order_status_slug": 1,
        "order_status": 1,
        "last_salla_direct_update_at": 1,
        "raw_by_source.salla_direct.status": 1,
        "raw_by_source.salla_direct.updated_at": 1,
    }
    sample_cursor = (
        db.unified_orders.find(sample_query, sample_projection)
        .sort([("order_date", 1), ("order_number", 1)])
        .limit(max(1, min(int(sample_limit), 200)))
    )
    under_review_sample = [row async for row in sample_cursor]

    return {
        "generated_at": datetime.now(timezone.utc),
        "total_salla_direct": await db.unified_orders.count_documents(base_query),
        "distribution": distribution,
        "under_review_sample": under_review_sample,
        "sample_limit": max(1, min(int(sample_limit), 200)),
        "read_only": True,
        "no_qoyod_calls": True,
    }
