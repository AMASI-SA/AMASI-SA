"""Strict reference-based matching for the Plan-B "missing" diagnostic.

User directive (2026-07-09): `order_number` is the SOLE match-key
between Salla, Mezan, and قيود. The Plan-B "needs send" bucket must
be strictly derived from:

        salla_order_number  ==  qoyod_invoices.reference

with no fallback to customer / amount / notes / description as a
PRIMARY match path.

Covered scenarios:
    T1  qoyod_invoices row where `reference == order_number` but
        `salla_order_number` is empty → order MUST be classified as
        sent (`already_in_qoyod`), never surface as
        `needs_plan_b_send`.
    T2  qoyod_invoices row where BOTH `reference` and
        `salla_order_number` equal `order_number` → sent.
    T3  qoyod_invoices row with alternative reference field
        (`external_reference`) equal to `order_number` → sent.
    T4  No qoyod_invoices row, no inbox marker → shows as
        needs_plan_b_send with all debug fields populated.
    T5  Debug bag includes `order_number`, `qoyod_reference`,
        `invoice_id`, `payment_id`, `remaining` for every returned
        row (user directive).
"""
from __future__ import annotations

from datetime import datetime, timezone

import mongomock_motor  # noqa: F401
import pytest

from integrations.qoyod_manual.missing_diagnostics import (
    list_missing_from_plan_b,
)


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_matching_strict_reference"]


async def _seed_unified(db, order_number: str, *,
                        order_date: str = "2026-07-05",
                        status: str = "completed",
                        total: float = 260.0):
    await db.unified_orders.insert_one({
        "user_id":            TENANT,
        "order_number":       order_number,
        "order_id":           f"oid-{order_number}",
        "order_status":       status,
        "order_status_slug":  status,
        "order_date":         order_date,
        "total_amount":       total,
        "currency":           "SAR",
        "customer_name":      "عميل تجريبي",
    })


async def _seed_invoice(db, *,
                        qid: str, order_number: str,
                        reference: str | None,
                        salla_order_number: str | None = None,
                        external_reference: str | None = None,
                        remaining: float = 0.0,
                        paid: float = 260.0,
                        total: float = 260.0,
                        status: str = "paid"):
    doc = {
        "user_id":            TENANT,
        "qoyod_invoice_id":   qid,
        "invoice_number":     f"INV-{qid}",
        "reference":          reference,
        "salla_order_number": salla_order_number,
        "external_reference": external_reference,
        "issue_date":         "2026-07-06",
        "total":              total,
        "paid_amount":        paid,
        "remaining":          remaining,
        "status":             status,
        "source":             "synced_from_qoyod",
        "last_sync_at":       datetime.now(timezone.utc),
        "created_at":         datetime.now(timezone.utc),
    }
    await db.qoyod_invoices.insert_one(doc)


# ─────────────────────────────────────────────────────────────────────
# T1 — reference alone (no salla_order_number) must match
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_match_when_only_reference_set(db):
    await _seed_unified(db, "271257282", total=260.0)
    await _seed_invoice(db, qid="335861",
                        order_number="271257282",
                        reference="271257282",
                        salla_order_number=None)
    res = await list_missing_from_plan_b(
        db, orders_user_id=TENANT, markers_user_id=TENANT,
        days=90, include_already_sent=True)
    orders = res["orders"]
    assert len(orders) == 1
    row = orders[0]
    assert row["order_number"] == "271257282"
    assert row["has_qoyod_invoice"] is True
    assert row["qoyod_invoice_id"] == "335861"
    assert row["missing_stage"] == "already_in_qoyod"
    assert row["debug"]["qoyod_reference"] == "271257282"
    assert row["debug"]["match_source"] == "qoyod_invoices.reference"
    assert res["counts"]["sent_to_qoyod"] == 1
    assert res["counts"]["visible_in_plan_b"] == 0
    assert res["counts"]["hidden_with_reason"] == 0


# ─────────────────────────────────────────────────────────────────────
# T2 — both reference AND salla_order_number set
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_match_when_both_reference_fields_set(db):
    await _seed_unified(db, "271052598", total=132.19)
    await _seed_invoice(db, qid="401",
                        order_number="271052598",
                        reference="271052598",
                        salla_order_number="271052598",
                        total=132.19, paid=132.19, remaining=0.0)
    res = await list_missing_from_plan_b(
        db, orders_user_id=TENANT, markers_user_id=TENANT,
        days=90, include_already_sent=True)
    assert res["counts"]["sent_to_qoyod"] == 1
    row = res["orders"][0]
    assert row["debug"]["qoyod_reference"] == "271052598"
    assert row["debug"]["remaining"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# T3 — external_reference matches (alternative Qoyod field)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_match_when_only_external_reference_set(db):
    await _seed_unified(db, "269591641", total=528.71)
    await _seed_invoice(db, qid="8801",
                        order_number="269591641",
                        reference=None,
                        salla_order_number=None,
                        external_reference="269591641",
                        total=528.71, remaining=0.0)
    res = await list_missing_from_plan_b(
        db, orders_user_id=TENANT, markers_user_id=TENANT,
        days=90, include_already_sent=True)
    assert res["counts"]["sent_to_qoyod"] == 1
    assert res["orders"][0]["missing_stage"] == "already_in_qoyod"


# ─────────────────────────────────────────────────────────────────────
# T4 — no invoice AND no marker → needs_plan_b_send
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_needs_send_when_no_invoice_no_marker(db):
    await _seed_unified(db, "999999999", total=100.0)
    # No invoice seeded, no inbox marker seeded.
    res = await list_missing_from_plan_b(
        db, orders_user_id=TENANT, markers_user_id=TENANT,
        days=90, include_already_sent=True)
    assert res["counts"]["sent_to_qoyod"] == 0
    assert res["counts"]["hidden_with_reason"] == 1
    row = res["orders"][0]
    assert row["missing_stage"] == "missing_from_integration_inbox"
    assert row["has_qoyod_invoice"] is False
    assert row["debug"]["qoyod_reference"] is None
    assert row["debug"]["invoice_id"] is None
    assert row["debug"]["payment_id"] is None


# ─────────────────────────────────────────────────────────────────────
# T5 — debug bag structure is stable per user contract
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_debug_bag_always_present(db):
    await _seed_unified(db, "269826009", total=686.23)
    # Row that matches by reference — verify all debug fields.
    await _seed_invoice(db, qid="9101",
                        order_number="269826009",
                        reference="269826009",
                        remaining=686.23, paid=0.0,
                        status="unpaid")
    # Also seed an inbox row with payment marker to exercise the
    # payment_marker_by_number map.
    await db.integration_inbox.insert_one({
        "id":                       "row-269826009",
        "user_id":                  TENANT,
        "trace_id":                 "tr-1",
        "salla_order_number":       "269826009",
        "received_at":              datetime.now(timezone.utc),
        "pipeline_stage":           "NORMALIZED",
        "manual_qoyod_invoice_id":  "9101",
        "manual_qoyod_payment_id":  "77001",
        "raw_payload":              {"data": {"created_at": "2026-07-06"}},
        "canonical_payload": {
            "order_number":  "269826009",
            "order_date":    "2026-07-06",
            "order_status":  "completed",
            "total_amount":  686.23,
            "currency":      "SAR",
        },
    })
    res = await list_missing_from_plan_b(
        db, orders_user_id=TENANT, markers_user_id=TENANT,
        days=90, include_already_sent=True)
    assert len(res["orders"]) == 1
    debug = res["orders"][0]["debug"]
    for key in ("order_number", "qoyod_reference", "invoice_id",
                "payment_id", "remaining", "qoyod_total",
                "qoyod_paid", "qoyod_status", "match_source"):
        assert key in debug, f"missing debug key: {key}"
    assert debug["order_number"] == "269826009"
    assert debug["qoyod_reference"] == "269826009"
    assert debug["invoice_id"] in ("9101",)
    assert debug["payment_id"] == "77001"
    assert debug["remaining"] == 686.23
