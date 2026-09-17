"""Bounded, read-only diagnosis of report date-window non-additivity.

This is an operator diagnostic, not a reporting or attribution policy change.
Only aggregates leave the process. It compares both partitions against one
snapshot of the whole window; it cannot detect orders absent from that window.
Explicit Snapchat source is deliberately separate from campaign attribution.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from salla_marketing_attribution import canonical_ad_platform
from .salla_outcomes import (
    ORDER_PROJECTION, _load_report_settings, _localized_order_period_date, _to_list,
)

DIAGNOSTIC_PROJECTION = {
    field: included for field, included in ORDER_PROJECTION.items()
    if field not in {
        "order_number", "reference_id", "order_id", "id", "products",
        "total_amount", "total", "campaign_id", "source_campaign_id",
        "utm_campaign_id", "ad_campaign_id", "utm_campaign", "campaign_name",
        "source_campaign_name", "ad_campaign_name",
    }
}


async def audit_period_partition(
    db, user_id: str, *, date_from: date, date_to: date, split_on: date,
    timezone_name: str, max_rows: int = 10_000,
) -> dict:
    if not date_from < split_on <= date_to:
        raise ValueError("split must create two nonempty periods")
    if not 1 <= max_rows <= 100_000:
        raise ValueError("row limit must be between 1 and 100000")
    zone = ZoneInfo(timezone_name)
    settings = await _load_report_settings(db, user_id)
    query = {
        "user_id": str(user_id),
        "order_date": {
            "$gte": (date_from - timedelta(days=1)).isoformat(),
            "$lte": (date_to + timedelta(days=1)).isoformat(),
        },
    }
    if settings.get("hide_inferred_date_orders"):
        query["order_date_inferred"] = {"$ne": True}
    rows = await _to_list(db.unified_orders.find(query, DIAGNOSTIC_PROJECTION), max_rows)
    if len(rows) > max_rows:
        raise ValueError("diagnostic exceeded row limit; no partial result")

    windows = {
        "whole": (date_from, date_to),
        "left": (date_from, split_on - timedelta(days=1)),
        "right": (split_on, date_to),
    }
    counts = {name: Counter() for name in ("all_orders", "explicit_snapchat_source")}
    explanations = Counter()
    for row in rows:
        local_date, _, _ = _localized_order_period_date(row, zone=zone)
        # Match the report's Mongo string bounds exactly, including a stored
        # timestamp string, rather than silently normalizing the candidate date.
        stored_date = row.get("order_date")
        if not isinstance(stored_date, str):
            raise ValueError("unsupported stored date type; no partial result")
        included = {}
        for name, (start, end) in windows.items():
            included[name] = bool(
                start.isoformat() <= local_date <= end.isoformat()
                and (start - timedelta(days=1)).isoformat() <= stored_date
                <= (end + timedelta(days=1)).isoformat()
            )
            if included[name]:
                counts["all_orders"][name] += 1
                if canonical_ad_platform(row) == "snapchat":
                    counts["explicit_snapchat_source"][name] += 1
        if included["whole"] and not (included["left"] or included["right"]):
            explanations["stored_date_outside_partition_query"] += 1

    result = {
        "timezone": timezone_name,
        "periods": {name: [start.isoformat(), end.isoformat()]
                    for name, (start, end) in windows.items()},
        "candidate_rows": len(rows),
        "complete_within_whole_window_candidates": True,
        "explanations": dict(explanations),
    }
    for name, values in counts.items():
        result[name] = {part: values[part] for part in windows}
        result[name]["gap"] = values["whole"] - values["left"] - values["right"]
    return result


async def _main(args):
    from motor.motor_asyncio import AsyncIOMotorClient

    # Use the explicitly selected runtime's existing connection. No dotenv or
    # fallback database: a Preview run is not evidence about Production.
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    try:
        result = await audit_period_partition(
            client[os.environ["DB_NAME"]], args.user_id,
            date_from=args.date_from, date_to=args.date_to, split_on=args.split_on,
            timezone_name=args.timezone, max_rows=args.max_rows,
        )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--date-to", type=date.fromisoformat, required=True)
    parser.add_argument("--split-on", type=date.fromisoformat, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--max-rows", type=int, default=10_000)
    args = parser.parse_args()
    try:
        asyncio.run(_main(args))
    except Exception as exc:
        # Database exceptions can contain connection details; never print them.
        raise SystemExit(f"Diagnostic failed ({type(exc).__name__}); no result emitted") from None
