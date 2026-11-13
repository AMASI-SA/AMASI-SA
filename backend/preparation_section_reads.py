"""Small navigation directories; selected sections retain canonical detail gates."""
from __future__ import annotations

from typing import Any
import re

from order_engine.repository import _STATUS_PATTERNS
from order_engine.service import _provider_status_native
from preparation_progress_dates import DateBasis, piece_progress_day


def _text(value: Any) -> str:
    return str(value or "").strip()


_ASSIGNMENT_DATE_PROJECTION = {field: 1 for field in (
    "responsible_employee_id", "employee_assigned_at", "assigned_at",
    "assignment_history.assignment_id", "assignment_history.assigned_at",
    "assignment_history.responsible_employee_id", "assignment_history.reason",
    "assignment_history.source",
)}


async def _registry_dates(db: Any, *, user_id: str, pieces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from preparation_supplier_dispatch import REGISTRY

    batch_ids = sorted({_text(row.get("batch_id")) for row in pieces} - {""})
    if not batch_ids:
        return {}
    rows = await db[REGISTRY].find(
        {"user_id": user_id, "batch_id": {"$in": batch_ids}},
        {"_id": 0, "batch_id": 1, "created_at": 1, "registered_at": 1},
    ).to_list(None)
    return {_text(row.get("batch_id")): row for row in rows}


async def _file_day_registries(db: Any, *, user_id: str, day: str) -> dict[str, dict[str, Any]]:
    from preparation_supplier_dispatch import REGISTRY
    # Keep the legacy day→batch restriction in Mongo before fetching physical
    # piece metadata. The legacy selector uses registration, not creation.
    registered = {"$convert": {"input": "$registered_at", "to": "date", "onError": None, "onNull": None}}
    rows = await db[REGISTRY].find({"user_id": user_id, "$expr": {"$eq": [
        {"$dateToString": {"date": registered, "format": "%Y-%m-%d",
            "timezone": "Asia/Riyadh", "onNull": "__unknown__"}}, day]}},
        {"_id": 0, "batch_id": 1, "created_at": 1, "registered_at": 1}).to_list(None)
    return {_text(row.get("batch_id")): row for row in rows if _text(row.get("batch_id"))}


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


async def progress_day_batches(
    db: Any, *, user_id: str, employee_id: str, date_basis: DateBasis = "file_created_at",
) -> dict[str, list[str]]:
    from preparation_supplier_dispatch import PIECES, DISPATCHES

    # Only outstanding physical pieces produce dates. No quantity or product
    # details cross this boundary; fully received/completed dates disappear.
    candidates = await db[PIECES].find(
        {"user_id": user_id, "responsible_employee_id": employee_id,
            "experiment_archived_at": None,
            "status": {"$in": ["assigned", "in_progress", "ready_for_receipt"]},
            "$or": [
                {"supplier_dispatch_status": {"$in": ["sent", "ready", "partial_received"]}},
                {"supplier_receiving_history.0": {"$exists": True}},
            ]},
        {"_id": 0, "batch_id": 1, "order_number": 1, "piece_id": 1,
         **(_ASSIGNMENT_DATE_PROJECTION if date_basis == "employee_assigned_at" else {})},
    ).to_list(None)
    candidate_ids = sorted({_text(row.get("piece_id")) for row in candidates} - {""})
    dispatched = await db[DISPATCHES].find(
        {"user_id": user_id, "sent_by_id": employee_id, "status": {"$ne": "building"},
         "source_files.cards.piece_id": {"$in": candidate_ids}},
        {"_id": 0, "source_files.cards.piece_id": 1},
    ).to_list(None) if candidate_ids else []
    dispatched_ids = {_text(card.get("piece_id")) for row in dispatched
                      for source in row.get("source_files") or [] for card in source.get("cards") or []}
    candidates = [row for row in candidates if _text(row.get("piece_id")) in dispatched_ids]
    numbers = sorted({_text(row.get("order_number")) for row in candidates} - {""})
    live = await _nonterminal_numbers(db, user_id, numbers) if numbers else set()
    candidates = [row for row in candidates if _text(row.get("order_number")) in live]
    registry = (await _registry_dates(db, user_id=user_id, pieces=candidates)
                if date_basis == "file_created_at" else {})
    result: dict[str, set[str]] = {}
    for row in candidates:
        batch_id = _text(row.get("batch_id"))
        if batch_id:
            key = piece_progress_day(row, registry.get(batch_id, {}), date_basis)
            result.setdefault(key, set()).add(batch_id)
    return {key: sorted(batches) for key, batches in result.items()}


async def progress_days(
    db: Any, *, user_id: str, employee_id: str, date_basis: DateBasis = "file_created_at",
) -> dict[str, Any]:
    dates = await progress_day_batches(db, user_id=user_id, employee_id=employee_id, date_basis=date_basis)
    keys = sorted(dates, key=lambda key: (key != "__unknown__", key), reverse=True)
    return {"ok": True, "employee_id": employee_id, "days": [{"key": key} for key in keys],
            "read_only": True, "date_basis": date_basis}


def _dispatch_ids(row: dict[str, Any]) -> set[str]:
    return {_text(card.get("piece_id")) for source in row.get("source_files") or []
            for card in source.get("cards") or [] if _text(card.get("piece_id"))}


async def progress_workspace(
    db: Any, *, user_id: str, employee_id: str, day: str, after: str = "",
    date_basis: DateBasis = "file_created_at",
) -> dict[str, Any]:
    from preparation_supplier_dispatch import (
        PIECES, DISPATCHES, _employee_workspace, _piece_products,
        piece_is_available_for_supplier_dispatch, annotate_waiting_pieces,
    )
    projection = {field: 1 for field in (
        "piece_id", "batch_id", "order_number", "order_item_id", "unit_index", "status",
        "assignment_status", "supplier_id", "supplier_dispatch_id", "supplier_dispatch_status",
        "sent_to_supplier_by_id", "supplier_receiving_session_id", "branch_handoff_at")}
    if date_basis == "employee_assigned_at":
        projection.update(_ASSIGNMENT_DATE_PROJECTION)
    # A batch can contain assignments from several days. Select exact pieces
    # before counting/pagination. File-date callers retain their day/batch
    # narrowing; assignment-date callers need no registry date read at all.
    registry = (await _file_day_registries(db, user_id=user_id, day=day)
                if date_basis == "file_created_at" else {})
    query = {"user_id": user_id, "responsible_employee_id": employee_id,
             "experiment_archived_at": None, "status": {"$ne": "cancelled"}}
    if date_basis == "file_created_at":
        query["batch_id"] = {"$in": list(registry)}
    metadata = await db[PIECES].find(query, {"_id": 0, **projection}).to_list(None)
    metadata = [row for row in metadata if _text(row.get("batch_id"))
                and piece_progress_day(row, registry.get(_text(row.get("batch_id")), {}), date_basis) == day]
    batches = sorted({_text(row.get("batch_id")) for row in metadata} - {""})
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
    return {"ok": True, "read_only": True, **workspace, "date_basis": date_basis,
        "next_cursor": page_ids[-1] if len(available) > 10 else None,
        "progress_day_summary": day_summary, "progress_supplier_totals": supplier_totals,
        "progress_active_piece_ids": remaining_ids}


async def waiting_count(db: Any, *, user_id: str, employee_id: str, group: str) -> dict[str, Any]:
    """Selected-group count, independent of first details and never a write gate.

    Read eligibility metadata only. Reuse the canonical current-order/local
    review policy once per order within this request, without a cross-request
    authority cache or customer-option/image/supplier hydration.
    """
    from preparation_supplier_dispatch import (
        PIECES, _waiting_query, _waiting_group_expression,
        piece_is_available_for_supplier_dispatch, annotate_waiting_pieces,
    )
    query = _waiting_query(user_id, employee_id)
    query["$expr"] = {"$eq": [_waiting_group_expression(), {"$literal": group}]}
    projection = {"_id": 0, **{key: 1 for key in (
        "order_number", "status", "assignment_status", "supplier_dispatch_status",
        "supplier_receiving_session_id", "experiment_archived_at")}}
    cursor = db[PIECES].find(query, projection).batch_size(256)
    eligibility = {}
    total = 0
    try:
        while True:
            batch = await cursor.to_list(length=256)
            if not batch:
                break
            candidates = [row for row in batch if piece_is_available_for_supplier_dispatch(row)]
            pending = {_text(row.get("order_number")): row for row in candidates
                       if _text(row.get("order_number")) not in eligibility}
            if pending:
                annotated = await annotate_waiting_pieces(db, user_id=user_id, pieces=list(pending.values()))
                eligibility.update({_text(row.get("order_number")): row.get("waiting_review_eligible") is True
                                    for row in annotated})
            total += sum(eligibility.get(_text(row.get("order_number")), False) for row in candidates)
    finally:
        await cursor.close()
    return {"ok": True, "employee_id": employee_id, "group": group,
            "eligible_piece_count": total, "read_only": True}


async def waiting_cards(db: Any, *, user_id: str, employee_id: str, after: str = "") -> dict[str, Any]:
    from preparation_supplier_dispatch import PIECES, _waiting_query, _waiting_group_expression
    from supplier_dispatch_waiting_policy import waiting_header_order_fields, waiting_header_order_dates
    from fastapi import HTTPException
    from base64 import urlsafe_b64decode, urlsafe_b64encode
    import json

    boundary = None
    legacy_after = ""
    try:
        if after.startswith("g2:"):
            boundary = json.loads(urlsafe_b64decode(after[3:] + "=" * (-len(after[3:]) % 4)))
            if (not isinstance(boundary, list) or len(boundary) != 2
                    or not all(isinstance(value, str) and value for value in boundary)):
                raise ValueError
            if datetime.fromisoformat(boundary[0]).tzinfo is None:
                raise ValueError
        elif after.startswith("g:") and after[2:]:
            # Already-open Android72/75 screens can finish a legacy page.
            legacy_after = after[2:]
        elif after:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid_waiting_group_cursor")

    def trimmed(field):
        return {"$trim": {"input": {"$convert": {"input": "$" + field,
            "to": "string", "onError": "", "onNull": ""}}}}

    pipeline = [
        {"$match": _waiting_query(user_id, employee_id)},
        {"$match": {"$expr": {"$and": [
            {"$ne": [trimmed("assignment_status"), "unassigned"]},
            {"$not": [{"$in": [trimmed("status"), ["blocked", "cancelled", "received", "ready_for_assembly"]]}]},
        ]}}},
        # Deduplicate physical units before reading current order metadata.
        # The representative header must come from an eligible order, never
        # from a terminal member that happens to share this product identity.
        {"$group": {"_id": {"group": _waiting_group_expression(), "order": trimmed("order_number")},
            "product_id": {"$first": "$product_id"}, "product_name": {"$first": "$product_name"},
            "sku": {"$first": "$sku"}, "image_url": {"$first": {"$ifNull": [
                "$selected_image_url", {"$ifNull": ["$resolved_image_url", "$image_url"]}]}}}},
    ]
    if legacy_after:
        pipeline.append({"$match": {"_id.group": {"$gt": legacy_after}}})
    candidates = []
    cursor = db[PIECES].aggregate(pipeline, batchSize=256)
    try:
        while rows := await cursor.to_list(256):
            candidates.extend(rows)
    finally:
        await cursor.close()
    numbers = sorted({row["_id"]["order"] for row in candidates})
    dates = await waiting_header_order_dates(db, user_id=user_id, order_numbers=numbers) if numbers else {}
    by_group = {}
    for row in candidates:
        identity = row.pop("_id")
        key, number = identity["group"], identity["order"]
        if number not in dates:
            continue
        group = by_group.setdefault(key, {"_id": key, "date": dates[number], "members": []})
        group["date"] = max(group["date"], dates[number])
        group["members"].append({"order": number, "date": dates[number], **row})
    candidates = sorted(by_group.values(), key=lambda row: row["_id"])
    candidates.sort(key=lambda row: row["date"], reverse=True)
    for row in candidates:
        row["members"].sort(key=lambda member: member["date"])
    groups = []
    eligibility = {}
    for offset in range(0, len(candidates), 16):
        rows = candidates[offset:offset + 16]
        pending = list(rows)
        accepted = {}
        while pending:
            # One representative per group first. A rejected order must
            # not hide another live member of the same product group.
            numbers = sorted({row["members"][-1]["order"] for row in pending} - set(eligibility))
            if numbers:
                eligibility.update(await waiting_header_order_fields(db, user_id=user_id, order_numbers=numbers))
            remaining = []
            for row in pending:
                member = row["members"].pop()
                if eligibility[member.pop("order")]["waiting_review_eligible"] is True:
                    accepted[row["_id"]] = {"key": row["_id"], **member, "lazy": True}
                elif row["members"]:
                    remaining.append(row)
            pending = remaining
        groups.extend(accepted[row["_id"]] for row in rows if row["_id"] in accepted)
        if boundary:
            date, key = boundary
            groups = [row for row in groups if row["date"] < date
                      or (row["date"] == date and row["key"] > key)]
        groups.sort(key=lambda row: row["key"])
        groups.sort(key=lambda row: row["date"], reverse=True)
        groups = groups[:11]
        # A candidate's newest possible date is an upper bound. Continue
        # if rejected local proof/payment could move it behind a later
        # candidate. Stop only when no unseen group can enter this page.
        if len(groups) == 11:
            cutoff = groups[-1]
            last = rows[-1]
            if (last["date"] < cutoff["date"]
                    or (last["date"] == cutoff["date"] and last["_id"] >= cutoff["key"])):
                break
    # Newest eligible order first; no global sales/count calculation on entry.
    page = groups[:10]
    next_cursor = None
    if len(groups) > 10:
        boundary = json.dumps([page[-1]["date"], page[-1]["key"]], separators=(",", ":")).encode()
        next_cursor = "g2:" + urlsafe_b64encode(boundary).decode().rstrip("=")
    return {"ok": True, "employee_id": employee_id, "read_only": True,
        "groups": [{key: value for key, value in row.items() if key != "date"} for row in page],
        "next_cursor": next_cursor,
        "suppliers": []}
