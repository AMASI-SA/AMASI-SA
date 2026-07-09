"""Diagnostic-only tests for `diagnose_pending_exclusion`.

Covers each primary_exclusion_reason path:

    T1  no_inbox_row               — order not present in inbox
    T2  received_at_out_of_window  — inbox row is older than `days`
    T3  no_salla_date              — canonical/raw carry no Salla date
    T4  pre_floor_date             — order_date < 2026-07-01
    T5  status_mismatch            — Salla status doesn't match tab
    T6  already_sent_marker        — newest trace has marker
    T7  cross_trace_sent           — older trace has marker
    T8  would_appear_in_page_b     — passes every gate

    T9  in_qoyod_invoices_reference is surfaced correctly
    T10 empty order_number handled

REMOVE alongside `pending_exclusion_diagnose.py` once Plan-B pending
list unification is stabilised.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import mongomock_motor  # noqa: F401
import pytest

from integrations.qoyod_manual.pending_exclusion_diagnose import (
    diagnose_pending_exclusion,
)


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_pending_exclusion_diagnose"]


def _canon(order_number, *, status_slug="delivered",
           status_native="تم التوصيل",
           order_date="2026-07-05", total=260.0):
    return {
        "order_number":       order_number,
        "order_date":         order_date,
        "created_at":         order_date,
        "order_status":       status_slug,
        "order_status_native": status_native,
        "total_amount":       total,
        "currency":           "SAR",
    }


async def _insert_inbox(db, *, order_number, canon=None,
                        received_at=None, salla_order_id=None,
                        manual_qoyod_invoice_id=None,
                        qoyod_invoice_id=None,
                        raw_data=None, row_id=None):
    doc = {
        "id":                 row_id or f"row-{order_number}-{datetime.now(timezone.utc).timestamp()}",
        "user_id":            TENANT,
        "trace_id":           f"tr-{order_number}",
        "salla_order_number": order_number,
        "salla_order_id":     salla_order_id or f"oid-{order_number}",
        "received_at":        received_at or datetime.now(timezone.utc),
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload":  canon if canon is not None else _canon(order_number),
        "raw_payload":        raw_data if raw_data is not None
                              else {"data": {"created_at": "2026-07-05"}},
    }
    if manual_qoyod_invoice_id:
        doc["manual_qoyod_invoice_id"] = manual_qoyod_invoice_id
    if qoyod_invoice_id:
        doc["qoyod_invoice_id"] = qoyod_invoice_id
    await db.integration_inbox.insert_one(doc)
    return doc["id"]


# ─────────────────────────────────────────────────────────────────────
# T1 — no inbox row at all
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_inbox_row(db):
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["999000111"], status="delivered")
    entry = res["orders"][0]
    assert entry["in_integration_inbox"] is False
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "no_inbox_row"


# ─────────────────────────────────────────────────────────────────────
# T2 — received_at outside window
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_received_at_out_of_window(db):
    old = datetime.now(timezone.utc) - timedelta(days=200)
    await _insert_inbox(db, order_number="270000001",
                        received_at=old)
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000001"], status="delivered", days=30)
    entry = res["orders"][0]
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "received_at_out_of_window"


# ─────────────────────────────────────────────────────────────────────
# T3 — no Salla date extractable
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_salla_date(db):
    canon = _canon("270000002")
    canon["order_date"] = None
    canon["created_at"] = None
    await _insert_inbox(db, order_number="270000002", canon=canon,
                        raw_data={"data": {}})
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000002"], status="delivered")
    entry = res["orders"][0]
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "no_salla_date"


# ─────────────────────────────────────────────────────────────────────
# T4 — pre-floor date
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pre_floor_date(db):
    canon = _canon("270000003", order_date="2026-06-15")
    await _insert_inbox(db, order_number="270000003", canon=canon)
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000003"], status="delivered")
    entry = res["orders"][0]
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "pre_floor_date"


# ─────────────────────────────────────────────────────────────────────
# T5 — status mismatch (delivered tab requested, order is completed)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_status_mismatch(db):
    canon = _canon("270000004",
                   status_slug="completed",
                   status_native="تم التنفيذ")
    await _insert_inbox(db, order_number="270000004", canon=canon)
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000004"], status="delivered")
    entry = res["orders"][0]
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "status_mismatch"
    assert entry["checks"]["status_matcher_target"] == "delivered"


# ─────────────────────────────────────────────────────────────────────
# T6 — already_sent marker on newest trace
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_already_sent_marker(db):
    await _insert_inbox(db, order_number="270000005",
                        manual_qoyod_invoice_id="5001")
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000005"], status="delivered")
    entry = res["orders"][0]
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "already_sent_marker"
    assert entry["checks"]["already_sent_marker_ref"] == "5001"


# ─────────────────────────────────────────────────────────────────────
# T7 — cross-trace sent (older trace has marker, newest doesn't)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cross_trace_sent(db):
    now = datetime.now(timezone.utc)
    await _insert_inbox(
        db, order_number="270000006",
        row_id="row-old",
        received_at=now - timedelta(hours=6),
        manual_qoyod_invoice_id="6001")
    # Newest trace — no marker.
    await _insert_inbox(
        db, order_number="270000006",
        row_id="row-new",
        received_at=now)
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000006"], status="delivered")
    entry = res["orders"][0]
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "cross_trace_sent"


# ─────────────────────────────────────────────────────────────────────
# T8 — passes every gate → would appear
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_would_appear_in_page_b(db):
    await _insert_inbox(db, order_number="270000007")
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000007"], status="delivered")
    entry = res["orders"][0]
    assert entry["verdict"] == "would_appear_in_page_b"
    assert entry["primary_exclusion_reason"] is None
    checks = entry["checks"]
    assert checks["passes_received_at_window"] is True
    assert checks["passes_top_limit_window"] is True
    assert checks["passes_salla_date_extraction"] is True
    assert checks["passes_floor_date"] is True
    assert checks["passes_status_matcher"] is True
    assert checks["excluded_by_already_sent_marker"] is False
    assert checks["excluded_by_cross_trace_sent"] is False


# ─────────────────────────────────────────────────────────────────────
# T9 — qoyod_invoices.reference hit is surfaced AND the current
#      cross-trace guard (which queries by salla_order_number) picks
#      it up. This documents current Page B behaviour precisely.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qoyod_reference_hit_reported(db):
    await _insert_inbox(db, order_number="270000008")
    await db.qoyod_invoices.insert_one({
        "user_id":            TENANT,
        "qoyod_invoice_id":   "9911",
        "invoice_number":     "INV-9911",
        "reference":          "270000008",
        "salla_order_number": "270000008",
        "total":              260.0,
        "paid_amount":        260.0,
        "remaining":          0.0,
        "status":             "paid",
        "created_at":         datetime.now(timezone.utc),
    })
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000008"], status="delivered")
    entry = res["orders"][0]
    assert entry["in_qoyod_invoices_reference"] is True
    assert entry["qoyod_reference_hit"]["qoyod_invoice_id"] == "9911"
    # Because qoyod_invoices carries salla_order_number, the current
    # cross-trace guard finds the record and excludes the order.
    assert entry["verdict"] == "excluded"
    assert entry["primary_exclusion_reason"] == "cross_trace_sent"


# ─────────────────────────────────────────────────────────────────────
# T9b — REFERENCE-ONLY invoice (no salla_order_number populated).
#       CRITICAL: this is the edge case the current cross-trace guard
#       MISSES. The order appears in Page B even though قيود has it.
#       This documents the exact drift Page A catches but Page B doesn't.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reference_only_invoice_leaks_into_page_b(db):
    await _insert_inbox(db, order_number="270000009")
    # Invoice synced from قيود with reference set BUT salla_order_number
    # empty (e.g., older sync before write-through populated the alias).
    await db.qoyod_invoices.insert_one({
        "user_id":            TENANT,
        "qoyod_invoice_id":   "9922",
        "invoice_number":     "INV-9922",
        "reference":          "270000009",     # ← the strict key
        "salla_order_number": "",              # ← empty, on purpose
        "total":              260.0,
        "paid_amount":        260.0,
        "remaining":          0.0,
        "status":             "paid",
        "created_at":         datetime.now(timezone.utc),
    })
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000009"], status="delivered")
    entry = res["orders"][0]
    # The diagnostic itself queries by reference, so it FOUND the invoice.
    assert entry["in_qoyod_invoices_reference"] is True
    assert entry["qoyod_reference_hit"]["qoyod_invoice_id"] == "9922"
    # But the current Page B pipeline queries only by salla_order_number
    # in the cross-trace guard — so it still surfaces the order.
    assert entry["verdict"] == "would_appear_in_page_b"


# ─────────────────────────────────────────────────────────────────────
# T10 — invalid input (empty order_number)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_empty_order_number_handled(db):
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["  "], status="delivered")
    entry = res["orders"][0]
    assert entry["verdict"] == "invalid_input"
    assert entry["primary_exclusion_reason"] == "empty_order_number"


# ─────────────────────────────────────────────────────────────────────
# T11 — batch of mixed orders returns one entry per input
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_batch_multiple_orders(db):
    await _insert_inbox(db, order_number="270000010")
    canon_pre = _canon("270000011", order_date="2026-06-01")
    await _insert_inbox(db, order_number="270000011", canon=canon_pre)
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["270000010", "270000011", "999999999"],
        status="delivered")
    assert len(res["orders"]) == 3
    by_on = {r["order_number"]: r for r in res["orders"]}
    assert by_on["270000010"]["verdict"] == "would_appear_in_page_b"
    assert by_on["270000011"]["primary_exclusion_reason"] == "pre_floor_date"
    assert by_on["999999999"]["primary_exclusion_reason"] == "no_inbox_row"
