"""rev39.1 — MADA candidate finder tests (real Mongo, isolated tenant).

Pins: SKIPPED rows rejected, dup-invoice rejected, pre-floor rejected,
non-mada never surfaces, fully-mapped order → ready_now (sorted
first), unmapped-only order → ready_with_product_create, zero writes.
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


def _row(order, *, sku="SKU-M", stage="CUSTOMER_RESOLVED",
         history=None, payment="mada", order_date="2026-07-05",
         hours_ago=1):
    return {
        "user_id": TENANT, "id": f"row-{order}-{hours_ago}",
        "trace_id": f"tr-{order}-{hours_ago}",
        "idempotency_key": f"idem-{order}-{hours_ago}",
        "connector_key": "salla",
        "salla_order_number": order, "salla_order_id": order,
        "pipeline_stage": stage,
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
    # B: unmapped SKU → ready_with_product_create (more recent than A).
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
    nums = [c["order_number"] for c in out["candidates"]]
    assert "801" in nums and "802" in nums
    assert not {"803", "804", "805", "806"} & set(nums)
    # ready_now ranked first even though 802 is more recent.
    assert out["candidates"][0]["order_number"] == "801"
    assert out["candidates"][0]["verdict"] == "ready_now"
    assert out["candidates"][0]["amount_difference"] == 0.0
    c802 = next(c for c in out["candidates"]
                if c["order_number"] == "802")
    assert c802["verdict"] == "ready_with_product_create"
    assert c802["unmapped_skus"] == ["SKU-B"]
    assert "فيتو SKIPPED (rev33)" in out["rejected_summary"]
    assert "توجد فاتورة قيود حقيقية" in out["rejected_summary"]


@pytest.mark.asyncio
async def test_finder_empty_when_no_mada(db):
    out = await find_mada_candidates(db, user_id=TENANT)
    assert out["candidates"] == [] and out["scanned_orders"] == 0


@pytest.mark.asyncio
async def test_dead_letter_rows_rejected_rev39_2(db):
    """rev39.2 — user picked 269997994 (DEAD_LETTER) which rev32.1
    absolutely vetoes at write time (kill switch). The finder must
    reject it upfront."""
    r = _row("901", sku="SKU-A", stage="DEAD_LETTER")
    await db.integration_inbox.insert_one(r)
    r2 = _row("902", sku="SKU-A", stage="CUSTOMER_RESOLVED",
              hours_ago=2)
    r2["dead_lettered_at"] = datetime.now(timezone.utc)  # rolled back
    await db.integration_inbox.insert_one(r2)
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-A", "qoyod_product_id": "9"})
    out = await find_mada_candidates(db, user_id=TENANT, limit=5)
    assert out["candidates"] == []
    assert out["rejected_summary"].get(
        "فيتو DEAD_LETTER/حالة محظورة (rev32.1)") == 2


@pytest.mark.asyncio
async def test_rev41_2_candidates_carry_send_diagnosis(db):
    """rev41.2 — each candidate embeds the unified send diagnosis;
    budget blockers are separated so a pinned/unarmed budget never
    hides real blockers. Clean candidate ranks first."""
    # Clean: one-shot-supported stage + resolved customer + mapped.
    clean = _row("911", sku="SKU-A", stage="NORMALIZED")
    clean["qoyod_customer_id"] = "55"
    await db.integration_inbox.insert_one(clean)
    # Dirty: DRY invoice id sentinel (policy must flag it).
    dirty = _row("912", sku="SKU-A", stage="NORMALIZED", hours_ago=0)
    dirty["qoyod_customer_id"] = "55"
    dirty["qoyod_invoice_id"] = "DRY:123"
    await db.integration_inbox.insert_one(dirty)
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-A", "qoyod_product_id": "9"})

    out = await find_mada_candidates(db, user_id=TENANT, limit=5)
    by_num = {c["order_number"]: c for c in out["candidates"]}
    assert {"911", "912"} <= set(by_num)

    d911 = by_num["911"]["send_diagnosis"]
    assert d911["ready_excluding_budget"] is True
    assert d911["non_budget_blockers"] == []
    assert [b["code"] for b in d911["budget_blockers"]] == [
        "budget_not_armed"]

    d912 = by_num["912"]["send_diagnosis"]
    assert d912["ready_excluding_budget"] is False
    codes = [b["code"] for b in d912["non_budget_blockers"]]
    assert "dry_invoice_id_detected" in codes

    # Clean candidate ranked first.
    assert out["candidates"][0]["order_number"] == "911"
