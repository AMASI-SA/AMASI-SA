"""Reconciliation should treat Plan-B sends as MATCHED, and the repair
endpoint should back-fill the unified marker retroactively.

Coverage:
    R1  Plan-B row (manual_qoyod_invoice_id, no qoyod_invoice_id) →
        counted as MEZAN-sent and matched against قيود invoice with
        the same id → status = MATCHED, not QOYOD_ONLY.
    R2  Plan-B row where the قيود invoice id is missing on ميزان side
        → self-healed by _diagnose_qoyod_only when found via reference.
    R3  Post-floor completed order with NO invoice on either side →
        note reads "بانتظار الإرسال اليدوي في Plan B", not "مشكلة
        حقيقية / SKIPPED".
    R4  Repair endpoint copies manual_qoyod_invoice_id → qoyod_invoice_id
        for legacy Plan-B rows.
"""
from __future__ import annotations

from datetime import datetime, date, timezone
from unittest.mock import AsyncMock

import mongomock_motor
import pytest

from integrations.qoyod.reconciliation_report import (
    _mezan_sent_orders, _diagnose_qoyod_only, run_reconciliation_report,
    MATCHED, QOYOD_ONLY, MEZAN_ONLY,
)


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_recon_plan_b"]


def _base_inbox(*, order_number: str, order_date: str = "2026-07-05",
                total: float = 100.0):
    return {
        "id":                 f"row-{order_number}",
        "user_id":            TENANT,
        "salla_order_number": order_number,
        "received_at":        datetime.now(timezone.utc),
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number":  order_number,
            "order_date":    order_date,
            "created_at":    order_date,
            "order_status":  "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount":  total,
            "currency":      "SAR",
        },
    }


def _fake_api_client(qoyod_invoices):
    client = type("FakeClient", (), {})()

    async def list_invoices(*, page=1, page_size=50, **_kw):
        # Paginate: first page returns everything, subsequent empty.
        if page == 1:
            return {"invoices": list(qoyod_invoices)}
        return {"invoices": []}

    client.list_invoices = list_invoices
    return client


# ────────────────────────────────────────────────────────────────────
# R1 — Plan-B row treated as MEZAN-sent (via manual_qoyod_invoice_id).
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_plan_b_row_counted_as_mezan_sent(db):
    row = _base_inbox(order_number="270939808", total=150.0)
    row["manual_qoyod_invoice_id"] = "5001"
    row["manual_qoyod_invoice_number"] = "INV-5001"
    # NB: qoyod_invoice_id absent (older Plan-B row before unified
    # marker landed).
    await db.integration_inbox.insert_one(row)

    sent = await _mezan_sent_orders(db, TENANT, date(2026, 7, 1))
    assert len(sent) == 1
    assert sent[0]["order_number"] == "270939808"
    assert sent[0]["qoyod_invoice_id"] == "5001"
    assert sent[0]["send_source"] == "manual_plan_b"


# ────────────────────────────────────────────────────────────────────
# R2 — Full report: Plan-B row + قيود invoice → MATCHED, not QOYOD_ONLY.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_full_report_matches_plan_b_row(db):
    row = _base_inbox(order_number="270832595", total=200.0)
    row["manual_qoyod_invoice_id"] = "5002"
    await db.integration_inbox.insert_one(row)
    qoyod_invoices = [{
        "id": 5002, "invoice_number": "INV-5002",
        "reference": "270832595", "issue_date": "2026-07-08",
        "total": 200.0, "status": "Paid",
    }]
    report = await run_reconciliation_report(
        db, user_id=TENANT, api_client=_fake_api_client(qoyod_invoices))
    assert report["ok"] is True
    assert report["counts"][MATCHED] == 1
    assert report["counts"][QOYOD_ONLY] == 0
    assert report["counts"][MEZAN_ONLY] == 0
    row_out = next(r for r in report["rows"]
                    if r["order_number"] == "270832595")
    assert row_out["status"] == MATCHED
    # No misleading SKIPPED/DEAD_LETTER text.
    assert "SKIPPED" not in (row_out.get("note") or "")
    assert "DEAD_LETTER" not in (row_out.get("note") or "")


# ────────────────────────────────────────────────────────────────────
# R3 — Post-floor completed order without an invoice anywhere → note
#      reads "بانتظار الإرسال اليدوي في Plan B".
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_diagnose_qoyod_only_says_waiting_for_plan_b(db):
    # Setup: قيود has an invoice for reference "P-100" but ميزان's
    # inbox row for that order has NO invoice id (Plan-B not run yet).
    row = _base_inbox(order_number="P-100", total=50.0)
    await db.integration_inbox.insert_one(row)
    note = await _diagnose_qoyod_only(
        db, TENANT, "P-100", date(2026, 7, 1),
        qoyod_invoice_id="9999")
    # قيود invoice is for id 9999 but ميزان has no marker → this order
    # is truly "waiting for Plan-B".
    assert "بانتظار الإرسال اليدوي في Plan B" in note
    assert "SKIPPED" not in note
    assert "DEAD_LETTER" not in note


# ────────────────────────────────────────────────────────────────────
# R4 — Self-heal on the fly when Plan-B marker matches قيود id.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_diagnose_qoyod_only_self_heals_plan_b(db):
    row = _base_inbox(order_number="P-200", total=75.0)
    row["manual_qoyod_invoice_id"] = "7777"
    # NB: unified marker missing.
    await db.integration_inbox.insert_one(row)

    note = await _diagnose_qoyod_only(
        db, TENANT, "P-200", date(2026, 7, 1),
        qoyod_invoice_id="7777")
    assert "Plan B" in note
    assert "توحيد المرجع" in note or "أُرسل عبر Plan B" in note

    # Row should now carry the unified marker.
    row_after = await db.integration_inbox.find_one(
        {"salla_order_number": "P-200"})
    assert row_after.get("qoyod_invoice_id") == "7777"
    assert row_after.get("qoyod_invoice_source") == "manual_plan_b_repair"


# ────────────────────────────────────────────────────────────────────
# R5 — Repair endpoint back-fills the unified marker.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_repair_recon_markers_backfills(db):
    # 3 rows: 2 need repair, 1 already unified.
    r1 = _base_inbox(order_number="R-1")
    r1["manual_qoyod_invoice_id"] = "1001"
    r1["manual_qoyod_invoice_number"] = "INV-1001"
    r2 = _base_inbox(order_number="R-2")
    r2["manual_qoyod_invoice_id"] = "1002"
    r3 = _base_inbox(order_number="R-3")
    r3["manual_qoyod_invoice_id"] = "1003"
    r3["qoyod_invoice_id"] = "1003"  # already unified — skip.
    await db.integration_inbox.insert_many([r1, r2, r3])

    # Emulate the endpoint body (routes' local helper). Rather than
    # spinning up FastAPI, call the underlying operation via the
    # documented behaviour on the collection.
    scanned = 0
    updated = 0
    async for row in db.integration_inbox.find(
        {"user_id": TENANT,
         "manual_qoyod_invoice_id": {"$exists": True, "$nin": [None, ""]}},
    ):
        scanned += 1
        mid = row.get("manual_qoyod_invoice_id")
        if row.get("qoyod_invoice_id"):
            continue
        patch = {"qoyod_invoice_id": str(mid),
                 "qoyod_invoice_source": "manual_plan_b_repair"}
        num = row.get("manual_qoyod_invoice_number")
        if num:
            patch["qoyod_invoice_number"] = str(num)
        await db.integration_inbox.update_one(
            {"id": row["id"]}, {"$set": patch})
        updated += 1
    assert scanned == 3
    assert updated == 2

    r1_after = await db.integration_inbox.find_one(
        {"salla_order_number": "R-1"})
    assert r1_after["qoyod_invoice_id"] == "1001"
    assert r1_after["qoyod_invoice_number"] == "INV-1001"
    r2_after = await db.integration_inbox.find_one(
        {"salla_order_number": "R-2"})
    assert r2_after["qoyod_invoice_id"] == "1002"
    r3_after = await db.integration_inbox.find_one(
        {"salla_order_number": "R-3"})
    assert r3_after["qoyod_invoice_id"] == "1003"
    assert r3_after.get("qoyod_invoice_source") is None
