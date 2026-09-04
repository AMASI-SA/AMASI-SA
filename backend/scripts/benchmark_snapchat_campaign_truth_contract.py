"""Bounded SNAP-REPORT-1 matching/contract benchmark (read-only, no network)."""
from __future__ import annotations

import argparse
import asyncio
import json
from time import perf_counter
import tracemalloc

import auth

from integrations_control_center import snapchat_campaign_created_order_semantics as outcomes
from integrations_control_center.snapchat_campaign_truth_contract import (
    apply_campaign_truth_contract,
)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return self.rows[:length]


class Orders:
    def __init__(self, rows):
        self.rows = rows
        self.query_count = 0

    def find(self, _query, _projection=None):
        self.query_count += 1
        return Cursor(self.rows)


class DB:
    def __init__(self, rows):
        self.unified_orders = Orders(rows)


async def run(campaign_count: int, order_count: int, page_size: int) -> dict:
    identities = [{
        "account_id": "account-benchmark",
        "campaign_id": f"campaign-{index}",
        "campaign_name": f"Campaign {index}",
    } for index in range(campaign_count)]
    rows = [{
        "user_id": "owner-benchmark",
        "id": f"order-{index}",
        "order_date": "2026-09-03",
        "created_at": "2026-09-03T12:00:00+03:00",
        "updated_at": "2026-09-03T12:05:00+03:00",
        "utm_campaign_id": f"campaign-{index % campaign_count}",
        "order_status": "completed",
        "total_amount": 100.0,
    } for index in range(order_count)]
    db = DB(rows)

    async def settings(_db, _user_id):
        return {
            "report_included_statuses": ["completed"],
            "hide_inferred_date_orders": False,
        }

    previous = auth.ensure_user_settings
    auth.ensure_user_settings = settings
    tracemalloc.start()
    started = perf_counter()
    try:
        by_campaign, _, coverage, _ = (
            await outcomes.build_created_and_financial_outcomes(
                db,
                "owner-benchmark",
                date_from="2026-09-03",
                date_to="2026-09-03",
                timezone_name="America/Los_Angeles",
                identities=identities,
            )
        )
        campaign_rows = []
        for identity in identities[:page_size]:
            key = (identity["account_id"], identity["campaign_id"])
            value = by_campaign.get(key, {})
            campaign_rows.append({
                **identity,
                "salla_results": value,
                "snapchat_purchases": 1,
                "snapchat_purchase_value_sar": 100.0,
                "snapchat_spend_sar": 50.0,
            })
        apply_campaign_truth_contract({
            "request_id": "benchmark",
            "business_timezone": "Asia/Riyadh",
            "account_timezone": "America/Los_Angeles",
            "salla_status": "complete",
            "snapchat_status": "complete",
            "matching_status": "complete",
            "totals": {
                "salla_total_orders": coverage["salla_total_orders"],
                "salla_matched_orders": coverage["salla_matched_orders"],
                "salla_sales_sar": order_count * 100.0,
                "snapchat_purchases": order_count,
                "snapchat_purchase_value_sar": order_count * 100.0,
                "snapchat_spend_sar": order_count * 50.0,
            },
            "campaigns": campaign_rows,
            "daily": [],
            "accounts": [],
            "source": {
                "platform_total_snapshot_ready": True,
                "platform_source_status": "complete",
                "salla_attribution": coverage,
            },
        })
    finally:
        elapsed_ms = (perf_counter() - started) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        auth.ensure_user_settings = previous

    return {
        "query_count": db.unified_orders.query_count,
        "response_time_ms": round(elapsed_ms, 3),
        "peak_memory_mb": round(peak / (1024 * 1024), 3),
        "campaign_count": campaign_count,
        "order_count": order_count,
        "page_size": page_size,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_status": "disabled_for_source_coherence",
        "matched_orders": coverage["salla_matched_orders"],
        "duplicate_orders_excluded": coverage["duplicate_orders_excluded"],
        "bounded_order_limit": 100_000,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaigns", type=int, default=5_000)
    parser.add_argument("--orders", type=int, default=10_000)
    parser.add_argument("--page-size", type=int, default=25)
    args = parser.parse_args()
    if not (1 <= args.campaigns <= 50_000):
        raise SystemExit("campaigns must be within 1..50000")
    if not (0 <= args.orders <= 100_000):
        raise SystemExit("orders must be within 0..100000")
    if not (1 <= args.page_size <= 100):
        raise SystemExit("page-size must be within 1..100")
    print(json.dumps(asyncio.run(run(
        args.campaigns,
        args.orders,
        args.page_size,
    )), sort_keys=True))


if __name__ == "__main__":
    main()
