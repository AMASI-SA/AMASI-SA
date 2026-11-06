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
        {"$group": {"_id": {"batch": "$batch_id", "order": "$order_number"},
                    "piece_ids": {"$addToSet": "$piece_id"}}},
    ]).to_list(None)
    from preparation_supplier_dispatch import DISPATCHES
    candidate_ids = sorted({_text(pid) for row in candidates for pid in row["piece_ids"]} - {""})
    dispatched = await db[DISPATCHES].find(
        {"user_id": user_id, "sent_by_id": employee_id, "status": {"$ne": "building"},
         "source_files.cards.piece_id": {"$in": candidate_ids}},
        {"_id": 0, "source_files.cards.piece_id": 1},
    ).to_list(None) if candidate_ids else []
    dispatched_ids = {_text(card.get("piece_id")) for row in dispatched
                      for source in row.get("source_files") or [] for card in source.get("cards") or []}
    candidates = [row for row in candidates if dispatched_ids.intersection(row["piece_ids"])]
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


def _dispatch_ids(row: dict[str, Any]) -> set[str]:
    return {_text(card.get("piece_id")) for source in row.get("source_files") or []
            for card in source.get("cards") or [] if _text(card.get("piece_id"))}


async def progress_workspace(db: Any, *, user_id: str, employee_id: str, day: str, after: str = "") -> dict[str, Any]:
    from preparation_supplier_dispatch import (
        PIECES, REGISTRY, DISPATCHES, _employee_workspace, _piece_products,
        piece_is_available_for_supplier_dispatch, annotate_waiting_pieces,
    )
    # Date selection reads registry metadata, not every employee product.
    if day == "__unknown__":
        dates = await progress_day_batches(db, user_id=user_id, employee_id=employee_id)
        batches = dates.get(day, [])
    else:
        registry = await db[REGISTRY].find({"user_id": user_id, "$expr": {"$eq": [
            {"$dateToString": {"date": {"$convert": {"input": "$registered_at", "to": "date", "onError": None, "onNull": None}},
                "format": "%Y-%m-%d", "timezone": "Asia/Riyadh", "onNull": "__unknown__"}}, day]}},
            {"_id": 0, "batch_id": 1}).to_list(None)
        batches = [_text(row.get("batch_id")) for row in registry]
    projection = {field: 1 for field in (
        "piece_id", "batch_id", "order_number", "order_item_id", "unit_index", "status",
        "assignment_status", "supplier_id", "supplier_dispatch_id", "supplier_dispatch_status",
        "sent_to_supplier_by_id", "supplier_receiving_session_id", "branch_handoff_at")}
    metadata = await db[PIECES].find({"user_id": user_id, "responsible_employee_id": employee_id,
        "experiment_archived_at": None, "status": {"$ne": "cancelled"}, "batch_id": {"$in": batches}},
        {"_id": 0, **projection}).to_list(None)
    numbers = sorted({_text(row.get("order_number")) for row in metadata} - {""})
    live = await _nonterminal_numbers(db, user_id, numbers) if numbers else set()
    metadata = [row for row in metadata if _text(row.get("order_number")) in live]
    ids = [_text(row.get("piece_id")) for row in metadata]
    dispatches = await db[DISPATCHES].find({"user_id": user_id, "sent_by_id": employee_id,
        "status": {"$ne": "building"}, "source_files.cards.piece_id": {"$in": ids}},
        {"_id": 0, "id": 1, "status": 1, "supplier_id": 1, "supplier_name": 1,
         "supplier_file_number": 1, "piece_count": 1, "sent_at": 1, "sent_by_name": 1,
         "source_file_numbers": 1, "source_files.file_number": 1, "source_files.registered_at": 1,
         "source_files.cards.piece_id": 1}).to_list(None)
    dispatched_ids = set().union(*(_dispatch_ids(row) for row in dispatches)) if dispatches else set()
    metadata = [row for row in metadata if _text(row.get("piece_id")) in dispatched_ids]
    # Only exceptional returned-to-assignment pieces need the full existing
    # review/payment authority calculation. Sent/ready counts require no DTO.
    waiting = [row for row in metadata if piece_is_available_for_supplier_dispatch(row)]
    annotated = await annotate_waiting_pieces(db, user_id=user_id, pieces=waiting) if waiting else []
    waiting_by_id = {_text(row.get("piece_id")): row for row in annotated}
    metadata = [waiting_by_id.get(_text(row.get("piece_id")), row) for row in metadata]
    projected = _piece_products(metadata, waiting_only=True)
    remaining_ids = sorted(_text(row.get("piece_id")) for row in projected
        if row.get("available_quantity") or row["sent_quantity"] + row["ready_quantity"] > 0)
    remaining = set(remaining_ids)
    day_summary = {"key": day, "assigned": len(projected), "remaining": len(remaining),
        "ready": len(projected) - len(remaining),
        "waiting_for_supplier": sum(bool(row["available_quantity"]) for row in projected),
        "with_suppliers": sum(row["sent_quantity"] + row["ready_quantity"] > 0 for row in projected)}
    supplier_totals: dict[str, int] = {}
    for row in metadata:
        if _text(row.get("piece_id")) in remaining and _text(row.get("supplier_id")):
            sid = _text(row["supplier_id"])
            supplier_totals[sid] = supplier_totals.get(sid, 0) + int(
                row.get("supplier_dispatch_status") == "sent") + int(
                row.get("supplier_dispatch_status") == "ready" or row.get("status") == "ready_for_receipt")
    # PDF history needs exact identities/progress, never full historical
    # product options or images. Include cross-date members of the same file.
    extras = sorted(dispatched_ids - set(ids))
    outside = await db[PIECES].find({"user_id": user_id, "responsible_employee_id": employee_id,
        "experiment_archived_at": None, "piece_id": {"$in": extras}}, {"_id": 0, **projection}).to_list(None) if extras else []
    extra_numbers = sorted({_text(row.get("order_number")) for row in outside} - {""})
    extra_live = await _nonterminal_numbers(db, user_id, extra_numbers) if extra_numbers else set()
    outside = [row for row in outside if _text(row.get("order_number")) in extra_live]
    outstanding_by_supplier: dict[str, set[str]] = {}
    for row in metadata + outside:
        if row.get("supplier_dispatch_status") in {"sent", "ready"} or row.get("status") == "ready_for_receipt":
            outstanding_by_supplier.setdefault(_text(row.get("supplier_id")), set()).add(_text(row.get("piece_id")))
    for dispatch in dispatches:
        member_ids = _dispatch_ids(dispatch)
        total = max(int(dispatch.get("piece_count") or 0), len(member_ids))
        complete = total > 0 and len(member_ids) == total
        outstanding = member_ids.intersection(outstanding_by_supplier.get(_text(dispatch.get("supplier_id")), set()))
        count = len(outstanding) if complete else 0 if dispatch.get("status") == "received" else total
        dispatch["progress"] = {"total": total, "remaining": count, "received": total - count,
            "remaining_piece_ids": sorted(outstanding), "piece_identity_complete": complete}
    available = [pid for pid in remaining_ids if pid > after]
    page_ids = available[:10]
    workspace = await _employee_workspace(db, user_id=user_id, employee_id=employee_id,
        limit=10, piece_grain=True, batch_ids=batches, piece_ids=page_ids, dispatch_rows=dispatches)
    return {"ok": True, "read_only": True, **workspace,
        "next_cursor": page_ids[-1] if len(available) > 10 else None,
        "progress_day_summary": day_summary, "progress_supplier_totals": supplier_totals,
        "progress_active_piece_ids": remaining_ids}


async def waiting_cards(db: Any, *, user_id: str, employee_id: str, after: str = "") -> dict[str, Any]:
    from preparation_supplier_dispatch import PIECES, _waiting_query, _waiting_group_expression
    from fastapi import HTTPException

    if after and (not after.startswith("g:") or not after[2:]):
        raise HTTPException(status_code=400, detail="invalid_waiting_group_cursor")
    pipeline = [
        {"$match": _waiting_query(user_id, employee_id)},
        {"$group": {"_id": _waiting_group_expression(),
            "product_id": {"$first": "$product_id"}, "product_name": {"$first": "$product_name"},
            "sku": {"$first": "$sku"}, "image_url": {"$first": {"$ifNull": [
                "$selected_image_url", {"$ifNull": ["$resolved_image_url", "$image_url"]}]}}}},
    ]
    # Stable product identity, not a changing count, is the page boundary. The
    # aggregation never reads or validates individual customer options here.
    if after:
        pipeline.append({"$match": {"_id": {"$gt": after[2:]}}})
    pipeline.extend([{"$sort": {"_id": 1}}, {"$limit": 11}])
    groups = await db[PIECES].aggregate(pipeline).to_list(11)
    next_cursor = "g:" + groups[9]["_id"] if len(groups) > 10 else None
    return {"ok": True, "employee_id": employee_id, "read_only": True,
        "groups": [{"key": row.pop("_id"), **row, "lazy": True} for row in groups[:10]],
        "next_cursor": next_cursor,
        "suppliers": []}
