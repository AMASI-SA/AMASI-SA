"""Iter-207 — Payment Gateway Metrics: orders_count semantics fix.

The Profit Summary card counts only orders that contribute to gross
revenue (confirmed + refunded). The Payment Gateway card was
previously counting ALL orders (including pending + cancelled),
producing inflated tallies that didn't line up with the Profit
Summary even though both used the same underlying data.

Iter-207 normalises `orders_count` on the gateway metrics endpoint to
match the Profit Summary semantics:

    orders_count       = confirmed + refunded   (contributes to gross)
    pending_orders_count   — tracked separately, NOT in orders_count
    cancelled_orders_count — tracked separately, NOT in orders_count

This test inserts a tiny fixture and asserts the bucket math.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from payment_gateway_metrics import compute_metrics  # noqa: E402


@pytest.mark.asyncio
async def test_gateway_orders_count_excludes_pending_and_cancelled():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = str(uuid.uuid4())
    try:
        # Seed 7 orders for one user, all on the same day:
        #   3 confirmed mada (counted)
        #   1 refunded mada (counted)
        #   1 pending mada  (excluded)
        #   1 cancelled mada (excluded)
        #   1 confirmed _other / unknown method (counted, in _other)
        def order(status, method="mada", total=100.0, num=None):
            return {
                "user_id": uid,
                "order_number": num or str(uuid.uuid4()),
                "order_status": status,
                "payment_method": method,
                "total_amount": total,
                "order_date": "2026-06-14",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        await db.unified_orders.insert_many([
            order("تم التوصيل"),            # confirmed
            order("تم التنفيذ"),            # confirmed
            order("completed"),             # confirmed
            order("مسترجع"),                # refunded
            order("بانتظار المراجعة"),      # pending → excluded
            order("ملغي"),                  # cancelled → excluded
            order("تم التوصيل",             # confirmed in _other
                  method="cryptobit_xyz"),
        ])

        # Smoke check Iter-83 status policy resolution
        res = await compute_metrics(
            db, uid,
            from_date="2026-06-14", to_date="2026-06-14",
        )

        # ─── Asserts ──────────────────────────────────────────
        mada_row = next(r for r in res["rows"] if r["key"] == "mada")
        other_row = next((r for r in res["rows"]
                          if r["key"] == "_other"), None)

        # 3 confirmed + 1 refunded = 4 (excludes pending + cancelled)
        assert mada_row["orders_count"] == 4, mada_row
        assert mada_row["pending_orders_count"] == 1
        assert mada_row["cancelled_orders_count"] == 1
        # refunded counted as orders_count and also in refunded count
        assert mada_row["refunded_orders_count"] == 1

        # _other has 1 confirmed
        assert other_row is not None
        assert other_row["orders_count"] == 1

        # Totals: 4 (mada) + 1 (_other) = 5
        assert res["totals"]["orders_count"] == 5, res["totals"]
        assert res["totals"]["pending_orders_count"] == 1
        assert res["totals"]["cancelled_orders_count"] == 1
        # gross = 4 confirmed/refunded × 100 = 400 (from mada)
        #       + 1 confirmed × 100 = 100 (from _other) = 500
        assert abs(res["totals"]["gross"] - 500.0) < 0.01

        # Sum of all displayed rows orders_count must equal the total
        # (this is what the merchant visually expects).
        sum_rows = sum(r["orders_count"] for r in res["rows"])
        assert sum_rows == res["totals"]["orders_count"], (
            f"row sum {sum_rows} != total "
            f"{res['totals']['orders_count']}"
        )

        # ─── Iter-207 — report_included_statuses alignment ────
        # When the merchant restricts dashboard reports to e.g.
        # "تم التوصيل" only, the gateway metrics must honour the
        # same filter so Profit Summary and Gateway show the same
        # universe.
        await db.settings.update_one(
            {"user_id": uid},
            {"$set": {"user_id": uid,
                      "report_included_statuses": ["تم التوصيل"]}},
            upsert=True,
        )
        res2 = await compute_metrics(
            db, uid,
            from_date="2026-06-14", to_date="2026-06-14",
        )
        # Only "تم التوصيل" orders should be counted now:
        #   1 mada "تم التوصيل" + 1 _other "تم التوصيل" = 2 total
        assert res2["totals"]["orders_count"] == 2, res2["totals"]
        # Everything else (refunded/pending/cancelled/تم التنفيذ/
        # completed) must be filtered out before bucketing.
        assert res2["totals"]["refunded_orders_count"] == 0
        assert res2["totals"]["pending_orders_count"] == 0
        assert res2["totals"]["cancelled_orders_count"] == 0

    finally:
        await db.unified_orders.delete_many({"user_id": uid})
        await db.settings.delete_many({"user_id": uid})
        c.close()
