"""Iter-246y — Tamara refund backfill (Dry-Run + Apply) tests.

Validates:
  1. Dry-run classifies every refund correctly.
  2. Apply rejects without the correct X-Apply-Token.
  3. Apply with the correct token mirrors Tabby's exact ledger schema:
       Debit revenue.bnpl_sales, Credit payment_gateway.tamara.receivable.
  4. Re-running apply is a no-op (idempotent).
  5. Tabby refunds are NOT touched.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


@pytest_asyncio.fixture
async def db_cli():
    c = AsyncIOMotorClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest_asyncio.fixture
async def ctx(db_cli):
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "iter246y", "email": f"iter246y-{suf}@x.com",
              "password": "pw1234567"},
    )
    body = r.json()
    uid = body["id"]

    # Seed a Tamara sale ALREADY in the ledger so the underlying-sale
    # guard passes for our backfillable refund.
    pid = f"pid-{suf}"
    sale_idem = f"bnpl_sale:tamara:{pid}"
    await db_cli.general_ledger.insert_many([
        {"id": f"sale-{suf}", "user_id": uid,
         "entry_no": 1,
         "txn_group_id": f"sale-grp-{suf}",
         "entry_type": "bnpl_sale", "status": "posted",
         "side": "debit", "amount": 200.0, "currency": "SAR",
         "entity_type": "payment_gateway", "entity_id": "tamara",
         "sub_account": "receivable",
         "transaction_date": "2026-06-09",
         "created_at": datetime.now(timezone.utc).isoformat(),
         "metadata": {"idempotency_key": sale_idem,
                      "provider": "tamara"}},
        {"id": f"saleb-{suf}", "user_id": uid,
         "entry_no": 2,
         "txn_group_id": f"sale-grp-{suf}",
         "entry_type": "bnpl_sale", "status": "posted",
         "side": "credit", "amount": 200.0, "currency": "SAR",
         "entity_type": "revenue", "entity_id": "bnpl_sales",
         "transaction_date": "2026-06-09",
         "created_at": datetime.now(timezone.utc).isoformat(),
         "metadata": {}},
    ])

    # Insert the corresponding Tamara refund (ready_to_backfill).
    await db_cli.payment_refunds.insert_one({
        "id": f"rfd-{suf}", "user_id": uid, "provider": "tamara",
        "provider_refund_id": f"rid-{suf}",
        "provider_payment_id": pid,
        "order_reference_id": "ORDER-1",
        "order_number": "ORDER-1",
        "amount": 200.0, "currency": "SAR",
        "status": "fully_refunded",
        "refunded_at": "2026-06-12T20:00:00Z",
        "is_pre_accounting": False,
    })

    # And a Tabby refund — must remain untouched.
    await db_cli.payment_refunds.insert_one({
        "id": f"tab-{suf}", "user_id": uid, "provider": "tabby",
        "provider_refund_id": f"tab-rid-{suf}",
        "provider_payment_id": f"tab-pid-{suf}",
        "amount": 50.0, "currency": "SAR",
        "refunded_at": "2026-06-10T10:00:00Z",
    })

    yield {"uid": uid, "token": body["access_token"], "suf": suf,
           "pid": pid}

    await db_cli.payment_refunds.delete_many({"user_id": uid})
    await db_cli.general_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_dry_run_classifies_ready_refund(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-backfill-dry-run",
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["iter"] == "iter246y"
    assert body["ready_to_backfill_count"] == 1
    assert body["ready_to_backfill_sum"] == 200.0
    assert body["proposed_entries"][0]["amount"] == 200.0
    legs = body["proposed_entries"][0]["legs"]
    assert any(
        l["entity"] == "payment_gateway.tamara.receivable"
        and l["side"] == "credit"
        for l in legs
    )
    assert any(
        l["entity"] == "revenue.bnpl_sales"
        and l["side"] == "debit"
        for l in legs
    )


@pytest.mark.asyncio
async def test_apply_rejects_without_token(ctx):
    r = requests.post(
        f"{BASE_URL}/api/admin/tamara-refund-backfill-apply",
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_apply_with_token_creates_refund_entries(ctx, db_cli):
    uid = ctx["uid"]
    r1 = requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-backfill-dry-run",
        headers=_h(ctx["token"]),
    )
    token = r1.json()["apply_token"]

    r2 = requests.post(
        f"{BASE_URL}/api/admin/tamara-refund-backfill-apply",
        headers={**_h(ctx["token"]), "X-Apply-Token": token},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["applied_count"] == 1
    assert body["applied_sum"] == 200.0

    # Verify the ledger now has a Tamara bnpl_refund credit leg.
    leg = await db_cli.general_ledger.find_one({
        "user_id": uid,
        "entry_type": "bnpl_refund",
        "entity_type": "payment_gateway",
        "entity_id": "tamara",
        "sub_account": "receivable",
        "side": "credit",
    })
    assert leg is not None
    assert abs(float(leg["amount"]) - 200.0) <= 0.01


@pytest.mark.asyncio
async def test_apply_is_idempotent(ctx, db_cli):
    uid = ctx["uid"]
    # First run
    r1 = requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-backfill-dry-run",
        headers=_h(ctx["token"]),
    )
    token1 = r1.json()["apply_token"]
    requests.post(
        f"{BASE_URL}/api/admin/tamara-refund-backfill-apply",
        headers={**_h(ctx["token"]), "X-Apply-Token": token1},
    )

    # Second run — dry-run should report 0 ready.
    r2 = requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-backfill-dry-run",
        headers=_h(ctx["token"]),
    )
    body = r2.json()
    assert body["ready_to_backfill_count"] == 0


@pytest.mark.asyncio
async def test_tabby_refunds_untouched(ctx, db_cli):
    uid = ctx["uid"]
    # Dry-run + apply for Tamara should NOT post anything for Tabby.
    r1 = requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-backfill-dry-run",
        headers=_h(ctx["token"]),
    )
    token = r1.json()["apply_token"]
    requests.post(
        f"{BASE_URL}/api/admin/tamara-refund-backfill-apply",
        headers={**_h(ctx["token"]), "X-Apply-Token": token},
    )

    tabby_refund_legs = await db_cli.general_ledger.count_documents({
        "user_id": uid,
        "entry_type": "bnpl_refund",
        "entity_id": "tabby",
    })
    assert tabby_refund_legs == 0
