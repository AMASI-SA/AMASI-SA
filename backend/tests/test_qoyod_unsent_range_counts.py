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
    _provider_failure_evidence,
)


TENANT = "main"
NOW = datetime(2026, 8, 13, 14, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["quarantine", "lock"])
@pytest.mark.parametrize("endpoint,operation", [
    ("GET /customers", "البحث عن العميل"),
    ("GET /products", "البحث عن المنتج"),
    ("POST /invoices", "إنشاء الفاتورة"),
    ("POST /invoice_payments", "تسجيل السداد"),
])
async def test_provider_failure_is_visible_without_changing_send_state(
    db, source, endpoint, operation,
):
    reference = "SYNTHETIC-FAILURE-001"
    await _insert_quarantined(
        db, order_number=reference, status_slug="completed", status_native="تم التنفيذ",
    )
    detail = {
        "endpoint": endpoint, "status_code": 404,
        "response_excerpt": "SYNTHETIC-PRIVATE-RESPONSE",
        "request_body": {"email": "synthetic-private@example.invalid"},
    }
    if source == "quarantine":
        await db.qoyod_manual_auto_quarantines.update_one(
            {"order_number": reference},
            {"$set": {"code": "qoyod_http_error", "detail": detail,
                      "message": "استجابة غير ناجحة من قيود (404)",
                      "last_seen_at": NOW}},
        )
    else:
        await db.qoyod_manual_auto_quarantines.delete_many({})
        await db.qoyod_manual_send_locks.insert_one({
            "user_id": TENANT, "order_number": reference, "status": "failed",
            "finished_at": NOW,
            "last_error": {"code": "qoyod_http_error", "detail": detail,
                           "message": "استجابة غير ناجحة من قيود (404)"},
        })
    before = await db.qoyod_manual_auto_quarantines.find({}).to_list(None)
    locks_before = await db.qoyod_manual_send_locks.find({}).to_list(None)
    result = await list_unsent_orders(db, user_id=TENANT, days=30, now=NOW)
    row = next(row for row in result["orders"] if row["order_number"] == reference)
    assert row["status"] == UNSENT
    assert row["retry_allowed"] is True
    evidence = row["provider_failure"]
    assert evidence["endpoint"] == endpoint
    assert evidence["operation"] == operation
    assert evidence["status_code"] == 404
    assert evidence["observed_at"].startswith("2026-08-13T14:30:00")
    assert "SYNTHETIC-PRIVATE-RESPONSE" not in str(result)
    assert "synthetic-private@example.invalid" not in str(result)
    assert await db.qoyod_manual_auto_quarantines.find({}).to_list(None) == before
    assert await db.qoyod_manual_send_locks.find({}).to_list(None) == locks_before
    assert await db.qoyod_invoices.count_documents({}) == 0


@pytest.mark.asyncio
async def test_live_queue_wrapper_retains_failure_and_hides_it_after_reconciliation(db):
    from qoyod_auto_unified.queue_api import _execute_list

    reference = "SYNTHETIC-FAILURE-002"
    await _insert_quarantined(
        db, order_number=reference, status_slug="completed", status_native="تم التنفيذ",
    )
    await db.qoyod_manual_auto_quarantines.update_one(
        {"order_number": reference},
        {"$set": {"detail": {"endpoint": "GET /customers", "status_code": 404}}},
    )
    # Unified orders can have no legacy inbox projection yet.
    await db.integration_inbox.delete_many({})
    result = await _execute_list(list_unsent_orders, db, user_id=TENANT, days=30, now=NOW)
    assert result["orders"][0]["provider_failure"]["endpoint"] == "GET /customers"
    await db.qoyod_invoices.insert_one({
        "user_id": TENANT, "reference": reference,
        "qoyod_official_reference": reference, "reference_provenance": "qoyod.reference",
        "qoyod_invoice_id": "SYNTHETIC-INVOICE-002",
    })
    result = await _execute_list(list_unsent_orders, db, user_id=TENANT, days=30, now=NOW)
    assert result["orders"][0]["status"] == SENT
    assert result["orders"][0]["provider_failure"] is None
    assert result["orders"][0]["retry_allowed"] is False


@pytest.mark.parametrize("detail", [
    None, [], {"endpoint": "GET /customers?email=private", "status_code": 404},
    {"endpoint": "GET /unknown/private", "status_code": 404},
    {"endpoint": "GET /customers", "status_code": "private"},
    {"endpoint": "GET /customers", "status_code": True},
])
def test_provider_failure_does_not_guess_or_expose_unknown_fields(detail):
    assert _provider_failure_evidence(detail) is None


def test_provider_invoice_id_and_untrusted_timestamp_are_not_exposed():
    result = _provider_failure_evidence(
        {"endpoint": "GET /invoices/123456", "status_code": 404}, "private text",
    )
    assert result["endpoint"] == "GET /invoices/{id}"
    assert result["observed_at"] is None



@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_qoyod_unsent_range_counts"]


async def _insert_sent(
    db, *, order_number: str, order_date: str, received_at: datetime,
) -> None:
    await db.unified_orders.insert_one({
        "user_id": TENANT,
        "order_id": f"salla-{order_number}",
        "order_number": order_number,
        "order_date": order_date,
        "order_status": "completed",
        "order_status_slug": "completed",
        "order_status_native": "تم التنفيذ",
        "payment_method": "mada",
        "payment_status": "paid",
        "payment_collection_status": "paid",
        "paid_amount": 100.0,
        "remaining_amount": 0.0,
        "has_remaining_amount": False,
        "total_amount": 100.0,
        "currency": "SAR",
    })
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
        "qoyod_official_reference": order_number,
        "reference_provenance": "qoyod.reference",
        "qoyod_invoice_id": f"invoice-{order_number}",
    })


async def _insert_quarantined(
    db, *, order_number: str, status_slug: str, status_native: str,
) -> None:
    await db.unified_orders.insert_one({
        "user_id": TENANT,
        "order_id": f"salla-{order_number}",
        "order_number": order_number,
        "order_date": "2026-08-08",
        "order_status": status_slug,
        "order_status_slug": status_slug,
        "order_status_native": status_native,
        "payment_method": "cod",
        "payment_status": "paid",
        "payment_collection_status": "paid",
        "paid_amount": 315.88,
        "remaining_amount": 0.0,
        "has_remaining_amount": False,
        "total_amount": 315.88,
        "currency": "SAR",
    })
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
        db,
        user_id=TENANT,
        days=90,
        from_date="2026-07-01",
        limit=1000,
        now=NOW,
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
        from_date="2026-07-01",
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
