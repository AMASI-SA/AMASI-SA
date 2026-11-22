"""Bounded employee statement pages over the canonical saved evidence."""
from copy import deepcopy
from datetime import datetime
from math import ceil

from operational_balance_store import STATES, digest, fail, initial, read
from operational_financial_activity_runtime import prepare_in_thread
from operational_provider_statement_reconciliation import (
    STATEMENTS, RECONCILIATION, _access, _authority, _bind_provider, _document,
    build_review,
)
from operational_read_runtime import read_source_cursor
from provider_statement_access import PROVIDER_NAMES

PAGE_SIZE = 10
STATE_PROJECTION = {"_id": 1, "owner_id": 1, "revision": 1, "status": 1,
    "started_at": 1, "engine.as_of": 1, RECONCILIATION + ".statement_summaries": 1}
METADATA_PROJECTION = {"_id": 1, "id": 1, "owner_id": 1, "metadata": 1,
    "metadata_hash": 1, "uploaded_at": 1}


def _time(value):
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.isoformat()
    except (TypeError, ValueError):
        fail("provider_statement_timestamp_invalid", "تعذر التحقق من وقت الكشف المحفوظ", 503)


def _started_at(value):
    # This is also the canonical writer's exact period token. Validate the
    # instant, but preserve Z/offset/fraction formatting for the next upload.
    _time(value)
    if value is not None and not isinstance(value, str):
        fail("provider_statement_timestamp_invalid", "تعذر التحقق من بداية الفترة المحفوظة", 503)
    return value


def _page(page, total):
    if type(page) is not int or page < 1 or (total and page > ceil(total / PAGE_SIZE)) or (not total and page != 1):
        fail("provider_statement_snapshot_changed", "تغير سجل الكشوف؛ أعد التحميل من الصفحة الأولى", 409)
    return {"page": page, "page_size": PAGE_SIZE, "total": total, "total_pages": ceil(total / PAGE_SIZE)}


def _statement(metadata):
    return {key: deepcopy(metadata.get(key)) for key in (
        "id", "provider", "statement_reference", "filename", "native_currency", "row_count")
    } | {"uploaded_at": _time(metadata.get("uploaded_at"))}


def _context(view):
    return {"session_scope": view["session_scope"], "active": view["active"], "revision": view["revision"],
        "started_at": _started_at(view.get("started_at")), "as_of": _time(view.get("as_of")),
        "permissions": {"read": True, "upload": view["permissions"]["upload"]}}


async def _history_identity(db, owner, actor, scopes):
    query = {"owner_id": owner, "metadata.provider": {"$in": sorted(scopes[0])}}
    states = await read_source_cursor(db[STATES].find({"_id": owner, "owner_id": owner}, STATE_PROJECTION).limit(1), 1)
    state = states[0] if states else initial(owner)
    count = await db[STATEMENTS].count_documents(query, maxTimeMS=1500)
    latest = await read_source_cursor(db[STATEMENTS].find(query, {"_id": 1, "uploaded_at": 1})
        .sort([("uploaded_at", -1), ("_id", -1)]).limit(1), 1)
    identity = digest([owner, actor, state["revision"], state.get("started_at"), sorted(scopes[0]),
        sorted(scopes[1]), sorted(scopes[2]), count, latest])
    return state, query, count, identity


async def history_page(db, owner, actor, *, page=1, snapshot=None):
    scopes = await _access(db, owner, actor)
    state, query, total, observed = await _history_identity(db, owner, actor, scopes)
    if (page > 1 and not snapshot) or snapshot is not None and snapshot != observed:
        fail("provider_statement_snapshot_changed", "تغير سجل الكشوف؛ أعد التحميل من الصفحة الأولى", 409)
    pagination = _page(page, total)
    records = await read_source_cursor(db[STATEMENTS].find(query, METADATA_PROJECTION)
        .sort([("uploaded_at", -1), ("_id", -1)]).skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE), PAGE_SIZE)
    summaries = state.get(RECONCILIATION, {}).get("statement_summaries", {})
    items = []
    for document in records:
        metadata = document.get("metadata")
        if (document.get("owner_id") != owner or not isinstance(metadata, dict)
                or metadata.get("provider") not in scopes[0] or document.get("metadata_hash") != digest(metadata)
                or metadata.get("id") != document.get("id") or document.get("_id") != document.get("id")):
            fail("provider_statement_metadata_invalid", "تعذر التحقق من فهرس الكشف المحفوظ", 503)
        summary = summaries.get(document["id"]) or {}
        counts = summary.get("counts")
        items.append({**_statement(metadata), "counts": deepcopy(counts),
            "reviewed_at": _time(summary.get("reviewed_at")), "review_as_of": _time(summary.get("review_as_of")),
            "status": "needs_review" if not counts or any(counts[key] for key in ("missing", "different", "blocked")) else "matched"})
    active = await _authority(db, owner, state)
    fresh_scopes = await _access(db, owner, actor)
    _, _, _, final_identity = await _history_identity(db, owner, actor, fresh_scopes)
    if scopes != fresh_scopes or observed != final_identity or len(items) != min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE):
        fail("provider_statement_snapshot_changed", "تغير سجل الكشوف؛ أعد التحميل من الصفحة الأولى", 409)
    return {"session_scope": digest([owner, actor]), "active": active, "revision": state["revision"],
        "started_at": _started_at(state.get("started_at")), "as_of": _time(state.get("engine", {}).get("as_of")),
        "permissions": {"read": True, "upload": active and bool(scopes[1])}, "snapshot": observed,
        "providers": [{"id": key, "name": name} for key, name in PROVIDER_NAMES.items() if key in scopes[0]],
        "items": items, "pagination": pagination}


async def review_page(db, owner, actor, statement_id, *, page=1, preview_hash=None):
    scopes = await _access(db, owner, actor)
    document = await _document(db, owner, statement_id, scopes[0])
    await _bind_provider(db, owner, actor, document["statement"]["provider"])
    state = await read(db, owner)
    # This mobile read offers no missing-event confirmation. The immutable
    # saved snapshot suffices; do not hydrate external payment/refund sources.
    result = await prepare_in_thread(build_review, state, document, actor)
    if (page > 1 and not preview_hash) or preview_hash is not None and preview_hash != result["preview_hash"]:
        fail("provider_statement_preview_stale", "تغيرت بيانات الكشف؛ أعد القراءة من الصفحة الأولى", 409)
    pagination = _page(page, len(result["rows"]))
    active = await _authority(db, owner, state)
    latest = await db[STATES].find_one({"_id": owner, "owner_id": owner}, {"revision": 1, "started_at": 1})
    if latest and (latest.get("revision"), latest.get("started_at")) != (state["revision"], state.get("started_at")):
        fail("provider_statement_preview_stale", "تغيرت بيانات الكشف؛ أعد القراءة من الصفحة الأولى", 409)
    if scopes != await _access(db, owner, actor):
        fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
    result["active"] = active
    result["permissions"] = {"upload": active and document["statement"]["provider"] in scopes[1]}
    fields = ("id", "source_row", "order_number", "provider", "kind", "event_date", "native_currency",
        "principal_amount", "fee_net", "fee_vat", "fee_total", "status", "reason_code", "message")
    return {**_context(result), "preview_hash": result["preview_hash"], "statement": _statement(result["statement"]),
        "counts": deepcopy(result["counts"]), "pagination": pagination,
        "rows": [{key: deepcopy(row.get(key)) for key in fields}
                 for row in result["rows"][(page - 1) * PAGE_SIZE:page * PAGE_SIZE]]}


async def upload_result(db, owner, actor, result):
    view = await review_page(db, owner, actor, result["statement"]["id"])
    return {**view, "applied_count": len(result["applied_row_ids"]), "duplicate": result["duplicate"],
        "replayed": result["replayed"], "reviewed_at": _time(result["reviewed_at"])}
