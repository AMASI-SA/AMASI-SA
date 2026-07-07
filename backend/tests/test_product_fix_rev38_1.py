"""rev38.1 — Single-SKU Product Fix tests (real Mongo, isolated
tenant, fake read-only Qoyod client). Pins:
- plan is read-only and classifies: none/adopt/ambiguous/create_needed
- adopt requires exact confirm token, DB-write-only, refuses ambiguity
- after adopt, the rev38 send-preflight amount_check turns GREEN
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.product_fix import (
    build_product_fix_plan, execute_product_fix_adopt,
)
from integrations.qoyod.send_preflight import build_send_preflight

TENANT = f"test-pfix-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_products_mapping",
         "qoyod_external_products", "qoyod_settings", "qoyod_invoices")
SKU = "AMS11981"


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


class _FakeQoyodReadOnly:
    def __init__(self, matches):
        self._matches = matches
        self.write_called = False
    async def me(self):
        return {"user": {"id": 1}}
    async def find_all_products_by_sku(self, sku, *, limit=10):
        return list(self._matches)
    def __getattr__(self, name):
        if name.startswith(("create", "post", "delete", "update")):
            raise AssertionError(f"WRITE {name} — product-fix must not "
                                 "write to Qoyod")
        raise AttributeError(name)


def _order_row():
    return {
        "user_id": TENANT, "id": "row-270513107",
        "trace_id": "fbf9b48371304703aff2e4b60b35a39d",
        "salla_order_number": "270513107",
        "salla_order_id": "270513107",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": "270513107", "order_number": "270513107",
            "order_date": "2026-07-04", "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "mada",
            "customer": {"name": "عميلة", "phone": "0500000009"},
            "items": [{"sku": SKU,
                       "name": "عباية ستيتش بناتي - تصميم أنيق مع طرحة",
                       "quantity": 1, "unit_price": 172.8,
                       "total": 198.72, "tax_amount": 25.92,
                       "discount_amount": 0.0}],
            "subtotal": 172.8, "tax_amount": 25.92,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 198.72,
        },
    }


@pytest.mark.asyncio
async def test_plan_single_live_match_recommends_adopt(db):
    await db.integration_inbox.insert_one(_order_row())
    client = _FakeQoyodReadOnly(
        [{"id": 4411, "sku": SKU, "name_ar": "عباية ستيتش بناتي"}])
    plan = await build_product_fix_plan(
        db, user_id=TENANT, sku=SKU, api_client=client)
    assert plan["recommended_action"] == "adopt"
    assert plan["adopt_candidate"]["qoyod_product_id"] == "4411"
    assert plan["read_only"] is True
    assert plan["create_payload_preview"] is not None


@pytest.mark.asyncio
async def test_plan_no_match_recommends_create_with_payload(db):
    await db.integration_inbox.insert_one(_order_row())
    plan = await build_product_fix_plan(
        db, user_id=TENANT, sku=SKU, api_client=_FakeQoyodReadOnly([]))
    assert plan["recommended_action"] == "create_needed"
    assert plan["create_payload_preview"] is not None
    assert plan["adopt_candidate"] is None


@pytest.mark.asyncio
async def test_plan_ambiguous_duplicates_refused(db):
    client = _FakeQoyodReadOnly(
        [{"id": 1, "sku": SKU}, {"id": 2, "sku": SKU}])
    plan = await build_product_fix_plan(
        db, user_id=TENANT, sku=SKU, api_client=client)
    assert plan["recommended_action"] == "ambiguous"
    out = await execute_product_fix_adopt(
        db, user_id=TENANT, sku=SKU, api_client=client,
        confirm_token=f"ADOPT-{SKU}", actor="test")
    assert out["ok"] is False
    assert out["reason"] == "adopt_refused_ambiguous"


@pytest.mark.asyncio
async def test_adopt_requires_exact_token(db):
    client = _FakeQoyodReadOnly([{"id": 4411, "sku": SKU}])
    out = await execute_product_fix_adopt(
        db, user_id=TENANT, sku=SKU, api_client=client,
        confirm_token="WRONG", actor="test")
    assert out["ok"] is False and out["reason"] == "bad_confirm_token"
    assert await db.qoyod_products_mapping.count_documents(
        {"user_id": TENANT}) == 0


@pytest.mark.asyncio
async def test_adopt_then_preflight_goes_green(db):
    """The exact user flow: preflight red (unmapped AMS11981) →
    adopt → preflight green with payload preview."""
    await db.integration_inbox.insert_one(_order_row())
    before = await build_send_preflight(
        db, user_id=TENANT, order_number="270513107",
        expected_payment_method="mada")
    assert before["checks"]["amount_check"]["passed"] is False
    assert before["checks"]["amount_check"]["unmapped_skus"] == [SKU]
    assert before["ready_to_send"] is False

    client = _FakeQoyodReadOnly(
        [{"id": 4411, "sku": SKU, "name_ar": "عباية ستيتش بناتي"}])
    out = await execute_product_fix_adopt(
        db, user_id=TENANT, sku=SKU, api_client=client,
        confirm_token=f"ADOPT-{SKU}", actor="test")
    assert out["ok"] is True
    assert out["adopted_qoyod_product_id"] == "4411"
    assert out["no_qoyod_writes"] is True

    after = await build_send_preflight(
        db, user_id=TENANT, order_number="270513107",
        expected_payment_method="mada")
    assert after["checks"]["amount_check"]["passed"] is True, \
        after["checks"]["amount_check"]
    assert after["checks"]["duplicate_check"]["passed"] is True
    assert after["ready_to_send"] is True
    assert after["invoice_payload_preview"] is not None
    assert after["trace_id"] == "fbf9b48371304703aff2e4b60b35a39d"
    assert after["total_amount"] == 198.72


class _FakeQoyodAuthBroken(_FakeQoyodReadOnly):
    async def me(self):
        raise RuntimeError("Qoyod API 401 qoyod_unauthorized")


@pytest.mark.asyncio
async def test_plan_auth_failure_is_search_failed_not_create(db):
    """A 401/timeout must NEVER be misread as 'product absent'."""
    plan = await build_product_fix_plan(
        db, user_id=TENANT, sku=SKU,
        api_client=_FakeQoyodAuthBroken([]))
    assert plan["recommended_action"] == "search_failed"
    assert "401" in (plan["qoyod_live_search_error"] or "")
    out = await execute_product_fix_adopt(
        db, user_id=TENANT, sku=SKU,
        api_client=_FakeQoyodAuthBroken([]),
        confirm_token=f"ADOPT-{SKU}", actor="test")
    assert out["ok"] is False
