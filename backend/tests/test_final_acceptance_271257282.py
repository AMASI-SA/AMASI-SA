"""FINAL acceptance criteria for order 271257282 (user directive
2026-07-09).

Given the exact production state of order 271257282 — invoice
#335861 exists in قيود with `reference="271257282"`, remaining=260,
payment step never registered — every downstream view MUST classify
it consistently:

    ✅ NOT in `list_unsent_orders`  as UNSENT.
    ✅ NOT in `missing_diagnostics`  as needs_plan_b_send.
    ✅ IN  `reconciliation_v2`      as MATCHED (if remaining=0)
                                     OR NEEDS_REPAIR_MARKER (if the
                                     inbox marker is missing).
    ✅ Each row exposes the mandatory debug bag:
       {order_number, qoyod_reference, invoice_id, payment_id,
        remaining, match_source}.
    ✅ The order appears in EXACTLY ONE bucket per view — never in
       multiple classifications at once.

STRICT match key: `reference == order_number`. No customer, amount,
notes, or description in the primary path.
"""
from __future__ import annotations

from datetime import datetime, timezone

import mongomock_motor  # noqa: F401
import pytest

from integrations.qoyod_manual.missing_diagnostics import (
    list_missing_from_plan_b,
)
from integrations.qoyod.unsent_orders import list_unsent_orders
from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2


TENANT = "main"
ORDER = "271257282"
INV = "335861"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_final_acceptance"]


async def _seed_baseline(db, *, remaining: float, marker_pay_id: str | None):
    """Seed the exact production state of order 271257282:

      • unified_orders row (completed, 2026-07-05, 260.00 SAR)
      • qoyod_invoices row with reference=order_number, invoice 335861
      • integration_inbox row with manual_qoyod_invoice_id=335861
        (payment marker optional)
    """
    await db.unified_orders.insert_one({
        "user_id":            TENANT,
        "order_number":       ORDER,
        "order_id":           "oid-1",
        "order_status":       "completed",
        "order_status_slug":  "completed",
        "order_date":         "2026-07-05",
        "total_amount":       260.0,
        "currency":           "SAR",
        "customer_name":      "عميل تجريبي",
    })
    total = 260.0
    paid = total - remaining
    status = "paid" if remaining == 0 else "partial"
    await db.qoyod_invoices.insert_one({
        "user_id":            TENANT,
        "qoyod_invoice_id":   INV,
        "invoice_number":     f"INV-{INV}",
        "reference":          ORDER,          # ← THE STRICT KEY
        "salla_order_number": ORDER,
        "issue_date":         "2026-07-06",
        "total":              total,
        "paid_amount":        paid,
        "remaining":          remaining,
        "status":             status,
        "source":             "synced_from_qoyod",
        "last_sync_at":       datetime.now(timezone.utc),
        "created_at":         datetime.now(timezone.utc),
    })
    inbox_doc = {
        "id":                       f"row-{ORDER}",
        "user_id":                  TENANT,
        "trace_id":                 f"tr-{ORDER}",
        "salla_order_number":       ORDER,
        "received_at":              datetime.now(timezone.utc),
        "pipeline_stage":           "NORMALIZED",
        "manual_qoyod_invoice_id":  INV,
        "raw_payload":              {"data": {"created_at": "2026-07-05"}},
        "canonical_payload": {
            "order_number":  ORDER,
            "order_date":    "2026-07-05",
            "order_status":  "completed",
            "total_amount":  260.0,
            "currency":      "SAR",
        },
    }
    if marker_pay_id is not None:
        inbox_doc["manual_qoyod_payment_id"] = marker_pay_id
    await db.integration_inbox.insert_one(inbox_doc)


# ─────────────────────────────────────────────────────────────────────
# A. unsent-orders view
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_not_shown_as_unsent(db):
    await _seed_baseline(db, remaining=0.0, marker_pay_id="8001")
    res = await list_unsent_orders(db, user_id=TENANT, days=90, limit=100)
    orders = res.get("orders") or []
    row = next((r for r in orders
                if r.get("order_number") == ORDER), None)
    assert row is not None, "order missing from unsent-orders response"
    # The critical rule: NOT classified as UNSENT.
    assert row["status"] == "أُرسل", (
        f"expected 'أُرسل' but got {row['status']!r} — "
        f"reason={row.get('reason')!r}")
    # Mandatory debug bag.
    dbg = row["debug"]
    assert dbg["order_number"] == ORDER
    assert dbg["qoyod_reference"] == ORDER
    assert dbg["invoice_id"] == INV
    assert dbg["match_source"] == "qoyod_invoices.reference"


# ─────────────────────────────────────────────────────────────────────
# B. missing-from-plan-b diagnostic
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_not_shown_as_needs_plan_b_send(db):
    await _seed_baseline(db, remaining=0.0, marker_pay_id="8001")
    res = await list_missing_from_plan_b(
        db, orders_user_id=TENANT, markers_user_id=TENANT,
        days=90, include_already_sent=True)
    assert res["counts"]["sent_to_qoyod"] == 1
    assert res["counts"]["hidden_with_reason"] == 0
    row = res["orders"][0]
    assert row["order_number"] == ORDER
    assert row["missing_stage"] in ("already_sent_plan_b",
                                     "already_in_qoyod")
    assert row["reason"] in ("already_sent",
                              "duplicate_invoice_in_qoyod")
    # Debug bag mandatory.
    dbg = row["debug"]
    assert dbg["order_number"] == ORDER
    assert dbg["qoyod_reference"] == ORDER
    assert dbg["invoice_id"] == INV


# ─────────────────────────────────────────────────────────────────────
# C. reconciliation_v2 — MATCHED when remaining=0
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reconciliation_matched_when_remaining_zero(db):
    await _seed_baseline(db, remaining=0.0, marker_pay_id="8001")
    res = await run_reconciliation_v2(
        db, orders_user_id=TENANT, markers_user_id=TENANT)
    counts = res["counts"]
    assert counts["مطابق"] == 1
    assert counts["يحتاج إرسال Plan B"] == 0
    assert counts["يحتاج Repair Marker"] == 0
    row = next(r for r in res["rows"] if r["order_number"] == ORDER)
    assert row["match"] == "مطابق"
    assert row["remaining"] == 0.0
    # Debug bag.
    dbg = row["debug"]
    assert dbg["order_number"] == ORDER
    assert dbg["qoyod_reference"] == ORDER
    assert dbg["invoice_id"] == INV
    assert dbg["payment_id"] == "8001"
    assert dbg["remaining"] == 0.0
    assert dbg["match_source"] == "reference"


# ─────────────────────────────────────────────────────────────────────
# D. reconciliation_v2 — NEEDS_REPAIR_MARKER when invoice exists but
#    inbox has no marker at all
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reconciliation_needs_repair_when_no_marker(db):
    # Same qoyod_invoices row, but no inbox row at all → no marker.
    await db.unified_orders.insert_one({
        "user_id":       TENANT, "order_number": ORDER,
        "order_id":      "oid-2",
        "order_status":  "completed", "order_status_slug": "completed",
        "order_date":    "2026-07-05", "total_amount": 260.0,
        "currency":      "SAR",
    })
    await db.qoyod_invoices.insert_one({
        "user_id":            TENANT,
        "qoyod_invoice_id":   INV,
        "invoice_number":     f"INV-{INV}",
        "reference":          ORDER,
        "salla_order_number": ORDER,
        "issue_date":         "2026-07-06",
        "total":              260.0, "paid_amount": 260.0,
        "remaining":          0.0,   "status": "paid",
        "source":             "synced_from_qoyod",
        "last_sync_at":       datetime.now(timezone.utc),
        "created_at":         datetime.now(timezone.utc),
    })
    res = await run_reconciliation_v2(
        db, orders_user_id=TENANT, markers_user_id=TENANT)
    counts = res["counts"]
    assert counts["يحتاج Repair Marker"] == 1
    assert counts["مطابق"] == 0
    row = next(r for r in res["rows"] if r["order_number"] == ORDER)
    assert row["match"] == "يحتاج Repair Marker"
    # Debug bag still populated.
    assert row["debug"]["invoice_id"] == INV


# ─────────────────────────────────────────────────────────────────────
# E. Single-bucket invariant: order appears in EXACTLY ONE bucket
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_order_appears_in_exactly_one_bucket(db):
    await _seed_baseline(db, remaining=0.0, marker_pay_id="8001")
    res = await run_reconciliation_v2(
        db, orders_user_id=TENANT, markers_user_id=TENANT)
    hits = [r for r in res["rows"] if r["order_number"] == ORDER]
    assert len(hits) == 1, (
        f"order {ORDER} appeared in {len(hits)} rows — must be exactly one")


# ─────────────────────────────────────────────────────────────────────
# F. Notes/description NOT used for primary matching (user directive)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_notes_and_description_not_primary_match(db):
    """Salla order 999888777 has NO invoice with matching reference,
    but a قيود invoice mentions '999888777' inside notes. Per the
    strict match rule, they MUST NOT be joined.
    """
    await db.unified_orders.insert_one({
        "user_id":            TENANT,
        "order_number":       "999888777",
        "order_status":       "completed",
        "order_status_slug":  "completed",
        "order_date":         "2026-07-05",
        "total_amount":       100.0,
    })
    await db.qoyod_invoices.insert_one({
        "user_id":            TENANT,
        "qoyod_invoice_id":   "orphan-1",
        "invoice_number":     "INV-orphan-1",
        "reference":          "",             # ← empty on purpose
        "salla_order_number": "",
        "notes":              "customer said order 999888777",
        "description":        "receipt for 999888777",
        "issue_date":         "2026-07-06",
        "total":              100.0,
        "remaining":          0.0,
        "paid_amount":        100.0,
        "status":             "paid",
        "source":             "synced_from_qoyod",
        "last_sync_at":       datetime.now(timezone.utc),
        "created_at":         datetime.now(timezone.utc),
    })
    res = await run_reconciliation_v2(
        db, orders_user_id=TENANT, markers_user_id=TENANT)
    counts = res["counts"]
    # Salla order stays needs-send (no strict match).
    assert counts["يحتاج إرسال Plan B"] == 1
    # قيود invoice stays qoyod-only orphan.
    assert counts["موجود في قيود فقط"] == 1
    # NOT matched (would be a false positive).
    assert counts["مطابق"] == 0
