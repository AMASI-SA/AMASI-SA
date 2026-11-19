"""Multipart noncash evidence and delivery retry through the actual HTTP routes."""
import os
from base64 import b64decode
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qs
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient

import accounting_shipping_native_observer as observer
import store_delivery_driver_app_routes as delivery
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router

PNG = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jf5kAAAAASUVORK5CYII=")
ACTOR = {"id": "driver-account", "role": "store_driver", "created_by": "merchant",
         "_session_client": "amasi_mobile"}


@asynccontextmanager
async def fixture(monkeypatch):
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    parsed = urlparse(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not parsed.username and not parsed.password and parse_qs(parsed.query).get("replicaSet")
    connection = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = connection["store_delivery_bank_test_" + uuid4().hex]

    async def seed():
        await db.users.insert_one({"id": "driver-account", "role": "store_driver", "created_by": "merchant"})
        await db.store_drivers.insert_one({"user_id": "merchant", "id": "driver",
            "account_user_id": "driver-account", "status": "active", "name": "Synthetic driver"})
        await db.store_delivery_assignments.insert_one({"user_id": "merchant", "id": "assignment",
            "driver_id": "driver", "order_id": "order-101", "order_number": "101",
            "active": True, "status": "out_for_delivery", "delivery_fee_snapshot": 20})
        await db.unified_orders.insert_one({"user_id": "merchant", "order_id": "order-101",
            "order_number": "101", "order_status_slug": "delivering", "remaining_amount": 70,
            "total_amount": 70, "paid_amount": 0, "payment_method": "cod", "payment_status": "unpaid"})
        await db.order_review_workflows.insert_one({"user_id": "merchant", "order_number": "101",
            "stage": "delivering", "assembly_status": "completed", "store_delivery_assignment_id": "assignment"})
        await db.mz2_financial_accounts.insert_one({"user_id": "merchant", "id": "bank",
            "name": "Synthetic bank", "account_type": "bank", "currency": "SAR", "status": "active"})
    await seed()
    provider = AsyncMock(return_value={"slug": "delivered"})
    monkeypatch.setattr(delivery, "_push_salla_delivery_status", provider)
    monkeypatch.setattr(observer, "observe_driver_delivery", AsyncMock(return_value={"state": "pending"}))
    app = FastAPI()
    app.include_router(make_store_delivery_payment_evidence_router(db, lambda: ACTOR))
    app.include_router(delivery.make_store_delivery_driver_app_router(db, lambda: ACTOR))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield db, client, provider
    finally:
        await connection.drop_database(db.name)
        connection.close()


async def upload(client, kind, key):
    response = await client.post("/store-delivery/evidence/" + kind,
        data={"assignment_id": "assignment"}, files={"file": (kind + ".png", PNG, "image/png")})
    assert response.status_code == 200, response.text
    return response.json()[key]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal"])
async def test_noncash_upload_completion_and_identical_retry_are_successful_once(monkeypatch, method):
    async with fixture(monkeypatch) as (db, client, provider):
        await check_completion(db, client, provider, method)


async def check_completion(db, client, provider, method):
    assert (await client.get("/store-delivery/app/bank-accounts")).json()["items"][0]["id"] == "bank"
    proof = await upload(client, "delivery-proof", "proof_reference")
    receipt = await upload(client, "receipt", "receipt_reference")
    payload = {"barcode": "101", "target_status": "delivered", "payment_method": method,
        "bank_account_id": "bank" if method == "bank_transfer" else None,
        "delivery_proof_reference": proof, "receipt_reference": receipt,
        "physical_cash_amount": None, "physical_cash_confirmed": False}
    first = await client.post("/store-delivery/app/deliveries/status", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "delivered"
    assert first.json()["earning_amount"] == 20
    assert first.json()["collection"]["review_status"] == "pending_accountant_review"

    async def snapshot():
        return {name: await db[name].find({}).to_list(None) for name in (
            "store_delivery_assignments", "store_delivery_driver_earnings", "store_delivery_collections",
            "store_delivery_payment_reviews", "store_delivery_receipts", "store_delivery_delivery_proofs",
            "unified_orders", "order_review_workflows", "store_delivery_events",
        )}
    before_retry = await snapshot()
    retry = await client.post("/store-delivery/app/deliveries/status", json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "delivered"
    assert await snapshot() == before_retry
    for changed in (
        {"receipt_reference": "another-receipt"}, {"delivery_proof_reference": "another-proof"},
        {"bank_account_id": "another-bank"}, {"physical_cash_confirmed": True},
        {"physical_cash_amount": "70.00"}, {"conversation_evidence_reference": "another-conversation"},
        {"payment_method": "card_terminal" if method == "bank_transfer" else "bank_transfer"},
    ):
        refused = await client.post("/store-delivery/app/deliveries/status", json={**payload, **changed})
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "driver_delivery_idempotency_conflict"
        assert await snapshot() == before_retry
    await db.store_delivery_delivery_proofs.update_one({"token": proof}, {"$set": {"status": "uploaded"}})
    interrupted = await snapshot()
    refused = await client.post("/store-delivery/app/deliveries/status", json=payload)
    assert refused.status_code == 409
    assert await snapshot() == interrupted
    await db.store_delivery_delivery_proofs.update_one({"token": proof}, {"$set": {"status": "bound"}})
    await db.users.update_one({"id": ACTOR["id"]}, {"$set": {"disabled": True}})
    refused = await client.post("/store-delivery/app/deliveries/status", json=payload)
    assert refused.status_code == 403
    assert await snapshot() == before_retry
    provider.assert_awaited_once()
    collection = before_retry["store_delivery_collections"][0]
    assert collection["amount"] == 70 and collection["cod_custody_amount"] == 0
    assert before_retry["store_delivery_payment_reviews"][0]["status"] == "pending"
    assert before_retry["unified_orders"][0]["store_delivery_payment_status"] == "pending_accountant_review"
