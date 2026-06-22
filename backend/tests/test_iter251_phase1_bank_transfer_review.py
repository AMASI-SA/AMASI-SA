"""Iter-251 · Phase 1 — Bank Transfer Review MVP tests.

Validates the lifecycle of a manual review entry through:
  • create → list → summary → confirm → idempotency (cannot re-confirm)
  • create → confirm-with-difference (received < expected)
  • create → reject (must include note)
  • idempotency: provider-source duplicates blocked by unique key.
"""
import os
import sys
import uuid

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from bank_transfer_review_routes import (  # noqa: E402
    make_bank_transfer_review_router, _r,
)


@pytest.fixture
def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


def _user(uid):
    return {"id": uid, "name": "Tester", "email": f"{uid}@t.local"}


async def _stub_current_user(uid):
    return _user(uid)


def _router_for(db, uid):
    """Build router with a static current_user dep so endpoints can
    be invoked through their internal handlers in tests."""
    async def dep():
        return _user(uid)
    return make_bank_transfer_review_router(db, dep)


def _ep(router, method, path):
    """Locate the bound endpoint function on the router for a path."""
    for r in router.routes:
        if r.path.endswith(path) and method in (r.methods or set()):
            return r.endpoint
    raise AssertionError(f"endpoint {method} {path} not found")


@pytest.mark.asyncio
async def test_phase1_create_and_confirm_exact(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    router = _router_for(db, uid)
    from bank_transfer_review_routes import ReviewCreateIn, ReviewConfirmIn

    create = _ep(router, "POST", "/bank-transfer-review")
    confirm = _ep(router, "POST", "/bank-transfer-review/{rid}/confirm")
    summary = _ep(router, "GET",  "/bank-transfer-review/summary")

    doc = await create(
        ReviewCreateIn(source_type="manual", source_id="T1",
                       source_account_name="Test", target_bank_id="BK",
                       target_bank_name="Bank X", expected_amount=1000,
                       transfer_date="2026-02-22"),
        _user(uid),
    )
    assert doc["status"] == "pending"
    assert doc["received_amount"] is None

    summed = await summary(_user(uid))
    assert summed["by_status"]["pending"]["count"] == 1

    confirmed = await confirm(doc["id"], ReviewConfirmIn(), _user(uid))
    assert confirmed["status"] == "confirmed"
    assert confirmed["received_amount"] == 1000
    assert confirmed["difference"] == 0
    assert confirmed["ledger_txn_group_id"]

    # Idempotency: can't re-confirm.
    with pytest.raises(HTTPException) as exc:
        await confirm(doc["id"], ReviewConfirmIn(), _user(uid))
    assert exc.value.status_code == 400

    # Cleanup.
    await db.bank_transfer_reviews.delete_many({"user_id": uid})
    await db.general_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase1_confirm_with_difference_keeps_residual(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    router = _router_for(db, uid)
    from bank_transfer_review_routes import (
        ReviewCreateIn, ReviewConfirmDiffIn,
    )
    create = _ep(router, "POST", "/bank-transfer-review")
    diff = _ep(router, "POST",
               "/bank-transfer-review/{rid}/confirm-with-difference")

    doc = await create(
        ReviewCreateIn(source_type="manual", source_id="T2",
                       source_account_name="A", target_bank_id="BK",
                       target_bank_name="B", expected_amount=10000,
                       transfer_date="2026-02-22"),
        _user(uid),
    )
    confirmed = await diff(
        doc["id"], ReviewConfirmDiffIn(received_amount=9990),
        _user(uid),
    )
    assert confirmed["status"] == "confirmed_with_difference"
    assert confirmed["received_amount"] == 9990
    assert confirmed["difference"] == -10.0
    assert confirmed["ledger_txn_group_id"]

    # Diff > expected → 400.
    doc2 = await create(
        ReviewCreateIn(source_type="manual", source_id="T2-X",
                       source_account_name="A", target_bank_id="BK",
                       target_bank_name="B", expected_amount=100,
                       transfer_date="2026-02-22"),
        _user(uid),
    )
    with pytest.raises(HTTPException) as exc:
        await diff(doc2["id"], ReviewConfirmDiffIn(received_amount=200),
                   _user(uid))
    assert exc.value.status_code == 400

    await db.bank_transfer_reviews.delete_many({"user_id": uid})
    await db.general_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase1_reject_requires_note(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    router = _router_for(db, uid)
    from bank_transfer_review_routes import (
        ReviewCreateIn, ReviewRejectIn,
    )
    create = _ep(router, "POST", "/bank-transfer-review")
    reject = _ep(router, "POST", "/bank-transfer-review/{rid}/reject")

    doc = await create(
        ReviewCreateIn(source_type="manual", source_id="T3",
                       source_account_name="A", target_bank_id="BK",
                       target_bank_name="B", expected_amount=500,
                       transfer_date="2026-02-22"),
        _user(uid),
    )
    rejected = await reject(
        doc["id"], ReviewRejectIn(review_note="duplicate"),
        _user(uid),
    )
    assert rejected["status"] == "rejected"
    assert rejected["review_note"] == "duplicate"

    # Cannot reject again.
    with pytest.raises(HTTPException) as exc:
        await reject(doc["id"], ReviewRejectIn(review_note="x"),
                     _user(uid))
    assert exc.value.status_code == 400

    await db.bank_transfer_reviews.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase1_idempotency_provider_source(db):
    """A provider-source duplicate (same source_type+source_id+
    target_bank_id) must be blocked with 409."""
    uid = f"u_{uuid.uuid4().hex[:6]}"
    router = _router_for(db, uid)
    from bank_transfer_review_routes import ReviewCreateIn

    create = _ep(router, "POST", "/bank-transfer-review")
    await create(
        ReviewCreateIn(source_type="salla", source_id="SETTLE-X",
                       source_account_name="Salla", target_bank_id="BK",
                       target_bank_name="B", expected_amount=100,
                       transfer_date="2026-02-22"),
        _user(uid),
    )
    with pytest.raises(HTTPException) as exc:
        await create(
            ReviewCreateIn(source_type="salla", source_id="SETTLE-X",
                           source_account_name="Salla",
                           target_bank_id="BK", target_bank_name="B",
                           expected_amount=100,
                           transfer_date="2026-02-22"),
            _user(uid),
        )
    assert exc.value.status_code == 409

    # But manual duplicates are allowed (different rule).
    await create(
        ReviewCreateIn(source_type="manual", source_id="X",
                       source_account_name="M", target_bank_id="BK",
                       target_bank_name="B", expected_amount=50,
                       transfer_date="2026-02-22"),
        _user(uid),
    )
    await create(
        ReviewCreateIn(source_type="manual", source_id="X",
                       source_account_name="M", target_bank_id="BK",
                       target_bank_name="B", expected_amount=50,
                       transfer_date="2026-02-22"),
        _user(uid),
    )
    cnt = await db.bank_transfer_reviews.count_documents(
        {"user_id": uid, "source_type": "manual"})
    assert cnt == 2

    await db.bank_transfer_reviews.delete_many({"user_id": uid})


def test_r_helper():
    assert _r(None) == 0.0
    assert _r("3.456") == 3.46
    assert _r(10) == 10.0
