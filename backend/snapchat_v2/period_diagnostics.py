"""Bounded, read-only diagnosis of report date-window non-additivity.

This is an operator diagnostic, not a reporting or attribution policy change.
Only aggregates leave the process. Each window uses the report candidate selector. Reads are not a database
transaction; concurrent order updates can affect the comparison.
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
from .period_candidates import bounded_period_cursor, period_candidate_query
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
    windows = {
        "whole": (date_from, date_to),
        "left": (date_from, split_on - timedelta(days=1)),
        "right": (split_on, date_to),
    }
    counts = {name: Counter() for name in ("all_orders", "explicit_snapchat_source")}
    candidate_counts = {}
    explanations = Counter()
    # Query each partition independently: computing both from the whole list
    # would always reconcile and would hide a candidate-selection regression.
    for part, (start, end) in windows.items():
        query = period_candidate_query(
            user_id, start, end, hide_inferred=settings.get("hide_inferred_date_orders"),
        )
        rows = await _to_list(
            bounded_period_cursor(db.unified_orders, query, DIAGNOSTIC_PROJECTION, max_rows),
            max_rows,
        )
        if len(rows) > max_rows:
            raise ValueError("diagnostic exceeded row limit; no partial result")
        candidate_counts[part] = len(rows)
        for row in rows:
            local_date, _, _ = _localized_order_period_date(row, zone=zone)
            if not start.isoformat() <= local_date <= end.isoformat():
                continue
            counts["all_orders"][part] += 1
            if canonical_ad_platform(row) == "snapchat":
                counts["explicit_snapchat_source"][part] += 1
            if part == "whole":
                stored_date = str(row.get("order_date") or "")
                target = "left" if local_date < split_on.isoformat() else "right"
                first, last = windows[target]
                if not (first - timedelta(days=1)).isoformat() <= stored_date <= (last + timedelta(days=1)).isoformat():
                    explanations["stored_date_outside_partition_query"] += 1

    result = {
        "timezone": timezone_name,
        "periods": {name: [start.isoformat(), end.isoformat()]
                    for name, (start, end) in windows.items()},
        "candidate_rows": candidate_counts["whole"],
        "candidate_rows_by_period": candidate_counts,
        "comparison_reads": "independent_bounded_windows",
        "complete_within_candidate_windows": True,
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
