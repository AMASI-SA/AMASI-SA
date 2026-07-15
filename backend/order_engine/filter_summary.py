"""Exact, read-only Salla status summaries for the Mezan OS orders screen.

The orders screen must mirror Salla's merchant-visible workflow states. Salla may
return a parent state in ``status.name`` and the actual child/custom state in
``status.customized``. The customized state is therefore authoritative.

This module intentionally contains no Qoyod classification. Accounting cards
belong to the Qoyod area and must never slow down or break the orders screen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _status_key(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


def _status_label(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    return text or "غير محدد"


def _effective_status_expression() -> dict[str, Any]:
    """Return Salla's exact merchant-visible child status.

    Example:
      status.name       = بإنتظار المراجعة   (parent workflow)
      status.customized = تم المراجعة        (actual child state)
    """
    return {
        "$ifNull": [
            "$raw_by_source.salla_direct.status.customized",
            {
                "$ifNull": [
                    "$order_status",
                    {
                        "$ifNull": [
                            "$raw_by_source.salla_direct.status.name",
                            {
                                "$ifNull": [
                                    "$raw_by_source.salla_direct.status.slug",
                                    "$order_status_slug",
                                ]
                            },
                        ]
                    },
                ]
            },
        ]
    }


async def build_order_filter_summary(db: Any, *, user_id: str) -> dict[str, Any]:
    """Return all exact Salla statuses as dynamic cards.

    No fixed enum is used. Custom statuses created in Salla therefore appear
    automatically without another Mezan deployment.
    """
    base_query = {
        "user_id": str(user_id),
        "raw_by_source.salla_direct": {"$exists": True},
    }

    total = await db.unified_orders.count_documents(base_query)
    pipeline = [
        {"$match": base_query},
        {"$project": {"status": _effective_status_expression()}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
    ]

    status_cards: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {"all": int(total)}
    async for row in db.unified_orders.aggregate(pipeline):
        label = _status_label(row.get("_id"))
        key = _status_key(label)
        count = int(row.get("count") or 0)
        status_cards.append({"key": key, "label": label, "count": count})
        status_counts[key] = count

    return {
        "total": int(total),
        "status_cards": status_cards,
        "status_counts": status_counts,
    }


async def build_order_status_diagnostic(
    db: Any,
    *,
    user_id: str,
    sample_limit: int = 100,
) -> dict[str, Any]:
    """Explain exact status sources without modifying orders."""
    base_query = {
        "user_id": str(user_id),
        "raw_by_source.salla_direct": {"$exists": True},
    }

    pipeline = [
        {"$match": base_query},
        {
            "$project": {
                "effective_status": _effective_status_expression(),
                "top_slug": "$order_status_slug",
                "top_name": "$order_status",
                "raw_slug": "$raw_by_source.salla_direct.status.slug",
                "raw_name": "$raw_by_source.salla_direct.status.name",
                "raw_customized": "$raw_by_source.salla_direct.status.customized",
                "order_date": 1,
                "updated_at": 1,
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
                    "raw_customized": "$raw_customized",
                },
                "count": {"$sum": 1},
                "oldest_order_date": {"$min": "$order_date"},
                "newest_order_date": {"$max": "$order_date"},
                "oldest_update": {"$min": "$updated_at"},
                "newest_update": {"$max": "$updated_at"},
            }
        },
        {"$sort": {"count": -1}},
    ]

    distribution: list[dict[str, Any]] = []
    async for row in db.unified_orders.aggregate(pipeline):
        distribution.append(
            {
                **dict(row.get("_id") or {}),
                "count": int(row.get("count") or 0),
                "oldest_order_date": row.get("oldest_order_date"),
                "newest_order_date": row.get("newest_order_date"),
                "oldest_update": row.get("oldest_update"),
                "newest_update": row.get("newest_update"),
            }
        )

    sample_cursor = (
        db.unified_orders.find(
            base_query,
            {
                "_id": 0,
                "order_number": 1,
                "order_date": 1,
                "updated_at": 1,
                "order_status_slug": 1,
                "order_status": 1,
                "raw_by_source.salla_direct.status": 1,
            },
        )
        .sort([("order_date", -1), ("order_number", -1)])
        .limit(max(1, min(int(sample_limit), 200)))
    )

    return {
        "generated_at": datetime.now(timezone.utc),
        "total_salla_direct": await db.unified_orders.count_documents(base_query),
        "distribution": distribution,
        "sample": [row async for row in sample_cursor],
        "sample_limit": max(1, min(int(sample_limit), 200)),
        "read_only": True,
        "no_qoyod_calls": True,
    }
