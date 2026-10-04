"""Bounded dashboard cart reads; preserve the existing date classification.

Mongo returns only aggregate counters and page identities. Large item arrays
and encrypted profiles are fetched for one page, never for the entire rail.
"""
import base64
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


META_FIELDS = ("cart_id", "purchased", "cart_created_at", "cart_updated_at",
               "first_seen_at", "last_received_at", "created_at", "updated_at")
DETAIL_FIELDS = (*META_FIELDS, "currency", "total", "items", "customer_identity_id")
BATCH_SIZE = 128
MAX_PAGE_SIZE = 100


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


def _converted(field):
    return {"$convert": {"input": "$" + field, "to": "date", "onError": None, "onNull": None}}


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
    recovered = {"$and": [{"$eq": ["$purchased", True]},
                            in_range(_first_date("cart_updated_at", "updated_at"))]}
    # Scalar accumulators, never $push of documents or a full-collection facet.
    count_pipeline = [
        {"$match": {"user_id": user_id}},
        {"$project": {"created": created, "active": active, "recovered": recovered}},
        {"$group": {"_id": None, **{field: {"$sum": {"$cond": ["$" + field, 1, 0]}}
                                      for field in ("created", "active", "recovered")}}},
    ]
    counts = await collection.aggregate(count_pipeline, maxTimeMS=60000).to_list(length=1)
    counts = counts[0] if counts else {}
    pipeline = [
        {"$match": {"user_id": user_id, "$expr": active}},
        {"$project": {"_id": 1,
            "_activity": _first_date("cart_updated_at", "last_received_at", "updated_at",
                                     "cart_created_at", "first_seen_at", "created_at"),
            "_key": {"$concat": [{"$type": "$_id"}, ":", {"$toString": "$_id"}]}}},
    ]
    if after is not None:
        pipeline.append({"$match": {"$or": [
            {"_activity": {"$lt": after[0]}},
            {"_activity": after[0], "_key": {"$lt": after[1]}},
        ]}})
    pipeline.extend([{"$sort": {"_activity": -1, "_key": -1}}, {"$limit": limit + 1}])
    # Adjacent sort+limit is a Mongo top-k operation over tiny metadata, not items.
    selected = await collection.aggregate(pipeline, allowDiskUse=True, maxTimeMS=60000).to_list(length=limit + 1)
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
