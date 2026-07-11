import pytest
import mongomock_motor

from orders_db import _merge_into
from salla_integration.sync import _refresh_plan_b_status_snapshot


def test_salla_direct_overrides_only_status_after_make():
    existing = {
        "order_status": "بإنتظار المراجعة",
        "order_status_slug": "in_review",
        "total_amount": 133.73,
        "payment_method": "mada",
        "customer_name": "Old Customer",
        "last_make_update_at": "2026-07-03T00:00:00+00:00",
        "field_sources": {
            "order_status": "make",
            "order_status_slug": "make",
            "total_amount": "make",
            "payment_method": "make",
            "customer_name": "make",
        },
    }

    incoming = {
        "order_status": "تم التنفيذ",
        "order_status_slug": "completed",
        "total_amount": 999.99,
        "payment_method": "bank_transfer",
        "customer_name": "Changed Customer",
    }

    merged = _merge_into(existing, incoming, "salla_direct")

    assert merged["order_status"] == "تم التنفيذ"
    assert merged["order_status_slug"] == "completed"

    # Protected Make fields must not be overwritten.
    assert merged["total_amount"] == 133.73
    assert merged["payment_method"] == "mada"
    assert merged["customer_name"] == "Old Customer"


@pytest.mark.asyncio
async def test_status_change_creates_fresh_plan_b_trace():
    client = mongomock_motor.AsyncMongoMockClient()
    db = client["test"]

    await db.integration_inbox.insert_one({
        "id": "old-id",
        "trace_id": "old-trace",
        "user_id": "tenant",
        "salla_order_number": "270212453",
        "received_at": "2026-07-03T17:49:25",
        "canonical_payload": {
            "order_number": "270212453",
            "order_status": "in_review",
            "order_status_native": "بإنتظار المراجعة",
            "total_amount": 133.73,
            "payment_method": "mada",
            "items": [{"sku": "A", "quantity": 1}],
        },
    })

    result = await _refresh_plan_b_status_snapshot(
        db,
        "tenant",
        "270212453",
        {
            "order_status": "تم التنفيذ",
            "order_status_slug": "completed",
        },
    )

    assert result["created"] is True

    rows = await db.integration_inbox.find(
        {"salla_order_number": "270212453"}
    ).to_list(length=10)

    assert len(rows) == 2

    newest = next(
        row for row in rows
        if row["trace_id"] != "old-trace"
    )

    assert newest["canonical_payload"]["order_status"] == "completed"
    assert newest["canonical_payload"]["order_status_native"] == "تم التنفيذ"
    assert newest["canonical_payload"]["total_amount"] == 133.73
    assert newest["canonical_payload"]["payment_method"] == "mada"
    assert newest["canonical_payload"]["items"] == [
        {"sku": "A", "quantity": 1}
    ]
    assert newest["source"] == "salla_direct_status_resync"
