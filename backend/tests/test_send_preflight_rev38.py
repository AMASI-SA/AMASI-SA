"""rev38 — Send Preflight READ-ONLY contract tests (real Mongo,
isolated tenant). Pins the 5 user-required checks for the mada canary:
scope date / payment method / duplicate real invoice / amount diff /
payload preview — with zero writes and zero Qoyod API calls.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.send_preflight import build_send_preflight

TENANT = f"test-preflight-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_invoices", "qoyod_settings",
         "qoyod_products_mapping", "qoyod_external_products",
         "qoyod_customers_mapping")


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


def _mada_row(order="270513107", order_date="2026-07-05",
              total=213.78):
    return {
        "user_id": TENANT, "id": f"row-{order}",
        "trace_id": f"trace-{order}",
        "salla_order_number": order, "salla_order_id": order,
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": order, "order_number": order,
            "order_date": order_date,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "mada",
            "customer": {"name": "عميل", "phone": "0500000001"},
            "items": [{"sku": "SKU-1", "name": "منتج",
                       "quantity": 1,
                       "unit_price": 185.9, "total": total,
                       "tax_amount": 27.88, "discount_amount": 0.0}],
            "subtotal": 185.9, "tax_amount": 27.88,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": total,
        },
    }


async def _seed_mapping(db):
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-1", "qoyod_product_id": "21"})


@pytest.mark.asyncio
async def test_mada_preflight_all_green(db):
    await db.integration_inbox.insert_one(_mada_row())
    await _seed_mapping(db)
    out = await build_send_preflight(
        db, user_id=TENANT, order_number="270513107",
        expected_payment_method="mada")
    assert out["ok"] and out["found"]
    assert out["trace_id"] == "trace-270513107"
    assert out["total_amount"] == 213.78
    assert out["checks"]["scope_check"]["passed"] is True
    assert out["checks"]["scope_check"]["order_created_date"] == "2026-07-05"
    assert out["checks"]["payment_check"]["passed"] is True
    assert out["checks"]["payment_check"]["payment_method"] == "mada"
    assert out["checks"]["duplicate_check"]["passed"] is True
    assert out["checks"]["amount_check"]["passed"] is True, \
        out["checks"]["amount_check"]
    assert out["ready_to_send"] is True
    assert out["invoice_payload_preview"] is not None
    assert out["read_only"] and out["no_qoyod_api_calls"]


@pytest.mark.asyncio
async def test_preflight_blocks_duplicate_real_invoice(db):
    await db.integration_inbox.insert_one(_mada_row())
    await _seed_mapping(db)
    await db.qoyod_invoices.insert_one(
        {"user_id": TENANT, "salla_order_number": "270513107",
         "salla_order_id": "270513107", "qoyod_invoice_id": "777"})
    out = await build_send_preflight(
        db, user_id=TENANT, order_number="270513107")
    dup = out["checks"]["duplicate_check"]
    assert dup["passed"] is False
    assert dup["existing_qoyod_invoice_id"] == "777"
    assert out["ready_to_send"] is False


@pytest.mark.asyncio
async def test_preflight_blocks_pre_floor_order(db):
    await db.integration_inbox.insert_one(
        _mada_row(order="268000001", order_date="2026-06-10"))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-1", "qoyod_product_id": "21"})
    out = await build_send_preflight(
        db, user_id=TENANT, order_number="268000001")
    assert out["checks"]["scope_check"]["passed"] is False
    assert "2026-06-10" in out["checks"]["scope_check"]["detail"]
    assert out["ready_to_send"] is False


@pytest.mark.asyncio
async def test_preflight_wrong_payment_method_fails_expectation(db):
    row = _mada_row(order="270999999")
    row["canonical_payload"]["payment_method"] = "tamara_installment"
    await db.integration_inbox.insert_one(row)
    await _seed_mapping(db)
    out = await build_send_preflight(
        db, user_id=TENANT, order_number="270999999",
        expected_payment_method="mada")
    assert out["checks"]["payment_check"]["passed"] is False
    assert out["ready_to_send"] is False


@pytest.mark.asyncio
async def test_preflight_unmapped_sku_blocks_amount_check(db):
    await db.integration_inbox.insert_one(_mada_row(order="270888888"))
    # NO product mapping seeded.
    out = await build_send_preflight(
        db, user_id=TENANT, order_number="270888888")
    amt = out["checks"]["amount_check"]
    assert amt["passed"] is False
    assert amt["unmapped_skus"] == ["SKU-1"]
    assert out["ready_to_send"] is False


@pytest.mark.asyncio
async def test_preflight_writes_nothing(db):
    await db.integration_inbox.insert_one(_mada_row())
    await _seed_mapping(db)
    before = {c: await db[c].count_documents({"user_id": TENANT})
              for c in COLLS}
    await build_send_preflight(
        db, user_id=TENANT, order_number="270513107")
    after = {c: await db[c].count_documents({"user_id": TENANT})
             for c in COLLS}
    assert before == after


@pytest.mark.asyncio
async def test_preflight_order_not_found(db):
    out = await build_send_preflight(
        db, user_id=TENANT, order_number="000000")
    assert out["ok"] is False and out["found"] is False
