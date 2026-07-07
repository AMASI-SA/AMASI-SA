"""rev39.1→rev43 — MADA candidate finder tests (real Mongo).

rev43: the finder's verdict comes ONLY from the single source of
truth `evaluate_order_for_qoyod_send`. Pins: ready_now impossible
with any blocker; DRY / SKIPPED / DEAD_LETTER / duplicate / pre-floor
never surface; unmapped-only → needs_product_adopt; zero writes.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.mada_candidates import find_mada_candidates

TENANT = f"test-mcand-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_invoices",
         "qoyod_products_mapping", "qoyod_settings")


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


def _row(order, *, sku="SKU-M", stage="NORMALIZED",
         history=None, payment="mada", order_date="2026-07-05",
         invoice_id=None, customer_id="55", hours_ago=1):
    return {
        "user_id": TENANT, "id": f"row-{order}-{hours_ago}",
        "trace_id": f"tr-{order}-{hours_ago}",
        "idempotency_key": f"idem-{order}-{hours_ago}",
        "connector_key": "salla",
        "salla_order_number": order, "salla_order_id": order,
        "pipeline_stage": stage,
        "qoyod_invoice_id": invoice_id,
        "qoyod_customer_id": customer_id,
        "stage_history": history or [{"stage": "RECEIVED"}],
        "received_at": datetime.now(timezone.utc)
        - timedelta(hours=hours_ago),
        "canonical_payload": {
            "order_id": order, "order_number": order,
            "order_date": order_date, "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": payment,
            "customer": {"name": "ع", "phone": "0500000001"},
            "items": [{"sku": sku, "name": "منتج", "quantity": 1,
                       "unit_price": 100.0, "total": 115.0,
                       "tax_amount": 15.0, "discount_amount": 0.0}],
            "subtotal": 100.0, "tax_amount": 15.0,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 115.0,
        },
    }


@pytest.mark.asyncio
async def test_finder_classifies_and_ranks(db):
    # A: fully mapped → ready_now.
    await db.integration_inbox.insert_one(_row("801", sku="SKU-A"))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-A", "qoyod_product_id": "9"})
    # B: unmapped SKU → needs_product_adopt (more recent than A).
    await db.integration_inbox.insert_one(
        _row("802", sku="SKU-B", hours_ago=0))
    # C: SKIPPED → rejected.
    await db.integration_inbox.insert_one(
        _row("803", stage="SKIPPED",
             history=[{"stage": "SKIPPED"}]))
    # D: has real invoice → rejected.
    await db.integration_inbox.insert_one(_row("804", sku="SKU-A"))
    await db.qoyod_invoices.insert_one(
        {"user_id": TENANT, "salla_order_number": "804",
         "qoyod_invoice_id": "700"})
    # E: pre-floor date → rejected.
    await db.integration_inbox.insert_one(
        _row("805", order_date="2026-06-01"))
    # F: not mada → never scanned.
    await db.integration_inbox.insert_one(_row("806", payment="tamara"))

    before = await db.integration_inbox.count_documents(
        {"user_id": TENANT})
    out = await find_mada_candidates(db, user_id=TENANT, limit=5)
    after = await db.integration_inbox.count_documents(
        {"user_id": TENANT})
    assert before == after  # zero writes

    assert out["ok"] and out["read_only"]
    assert out["source"] == "evaluate_order_for_qoyod_send"
    nums = [c["order_number"] for c in out["candidates"]]
    assert "801" in nums and "802" in nums
    assert not {"803", "804", "805", "806"} & set(nums)
    # ready_now ranked first even though 802 is more recent.
    assert out["candidates"][0]["order_number"] == "801"
    assert out["candidates"][0]["verdict"] == "ready_now"
    assert out["candidates"][0]["send_eligibility"]["blockers"] == []
    c802 = next(c for c in out["candidates"]
                if c["order_number"] == "802")
    assert c802["verdict"] == "needs_product_adopt"
    assert c802["unmapped_skus"] == ["SKU-B"]
    assert "skipped_dead_letter_check" in out["rejected_summary"]
    assert "duplicate_check" in out["rejected_summary"]
    assert "sync_start_date_check" in out["rejected_summary"]


@pytest.mark.asyncio
async def test_finder_empty_when_no_mada(db):
    out = await find_mada_candidates(db, user_id=TENANT)
    assert out["candidates"] == [] and out["scanned_orders"] == 0


@pytest.mark.asyncio
async def test_dead_letter_rows_rejected(db):
    """DEAD_LETTER stage OR dead_lettered_at stamp (rev32.1) must
    never surface — SSOT skipped_dead_letter_check refuses both."""
    await db.integration_inbox.insert_one(
        _row("901", sku="SKU-A", stage="DEAD_LETTER"))
    r2 = _row("902", sku="SKU-A", hours_ago=2)
    r2["dead_lettered_at"] = datetime.now(timezone.utc)  # rolled back
    await db.integration_inbox.insert_one(r2)
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-A", "qoyod_product_id": "9"})
    out = await find_mada_candidates(db, user_id=TENANT, limit=5)
    assert out["candidates"] == []
    assert out["rejected_summary"].get("skipped_dead_letter_check") == 2


@pytest.mark.asyncio
async def test_rev43_dry_never_surfaces_and_invariant_holds(db):
    """The 269773218-class contradiction is now impossible: a DRY
    order can NEVER appear as a candidate, and every ready_now
    candidate has zero blockers."""
    clean = _row("911", sku="SKU-A")
    await db.integration_inbox.insert_one(clean)
    dirty = _row("912", sku="SKU-A", hours_ago=0,
                 invoice_id="DRY:123")
    await db.integration_inbox.insert_one(dirty)
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-A", "qoyod_product_id": "9"})

    out = await find_mada_candidates(db, user_id=TENANT, limit=5)
    nums = {c["order_number"] for c in out["candidates"]}
    assert "911" in nums and "912" not in nums
    assert "dry_check" in out["rejected_summary"]
    for c in out["candidates"]:
        if c["verdict"] == "ready_now":
            assert c["send_eligibility"]["ready_to_send"] is True
            assert c["send_eligibility"]["blockers"] == []
