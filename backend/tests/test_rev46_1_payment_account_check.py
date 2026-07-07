"""rev46.1 — SSOT payment_account_mapping_check (gap found on prod).

The first credit_card canary send SKIPPED at the SAS gate
(payment_method_mapping_missing) DESPITE a fully-green diagnosis.
SSOT must mirror EVERY send-path blocker — pins the new check.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.send_eligibility_ssot import (
    evaluate_order_for_qoyod_send,
)

TENANT = f"test-r461-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_settings",
         "qoyod_products_mapping", "qoyod_invoices")


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


def _row(order, pm="credit_card"):
    return {
        "user_id": TENANT, "id": f"row-{order}",
        "trace_id": f"tr-{order}",
        "idempotency_key": f"idem-{order}",
        "connector_key": "salla",
        "salla_order_number": str(order), "salla_order_id": str(order),
        "pipeline_stage": "SKIPPED", "skip_class": "transient",
        "qoyod_customer_id": None,
        "stage_history": [{"from_stage": "NORMALIZED",
                           "to_stage": "SKIPPED",
                           "at": datetime.now(timezone.utc),
                           "actor": "worker"}],
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": str(order), "order_number": str(order),
            "order_date": "2026-07-06T10:00:00Z",
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": pm,
            "customer": {"name": "ع", "phone": "0500000001"},
            "items": [{"sku": "SKU-P", "name": "منتج", "quantity": 1,
                       "unit_price": 100.0, "total": 115.0,
                       "tax_amount": 15.0, "discount_amount": 0.0}],
            "subtotal": 100.0, "tax_amount": 15.0,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 115.0,
        },
    }


@pytest.mark.asyncio
async def test_missing_payment_account_blocks(db):
    """Reproduces the prod incident: mapped products, green
    everything, but NO Qoyod account for credit_card."""
    await db.integration_inbox.insert_one(_row("801"))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-P", "qoyod_product_id": "9"})
    await db.qoyod_settings.insert_one(
        {"user_id": TENANT, "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "2"},
            {"salla_method": "tabby_installment",
             "qoyod_account_id": "1"}]})
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="801")
    assert ev["ready_to_send"] is False
    assert ev["payment_account_mapping_check"]["passed"] is False
    codes = [b["code"] for b in ev["blockers"]]
    assert codes == ["payment_account_mapping_check"]


@pytest.mark.asyncio
async def test_mapped_payment_account_green(db):
    await db.integration_inbox.insert_one(_row("802"))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-P", "qoyod_product_id": "9"})
    await db.qoyod_settings.insert_one(
        {"user_id": TENANT, "payment_method_mapping": [
            {"salla_method": "credit_card",
             "qoyod_account_id": "77"}]})
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="802")
    assert ev["payment_account_mapping_check"]["passed"] is True
    assert ev["payment_account_mapping_check"]["qoyod_account_id"] \
        == "77"
    assert ev["ready_to_send"] is True
