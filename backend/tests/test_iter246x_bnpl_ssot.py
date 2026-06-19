"""Iter-246x — BNPL SSOT enforcement bundle.

Covers:
  1. Block transfers on Tamara/Tabby provider accounts.
  2. Duplicate-period guard on settlement registration.
  3. Invoice-day guard (Tamara=Sat, Tabby=Mon).
  4. Settlement health endpoint.
  5. Read-only health endpoint never writes.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

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
        json={"name": "iter246x", "email": f"iter246x-{suf}@x.com",
              "password": "pw1234567"},
    )
    body = r.json()
    uid = body["id"]

    # Seed accounts: 1 BNPL wallet (tamara) + 1 bank.
    tamara_acc_id = f"acc-tamara-{suf}"
    bank_acc_id = f"acc-bank-{suf}"
    await db_cli.accounts.insert_many([
        {"id": tamara_acc_id, "user_id": uid, "name": "تمارا",
         "currency": "SAR", "status": "active",
         "current_balance": 5000.0,
         "account_type": "payment_gateway",
         "provider_name": "tamara",
         "normalized_payment_method": "buy_now_pay_later"},
        {"id": bank_acc_id, "user_id": uid, "name": "بنك الإنماء",
         "currency": "SAR", "status": "active",
         "current_balance": 0.0,
         "account_type": "bank"},
    ])

    yield {"uid": uid, "token": body["access_token"],
           "tamara_acc_id": tamara_acc_id,
           "bank_acc_id": bank_acc_id, "suf": suf}

    await db_cli.accounts.delete_many({"user_id": uid})
    await db_cli.transfers.delete_many({"user_id": uid})
    await db_cli.account_transactions.delete_many({"user_id": uid})
    await db_cli.general_ledger.delete_many({"user_id": uid})


# ───────────────────────── 1. Transfer block ──────────────────────


@pytest.mark.asyncio
async def test_transfer_from_tamara_account_is_blocked(ctx):
    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={"from_account_id": ctx["tamara_acc_id"],
              "to_account_id": ctx["bank_acc_id"],
              "amount": 100.0,
              "transfer_date": datetime.now(timezone.utc).date()
                  .isoformat()},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 400, r.text
    assert "BNPL" in r.text or "تسويات" in r.text


@pytest.mark.asyncio
async def test_transfer_to_tamara_account_is_blocked(ctx):
    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={"from_account_id": ctx["bank_acc_id"],
              "to_account_id": ctx["tamara_acc_id"],
              "amount": 100.0,
              "transfer_date": datetime.now(timezone.utc).date()
                  .isoformat()},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 400, r.text
    assert "Tamara/Tabby" in r.text or "تسويات" in r.text


# ────────────────────── 2. Invoice-day guard ──────────────────────


@pytest.mark.asyncio
async def test_invoice_day_guard_blocks_future_period(ctx):
    """Period_to in the FUTURE → eligible_iso is also future → save
    must be blocked with the invoice-day error."""
    far_future = (datetime.now(timezone.utc) + timedelta(days=30))\
        .date().isoformat()
    r = requests.post(
        f"{BASE_URL}/api/bnpl/settlements/register",
        json={"provider": "tamara",
              "bank_account_id": ctx["bank_acc_id"],
              "transferred_amount": 100.0,
              "settlement_reference": f"REF-{ctx['suf']}",
              "settlement_date": datetime.now(timezone.utc).date()
                  .isoformat(),
              "period_from": far_future,
              "period_to": far_future},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert "فاتورة المزود" in body.get("detail", "")


# ───────────────── 3. Duplicate-period guard ──────────────────────


@pytest.mark.asyncio
async def test_duplicate_period_settlement_is_rejected(ctx, db_cli):
    """Seed a `bnpl_settlement` ledger entry for a past period,
    then attempting to register another settlement for the SAME
    period must be rejected with 409."""
    uid = ctx["uid"]
    suf = ctx["suf"]
    period_from = "2026-05-23"
    period_to = "2026-05-29"
    txn_g = f"existing-{suf}"

    # Insert an EXISTING settlement leg (the CREDIT close-out) so
    # `_find_existing_period_settlement` will spot it.
    await db_cli.general_ledger.insert_one({
        "id": f"ex-{suf}",
        "entry_no": f"ex-en-{suf}",
        "user_id": uid,
        "txn_group_id": txn_g,
        "entry_type": "bnpl_settlement",
        "status": "posted",
        "side": "credit",
        "amount": 1000.0,
        "currency": "SAR",
        "entity_type": "payment_gateway",
        "entity_id": "tamara",
        "sub_account": "receivable",
        "transaction_date": "2026-05-30",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"provider": "tamara",
                     "settlement_reference": "OLD-REF",
                     "settlement_date": "2026-05-30",
                     "transferred_amount": 1000.0,
                     "period_from": period_from,
                     "period_to": period_to},
    })

    r = requests.post(
        f"{BASE_URL}/api/bnpl/settlements/register",
        json={"provider": "tamara",
              "bank_account_id": ctx["bank_acc_id"],
              "transferred_amount": 500.0,
              "settlement_reference": f"NEW-{suf}",
              "settlement_date": "2026-05-31",
              "period_from": period_from,
              "period_to": period_to},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert "مسجلة مسبقاً" in body.get("detail", "")
    assert "OLD-REF" in body.get("detail", "")


# ───────────────── 4. Health endpoint ─────────────────────────────


@pytest.mark.asyncio
async def test_health_endpoint_returns_both_providers(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/bnpl-settlement-health?provider=all",
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["iter"] == "iter246x"
    provs = [p["provider"] for p in body["providers"]]
    assert "tamara" in provs and "tabby" in provs


@pytest.mark.asyncio
async def test_health_endpoint_lists_recent_settlements(ctx, db_cli):
    uid = ctx["uid"]
    suf = ctx["suf"]
    await db_cli.general_ledger.insert_one({
        "id": f"set-{suf}",
        "entry_no": f"set-en-{suf}",
        "user_id": uid,
        "txn_group_id": f"set-grp-{suf}",
        "entry_type": "bnpl_settlement",
        "status": "posted",
        "side": "credit",
        "amount": 250.0,
        "currency": "SAR",
        "entity_type": "payment_gateway",
        "entity_id": "tamara",
        "sub_account": "receivable",
        "transaction_date": "2026-05-30",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"provider": "tamara",
                     "settlement_reference": "REF-SET",
                     "period_from": "2026-05-23",
                     "period_to": "2026-05-29",
                     "transferred_amount": 250.0},
    })
    r = requests.get(
        f"{BASE_URL}/api/audit/bnpl-settlement-health?provider=tamara",
        headers=_h(ctx["token"]),
    )
    body = r.json()
    tam = body["providers"][0]
    assert tam["last_settlement"] is not None
    assert tam["last_settlement"]["settlement_reference"] == "REF-SET"
    assert any(
        s["settlement_reference"] == "REF-SET"
        for s in tam["recent_settlements"]
    )


@pytest.mark.asyncio
async def test_health_endpoint_is_read_only(ctx, db_cli):
    uid = ctx["uid"]
    before = await db_cli.general_ledger.count_documents({"user_id": uid})
    requests.get(
        f"{BASE_URL}/api/audit/bnpl-settlement-health?provider=all",
        headers=_h(ctx["token"]),
    )
    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == before
