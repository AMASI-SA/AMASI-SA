"""Conservative date candidates; final timestamp precedence stays in Python.

Never require the normalized order_date to agree with a provider timestamp.
A single OR query keeps one document per order and remains tenant scoped.
Noncanonical ISO strings are admitted for the existing Python parser rather
than silently losing formats it accepts (basic/week dates and whitespace).
The caller must retain its row cap and reject overflow, never return a prefix.
"""
from datetime import date, datetime, time, timedelta, timezone


TIMESTAMP_PATHS = (
    "created_at", "order_created_at", "created_at_utc", "source_created_at",
    "raw_by_source.salla_direct.created_at", "raw_by_source.salla_direct.date",
)


def bounded_period_cursor(collection, query, projection, limit):
    cursor = collection.find(query, projection)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "max_time_ms"):
        cursor = cursor.max_time_ms(15_000)
    return cursor


def period_candidate_query(user_id: str, start: date, end: date, *, hide_inferred=False):
    if start > end:
        raise ValueError("invalid period")
    # Two calendar days cover both the source offset and account offset,
    # including opposite sides of the date line. The upper day is exclusive.
    lower = start - timedelta(days=2)
    upper = end + timedelta(days=3)
    string_bounds = {"$gte": lower.isoformat(), "$lt": upper.isoformat()}
    date_bounds = {
        "$gte": datetime.combine(lower, time.min, timezone.utc),
        "$lt": datetime.combine(upper, time.min, timezone.utc),
    }
    paths = ["order_date"]
    for path in TIMESTAMP_PATHS:
        paths.extend((path, f"{path}.date", f"{path}.value"))
    candidates = []
    for path in paths:
        candidates.extend((
            {path: {"$type": "string", **string_bounds}},
            {path: {"$type": "date", **date_bounds}},
            {path: {"$type": "string", "$ne": "", "$not": {"$regex": r"^\d{4}-\d{2}-\d{2}"}}},
        ))
    query = {"user_id": str(user_id), "$or": candidates}
    if hide_inferred:
        query["order_date_inferred"] = {"$ne": True}
    return query
