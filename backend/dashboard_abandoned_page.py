"""Bounded dashboard cart reads; preserve the existing date classification.

Mongo returns only aggregate counters and page identities. Large item arrays
and encrypted profiles are fetched for one page, never for the entire rail.
"""
import base64
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones


META_FIELDS = ("cart_id", "purchased", "cart_created_at", "cart_updated_at",
               "first_seen_at", "last_received_at", "created_at", "updated_at")
DETAIL_FIELDS = (*META_FIELDS, "currency", "total", "items", "customer_identity_id")
BATCH_SIZE = 128
MAX_PAGE_SIZE = 100
_TIMEZONES = sorted(available_timezones())


def decode_cursor(cursor, start, end):
    if not cursor:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if value["period"] != [start, end]:
            raise ValueError()
        timestamp = datetime.fromisoformat(value["time"])
        if timestamp.tzinfo is None or not isinstance(value["id"], str):
            raise ValueError()
        return timestamp, value["id"]
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("invalid_cart_cursor") from exc


def page_arguments(start, end, limit, cursor):
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("invalid_cart_limit")
    if end < start:
        start, end = end, start
    start_at = datetime.combine(date.fromisoformat(start), time(), ZoneInfo("Asia/Riyadh"))
    end_at = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time(), ZoneInfo("Asia/Riyadh"))
    return start, end, start_at, end_at, decode_cursor(cursor, start, end)


def _convert(value, target):
    return {"$convert": {"input": value, "to": target, "onError": None, "onNull": None}}


def _text(value):
    return {"$trim": {"input": {"$ifNull": [_convert(value, "string"), ""]}}}


def _converted(field):
    """Normalize stored Salla timestamp shapes inside Mongo, without loading rows.

    Match the ingestion parser's seconds/milliseconds threshold and one-level
    date envelope. Mongo dates have millisecond precision; sub-millisecond
    ordering remains limited by BSON date precision. ISO week dates and offsets
    with seconds are not supported here. Ambiguous/nonexistent local times in
    DST zones follow Mongo's timezone rules, which can differ from Python's
    fold=0 policy; Asia/Riyadh has no such transitions. This is compatibility
    for the tested Salla timestamp shapes, not a general Python ISO interpreter.
    """
    value = "$" + field
    # _first at ingestion skips only None and the empty string (not zero).
    unwrapped = None
    for name in reversed(("date", "datetime", "value", "timestamp")):
        candidate = value + "." + name
        unwrapped = {"$cond": [{"$and": [
            {"$ne": [{"$ifNull": [candidate, None]}, None]},
            {"$ne": [candidate, ""]},
        ]}, candidate, unwrapped]}
    numeric = {"$let": {"vars": {"millis": {"$cond": [
        {"$gte": [{"$abs": "$$number"}, 100_000_000_000]},
        "$$number", {"$multiply": ["$$number", 1000]},
    ]}}, "in": {"$cond": [{"$and": [
        {"$gte": ["$$millis", -62135596800000]},
        {"$lte": ["$$millis", 253402300799999]},
    ]}, _convert("$$millis", "date"), None]}}}
    js_date = {"$let": {"vars": {"js": {"$regexFind": {
        "input": "$$text", "regex": r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}) (\d{4}) (\d{2}:\d{2}:\d{2}) GMT([+-]\d{4})",
    }}}, "in": {"$cond": [{"$ne": ["$$js", None]}, {"$dateFromString": {
        "dateString": {"$concat": [
            {"$arrayElemAt": ["$$js.captures", 2]}, "-",
            {"$arrayElemAt": [["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"],
                {"$indexOfArray": [["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], {"$arrayElemAt": ["$$js.captures", 0]}]}]}, "-",
            {"$arrayElemAt": ["$$js.captures", 1]}, "T",
            {"$arrayElemAt": ["$$js.captures", 3]},
            {"$arrayElemAt": ["$$js.captures", 4]},
        ]}, "format": "%Y-%m-%dT%H:%M:%S%z", "onError": None, "onNull": None,
    }}, None]}}}
    iso = {"$cond": [
        {"$regexMatch": {"input": "$$text", "regex": r"(?:[zZ]|[+-]\d{2}:?\d{2})$"}},
        {"$dateFromString": {"dateString": "$$text", "onError": None, "onNull": None}},
        {"$dateFromString": {"dateString": "$$text", "timezone": "$$zone", "onError": None, "onNull": None}},
    ]}
    # dateFromString also accepts human prose that Python's ISO parser rejects.
    parsed_string = {"$cond": [
        {"$regexMatch": {"input": "$$text", "regex": r"^\d{4}-\d{2}-\d{2}(?:[Tt ].*)?$"}},
        iso, js_date,
    ]}
    historical = {"$let": {"vars": {
        "value": {"$cond": [{"$eq": [{"$type": value}, "object"]}, unwrapped, value]},
        "hint": _text(value + ".timezone"),
    }, "in": {"$let": {"vars": {
        "text": _text("$$value"),
        "number": {"$cond": [{"$in": [{"$type": "$$value"}, ["int", "long", "double", "decimal", "string"]]}, _convert("$$value", "double"), None]},
        "zone": {"$cond": [{"$eq": ["$$hint", ""]}, "UTC",
            {"$cond": [{"$in": ["$$hint", _TIMEZONES]}, "$$hint", "UTC"]}]},
    }, "in": {"$switch": {"branches": [
        {"case": {"$eq": [{"$type": "$$value"}, "date"]}, "then": "$$value"},
        {"case": {"$ne": ["$$number", None]}, "then": numeric},
        {"case": {"$eq": [{"$type": "$$value"}, "string"]}, "then": parsed_string},
    ], "default": None}}}}}}
    # Normalized ingestion writes ISO UTC strings. Keep that common path cheap;
    # legacy forms alone pay the envelope/numeric/timezone parsing cost.
    return {"$cond": [{"$and": [
        {"$eq": [{"$type": value}, "string"]},
        {"$regexMatch": {"input": _text(value), "regex": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"}},
    ]}, _convert(_text(value), "date"), historical]}


def _first_date(*fields):
    value = None
    for field in reversed(fields):
        value = {"$ifNull": [_converted(field), value]}
    return value


async def read_cart_page(collection, user_id, *, start, end, limit=50, cursor=None):
    """Mongo performs date filtering, counters and top-k sorting.

    No tenant-wide Python cursor/list or offset-sized buffer. Item payloads and
    customer references are loaded only after selecting this page's identities.
    Counters include the complete period; they are not computed from the page.
    """
    start, end, start_at, end_at, after = page_arguments(start, end, limit, cursor)
    def in_range(value):
        return {"$and": [{"$gte": [value, start_at]}, {"$lt": [value, end_at]}]}
    created = in_range(_converted("cart_created_at"))
    active = {"$and": [created, {"$ne": [{"$ifNull": ["$purchased", False]}, True]}]}
    # Scalar counters and top-k page selection share one metadata-only scan.
    # Facet outputs are bounded: one count record and at most limit+1 keys.
    # No branch pushes the complete period into an array.
    page_stages = [{"$match": {"_active": True}}]
    if after is not None:
        page_stages.append({"$match": {"$or": [
            {"_activity": {"$lt": after[0]}},
            {"_activity": after[0], "_key": {"$lt": after[1]}},
        ]}})
    page_stages.extend([{"$sort": {"_activity": -1, "_key": -1}},
                        {"$limit": limit + 1},
                        {"$project": {"_id": 1, "_activity": 1, "_key": 1}}])
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$project": {
            "_created": _converted("cart_created_at"),
            "_updated": _converted("cart_updated_at"),
            "_updated_fallback": _converted("updated_at"),
            "last_received_at": 1, "first_seen_at": 1, "created_at": 1,
            "purchased": 1,
        }},
        {"$project": {
            "_created": in_range("$_created"),
            "_active": {"$and": [in_range("$_created"),
                {"$ne": [{"$ifNull": ["$purchased", False]}, True]}]},
            "_recovered": {"$and": [{"$eq": ["$purchased", True]},
                in_range({"$ifNull": ["$_updated", "$_updated_fallback"]})]},
            "_activity": {"$ifNull": ["$_updated", {"$ifNull": [
                _converted("last_received_at"), {"$ifNull": ["$_updated_fallback", {
                    "$ifNull": ["$_created", _first_date("first_seen_at", "created_at")]}]}]}]},
            "_key": {"$concat": [{"$type": "$_id"}, ":", {"$toString": "$_id"}]},
        }},
        {"$facet": {
            "counts": [{"$group": {"_id": None, **{
                field: {"$sum": {"$cond": ["$_" + field, 1, 0]}}
                for field in ("created", "active", "recovered")}}}],
            "page": page_stages,
        }},
    ]
    result = await collection.aggregate(pipeline, allowDiskUse=True, maxTimeMS=60000).to_list(length=1)
    result = result[0] if result else {}
    counts = (result.get("counts") or [{}])[0]
    selected = result.get("page") or []
    has_more = len(selected) > limit
    selected = selected[:limit]
    rows = []
    if selected:
        details = await collection.find(
            {"user_id": user_id, "_id": {"$in": [entry["_id"] for entry in selected]}, "$expr": active},
            {field: 1 for field in DETAIL_FIELDS},
        ).limit(limit).to_list(length=limit)
        by_id = {row["_id"]: row for row in details}
        rows = [by_id[entry["_id"]] for entry in selected if entry["_id"] in by_id]
        for row in rows:
            row.pop("_id", None)
    next_cursor = None
    if has_more:
        last = selected[-1]
        timestamp = last["_activity"].replace(tzinfo=timezone.utc)
        next_cursor = base64.urlsafe_b64encode(json.dumps({
            "period": [start, end], "time": timestamp.isoformat(), "id": last["_key"],
        }).encode()).decode()
    return rows, counts.get("created", 0), counts.get("recovered", 0), {
        "limit": limit, "has_more": has_more, "next_cursor": next_cursor,
        "total_active": counts.get("active", 0),
    }
