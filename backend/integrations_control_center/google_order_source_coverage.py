"""Bounded diagnostic subset of order source-date consistency, not sales.

Candidate discovery cannot prove source completeness: first-writer drift,
missing orders, sync gaps and concurrent writes can all omit candidates.
No order, product, customer, financial or provider data is returned.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .google_order_source_binding import bound_salla_order_date

_PROJECTION = {"_id": 0, "user_id": 1, "order_id": 1, "order_number": 1, "order_date": 1,
               "raw_by_source.salla_direct.id": 1, "raw_by_source.salla_direct.reference_id": 1,
               "raw_by_source.salla_direct.date": 1, "raw_by_source.salla_direct.created_at": 1}


async def read_source_date_coverage(db, *, owner_id, date_from, date_to, as_of, limit=5000):
    if (not isinstance(owner_id, str) or not owner_id or owner_id.strip() != owner_id
            or not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None
            or type(limit) is not int or not 1 <= limit <= 5000):
        raise ValueError("invalid diagnostic boundary")
    try:
        if not isinstance(date_from, str) or not isinstance(date_to, str):
            raise ValueError()
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        if start.isoformat() != date_from or end.isoformat() != date_to:
            raise ValueError()
        if start > end or (end - start).days > 366 or end >= as_of.astimezone(ZoneInfo("Asia/Riyadh")).date():
            raise ValueError()
        lower, upper = (start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat()
    except (ValueError, OverflowError):
        raise ValueError("invalid closed date period") from None
    query = {"user_id": owner_id, "order_date": {"$gte": lower, "$lte": upper}}
    cursor = db["unified_orders"].find(query, _PROJECTION).limit(limit + 1).max_time_ms(3000)
    rows = await cursor.to_list(length=limit + 1)
    if any(not isinstance(row, dict) or row.get("user_id") != owner_id
           or not isinstance(row.get("order_date"), str) or not lower <= row["order_date"] <= upper for row in rows):
        raise ValueError("query scope unverified")
    truncated = len(rows) > limit
    counts = {"observed_bound_inside": 0, "observed_bound_outside": 0, "observed_unverified": 0}
    if not truncated:
        for row in rows:
            evidence = bound_salla_order_date(row, owner_id=owner_id)
            if evidence["status"] != "observed_bound_source_instant":
                counts["observed_unverified"] += 1
            elif date_from <= evidence["business_date_riyadh"] <= date_to:
                counts["observed_bound_inside"] += 1
            else:
                counts["observed_bound_outside"] += 1
    return {"period": {"from": date_from, "to": date_to, "timezone": "Asia/Riyadh"},
            **{key: None if truncated else value for key, value in counts.items()},
            "truncated": truncated, "coverage_complete": False, "snapshot_consistent": False,
            "sales": None, "profitability": None, "source_writes": 0, "execution_authorized": False,
            "limitations": ["candidate_query_does_not_prove_coverage", "source_snapshot_mutable",
                            "product_mapping_unverified", "refunds_and_costs_unverified", "index_unverified"]}
