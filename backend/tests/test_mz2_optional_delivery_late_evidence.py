"""Mandatory original delivery proof -> immutable C3 -> optional late extra evidence -> native COD.

Only Salla transport is stubbed by the existing cash fixture. Driver HTTP uses
the production mobile principal bridge, and all persistence/writers are real.
This suite requires the reviewed optional-delivery foundation.
"""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, Header, Request, HTTPException
from httpx import ASGITransport, AsyncClient

from test_mz2_driver_physical_cash import cash, prepare_delivery, financial_snapshot
from test_mz2_driver_pos_manual_review import manual
from test_mz2_shipping_native import OWNER, AT
from accounting_shipping_native_contract import EVIDENCE
from accounting_shipping_native_routes import BASE, install_shipping_native_routes
from accounting_ledger_v2 import read_reporting_entries_v2
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router
from store_delivery_driver_app_routes import make_store_delivery_driver_app_router
from store_delivery_cash_evidence import SCHEMA as CASH_EVIDENCE_SCHEMA, validate_evidence

DRIVER = "cash-driver-user"
REVIEWER = "optional-proof-reviewer"
UPLOAD = "/api/store-delivery/evidence/late-delivery"
QUEUE = "/api" + BASE + "/late-delivery-evidence"
RECOGNIZE = "/api" + BASE + "/recognize-driver"
DELIVER = "/api/store-delivery/app/deliveries/status"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-original-delivery-image" * 8


@pytest_asyncio.fixture
async def optional(cash):
    from mobile_app_request_context import mobile_app_request_user
    await cash.db.users.insert_one({"id": REVIEWER, "created_by": OWNER, "role": "employee",
        "is_active": True, "accounting_permissions": ["accounting.shipping.view", "accounting.shipping.contracts.review"]})

    async def principal(request: Request, x_actor: str = Header(default=DRIVER)):
        user = await cash.db.users.find_one({"id": x_actor}, {"_id": 0})
        if user.get("role") == "store_driver":
            user["_session_client"] = "amasi_mobile"
        return await mobile_app_request_user(cash.db, user, path=request.url.path, method=request.method)

    app, native = FastAPI(), APIRouter()
    app.include_router(make_store_delivery_driver_app_router(cash.db, principal), prefix="/api")
    app.include_router(make_store_delivery_payment_evidence_router(cash.db, principal), prefix="/api")
    install_shipping_native_routes(native, cash.db, principal)
    app.include_router(native, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://isolated-optional-delivery") as http:
        yield SimpleNamespace(cash=cash, db=cash.db, http=http)


async def prepare_with_required_photo(ctx, *, actual="475.00"):
    assignment, payload = await prepare_delivery(ctx.cash)
    assert payload.get("delivery_proof_reference")
    payload.update(physical_cash_amount=actual, physical_cash_confirmed=True)
    return assignment, payload


async def rows(ctx):
    return await read_reporting_entries_v2(ctx.db, user_id=OWNER, effective_before="2027-01-01T00:00:00Z")


def without_pin(row):
    return {key: value for key, value in row.items() if key != "mz2_shipping_pin"}


@pytest.mark.asyncio
@pytest.mark.parametrize("actual,variance", [("475.00", "-25.00"), ("500.00", "0.00"), ("525.00", "25.00")])
async def test_real_optional_delivery_late_evidence_chain_preserves_original_cash_and_period(optional, actual, variance):
    ctx = optional
    assignment, payload = await prepare_with_required_photo(ctx, actual=actual)
    financial_before = await financial_snapshot(ctx.db)
    delivered = await ctx.http.post(DELIVER, json=payload)
    assert delivered.status_code == 200, delivered.text
    collection = await ctx.db.store_delivery_collections.find_one({"user_id": OWNER, "assignment_id": assignment})
    original_assignment = await ctx.db.store_delivery_assignments.find_one({"user_id": OWNER, "id": assignment})
    c3 = validate_evidence(collection, OWNER, "driver-f")
    assert c3["schema"] == CASH_EVIDENCE_SCHEMA
    assert c3["cod_amount"] == "500.00" and c3["physical_cash_amount"] == actual and c3["variance"] == variance
    assert c3["confirmation_actor"] == DRIVER and c3["confirmed_at"] == AT
    assert collection.get("delivery_proof_reference") == payload["delivery_proof_reference"]
    assert c3.get("delivery_proof_reference") == payload["delivery_proof_reference"]
    assert Decimal(str(collection["amount"])) == Decimal("500.00")
    assert Decimal(str(collection["cod_custody_amount"])) == Decimal("500.00")
    assert await ctx.db.store_delivery_collections.count_documents({"assignment_id": assignment}) == 1
    assert await ctx.db.store_delivery_delivery_proofs.count_documents({"assignment_id": assignment}) == 1
    assert await financial_snapshot(ctx.db) == financial_before

    # These two real HTTP calls traverse the production mobile auth bridge.
    listed = await ctx.http.get(UPLOAD, params={"assignment_id": assignment})
    assert listed.status_code == 200 and listed.json()["items"] == [], listed.text
    uploaded = await ctx.http.post(UPLOAD, data={"assignment_id": assignment,
        "request_id": "optional-photo-upload-1", "reason": "Original delivery photo became available", "captured_at": AT},
        files={"file": ("delivered.png", PNG, "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    attachment = uploaded.json()["attachment"]
    assert attachment["uploader_id"] == DRIVER and attachment["source"]["c3"]["seal"] == c3["seal"]
    assert attachment["financial_effect"] == "none"
    listed = await ctx.http.get(UPLOAD, params={"assignment_id": assignment})
    assert listed.status_code == 200 and listed.json()["items"][0]["attachment"]["id"] == attachment["id"]
    review = await ctx.http.post(QUEUE + "/" + attachment["id"] + "/review", headers={"x-actor": REVIEWER},
        json={"request_id": "optional-photo-review-1", "decision": "approved", "note": "Image matched original delivery"})
    assert review.status_code == 200 and review.json()["state"] == "approved", review.text
    assert await financial_snapshot(ctx.db) == financial_before
    assert await ctx.db.store_delivery_collections.find_one({"_id": collection["_id"]}) == collection
    assert await ctx.db.store_delivery_assignments.find_one({"_id": original_assignment["_id"]}) == original_assignment
    paused = await ctx.http.post(RECOGNIZE, headers={"x-actor": OWNER}, json={"assignment_id": assignment})
    assert paused.status_code == 423, paused.text
    denied = await ctx.http.post(RECOGNIZE, headers={"x-actor": REVIEWER}, json={"assignment_id": assignment})
    assert denied.status_code == 403, denied.text
    assert await financial_snapshot(ctx.db) == financial_before

    # Explicit scratch-fixture control only; the product never unpauses finance.
    await ctx.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": False}})
    posted = await ctx.http.post(RECOGNIZE, headers={"x-actor": OWNER}, json={"assignment_id": assignment})
    assert posted.status_code == 200, posted.text
    posted_rows = await rows(ctx)
    replay = await ctx.http.post(RECOGNIZE, headers={"x-actor": OWNER}, json={"assignment_id": assignment})
    assert replay.status_code == 200 and replay.json()["txn_group_id"] == posted.json()["txn_group_id"]
    assert await rows(ctx) == posted_rows
    journal = [row for row in posted_rows if row["txn_group_id"] == posted.json()["txn_group_id"]]
    driver = [row for row in journal if row["entity_type"] == "store_driver"]
    assert len(driver) == 1 and driver[0]["side"] == "debit" and Decimal(driver[0]["amount"]) == Decimal("500")
    assert all(datetime.fromisoformat(row["effective_at"].replace("Z", "+00:00")) == datetime.fromisoformat(AT) for row in journal)
    assert await ctx.db[EVIDENCE].count_documents({"user_id": OWNER, "assignment_id": assignment}) == 1
    after = await ctx.db.store_delivery_collections.find_one({"_id": collection["_id"]})
    assert without_pin(after) == without_pin(collection)
    assert validate_evidence(after, OWNER, "driver-f") == c3
    assert without_pin(await ctx.db.store_delivery_assignments.find_one({"_id": original_assignment["_id"]})) == without_pin(original_assignment)


@pytest.mark.asyncio
async def test_optional_photo_cash_delivery_retry_is_exactly_idempotent(optional):
    ctx = optional
    assignment, payload = await prepare_with_required_photo(ctx)
    delivered = await ctx.http.post(DELIVER, json=payload)
    assert delivered.status_code == 200, delivered.text
    collection = await ctx.db.store_delivery_collections.find_one({"assignment_id": assignment})
    financial_before = await financial_snapshot(ctx.db)
    event_count = await ctx.db.store_delivery_events.count_documents({"assignment_id": assignment})
    earnings_count = await ctx.db.store_delivery_driver_earnings.count_documents({"assignment_id": assignment})
    retry = await ctx.http.post(DELIVER, json=payload)
    assert retry.status_code == 200, retry.text
    assert await ctx.db.store_delivery_collections.find_one({"assignment_id": assignment}) == collection
    assert await ctx.db.store_delivery_collections.count_documents({"assignment_id": assignment}) == 1
    assert await ctx.db.store_delivery_events.count_documents({"assignment_id": assignment}) == event_count
    assert await ctx.db.store_delivery_driver_earnings.count_documents({"assignment_id": assignment}) == earnings_count
    assert await financial_snapshot(ctx.db) == financial_before
    assert ctx.cash.salla.await_count == 1


@pytest.mark.asyncio
async def test_optional_evidence_auth_bridge_does_not_grant_driver_management(optional):
    from mobile_app_request_context import mobile_app_request_user
    with pytest.raises(HTTPException) as denied:
        await mobile_app_request_user(optional.db, optional.cash.driver,
            path="/api/store-delivery/drivers", method="GET")
    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "mobile_app_route_not_allowed"
