"""Regression coverage for Qoyod 7/30/90-day exception counts.

The cards must use Salla's order creation date and must be counted before the
public table-row limit.  Raw webhook/status traces are not the unit being
counted.
"""
from datetime import datetime, timezone

import mongomock_motor
import pytest

from integrations.qoyod.unsent_orders import (
    FAILED,
    SENT,
    UNSENT,
    list_unsent_orders,
)


TENANT = "main"
NOW = datetime(2026, 8, 13, 14, 30, tzinfo=timezone.utc)


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_qoyod_unsent_range_counts"]


async def _insert_sent(
    db, *, order_number: str, order_date: str, received_at: datetime,
) -> None:
    await db.integration_inbox.insert_one({
        "id": f"row-{order_number}",
        "trace_id": f"trace-{order_number}",
        "user_id": TENANT,
        "salla_order_number": order_number,
        "received_at": received_at,
        "pipeline_stage": "NORMALIZED",
        "canonical_payload": {
            "order_number": order_number,
            "order_date": order_date,
            "created_at": order_date,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount": 100.0,
            "payment_method": "mada",
        },
        "raw_payload": {
            "data": {
                "created_at": order_date,
            },
        },
    })
    await db.qoyod_invoices.insert_one({
        "user_id": TENANT,
        "reference": order_number,
    })


async def _insert_quarantined(
    db, *, order_number: str, status_slug: str, status_native: str,
) -> None:
    await db.integration_inbox.insert_one({
        "id": f"row-{order_number}",
        "trace_id": f"trace-{order_number}",
        "user_id": TENANT,
        "salla_order_number": order_number,
        "received_at": NOW,
        "pipeline_stage": "NORMALIZED",
        "canonical_payload": {
            "order_number": order_number,
            "order_date": "2026-08-08",
            "created_at": "2026-08-08",
            "order_status": status_slug,
            "order_status_native": status_native,
            "total_amount": 315.88,
            "payment_method": "cod",
        },
        "raw_payload": {"data": {"created_at": "2026-08-08"}},
    })
    await db.qoyod_manual_auto_quarantines.insert_one({
        "_id": f"{TENANT}:{order_number}",
        "user_id": TENANT,
        "order_number": order_number,
        "status": "open",
        "code": "qoyod_preflight_total_mismatch",
        "message": "فرق المبلغ 7.0 ريال أكبر من 0.01 — أُوقف الإرسال",
    })


@pytest.mark.asyncio
async def test_range_uses_salla_order_date_not_recent_backfill_time(db):
    # All three rows arrived in Mezan today, as happens during a backfill.
    # Their Salla creation dates belong to different requested periods.
    await _insert_sent(
        db,
        order_number="recent-7",
        order_date="2026-08-10",
        received_at=NOW,
    )
    await _insert_sent(
        db,
        order_number="older-30",
        order_date="2026-07-20",
        received_at=NOW,
    )
    await _insert_sent(
        db,
        order_number="older-90",
        order_date="2026-07-03",
        received_at=NOW,
    )

    seven = await list_unsent_orders(
        db, user_id=TENANT, days=7, limit=1000, now=NOW,
    )
    thirty = await list_unsent_orders(
        db, user_id=TENANT, days=30, limit=1000, now=NOW,
    )
    ninety = await list_unsent_orders(
        db, user_id=TENANT, days=90, limit=1000, now=NOW,
    )

    assert seven["requested_order_start_date"] == "2026-08-06"
    assert thirty["requested_order_start_date"] == "2026-07-14"
    # The 90-day selector remains bounded by the fixed integration start.
    assert ninety["requested_order_start_date"] == "2026-07-01"

    assert seven["counts"][SENT] == 1
    assert thirty["counts"][SENT] == 2
    assert ninety["counts"][SENT] == 3


@pytest.mark.asyncio
async def test_cards_count_full_period_before_table_row_limit(db):
    for index, order_date in enumerate(
        ("2026-07-03", "2026-07-20", "2026-08-10"), start=1,
    ):
        await _insert_sent(
            db,
            order_number=f"count-before-limit-{index}",
            order_date=order_date,
            received_at=NOW,
        )

    result = await list_unsent_orders(
        db,
        user_id=TENANT,
        days=90,
        limit=2,
        now=NOW,
    )

    assert result["counts"][SENT] == 3
    assert result["total"] == 3
    assert result["matched_order_count"] == 3
    assert result["returned_order_count"] == 2
    assert result["truncated"] is True
    assert len(result["orders"]) == 2


@pytest.mark.asyncio
async def test_noneligible_current_salla_status_hides_old_failure(db):
    """Order 273187928 must not stay failed/retryable after Salla marks it
    ``تم الشحن`` (shipped), which is outside the closed Qoyod status gate.
    """
    await _insert_quarantined(
        db,
        order_number="273187928",
        status_slug="shipped",
        status_native="تم الشحن",
    )

    result = await list_unsent_orders(
        db, user_id=TENANT, days=90, limit=1000, now=NOW,
    )

    assert result["counts"][FAILED] == 0
    assert result["excluded_not_eligible"] == 1
    assert result["orders"] == []
    assert "تم الشحن" not in result["salla_status_counts"]


@pytest.mark.asyncio
async def test_eligible_current_salla_status_keeps_error_in_unsent_list(db):
    await _insert_quarantined(
        db,
        order_number="eligible-failure",
        status_slug="completed",
        status_native="تم التنفيذ",
    )

    result = await list_unsent_orders(
        db, user_id=TENANT, days=90, limit=1000, now=NOW,
    )

    assert result["counts"][UNSENT] == 1
    assert result["counts"][FAILED] == 0
    assert result["excluded_not_eligible"] == 0
    assert result["orders"][0]["status"] == UNSENT
    assert result["orders"][0]["retry_allowed"] is True
