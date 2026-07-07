"""rev43.1 — SKIPPED Forensics tests (real Mongo, isolated tenant).

Pins: read-only, correct reason classification, and the LOCK proof —
a status-skipped order whose later completed webhook ALSO got
SKIPPED (payment allowlist) is reported as
`completed_webhook_also_skipped` with a locked example.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.skipped_forensics import build_skipped_forensics

TENANT = f"test-skf-{uuid4().hex[:8]}"


@pytest_asyncio.fixture()
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await database.integration_inbox.delete_many({"user_id": TENANT})
    yield database
    await database.integration_inbox.delete_many({"user_id": TENANT})
    client.close()


def _row(order, *, status, status_native, note, gate_reason=None,
         stage="SKIPPED", hours_ago=1, suffix=""):
    now = datetime.now(timezone.utc)
    r = {
        "user_id": TENANT, "id": f"row-{order}{suffix}",
        "trace_id": f"tr-{order}{suffix}",
        "idempotency_key": f"idem-{order}{suffix}",
        "connector_key": "salla",
        "salla_order_number": str(order), "salla_order_id": str(order),
        "pipeline_stage": stage,
        "pipeline_finished_at": now - timedelta(hours=hours_ago),
        "received_at": now - timedelta(hours=hours_ago),
        "stage_history": [
            {"from_stage": "RECEIVED", "to_stage": "NORMALIZED",
             "at": now - timedelta(hours=hours_ago, minutes=1),
             "actor": "webhook"},
            {"from_stage": "NORMALIZED", "to_stage": stage,
             "at": now - timedelta(hours=hours_ago),
             "actor": "worker", "note": note},
        ] if stage == "SKIPPED" else [
            {"from_stage": "RECEIVED", "to_stage": "NORMALIZED",
             "at": now - timedelta(hours=hours_ago),
             "actor": "webhook"}],
        "raw_payload": {"event": "order.status.updated"},
        "canonical_payload": {
            "order_number": str(order), "order_status": status,
            "order_status_native": status_native,
            "payment_method": "mada", "order_date": "2026-07-05",
        },
    }
    if gate_reason:
        r["selective_auto_send_gate"] = {"eligible": False,
                                         "reason": gate_reason}
    return r


@pytest.mark.asyncio
async def test_forensics_proves_the_lock(db):
    # Order A: first webhook under_review → SKIPPED (status). Later
    # completed webhook → ALSO SKIPPED (payment allowlist) = LOCKED.
    await db.integration_inbox.insert_one(_row(
        "7001", status="under_review",
        status_native="بإنتظار المراجعة",
        note="selective_auto_send_gate: status_not_in_allow_list",
        gate_reason="status_not_in_allow_list", hours_ago=10))
    await db.integration_inbox.insert_one(_row(
        "7001", status="completed", status_native="تم التنفيذ",
        note="selective_auto_send_gate: payment_method_not_in_allow_list",
        gate_reason="payment_method_not_in_allow_list",
        hours_ago=2, suffix="-b"))
    # Order B: status-skipped, completed webhook PROGRESSED (control).
    await db.integration_inbox.insert_one(_row(
        "7002", status="in_progress", status_native="قيد التنفيذ",
        note="business_rule: status_not_eligible", hours_ago=9))
    await db.integration_inbox.insert_one(_row(
        "7002", status="completed", status_native="تم التنفيذ",
        note="", stage="COMPLETED", hours_ago=1, suffix="-b"))
    # Order C: payment-scope skip only (not a status case).
    await db.integration_inbox.insert_one(_row(
        "7003", status="completed", status_native="تم التنفيذ",
        note="rev33.2 canary_scope_skip: pm='mada' outside allowlist "
             "during Live Canary — no write attempted", hours_ago=3))

    before = await db.integration_inbox.count_documents(
        {"user_id": TENANT})
    out = await build_skipped_forensics(db, user_id=TENANT, limit=20)
    after = await db.integration_inbox.count_documents(
        {"user_id": TENANT})
    assert before == after  # read-only

    # Part 1 — transitions listed with classification.
    rows = out["last_skipped_transitions"]
    by = {}
    for r in rows:
        by.setdefault(r["order_number"], []).append(r)
    assert by["7001"][0]["old_stage"] == "NORMALIZED"
    classes = {r["reason_class"] for r in rows}
    assert "status_not_enabled" in classes
    assert "payment_method_scope" in classes
    assert out["reason_class_counts"]["status_not_enabled"] >= 2

    # Part 2+3 — the LOCK proof.
    cases = {c["skipped_row"]["order_number"]: c
             for c in out["status_skipped_cases"]}
    a = cases["7001"]
    assert a["completed_webhook_found"] is True
    assert a["verdict"] == "completed_webhook_also_skipped"
    assert a["completed_row"]["skip_reason_class"] \
        == "payment_method_scope"
    b = cases["7002"]
    assert b["verdict"] == "completed_webhook_progressed"

    locked = out["locked_despite_completed_examples"]
    assert any(e["order_number"] == "7001" for e in locked)
