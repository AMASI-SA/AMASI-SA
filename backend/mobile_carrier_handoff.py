"""Small employee custody pages over current, locally saved order evidence.

Creation time bounds the two queues. A dated execution is display evidence only;
missing/ambiguous execution history never becomes an invented creation/update
time. Counts read bounded metadata batches; only the eight visible orders load
the saved native completion proofs. No provider refresh or accounting writes.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import heapq
import json
from math import ceil
import re
from uuid import UUID

from fastapi import HTTPException
from pymongo.errors import ExecutionTimeout

from assembly_status_policy import canonical_status
from operational_courier_transitions import COMPLETED_PROOF, transition_observation
from operational_delivery_evidence import _instant
from operational_order_completed_evidence import completion_time
from shipping_companies import normalize_shipping_company

ORDERS = "unified_orders"
WORKFLOWS = "order_review_workflows"
HISTORY = "order_activity_events_v2"
RECEIPT = "carrier_handoff_receipt"
PAGE_SIZE = 8
BATCH_SIZE = 128
MAX_CANDIDATES = 8192
MAX_HISTORY_ROWS = 2048
READ_TIMEOUT_SECONDS = 8
MAX_TIME_MS = 1500
CREATION_INDEX = [("user_id", 1), ("order_date", -1)]
UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_LIST_RAW_FIELDS = ("id", "reference_id", "order_number", "date", "created_at", "order_date",
    "status", "status_slug")
LIST_PROJECTION = {"_id": 0, "user_id": 1, "order_id": 1, "salla_order_id": 1, "order_number": 1,
    "order_status": 1, "order_status_slug": 1,
    "g47_salla_snapshot.requires_authoritative_refresh": 1, "g47_salla_snapshot.component_pending": 1,
    "customer_name": 1, "shipping_city": 1, "shipping_company": 1,
    "store_delivery_assignment_id": 1, "salla_shipping_current.company_name": 1,
    "salla_shipping_current.company_code": 1, "salla_shipping_current.tracking_number": 1,
    **{"raw_by_source.salla_direct." + field: 1 for field in _LIST_RAW_FIELDS}}
WORKFLOW_PROJECTION = {"_id": 0, **{field: 1 for field in (
    "user_id", "order_number", "stage", "carrier_label_type", "carrier_name", "carrier_tracking_number",
    "carrier_label_barcode", "carrier_label_print_confirmed", "carrier_handoff_state",
    "carrier_handoff_employee_id", "carrier_handoff_employee_name", "carrier_handoff_scanned_at",
    "shipping_status_epoch", "store_delivery_assignment_id", "store_courier_assignee_id",
    "customer_service_hold_active")}}

# capture_time checks every native shipping identity alias. Keep those exact
# fields, but never product lines, options, payment details or address payloads.
from operational_completed_capture import _RAW_SHIPPING_KEYS
_PROOF_RAW_FIELDS = set(_LIST_RAW_FIELDS) | _RAW_SHIPPING_KEYS | {
    "order_id", "salla_order_id", "order_reference_id", "updated_at", "date_updated", "status_changed_at",
    "merchant", "merchant_id", "store_id", "shipping_status", "shipment_status"}
PROOF_PROJECTION = {"_id": 0, "user_id": 1, "order_id": 1, "salla_order_id": 1, "order_number": 1,
    "order_status": 1, "order_status_slug": 1, "g47_salla_snapshot": 1, "salla_shipping_current": 1,
    "customer_name": 1, "shipping_city": 1, "shipping_company": 1, "store_delivery_assignment_id": 1,
    "merchant": 1, "merchant_id": 1, "store_id": 1,
    **{"raw_by_source.salla_direct." + field: 1 for field in _PROOF_RAW_FIELDS}}
HISTORY_PROJECTION = {"_id": 0, **{field: 1 for field in (
    "user_id", "order_number", "provider_order_id", "source_event_id", "native_status_transition")}}


def now():
    return datetime.now(timezone.utc)


def fail(code, message, status=409, **details):
    raise HTTPException(status, detail={"code": code, "message": message, **details})


def session_scope(owner, actor):
    return hashlib.sha256(json.dumps(["carrier_handoff_v1", owner, actor],
        ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def check_scope(owner, actor, expected):
    scope = session_scope(owner, actor)
    if expected != scope:
        fail("carrier_handoff_session_scope_changed", "تغير حساب جلسة الاستلام؛ أعد فتح الصفحة.", not_applied=True)
    return scope


def checked_uuid(value, name):
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        fail("carrier_handoff_" + name + "_invalid", "معرّف محاولة الاستلام غير صالح.", 422, not_applied=True)
    return str(UUID(value))


def raw_order(source):
    return ((source or {}).get("raw_by_source") or {}).get("salla_direct") or {}


def order_created_at(source):
    raw = raw_order(source)
    return _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))


def source_identity(source, owner):
    raw = raw_order(source)
    number, oid = str(source.get("order_number") or ""), str(raw.get("id") or "")
    return bool(number and oid and source.get("user_id") == owner
        and str(raw.get("reference_id") or raw.get("order_number") or "") == number
        and all(source.get(key) is None or str(source[key]) == oid for key in ("order_id", "salla_order_id")))


def external_carrier(source, workflow=None):
    workflow = workflow or {}
    if (workflow.get("carrier_label_type") == "store_courier" or workflow.get("store_delivery_assignment_id")
            or workflow.get("store_courier_assignee_id") or source.get("store_delivery_assignment_id")):
        return None
    current = source.get("salla_shipping_current") or {}
    names = [current.get("company_name"), source.get("shipping_company"), workflow.get("carrier_name")]
    normalized = [normalize_shipping_company(value) for value in names if isinstance(value, str) and value.strip()]
    if not normalized or any(key in {"mandoob", "mandoob_riyadh", "pickup"} for key, _ in normalized):
        return None
    return next(((key, name) for key, name in normalized if key != "unknown"), None)


def _creation_range(start, end):
    # Native values may be BSON UTC, ISO offsets, or Salla's {date, timezone}.
    # Widen the text prefilter by two calendar days; exact aware instants below
    # enforce the actual 30*24h boundary without lexical timezone assumptions.
    lower = (start - timedelta(days=2)).date().isoformat()
    upper = (end + timedelta(days=2)).date().isoformat()
    paths = ["raw_by_source.salla_direct." + field for field in ("date", "created_at", "order_date")]
    return {"$or": [condition for path in paths for target in (path, path + ".date") for condition in (
        {target: {"$type": "string", "$gte": lower, "$lte": upper}},
        {target: {"$type": "date", "$gte": start, "$lte": end}})]}


def candidate_query(owner, start, end):
    prefix = "raw_by_source.salla_direct."
    current = {"$or": [{prefix + path: {"$in": ["completed", "تم التنفيذ"]}} for path in (
        "status", "status.slug", "status.name", "status.customized.slug", "status.customized.name", "status_slug")]}
    excluded = "مندوب|mandoob|pickup|استلام من المتجر|تسليم مباشر"
    # salla_integration.sync derives this indexed helper from the native date.
    # It only narrows discovery. Missing/null/empty helpers retain a bounded
    # index branch; the exact native timestamp remains the inclusion authority.
    indexed_date = {"$or": [
        {"order_date": {"$gte": (start - timedelta(days=2)).date().isoformat(),
                        "$lte": (end + timedelta(days=2)).date().isoformat()}},
        {"order_date": {"$in": [None, ""]}},
    ]}
    query = {"user_id": owner, "$and": [indexed_date, current, _creation_range(start, end),
        {"store_delivery_assignment_id": {"$in": [None, ""]}},
        {"salla_shipping_current.company_name": {"$not": re.compile(excluded, re.I)}},
        {"shipping_company": {"$not": re.compile(excluded, re.I)}},
    ]}
    # A confirmed workflow can supply a missing/unknown source carrier name.
    # The bounded workflow join performs the positive external-carrier check.
    return query


def _candidate_cursor(db, owner, start, end):
    # One indexed stream, not an order-number restart for every metadata batch.
    # This index already exists in server startup. Never create indexes in GET.
    return (db[ORDERS].find(candidate_query(owner, start, end), LIST_PROJECTION)
        .hint(CREATION_INDEX).sort([("order_date", -1)]).max_time_ms(MAX_TIME_MS)
        .batch_size(BATCH_SIZE).limit(MAX_CANDIDATES + 1))


async def _metadata_batches(cursor):
    batch = []
    async for row in cursor:
        batch.append(row)
        if len(batch) == BATCH_SIZE:
            yield batch
            batch = []
    if batch:
        yield batch


async def _read(collection, selector, projection, *, limit, sort=None):
    cursor = collection.find(selector, projection).max_time_ms(MAX_TIME_MS)
    if sort:
        cursor = cursor.sort(sort)
    return await cursor.limit(limit).to_list(length=limit)


def execution_time(source, history, end):
    """Current completed event for display; keep financial chronology separate.

    A current native changed_at proves the present transition even if the order
    was completed once before. Without it, examine the latest typed stored
    transition. Generic order updates and receipt times are never substitutes.
    """
    raw = raw_order(source)
    try:
        if canonical_status(source) != "completed" or not source_identity(source, source.get("user_id")):
            return None
        status = raw.get("status")
        effective = (status.get("customized") or status) if isinstance(status, dict) else status
        direct = [value for value in (
            effective.get("changed_at") if isinstance(effective, dict) else None,
            raw.get("status_changed_at")) if value is not None]
        if direct:
            # Strip older, independently dated proof only from this transient
            # display view. The strict source/clock validator still runs.
            current = source.get("salla_shipping_current") or {}
            detached = {**source, "salla_shipping_current": {k: v for k, v in current.items() if k != COMPLETED_PROOF}}
            return completion_time(raw, detached, [], end)
        valid, other_transitions = [], []
        for row in history:
            if (row.get("user_id") != source["user_id"] or row.get("order_number") != source["order_number"]
                    or row.get("provider_order_id") != str(raw.get("id")) or not row.get("source_event_id")):
                return None
            proof = transition_observation(row.get("native_status_transition"))
            if proof.get("target_status") == "completed":
                if proof.get("delivered") is not False or proof.get("source"):
                    return None
                valid.append((_instant(proof.get("at")), row))
            else:
                other_transitions.append(_instant(proof.get("at")))
        candidates = []
        if valid:
            latest = max(at for at, _ in valid)
            current = source.get("salla_shipping_current") or {}
            detached = {**source, "salla_shipping_current": {k: v for k, v in current.items() if k != COMPLETED_PROOF}}
            candidates.append(completion_time(raw, detached, [row for at, row in valid if at == latest], end))
        try:
            candidates.append(completion_time(raw, source, [], end))
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        selected = max(candidates) if candidates else None
        if selected is None or any(at >= selected for at in other_transitions):
            return None
        return selected
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _queue_item(source, workflow, owner, actor, start, end):
    if not source_identity(source, owner) or canonical_status(source) != "completed":
        return None
    try:
        created = order_created_at(source)
    except (ValueError, TypeError):
        return None
    if not start <= created <= end:
        return None
    carrier = external_carrier(source, workflow)
    if not carrier:
        return None
    employee = str(workflow.get("carrier_handoff_employee_id") or "").strip()
    if employee:
        if employee != actor or workflow.get("carrier_handoff_state") != "with_handoff_employee":
            return None
        bucket = "received"
        try:
            received = _instant(workflow.get("carrier_handoff_scanned_at"))
        except (ValueError, TypeError):
            return None
        if received > end:
            return None
    else:
        bucket, received = "waiting", None
    current = source.get("salla_shipping_current") or {}
    confirmed = workflow.get("carrier_label_print_confirmed") is True
    item = {"order_number": source["order_number"], "order_id": str(raw_order(source)["id"]),
        "customer_name": source.get("customer_name") or None, "city": source.get("shipping_city") or None,
        "carrier_key": carrier[0], "carrier_name": carrier[1],
        "tracking_number": workflow.get("carrier_tracking_number") or current.get("tracking_number") or None,
        "order_status": "completed", "created_at": created.isoformat(), "executed_at": None,
        "received_at": received.isoformat() if received else None,
        "employee_id": employee or None, "employee_name": workflow.get("carrier_handoff_employee_name") or None,
        "label_confirmed": confirmed, "can_receive": bool(bucket == "waiting" and confirmed
            and workflow.get("stage") == "completed" and not workflow.get("customer_service_hold_active"))}
    return bucket, received or created, item


async def queue_page(db, owner, actor, *, bucket, page=1, query=""):
    query = query.strip()
    if bucket not in {"received", "waiting"} or type(page) is not int or page < 1 or page > MAX_CANDIDATES // PAGE_SIZE:
        fail("carrier_handoff_page_invalid", "صفحة الشحنات غير صالحة.", 422)
    end, counts, total, seen, selected = now(), {"received": 0, "waiting": 0}, 0, set(), []
    start = end - timedelta(days=30)
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            candidates = _candidate_cursor(db, owner, start, end)
            async for sources in _metadata_batches(candidates):
                numbers = [row["order_number"] for row in sources]
                if len(set(numbers)) != len(numbers) or seen.intersection(numbers):
                    fail("carrier_handoff_source_ambiguous", "تعذر التحقق من قائمة الشحنات.", 503)
                seen.update(numbers)
                if len(seen) > MAX_CANDIDATES:
                    fail("carrier_handoff_read_budget_exceeded", "تعذر إكمال عدّ الشحنات الآن؛ أعد المحاولة.", 503)
                workflows = await _read(db[WORKFLOWS], {"user_id": owner, "order_number": {"$in": numbers}},
                    WORKFLOW_PROJECTION, limit=BATCH_SIZE + 1)
                if len(workflows) != len({row["order_number"] for row in workflows}):
                    fail("carrier_handoff_source_ambiguous", "تعذر التحقق من عهدة الشحنات.", 503)
                by_number = {row["order_number"]: row for row in workflows}
                for source in sources:
                    result = _queue_item(source, by_number.get(source["order_number"], {}), owner, actor["id"], start, end)
                    if result is None:
                        continue
                    item_bucket, at, item = result
                    counts[item_bucket] += 1
                    from carrier_handoff import normalize_shipping_barcode
                    matches = (query.casefold() == str(item["order_number"]).casefold()
                        or bool(item["tracking_number"]) and normalize_shipping_barcode(query)
                        == normalize_shipping_barcode(item["tracking_number"]))
                    if item_bucket != bucket or query and not matches:
                        continue
                    total += 1
                    entry = (at, item["order_number"], item)
                    if len(selected) < page * PAGE_SIZE:
                        heapq.heappush(selected, entry)
                    elif entry[:2] > selected[0][:2]:
                        heapq.heapreplace(selected, entry)
            pages = ceil(total / PAGE_SIZE)
            if page > max(pages, 1):
                fail("carrier_handoff_page_changed", "تغيرت قائمة الشحنات؛ أعد التحميل من الصفحة الأولى.")
            items = [row[2] for row in sorted(selected, reverse=True)][(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
            if items:
                numbers = [row["order_number"] for row in items]
                proofs = await _read(db[ORDERS], {"user_id": owner, "order_number": {"$in": numbers}},
                    PROOF_PROJECTION, limit=PAGE_SIZE + 1)
                current_workflows = await _read(db[WORKFLOWS], {"user_id": owner, "order_number": {"$in": numbers}},
                    WORKFLOW_PROJECTION, limit=PAGE_SIZE + 1)
                histories = await _read(db[HISTORY], {"user_id": owner, "order_number": {"$in": numbers},
                    "native_status_transition": {"$exists": True}}, HISTORY_PROJECTION, limit=MAX_HISTORY_ROWS + 1)
                if len(histories) > MAX_HISTORY_ROWS:
                    fail("carrier_handoff_history_unavailable", "تعذر إكمال قراءة أوقات التنفيذ؛ أعد المحاولة.", 503)
                by_number, history_by_number = {row["order_number"]: row for row in proofs}, defaultdict(list)
                current_by_number = {row["order_number"]: row for row in current_workflows}
                if len(by_number) != len(proofs) or len(current_by_number) != len(current_workflows):
                    fail("carrier_handoff_source_ambiguous", "تعذر التحقق من قائمة الشحنات.", 503)
                for event in histories:
                    history_by_number[event["order_number"]].append(event)
                for item in items:
                    source = by_number.get(item["order_number"])
                    checked = _queue_item(source, current_by_number.get(item["order_number"], {}),
                        owner, actor["id"], start, end) if source else None
                    if checked is None or checked[0] != bucket or checked[2] != item:
                        fail("carrier_handoff_page_changed", "تغيرت قائمة الشحنات؛ أعد التحميل من الصفحة الأولى.")
                    executed = execution_time(source, history_by_number[item["order_number"]], end)
                    item["executed_at"] = executed.isoformat() if executed else None
    except (TimeoutError, ExecutionTimeout):
        fail("carrier_handoff_read_timeout", "تعذر إكمال قراءة الشحنات الآن؛ أعد المحاولة.", 503)
    return {"schema": 1, "bucket": bucket, "page": page, "page_size": PAGE_SIZE, "total": total,
        "total_pages": pages, "query": query, "counts": counts, "items": items,
        "window": {"basis": "created_at", "field": "created_at", "days": 30, "from": start.isoformat(), "to": end.isoformat()},
        "actor": actor, "session_scope": session_scope(owner, actor["id"]), "permissions": {"scan": True}}


def public_receipt(receipt):
    return {key: receipt[key] for key in ("receipt_id", "request_id", "session_id", "session_scope", "employee_id",
        "order_number", "carrier_key", "carrier_name", "received_at")}


async def session_summary(db, owner, actor, session_id, expected_scope, request_id=None):
    scope = check_scope(owner, actor["id"], expected_scope)
    session_id = checked_uuid(session_id, "session_id")
    request_id = checked_uuid(request_id, "request_id") if request_id is not None else None
    selector = {"user_id": owner, "carrier_handoff_employee_id": actor["id"],
        RECEIPT + ".session_scope": scope, RECEIPT + ".session_id": session_id}
    # Group saved acknowledgements on Mongo; queue status/date does not change
    # the count for a physical receiving session. Do not hydrate order rows.
    pipeline = [{"$match": selector}, {"$project": {"_id": 0, RECEIPT: 1}}, {"$facet": {
        "companies": [{"$sort": {RECEIPT + ".received_at": 1, RECEIPT + ".receipt_id": 1}},
            {"$group": {"_id": "$" + RECEIPT + ".carrier_key", "count": {"$sum": 1},
                "carrier_name": {"$first": "$" + RECEIPT + ".carrier_name"}}},
            {"$sort": {"count": -1, "_id": 1}}, {"$limit": 129}],
        "latest": [{"$sort": {RECEIPT + ".received_at": -1, RECEIPT + ".receipt_id": -1}}, {"$limit": 1}],
        "pending": [{"$match": {RECEIPT + ".request_id": request_id}}, {"$limit": 2}],
    }}]
    rows = await db[WORKFLOWS].aggregate(pipeline, maxTimeMS=MAX_TIME_MS).to_list(length=1)
    result = rows[0] if rows else {"companies": [], "latest": [], "pending": []}
    groups, latest, pending = result["companies"], result["latest"], result["pending"]
    if len(groups) > 128:
        fail("carrier_handoff_session_unavailable", "تعذر التحقق من ملخص جلسة الاستلام.", 503)
    companies = [{"carrier_key": row["_id"], "carrier_name": row["carrier_name"], "count": row["count"]} for row in groups]
    if len(pending) > 1:
        fail("carrier_handoff_receipt_ambiguous", "تعذر التحقق من نتيجة الاستلام.", 503)
    return {"schema": 1, "session_id": session_id, "session_scope": scope, "actor": actor,
        "total": sum(row["count"] for row in companies), "companies": companies,
        "last_receipt": public_receipt(latest[0][RECEIPT]) if latest else None,
        "pending_receipt": public_receipt(pending[0][RECEIPT]) if pending else None}
