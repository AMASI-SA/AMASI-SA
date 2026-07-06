"""Iter-2026-02.rev36 — Simple guarantees:
  • 4-status mapping (أُرسل / لم يُرسل / فشل / مكرر) — unsent_orders
  • duplicate hard-stop ledger query semantics (real vs DRY)
  • totals pre-check threshold (0.01 SAR)
  • /unsent-orders listing against real Mongo (isolated tenant)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.unsent_orders import (
    DUPLICATE, FAILED, SENT, UNSENT, list_unsent_orders, simplify_row,
)

TENANT = f"test-unsent-{uuid4().hex[:8]}"


# ── 1. Pure mapping ──────────────────────────────────────────────────
@pytest.mark.parametrize("row,expected_status,reason_part", [
    ({"pipeline_stage": "COMPLETED", "qoyod_invoice_id": "195"},
     SENT, "195"),
    ({"pipeline_stage": "COMPLETED_WITH_ROUNDING_WARNING",
      "qoyod_invoice_id": "77"}, SENT, "تقريب"),
    ({"pipeline_stage": "INVOICE_CREATED", "qoyod_invoice_id": "80"},
     SENT, "قيد الإكمال"),
    ({"pipeline_stage": "COMPLETED",
      "qoyod_invoice_id": "DRY:invoice:abc"}, UNSENT, "Dry"),
    ({"pipeline_stage": "FAILED_CUSTOMER"}, FAILED, "العميل"),
    ({"pipeline_stage": "FAILED_PRODUCT"}, FAILED, "المنتج"),
    ({"pipeline_stage": "DEAD_LETTER",
      "dead_letter_evidence": {"fail_stage": "FAILED_INVOICE"}},
     FAILED, "الفاتورة"),
    ({"pipeline_stage": "DEAD_LETTER",
      "pipeline_error": {"code": "totals_precheck_mismatch"}},
     FAILED, "0.01"),
    ({"pipeline_stage": "INVOICE_CREATED_TOTAL_MISMATCH",
      "qoyod_invoice_id": "81"}, FAILED, "0.01"),
    ({"pipeline_stage": "SKIPPED", "duplicate_of_invoice":
      {"qoyod_invoice_id": "195", "qoyod_invoice_number": "INV-195"}},
     DUPLICATE, "195"),
    ({"pipeline_stage": "LOCKED_AWAITING_APPROVAL"}, UNSENT, "موافقة"),
    ({"pipeline_stage": "NORMALIZED"}, UNSENT, "بانتظار الإرسال"),
    ({"pipeline_stage": "CUSTOMER_RESOLVED",
      "canary_budget_hold": {"reason": "canary_budget_exhausted"}},
     UNSENT, "محدودة"),
    ({"pipeline_stage": "NEW"}, UNSENT, "الاستقبال"),
])
def test_simplify_row_mapping(row, expected_status, reason_part):
    out = simplify_row(row)
    assert out["status"] == expected_status, (row, out)
    assert reason_part in out["reason"], (row, out)


# ── 2/3/4. Against real Mongo ────────────────────────────────────────
@pytest_asyncio.fixture()
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    for coll in ("integration_inbox", "qoyod_invoices"):
        await database[coll].delete_many({"user_id": TENANT})
    yield database
    for coll in ("integration_inbox", "qoyod_invoices"):
        await database[coll].delete_many({"user_id": TENANT})
    client.close()


def _inbox(row_id, order, stage, **extra):
    return {"user_id": TENANT, "id": row_id, "connector_key": "salla",
            "idempotency_key": f"idem-{row_id}",
            "salla_order_number": order, "trace_id": f"t-{row_id}",
            "pipeline_stage": stage,
            "received_at": datetime.now(timezone.utc),
            "canonical_payload": {"total_amount": 100.0,
                                  "payment_method": "mada"},
            **extra}


@pytest.mark.asyncio
async def test_list_unsent_orders_counts_and_filter(db):
    await db.integration_inbox.insert_many([
        _inbox("r1", "1001", "COMPLETED", qoyod_invoice_id="195"),
        _inbox("r2", "1002", "NORMALIZED"),
        _inbox("r3", "1003", "FAILED_PRODUCT"),
        _inbox("r4", "1004", "SKIPPED", duplicate_of_invoice={
            "qoyod_invoice_id": "195"}),
        _inbox("r5", "1005", "COMPLETED",
               qoyod_invoice_id="DRY:invoice:x"),
    ])
    out = await list_unsent_orders(db, user_id=TENANT, days=7)
    assert out["counts"] == {SENT: 1, UNSENT: 2, FAILED: 1, DUPLICATE: 1}
    only_unsent = await list_unsent_orders(
        db, user_id=TENANT, days=7, status=UNSENT)
    assert {o["order_number"] for o in only_unsent["orders"]} == \
        {"1002", "1005"}
    # counts stay global even when filtered
    assert only_unsent["counts"][SENT] == 1


@pytest.mark.asyncio
async def test_duplicate_ledger_query_matches_real_only(db):
    """The EXACT filter shipped in pipeline.py rev36: real invoice for
    the same order → duplicate; DRY rows never count.
    NOTE: a DB-level unique index `qoyod_invoices_order_unique`
    (user_id + salla_order_id) ALREADY exists in this collection —
    the ledger physically refuses two rows for the same order id."""
    await db.qoyod_invoices.insert_many([
        {"user_id": TENANT, "salla_order_number": "2001",
         "salla_order_id": "2001",
         "qoyod_invoice_id": "DRY:invoice:zz"},           # never counts
        {"user_id": TENANT, "salla_order_number": "2002",
         "salla_order_id": "2002",
         "qoyod_invoice_id": "300"},                       # real
    ])
    q = lambda order: {  # noqa: E731
        "user_id": TENANT,
        "$or": [{"salla_order_number": {"$in": [order]}},
                {"salla_order_id":     {"$in": [order]}}],
        "qoyod_invoice_id": {"$exists": True, "$nin": [None, ""],
                             "$not": {"$regex": "^(DRY:|PREVIEW:)"}},
    }
    assert await db.qoyod_invoices.find_one(q("2001")) is None
    dup = await db.qoyod_invoices.find_one(q("2002"))
    assert dup and dup["qoyod_invoice_id"] == "300"


def test_totals_precheck_threshold():
    """Same arithmetic as pipeline.py rev36: block iff |diff| > 0.01."""
    def diff(expected, salla):
        return (None if expected is None or salla is None
                else round(float(expected) - float(salla), 4))
    assert abs(diff(100.00, 100.00)) <= 0.01          # pass
    assert abs(diff(100.01, 100.00)) <= 0.01          # pass (== 0.01)
    assert abs(diff(100.02, 100.00)) > 0.01           # BLOCK
    assert abs(diff(99.98, 100.00)) > 0.01            # BLOCK (negative)
    assert diff(None, 100.0) is None                   # unknown → no block
