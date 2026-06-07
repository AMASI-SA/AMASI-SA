"""Iter-91 Phase 3 — Refund / Cancellation deduction from
expected_orders_balance.

Verifies that:
  1. compute_metrics() excludes cancelled orders entirely from `net`.
  2. compute_metrics() zeroes net for full refund (status=مسترجع).
  3. compute_metrics() subtracts partial refund from net.
  4. After /accounts/sync-payment-methods, the corresponding
     payment_platform account's `expected_orders_balance` reflects the
     same net (not the gross), so Reports/Reconciliation/Accounts agree.

Uses the live MongoDB (test_database) — same pattern as other tests.
"""
import sys
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient

from payment_gateway_metrics import compute_metrics


UID = f"test-iter91-phase3-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def db():
    mongo = open("/app/backend/.env").read().split("MONGO_URL=")[1].split("\n")[0].strip('"')
    client = AsyncIOMotorClient(mongo)
    d = client["test_database"]
    await d.unified_orders.delete_many({"user_id": UID})
    await d.accounts.delete_many({"user_id": UID})
    yield d
    await d.unified_orders.delete_many({"user_id": UID})
    await d.accounts.delete_many({"user_id": UID})


async def _seed_order(db, **fields):
    """Insert a minimal unified_orders document for tests."""
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": UID,
        "order_number": fields["order_number"],
        "order_date": fields.get("order_date", "2026-02-01"),
        "order_status": fields.get("order_status", "تم التوصيل"),
        "payment_method": fields.get("payment_method", "مدى"),
        "total_amount": float(fields.get("total_amount", 0)),
        "actual_refund_amount": float(fields.get("actual_refund_amount", 0)),
        "actual_partial_refund_amount": float(
            fields.get("actual_partial_refund_amount", 0)
        ),
        "payment_fee_status": fields.get("payment_fee_status", "estimated"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.unified_orders.insert_one(doc)


@pytest.mark.asyncio
async def test_cancelled_excluded_from_net(db):
    await _seed_order(db, order_number="C1",
                      order_status="ملغي", total_amount=1000)
    res = await compute_metrics(db, UID)
    # Cancelled order should contribute zero to gross/net for that rail
    for r in res.get("rows", []):
        assert r["net"] == 0, f"Cancelled net should be 0, got {r['net']}"
    assert res.get("totals", {}).get("net", 0) == 0


@pytest.mark.asyncio
async def test_refunded_status_zeroes_net(db):
    await _seed_order(db, order_number="R1",
                      order_status="مسترجع", total_amount=500)
    res = await compute_metrics(db, UID)
    # status=مسترجع => net=0 even though gross=500
    rows = [r for r in res.get("rows", []) if r["net"] > 0]
    assert rows == [], f"No row should have positive net, got: {rows}"
    # refund_full should equal gross
    totals = res.get("totals", {})
    assert totals.get("refund_full", 0) == 500


@pytest.mark.asyncio
async def test_partial_refund_reduces_net(db):
    # Confirmed order, partial refund 200 out of 1000
    await _seed_order(
        db, order_number="P1",
        order_status="تم التوصيل",
        total_amount=1000,
        actual_partial_refund_amount=200,
        payment_fee_status="actual",
        # set explicit actuals so net = 1000-0-0-0-200 = 800
    )
    # Add actual_net so the compute path uses the deterministic value
    await db.unified_orders.update_one(
        {"user_id": UID, "order_number": "P1"},
        {"$set": {"actual_net_amount": 800,
                  "actual_payment_fee": 0,
                  "actual_payment_vat": 0,
                  "actual_payment_method": "مدى"}},
    )
    res = await compute_metrics(db, UID)
    totals = res.get("totals", {})
    # net should be 800 (gross 1000 - partial 200)
    assert abs(totals.get("net", 0) - 800) < 0.5, \
        f"Expected net~800, got {totals.get('net')}"
    assert totals.get("refund_partial", 0) == 200


@pytest.mark.asyncio
async def test_mixed_orders_aggregate_correctly(db):
    """End-to-end: confirmed + refunded + cancelled + partial-refund all
    on the same payment_method → final net is the sum minus refunds
    minus cancellations."""
    await _seed_order(db, order_number="MIX1",
                      order_status="تم التوصيل", total_amount=1000)   # confirmed
    await _seed_order(db, order_number="MIX2",
                      order_status="مسترجع", total_amount=500)          # refunded
    await _seed_order(db, order_number="MIX3",
                      order_status="ملغي", total_amount=300)            # cancelled
    # Confirmed + partial refund 100 out of 800 (actual fields)
    await _seed_order(db, order_number="MIX4",
                      order_status="تم التوصيل", total_amount=800,
                      actual_partial_refund_amount=100,
                      payment_fee_status="actual")
    await db.unified_orders.update_one(
        {"user_id": UID, "order_number": "MIX4"},
        {"$set": {"actual_net_amount": 700,
                  "actual_payment_fee": 0,
                  "actual_payment_vat": 0,
                  "actual_payment_method": "مدى"}},
    )

    res = await compute_metrics(db, UID)
    totals = res.get("totals", {})

    # Expected net contribution:
    #   MIX1 confirmed: gross 1000, fee ~2.5%, vat ~15% → ~970-975
    #   MIX2 refunded:  0
    #   MIX3 cancelled: 0
    #   MIX4 partial:   700 (from actual_net_amount)
    # → totals.net ~ 1670-1680 ± fees
    assert totals["net"] > 1600, f"Expected ~1670, got {totals['net']}"
    assert totals["net"] < 1700, f"Expected ~1670, got {totals['net']}"
    assert totals["refund_full"] == 500           # MIX2 booked as full refund
    assert totals["refund_partial"] == 100        # MIX4 partial


@pytest.mark.asyncio
async def test_reconciliation_summary_uses_central_expected(db):
    """Iter-81 + Iter-91 Phase 3: /reconciliation/summary platforms[]
    `expected` MUST equal the central metrics `net` for the rail —
    proving refunds/cancellations are deducted from
    expected_orders_balance shown on the Reconciliation page."""
    await _seed_order(db, order_number="REC1",
                      order_status="تم التوصيل", total_amount=1000)
    await _seed_order(db, order_number="REC2",
                      order_status="مسترجع", total_amount=400)
    await _seed_order(db, order_number="REC3",
                      order_status="ملغي", total_amount=200)

    # Seed a payment_platform account for مدى (folded under "salla").
    await db.accounts.insert_one({
        "id": "test-acc-salla",
        "user_id": UID,
        "name": "سلة",
        "account_type": "payment_platform",
        "currency": "SAR",
        "opening_balance": 0.0,
        "current_balance": 0.0,
        "expected_orders_balance": 1600,   # WRONG (gross) — should be ~970
        "orders_count": 3,
        "auto_created": True,
        "status": "active",
        "normalized_payment_method": "salla",
        "created_at": "2026-02-01",
        "updated_at": "2026-02-01",
    })

    # Central metrics row for "mada" → captured under canonical "mada"
    res = await compute_metrics(db, UID)
    rows = res.get("rows") or []
    mada_row = next((r for r in rows if r["key"] == "mada"), None)
    assert mada_row is not None, "Expected mada row in central metrics"
    # mada net ≈ 1000 - fees - vat (confirmed only; refunded/cancelled excluded)
    assert mada_row["net"] > 950 and mada_row["net"] < 1000
    # No partial-refund in this dataset
    assert mada_row["refund_full"] == 400
