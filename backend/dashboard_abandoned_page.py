"""Bounded dashboard cart reads; preserve the existing date classification.

Only timestamp metadata is streamed across the full tenant. Large item arrays
and encrypted profiles are fetched for one page, never for the entire rail.
"""
import base64
import heapq
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from bson import json_util

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


async def read_cart_page(collection, user_id, *, start, end, limit=50, cursor=None):
    # Import lazily to keep the established date parser as the single authority.
    from dashboard_v2_routes import select_abandoned_carts_for_period, _cart_activity_at

    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("invalid_cart_limit")
    if end < start:
        start, end = end, start
    after = decode_cursor(cursor, start, end)
    heap = []
    abandoned_count = recovered_count = active_count = 0
    start_at = datetime.combine(date.fromisoformat(start), time(), ZoneInfo("Asia/Riyadh"))
    end_at = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time(), ZoneInfo("Asia/Riyadh"))
    def converted(field):
        return {"$convert": {"input": "$" + field, "to": "date", "onError": None, "onNull": None}}
    def in_range(value):
        return {"$and": [{"$gte": [value, start_at]}, {"$lt": [value, end_at]}]}
    # Provider cart timestamps are normalized to ISO UTC on ingestion. The
    # recovery counter retains its existing recovery-day meaning; only active
    # carts created in the selected period can enter the display page.
    query = {"user_id": user_id, "$expr": {"$or": [
        in_range(converted("cart_created_at")),
        {"$and": [{"$eq": ["$purchased", True]}, in_range({"$ifNull": [
            converted("cart_updated_at"), converted("updated_at")]} )]},
    ]}}
    metadata = collection.find(query, {field: 1 for field in META_FIELDS}).batch_size(BATCH_SIZE)
    try:
        async for row in metadata:
            active, abandoned, recovered = select_abandoned_carts_for_period([row], start=start, end=end)
            abandoned_count += abandoned
            recovered_count += recovered
            if not active:
                continue
            active_count += 1
            # Explicit unique tie-breaker prevents duplicate/omitted rows between
            # pages when provider timestamps are equal. No offset-sized heap.
            key = (_cart_activity_at(row) or datetime.min.replace(tzinfo=timezone.utc),
                   json_util.dumps(row["_id"], sort_keys=True))
            if after is not None and key >= after:
                continue
            entry = (key, row["_id"])
            if len(heap) < limit + 1:
                heapq.heappush(heap, entry)
            elif key > heap[0][0]:
                heapq.heapreplace(heap, entry)
    finally:
        await metadata.close()
    selected = sorted(heap, reverse=True)
    has_more = len(selected) > limit
    selected = selected[:limit]
    rows = []
    if selected:
        details = collection.find({**query, "_id": {"$in": [entry[1] for entry in selected]}},
                                  {field: 1 for field in DETAIL_FIELDS}).limit(limit)
        rows = await details.to_list(length=limit)
        by_id = {json_util.dumps(row["_id"], sort_keys=True): row for row in rows}
        rows = [by_id[entry[0][1]] for entry in selected if entry[0][1] in by_id]
        for row in rows:
            row.pop("_id", None)
    next_cursor = None
    if has_more:
        key = selected[-1][0]
        next_cursor = base64.urlsafe_b64encode(json.dumps({
            "period": [start, end], "time": key[0].isoformat(), "id": key[1],
        }).encode()).decode()
    return rows, abandoned_count, recovered_count, {
        "limit": limit, "has_more": has_more, "next_cursor": next_cursor,
        "total_active": active_count,
    }
