"""Pre-Day 3 — Qoyod Compliance Watch tests.

Pure-function classification + live DB orphan listing.
"""
from __future__ import annotations

import os
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.models import (
    ELIGIBILITY_STATUSES, ELIGIBILITY_REASONS,
)
from integrations.qoyod.compliance import (
    classify_eligibility, list_orphan_orders, compliance_summary,
    reconciliation_check, COMPLETED_TRIGGER_STATUSES,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ─────────────────────────────────────────────────────────────────────
# A) classify_eligibility — pure function
# ─────────────────────────────────────────────────────────────────────
def test_not_completed_is_not_eligible():
    order = {"order_status": "تم الشحن"}
    s, r = classify_eligibility(order, None)
    assert s == "not_eligible"
    assert r == "order_status_not_completed"


def test_completed_no_qoyod_row_is_eligible_pending():
    order = {"order_status": "تم التنفيذ"}
    s, r = classify_eligibility(order, None)
    assert s == "eligible_pending"
    assert r == "order_completed_ready_to_send"


def test_completed_already_sent():
    order = {"order_status": "تم التنفيذ"}
    qrow  = {"status": "sent"}
    s, r = classify_eligibility(order, qrow)
    assert s == "sent_to_qoyod"
    assert r == "already_sent"


def test_completed_invoice_sent_receipt_failed():
    order = {"order_status": "تم التنفيذ"}
    qrow  = {"status": "invoice_sent_receipt_failed",
             "last_error": {"code": "qoyod_validation_error"}}
    s, r = classify_eligibility(order, qrow)
    assert s == "invoice_sent_receipt_failed"
    assert r == "qoyod_api_error"


def test_completed_failed_customer():
    order = {"order_status": "تم التنفيذ"}
    qrow  = {"status": "failed",
             "last_error": {"code": "missing_customer_phone"}}
    s, r = classify_eligibility(order, qrow)
    assert s == "failed_before_qoyod"
    assert r == "missing_customer_data"


def test_completed_failed_product():
    order = {"order_status": "تم التنفيذ"}
    qrow  = {"status": "failed",
             "last_error": {"code": "missing_product_mapping"}}
    s, r = classify_eligibility(order, qrow)
    assert s == "failed_before_qoyod"
    assert r == "missing_product_mapping"


def test_english_completed_alias_is_eligible():
    # The user-configured trigger keeps "completed" (English) as well.
    order = {"order_status": "completed"}
    s, _ = classify_eligibility(order, None)
    assert s == "eligible_pending"


def test_all_returned_codes_are_from_closed_vocabulary():
    # Sanity: every (status, reason) we can possibly return must be
    # listed in the public vocab tuples — otherwise the UI breaks.
    scenarios = [
        ({"order_status": "غير ذلك"},                  None),
        ({"order_status": "تم التنفيذ"},               None),
        ({"order_status": "تم التنفيذ"},               {"status": "sent"}),
        ({"order_status": "تم التنفيذ"},               {"status": "invoice_sent_receipt_failed"}),
        ({"order_status": "تم التنفيذ"},               {"status": "failed", "last_error": {"code": "missing_customer_phone"}}),
        ({"order_status": "تم التنفيذ"},               {"status": "failed", "last_error": {"code": "missing_product_mapping"}}),
        ({"order_status": "تم التنفيذ"},               {"status": "failed", "last_error": {"code": "payment_method_unknown"}}),
        ({"order_status": "تم التنفيذ"},               {"status": "failed", "last_error": {"code": "qoyod_5xx"}}),
        ({"order_status": "تم التنفيذ"},               {"status": "skipped"}),
    ]
    for order, qrow in scenarios:
        s, r = classify_eligibility(order, qrow)
        assert s in ELIGIBILITY_STATUSES
        assert r in ELIGIBILITY_REASONS


# ─────────────────────────────────────────────────────────────────────
# B) list_orphan_orders + compliance_summary — live DB
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_orphan_listing_and_summary(db):
    user_id = f"test_compliance_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    try:
        # Seed 4 unified_orders:
        #   1) completed + no qoyod row     → eligible_pending
        #   2) completed + qoyod sent       → sent_to_qoyod (NOT in orphans)
        #   3) completed + qoyod failed     → failed_before_qoyod
        #   4) shipping (not completed)     → not_eligible (NOT in orphans)
        await db.unified_orders.insert_many([
            {"user_id": user_id, "salla_order_id": "O-1",
             "order_number": f"{user_id}-N1",
             "order_status": "تم التنفيذ",
             "order_date": now - timedelta(days=3),
             "customer_name": "أحمد", "customer_phone": "0500000001",
             "total_amount": 100.0},
            {"user_id": user_id, "salla_order_id": "O-2",
             "order_number": f"{user_id}-N2",
             "order_status": "تم التنفيذ",
             "order_date": now - timedelta(days=2),
             "customer_name": "محمد", "customer_phone": "0500000002",
             "total_amount": 200.0},
            {"user_id": user_id, "salla_order_id": "O-3",
             "order_number": f"{user_id}-N3",
             "order_status": "تم التنفيذ",
             "order_date": now - timedelta(days=1),
             "customer_name": "فاطمة", "customer_phone": "0500000003",
             "total_amount": 300.0},
            {"user_id": user_id, "salla_order_id": "O-4",
             "order_number": f"{user_id}-N4",
             "order_status": "تم الشحن",
             "order_date": now,
             "customer_name": "خالد", "customer_phone": "0500000004",
             "total_amount": 400.0},
        ])

        # Seed qoyod_invoices for O-2 (sent) and O-3 (failed_customer).
        await db.qoyod_invoices.insert_many([
            {"user_id": user_id, "salla_order_id": "O-2",
             "status": "sent", "trace_id": "trace-2",
             "updated_at": now},
            {"user_id": user_id, "salla_order_id": "O-3",
             "status": "failed",
             "last_error": {"code": "missing_customer_phone"},
             "trace_id": "trace-3",
             "updated_at": now},
        ])

        items = await list_orphan_orders(db, user_id)
        ids = {i["salla_order_id"] for i in items}
        # Only O-1 and O-3 should be listed — O-2 is sent, O-4 not eligible.
        assert ids == {"O-1", "O-3"}

        by_id = {i["salla_order_id"]: i for i in items}
        assert by_id["O-1"]["eligibility_status"] == "eligible_pending"
        assert by_id["O-3"]["eligibility_status"] == "failed_before_qoyod"
        assert by_id["O-3"]["eligibility_reason"] == "missing_customer_data"

        summary = await compliance_summary(db, user_id)
        assert summary["schema_version"] == 1
        assert summary["completed_orders_total"] == 3
        assert summary["sent_to_qoyod"] == 1
        assert summary["failed_before_qoyod"] == 1
        assert summary["eligible_pending"] == 1
        assert summary["invoice_sent_receipt_failed"] == 0
        # Vocabularies must be echoed for the UI.
        assert set(summary["eligibility_statuses"]) == set(ELIGIBILITY_STATUSES)
        assert set(summary["eligibility_reasons"])  == set(ELIGIBILITY_REASONS)
    finally:
        await db.unified_orders.delete_many({"user_id": user_id})
        await db.qoyod_invoices.delete_many({"user_id": user_id})


def test_completed_trigger_includes_arabic_and_english():
    assert "تم التنفيذ" in COMPLETED_TRIGGER_STATUSES
    assert "completed"   in COMPLETED_TRIGGER_STATUSES


# ─────────────────────────────────────────────────────────────────────
# C) reconciliation_check — three-number diff card
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reconciliation_check_counts_and_diff(db):
    user_id = f"test_recon_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    try:
        await db.unified_orders.insert_many([
            {"user_id": user_id, "salla_order_id": f"R-{i}",
             "order_number": f"{user_id}-N{i}",
             "order_status": "تم التنفيذ",
             "order_date": now - timedelta(days=i + 1)}
            for i in range(5)
        ])
        # 2 of the 5 are already in Qoyod as "sent".
        await db.qoyod_invoices.insert_many([
            {"user_id": user_id, "salla_order_id": "R-0",
             "status": "sent", "trace_id": "t0", "updated_at": now},
            {"user_id": user_id, "salla_order_id": "R-1",
             "status": "sent", "trace_id": "t1", "updated_at": now},
        ])
        rec = await reconciliation_check(db, user_id)
        assert rec["eligible_orders_count"] == 5
        assert rec["qoyod_invoices_count"]  == 2
        assert rec["difference"]             == 3
        assert rec["has_diff"] is True
        assert "filter=unsent" in rec["drilldown_url"]
        assert rec["oldest_unsent_at"] is not None
        assert rec["schema_version"] == 1
    finally:
        await db.unified_orders.delete_many({"user_id": user_id})
        await db.qoyod_invoices.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_reconciliation_check_no_diff_when_all_sent(db):
    user_id = f"test_recon2_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    try:
        await db.unified_orders.insert_one({
            "user_id": user_id, "salla_order_id": "R-X",
            "order_number": f"{user_id}-N1",
            "order_status": "تم التنفيذ", "order_date": now,
        })
        await db.qoyod_invoices.insert_one({
            "user_id": user_id, "salla_order_id": "R-X",
            "status": "sent", "trace_id": "tx", "updated_at": now,
        })
        rec = await reconciliation_check(db, user_id)
        assert rec["eligible_orders_count"] == 1
        assert rec["qoyod_invoices_count"]  == 1
        assert rec["difference"]            == 0
        assert rec["has_diff"] is False
        assert rec["oldest_unsent_at"]      is None
    finally:
        await db.unified_orders.delete_many({"user_id": user_id})
        await db.qoyod_invoices.delete_many({"user_id": user_id})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
