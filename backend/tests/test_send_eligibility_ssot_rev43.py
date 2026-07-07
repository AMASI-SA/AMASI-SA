"""rev43 — SSOT send-eligibility tests (real Mongo, isolated tenant).

Pins the user decree:
  1. ONE source of truth `evaluate_order_for_qoyod_send` — contract
     fields exactly as decreed.
  2. IMPOSSIBLE: ready_now with any blocker (candidates consistency).
  3. DRY / SKIPPED / DEAD_LETTER never surface as ready.
  4. one_shot refuses (fail-closed) any non-green order BEFORE any
     Qoyod client construction.
  5. Preview endpoint reads from the same single source.
  6. READ-ONLY: zero writes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.mada_candidates import find_mada_candidates
from integrations.qoyod.one_shot_reprocess import (
    OneShotRefused, reprocess_one_order,
)
from integrations.qoyod.send_eligibility_ssot import (
    build_send_eligibility_preview, evaluate_order_for_qoyod_send,
)

TENANT = f"test-ssot-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_invoices", "qoyod_settings",
         "qoyod_products_mapping", "qoyod_customers_mapping",
         "qoyod_per_order_approvals")

CONTRACT_KEYS = (
    "eligible", "ready_to_send", "blockers",
    "primary_blocker_code", "primary_blocker_reason",
    "duplicate_check", "amount_check", "product_mapping_check",
    "stage_check", "dry_check", "skipped_dead_letter_check",
    "sync_start_date_check",
)


@pytest_asyncio.fixture()
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    for c in COLLS:
        await database[c].delete_many({"user_id": TENANT})
    yield database
    for c in COLLS:
        await database[c].delete_many({"user_id": TENANT})
    client.close()


def _row(order, *, sku="SKU-S", stage="NORMALIZED", history=None,
         pm="mada", order_date="2026-07-05", status="completed",
         status_native="تم التنفيذ", invoice_id=None,
         customer_id="55", hours_ago=1):
    from datetime import timedelta
    return {
        "user_id": TENANT, "id": f"row-{order}",
        "trace_id": f"tr-{order}",
        "connector_key": "salla",
        "idempotency_key": f"idem-{order}",
        "salla_order_number": str(order), "salla_order_id": str(order),
        "pipeline_stage": stage,
        "qoyod_invoice_id": invoice_id,
        "qoyod_customer_id": customer_id,
        "stage_history": history or [{"stage": "RECEIVED"}],
        "received_at": datetime.now(timezone.utc)
        - timedelta(hours=hours_ago),
        "canonical_payload": {
            "order_id": str(order), "order_number": str(order),
            "order_date": order_date, "order_status": status,
            "order_status_native": status_native,
            "payment_method": pm,
            "customer": {"name": "ع", "phone": "0500000001"},
            "items": [{"sku": sku, "name": "منتج", "quantity": 1,
                       "unit_price": 100.0, "total": 115.0,
                       "tax_amount": 15.0, "discount_amount": 0.0}],
            "subtotal": 100.0, "tax_amount": 15.0,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 115.0,
        },
    }


async def _map_sku(db, sku="SKU-S"):
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": sku, "qoyod_product_id": "9",
         "dry_run_only": False})


# ── 1. Contract + green path ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_contract_fields_and_green_order(db):
    await db.integration_inbox.insert_one(_row("101"))
    await _map_sku(db)
    before = {c: await db[c].count_documents({"user_id": TENANT})
              for c in COLLS}
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="101")
    after = {c: await db[c].count_documents({"user_id": TENANT})
             for c in COLLS}
    assert before == after, "READ-ONLY violated"
    for k in CONTRACT_KEYS:
        assert k in ev, f"missing contract key: {k}"
    assert ev["eligible"] is True
    assert ev["ready_to_send"] is True
    assert ev["blockers"] == []
    assert ev["primary_blocker_code"] is None
    for chk in ("duplicate_check", "amount_check",
                "product_mapping_check", "stage_check", "dry_check",
                "skipped_dead_letter_check", "sync_start_date_check"):
        assert ev[chk]["passed"] is True, chk


# ── 2. DRY / SKIPPED / DEAD_LETTER never ready ──────────────────────
@pytest.mark.asyncio
async def test_dry_skipped_dead_letter_always_blocked(db):
    await db.integration_inbox.insert_one(
        _row("201", invoice_id="DRY:77"))
    await db.integration_inbox.insert_one(
        _row("202", stage="SKIPPED", history=[{"stage": "SKIPPED"}]))
    dead = _row("203", stage="DEAD_LETTER")
    dead["dead_lettered_at"] = datetime.now(timezone.utc)
    await db.integration_inbox.insert_one(dead)
    await _map_sku(db)

    ev1 = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="201")
    assert ev1["ready_to_send"] is False
    assert ev1["dry_check"]["passed"] is False
    assert any(b["code"] == "dry_check" for b in ev1["blockers"])

    ev2 = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="202")
    assert ev2["ready_to_send"] is False
    assert ev2["skipped_dead_letter_check"]["passed"] is False
    assert ev2["stage_check"]["passed"] is False

    ev3 = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="203")
    assert ev3["ready_to_send"] is False
    assert ev3["skipped_dead_letter_check"]["passed"] is False


# ── 3. Candidates consistency — the contradiction is IMPOSSIBLE ─────
@pytest.mark.asyncio
async def test_candidates_never_ready_with_blockers(db):
    # green
    await db.integration_inbox.insert_one(_row("301", sku="SKU-A"))
    # DRY invoice (the 269773218-class contradiction)
    await db.integration_inbox.insert_one(
        _row("302", sku="SKU-A", invoice_id="DRY:88", hours_ago=0))
    # SKIPPED
    await db.integration_inbox.insert_one(
        _row("303", sku="SKU-A", stage="SKIPPED",
             history=[{"stage": "SKIPPED"}]))
    # DEAD_LETTER
    dl = _row("304", sku="SKU-A", stage="DEAD_LETTER")
    dl["dead_lettered_at"] = datetime.now(timezone.utc)
    await db.integration_inbox.insert_one(dl)
    # unmapped product only
    await db.integration_inbox.insert_one(
        _row("305", sku="SKU-UNMAPPED", hours_ago=2))
    # real duplicate invoice
    await db.integration_inbox.insert_one(
        _row("306", sku="SKU-A", hours_ago=3))
    await db.qoyod_invoices.insert_one(
        {"user_id": TENANT, "salla_order_number": "306",
         "qoyod_invoice_id": "700"})
    await _map_sku(db, "SKU-A")

    out = await find_mada_candidates(db, user_id=TENANT, limit=10)
    assert out["source"] == "evaluate_order_for_qoyod_send"
    by = {c["order_number"]: c for c in out["candidates"]}

    # THE INVARIANT: ready_now ⇒ zero blockers, always.
    for c in out["candidates"]:
        if c["verdict"] == "ready_now":
            assert c["send_eligibility"]["blockers"] == []
            assert c["send_eligibility"]["ready_to_send"] is True

    assert by["301"]["verdict"] == "ready_now"
    # DRY / SKIPPED / DEAD_LETTER / duplicate never surface at all.
    assert not {"302", "303", "304", "306"} & set(by)
    # unmapped product → needs_product_adopt (NOT ready, no creation).
    assert by["305"]["verdict"] == "needs_product_adopt"
    assert by["305"]["send_eligibility"]["primary_blocker_code"] \
        == "product_mapping_check"


# ── 4. one_shot fail-closed SSOT gate ────────────────────────────────
@pytest.mark.asyncio
async def test_one_shot_refuses_non_green_order(db):
    # Gates open + unmapped product: every legacy guard passes, only
    # the rev43 SSOT gate stands — it must refuse BEFORE any client.
    await db.qoyod_settings.insert_one(
        {"user_id": TENANT, "production_writes_locked": False,
         "selective_live_send_enabled": True, "dry_run_mode": False})
    await db.integration_inbox.insert_one(
        _row("401", sku="SKU-NOMAP"))
    with patch(
        "integrations.qoyod.one_shot_reprocess.get_api_key",
        new_callable=AsyncMock, return_value="fake-key",
    ), patch(
        "integrations.qoyod.one_shot_reprocess._quarantine_dry_mappings",
        new_callable=AsyncMock, return_value={"quarantined": 0},
    ), patch(
        "integrations.qoyod.one_shot_reprocess._reset_row_to_stage",
        new_callable=AsyncMock,
    ):
        with pytest.raises(OneShotRefused) as exc:
            await reprocess_one_order(
                db, user_id=TENANT, order_number="401",
                confirm="REPROCESS-401", actor="test")
    assert exc.value.code == "ssot_not_ready_to_send"
    assert exc.value.extra["primary_blocker_code"] \
        == "product_mapping_check"


# ── 5. Preview from the same single source ───────────────────────────
@pytest.mark.asyncio
async def test_preview_first_orders_same_source(db):
    await db.integration_inbox.insert_one(_row("501", sku="SKU-A"))
    await db.integration_inbox.insert_one(
        _row("502", sku="SKU-A", invoice_id="DRY:9", hours_ago=0))
    await _map_sku(db, "SKU-A")
    out = await build_send_eligibility_preview(
        db, user_id=TENANT, limit=20)
    assert out["source"] == "evaluate_order_for_qoyod_send"
    assert out["read_only"] is True
    by = {i["order_number"]: i for i in out["items"]}
    assert by["501"]["ready_to_send"] is True
    assert by["501"]["blocker_codes"] == []
    assert by["502"]["ready_to_send"] is False
    assert "dry_check" in by["502"]["blocker_codes"]
    assert out["ready_to_send_count"] == 1
    assert out["blocked_count"] == 1


# ── 6. Duplicate + policy dedupe (one root cause listed once) ───────
@pytest.mark.asyncio
async def test_duplicate_deduped_with_policy_already_sent(db):
    await db.integration_inbox.insert_one(_row("601", sku="SKU-A"))
    await db.qoyod_invoices.insert_one(
        {"user_id": TENANT, "salla_order_number": "601",
         "qoyod_invoice_id": "701"})
    await _map_sku(db, "SKU-A")
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="601")
    codes = [b["code"] for b in ev["blockers"]]
    assert "duplicate_check" in codes
    assert "already_sent" not in codes  # deduped — one root cause
