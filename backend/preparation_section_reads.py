"""Small navigation directories; selected sections retain canonical detail gates."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
import re

from order_engine.repository import _STATUS_PATTERNS
from order_engine.service import _provider_status_native


def _text(value: Any) -> str:
    return str(value or "").strip()


def _day(value: Any) -> str:
    try:
        date = value if isinstance(value, datetime) else datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=ZoneInfo("UTC"))
        return date.astimezone(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "__unknown__"


async def _nonterminal_numbers(db: Any, user_id: str, numbers: list[str]) -> set[str]:
    # Reuse Order Engine's custom/current status precedence. Read status fields
    # only: navigation must not hydrate customers, products or payment facts.
    remaining = set(numbers)
    rows = db.unified_orders.find(
        {"user_id": user_id, "order_number": {"$in": numbers}},
        {"_id": 0, "order_number": 1, "order_status": 1,
         "raw_by_source.salla_direct.status": 1, "raw_by_source.salla_direct.status_slug": 1},
    )
    async for row in rows:
        raw = ((row.get("raw_by_source") or {}).get("salla_direct") or {})
        status = _provider_status_native(raw, current_status=row.get("order_status"))
        effective = " ".join(_text(status).replace("_", " ").casefold().split())
        if any(re.fullmatch(_STATUS_PATTERNS[group], effective, re.IGNORECASE)
               for group in ("completed", "shipping", "cancelled", "refunded")):
            remaining.discard(_text(row.get("order_number")))
    return remaining


async def progress_day_batches(db: Any, *, user_id: str, employee_id: str) -> dict[str, list[str]]:
    from preparation_supplier_dispatch import PIECES, REGISTRY

    # Only outstanding physical pieces produce dates. No quantity or product
    # details cross this boundary; fully received/completed dates disappear.
    candidates = await db[PIECES].aggregate([
        {"$match": {"user_id": user_id, "responsible_employee_id": employee_id,
            "experiment_archived_at": None,
            "status": {"$in": ["assigned", "in_progress", "ready_for_receipt"]},
            "$or": [
                {"supplier_dispatch_status": {"$in": ["sent", "ready", "partial_received"]}},
                {"supplier_receiving_history.0": {"$exists": True}},
            ]}},
        {"$group": {"_id": {"batch": "$batch_id", "order": "$order_number"}}},
    ]).to_list(None)
    numbers = sorted({_text(row["_id"].get("order")) for row in candidates} - {""})
    live = await _nonterminal_numbers(db, user_id, numbers) if numbers else set()
    batch_ids = sorted({_text(row["_id"].get("batch")) for row in candidates
                        if _text(row["_id"].get("order")) in live} - {""})
    if not batch_ids:
        return {}
    registry = await db[REGISTRY].find(
        {"user_id": user_id, "batch_id": {"$in": batch_ids}},
        {"_id": 0, "batch_id": 1, "registered_at": 1},
    ).to_list(None)
    dates = {_text(row.get("batch_id")): _day(row.get("registered_at")) for row in registry}
    result: dict[str, list[str]] = {}
    for batch_id in batch_ids:
        result.setdefault(dates.get(batch_id, "__unknown__"), []).append(batch_id)
    return result


async def progress_days(db: Any, *, user_id: str, employee_id: str) -> dict[str, Any]:
    dates = await progress_day_batches(db, user_id=user_id, employee_id=employee_id)
    keys = sorted(dates, key=lambda key: (key != "__unknown__", key), reverse=True)
    return {"ok": True, "employee_id": employee_id, "days": [{"key": key} for key in keys], "read_only": True}


async def progress_workspace(db: Any, *, user_id: str, employee_id: str, day: str) -> dict[str, Any]:
    from preparation_supplier_dispatch import _employee_workspace

    dates = await progress_day_batches(db, user_id=user_id, employee_id=employee_id)
    return {"ok": True, "read_only": True, **await _employee_workspace(
        db, user_id=user_id, employee_id=employee_id,
        limit=50000, piece_grain=True, batch_ids=dates.get(day, []),
    )}


async def waiting_cards(db: Any, *, user_id: str, employee_id: str) -> dict[str, Any]:
    from preparation_supplier_dispatch import PIECES, _waiting_query, _waiting_group_expression

    groups = await db[PIECES].aggregate([
        {"$match": _waiting_query(user_id, employee_id)},
        {"$group": {"_id": _waiting_group_expression(),
            "product_id": {"$first": "$product_id"}, "product_name": {"$first": "$product_name"},
            "sku": {"$first": "$sku"}, "image_url": {"$first": {"$ifNull": [
                "$selected_image_url", {"$ifNull": ["$resolved_image_url", "$image_url"]}]}}}},
        {"$sort": {"product_name": 1, "_id": 1}},
    ]).to_list(None)
    return {"ok": True, "employee_id": employee_id, "read_only": True,
        "groups": [{"key": row.pop("_id"), **row, "lazy": True} for row in groups],
        "suppliers": []}
