"""Actual replacement-receipt upload -> resubmit -> existing native review.

The reused manual/history fixture owns a disposable loopback Mongo replica DB,
real canonical POS/bank identities, and a Legacy command monitor. No ledger,
receipt upload, destination resolver or review writer is mocked.
"""
from decimal import Decimal
import hashlib
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient

from accounting_ledger_v2 import (
    GROUPS_COLLECTION, GENERAL_LEDGER_COLLECTION, AUDIT_COLLECTION, SEQUENCES_COLLECTION,
)
from accounting_shipping_native import recognize_cod, _rows, _balance
from accounting_shipping_native_contract import EVIDENCE, EVENTS
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router
from store_delivery_payment_resubmission_routes import make_store_delivery_payment_resubmission_router
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
from test_mz2_bank_evidence_adapters import imported
from test_mz2_driver_payment_review import driver_delivery
from test_mz2_driver_pos_manual_review import manual, approval
from test_mz2_shipping_native import OWNER

DRIVER = "replacement-driver"
UPLOAD = "/store-delivery/evidence/receipt"
REVIEW = "/store-delivery/payment-review/"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-replacement-receipt" * 8
FINANCIAL = (GROUPS_COLLECTION, GENERAL_LEDGER_COLLECTION, AUDIT_COLLECTION,
             SEQUENCES_COLLECTION, EVIDENCE, EVENTS, "mz2_daily_movements")


@pytest_asyncio.fixture
async def receipts(manual):
    db = manual.db
    await db.store_drivers.update_one({"user_id": OWNER, "id": "driver-f"},
                                     {"$set": {"account_user_id": DRIVER}})
    await db.users.insert_many([
        {"id": DRIVER, "role": "store_driver", "created_by": OWNER, "is_active": True},
        {"id": "other-driver", "role": "store_driver", "created_by": OWNER, "is_active": True},
        {"id": "foreign-driver", "role": "store_driver", "created_by": "foreign-owner", "is_active": True},
    ])
    await db.store_drivers.insert_many([
        {"user_id": OWNER, "id": "driver-other", "account_user_id": "other-driver", "status": "active"},
        {"user_id": "foreign-owner", "id": "driver-foreign", "account_user_id": "foreign-driver", "status": "active"},
    ])
    await db.mz2_financial_accounts.update_one({"user_id": OWNER, "id": "bank-f"},
        {"$set": {"account_type": "bank", "currency": "SAR", "status": "active", "name": "Synthetic bank"}}, upsert=True)

    async def current_user(x_actor: str = Header(default=DRIVER)):
        # Explicit synthetic auth transport; identity itself comes from Mongo.
        actor = await db.users.find_one({"id": x_actor}, {"_id": 0})
        if not actor or actor.get("disabled") is True or actor.get("is_active") is False:
            raise HTTPException(403, "synthetic_actor_unavailable")
        return actor

    app = FastAPI()
    for factory in (make_store_delivery_payment_evidence_router,
                    make_store_delivery_payment_resubmission_router,
                    make_store_delivery_payment_review_router):
        app.include_router(factory(db, current_user))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://isolated-receipt") as http:
        yield SimpleNamespace(db=db, http=http, native=manual)


async def snapshot(ctx):
    return {name: await ctx.db[name].find({}).sort("_id", 1).to_list(None) for name in FINANCIAL}


async def decide(ctx, assignment, payload):
    return await ctx.http.post(REVIEW + assignment, headers={"x-actor": OWNER}, json=payload)


async def ready(ctx, method="card_terminal", state="rejected"):
    assignment = await driver_delivery(ctx.db, method)
    await ctx.db.store_delivery_assignments.update_one({"user_id": OWNER, "id": assignment},
                                                      {"$set": {"active": True}})
    await recognize_cod(ctx.db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    payload = approval(ctx.native)
    if method == "bank_transfer":
        movement = await imported(ctx.db, OWNER, "bank-f", value="500.00", direction="in",
                                  day="2026-09-03", reference="REPLACEMENT-BANK-1")
        payload = {"decision": "approved", "note": "Reviewed actual replacement bank receipt",
                   "destination_financial_id": "bank-f", "settlement_reference": movement["id"]}
    if state == "rejected" and method != "cash":
        rejected = await decide(ctx, assignment, {"decision": "rejected", "note": "Original receipt needs correction"})
        assert rejected.status_code == 200, rejected.text
    elif state == "approved":
        accepted = await decide(ctx, assignment, payload)
        assert accepted.status_code == 200, accepted.text
    return assignment, payload


async def upload(ctx, assignment, actor=DRIVER):
    return await ctx.http.post(UPLOAD, headers={"x-actor": actor},
        data={"assignment_id": assignment}, files={"file": ("replacement.png", PNG, "image/png")})


async def resubmit(ctx, assignment, token, method="card_terminal"):
    return await ctx.http.post("/store-delivery/app/payment-review/" + assignment + "/resubmit",
        headers={"x-actor": DRIVER}, json={"receipt_reference": token,
                                          "bank_account_id": "bank-f" if method == "bank_transfer" else None})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal"])
async def test_real_rejected_delivery_upload_resubmission_and_separate_approval(receipts, method):
    ctx = receipts
    assignment, payload = await ready(ctx, method)
    before = await snapshot(ctx)
    old_event = await ctx.db[EVENTS].find_one({"user_id": OWNER, "kind": "driver_payment_review"})
    proofs = await ctx.db.store_delivery_delivery_proofs.find({}).to_list(None)
    uploaded = await upload(ctx, assignment)
    assert uploaded.status_code == 200, uploaded.text
    token = uploaded.json()["receipt_reference"]
    receipt = await ctx.db.store_delivery_receipts.find_one({"user_id": OWNER, "token": token})
    assert receipt["status"] == "uploaded" and bytes(receipt["content"]) == PNG
    assert receipt["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert receipt["created_by_account_user_id"] == DRIVER
    assert receipt["driver_id"] == "driver-f" and receipt["assignment_id"] == assignment
    assert await snapshot(ctx) == before

    submitted = await resubmit(ctx, assignment, token, method)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending" and submitted.json()["revision"] == 2
    assert (await ctx.db.store_delivery_receipts.find_one({"token": token}))["status"] == "bound"
    assert (await ctx.db.store_delivery_receipts.find_one({"token": "receipt-1"}))["status"] == "superseded"
    assert await snapshot(ctx) == before
    assert await ctx.db.store_delivery_delivery_proofs.find({}).to_list(None) == proofs
    collection = await ctx.db.store_delivery_collections.find_one({"assignment_id": assignment})
    assert collection["delivery_proof_reference"] == "proof-1"
    assert await ctx.db.store_delivery_delivery_proofs.count_documents({"token": token}) == 0

    approved = await decide(ctx, assignment, payload)
    assert approved.status_code == 200 and approved.json()["state"] == "posted", approved.text
    assert await ctx.db[GROUPS_COLLECTION].count_documents({}) == len(before[GROUPS_COLLECTION]) + 1
    assert await ctx.db[EVENTS].find_one({"_id": old_event["_id"]}) == old_event
    events = await ctx.db[EVENTS].find({"kind": "driver_payment_review", "assignment_id": assignment}).to_list(None)
    assert {(row["review_revision"], row["decision"]) for row in events} == {(1, "rejected"), (2, "approved")}
    rows = await _rows(ctx.db, OWNER)
    assert _balance(rows, ("store_driver", "driver-f", "cod_receivable")) == Decimal("0")
    assert _balance(rows, ("bank", "bank-f", "main")) == Decimal("1500" if method == "bank_transfer" else "1000")
    assert _balance(rows, ("asset", ctx.native.fact["id"], "other_receivable")) == Decimal("0" if method == "bank_transfer" else "500")
    after = await snapshot(ctx)
    repeated = await decide(ctx, assignment, payload)
    assert repeated.status_code == 200 and repeated.json()["state"] == "already_posted", repeated.text
    assert await snapshot(ctx) == after


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["pending", "approved", "cash", "missing_review", "foreign_owner",
                                 "wrong_driver", "inactive_driver", "inactive_assignment",
                                 "wrong_review_driver", "wrong_review_order_id",
                                 "wrong_review_order_number", "duplicate_review"])
async def test_delivered_receipt_upload_rejects_ineligible_review_or_identity(receipts, case):
    ctx = receipts
    method = "cash" if case == "cash" else "card_terminal"
    state = case if case in {"pending", "approved"} else "rejected"
    assignment, _ = await ready(ctx, method, state)
    actor = {"foreign_owner": "foreign-driver", "wrong_driver": "other-driver"}.get(case, DRIVER)
    if case == "missing_review":
        await ctx.db.store_delivery_payment_reviews.delete_one({"assignment_id": assignment})
    elif case == "inactive_driver":
        await ctx.db.store_drivers.update_one({"id": "driver-f"}, {"$set": {"status": "inactive"}})
    elif case == "inactive_assignment":
        await ctx.db.store_delivery_assignments.update_one({"id": assignment}, {"$set": {"active": False}})
    elif case in {"wrong_review_driver", "wrong_review_order_id", "wrong_review_order_number"}:
        field = {"wrong_review_driver": "driver_id", "wrong_review_order_id": "order_id",
                 "wrong_review_order_number": "order_number"}[case]
        await ctx.db.store_delivery_payment_reviews.update_one(
            {"assignment_id": assignment}, {"$set": {field: "different-identity"}})
    elif case == "duplicate_review":
        duplicate = await ctx.db.store_delivery_payment_reviews.find_one(
            {"assignment_id": assignment}, {"_id": 0})
        duplicate["id"] = "duplicate-review"
        await ctx.db.store_delivery_payment_reviews.insert_one(duplicate)
    before = await snapshot(ctx)
    receipt_count = await ctx.db.store_delivery_receipts.count_documents({})
    response = await upload(ctx, assignment, actor)
    assert response.status_code in {403, 404, 409}, response.text
    assert await ctx.db.store_delivery_receipts.count_documents({}) == receipt_count
    assert await snapshot(ctx) == before


@pytest.mark.asyncio
async def test_replacement_receipt_while_paused_has_no_financial_effect(receipts):
    ctx = receipts
    assignment, payload = await ready(ctx)
    await ctx.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    controls = await ctx.db.mz2_atomic_owners.find_one({"_id": OWNER})
    before = await snapshot(ctx)
    uploaded = await upload(ctx, assignment)
    assert uploaded.status_code == 200, uploaded.text
    submitted = await resubmit(ctx, assignment, uploaded.json()["receipt_reference"])
    assert submitted.status_code == 200 and submitted.json()["status"] == "pending", submitted.text
    blocked = await decide(ctx, assignment, payload)
    assert blocked.status_code == 423 and blocked.json()["detail"]["code"] == "mz2_writes_paused"
    assert await snapshot(ctx) == before
    assert await ctx.db.mz2_atomic_owners.find_one({"_id": OWNER}) == controls
