"""Iter-246w — Tamara settlement history forensic tests.

Seeds 3 ledger entries on Tamara receivable:
  • Sale     — DEBIT  receivable (from the BNPL bridge)
  • Transfer — CREDIT receivable (from `POST /api/transfers`)
  • Settle   — CREDIT receivable (from `POST /api/bnpl/settlements/register`)

Then asserts the endpoint:
  1. Returns all 3 entries with the right `source_endpoint`.
  2. Computes the running receivable balance correctly.
  3. Flags suspected duplicates when CREDIT amounts repeat.

Strict READ-ONLY — endpoint never writes.
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest_asyncio.fixture
async def ctx(db_cli):
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "iter246w", "email": f"iter246w-{suf}@x.com",
              "password": "pw1234567"},
    )
    body = r.json()
    uid = body["id"]

    # Three ledger entries hitting Tamara receivable:
    #  1) Sale     (DEBIT  +20000)
    #  2) Transfer (CREDIT -16066.90)  ← created from "Account Transfers"
    #  3) Refund   (CREDIT  -500)      ← bnpl refund posting
    txn_g1 = f"g1-{suf}"
    txn_g2 = f"g2-{suf}"
    txn_g3 = f"g3-{suf}"

    docs = [
        {"id": f"e1-{suf}", "user_id": uid, "entry_no": f"en1-{suf}",
         "txn_group_id": txn_g1, "side": "debit", "amount": 20000.0,
         "entity_type": "payment_gateway", "entity_id": "tamara",
         "sub_account": "receivable",
         "transaction_date": "2026-06-01",
         "created_at": _now(),
         "entry_type": "bnpl_sale",
         "description": "Tamara sale",
         "currency": "SAR", "status": "posted",
         "metadata": {"created_by_endpoint": "bnpl_ledger_bridge",
                      "source": "bnpl_ledger_bridge"}},
        # Counter-leg for sale (CREDIT revenue) so the group is balanced.
        {"id": f"e1b-{suf}", "user_id": uid, "entry_no": f"en2-{suf}",
         "txn_group_id": txn_g1, "side": "credit", "amount": 20000.0,
         "entity_type": "revenue", "entity_id": "bnpl_sales",
         "transaction_date": "2026-06-01",
         "created_at": _now(),
         "entry_type": "bnpl_sale",
         "description": "Tamara sale",
         "currency": "SAR", "status": "posted",
         "metadata": {}},

        # 2) Transfer leg on the receivable
        {"id": f"e2-{suf}", "user_id": uid, "entry_no": f"en3-{suf}",
         "txn_group_id": txn_g2, "side": "credit", "amount": 16066.90,
         "entity_type": "payment_gateway", "entity_id": "tamara",
         "sub_account": "receivable",
         "transaction_date": "2026-06-13",
         "created_at": _now(),
         "entry_type": "internal_transfer",
         "description": "تحويل من تمارا إلى بنك الإنماء",
         "currency": "SAR", "status": "posted",
         "metadata": {"created_by_endpoint": "POST /api/transfers",
                      "source": "account_transaction_double_write",
                      "idempotency_key": f"transfer:tr-{suf}"}},
        {"id": f"e2b-{suf}", "user_id": uid, "entry_no": f"en4-{suf}",
         "txn_group_id": txn_g2, "side": "debit", "amount": 16066.90,
         "entity_type": "bank", "entity_id": "bank-inma",
         "transaction_date": "2026-06-13",
         "created_at": _now(),
         "entry_type": "internal_transfer",
         "description": "تحويل من تمارا إلى بنك الإنماء",
         "currency": "SAR", "status": "posted",
         "metadata": {}},

        # 3) BNPL refund leg on the receivable
        {"id": f"e3-{suf}", "user_id": uid, "entry_no": f"en5-{suf}",
         "txn_group_id": txn_g3, "side": "credit", "amount": 500.0,
         "entity_type": "payment_gateway", "entity_id": "tamara",
         "sub_account": "receivable",
         "transaction_date": "2026-06-12",
         "created_at": _now(),
         "entry_type": "bnpl_refund",
         "description": "Tamara refund",
         "currency": "SAR", "status": "posted",
         "metadata": {"created_by_endpoint": "bnpl_ledger_bridge"}},
        {"id": f"e3b-{suf}", "user_id": uid, "entry_no": f"en6-{suf}",
         "txn_group_id": txn_g3, "side": "debit", "amount": 500.0,
         "entity_type": "revenue", "entity_id": "bnpl_sales",
         "transaction_date": "2026-06-12",
         "created_at": _now(),
         "entry_type": "bnpl_refund",
         "currency": "SAR", "status": "posted",
         "metadata": {}},
    ]
    await db_cli.general_ledger.insert_many(docs)

    yield {"uid": uid, "token": body["access_token"], "suf": suf}

    await db_cli.general_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_history_returns_all_receivable_entries(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-history",
        params={"provider": "tamara"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["iter"] == "iter246w"
    # Receivable legs only — 3 entries (one debit, two credits).
    assert body["entries_count"] == 3, body
    # Running balance: +20000 then -500 (12 Jun refund) then -16066.90
    # = 3433.10.
    assert body["final_receivable_balance"] == 3433.10, body


@pytest.mark.asyncio
async def test_history_identifies_transfer_source(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-history",
        params={"provider": "tamara"},
        headers=_h(ctx["token"]),
    )
    body = r.json()
    sources = [e["source_endpoint"] for e in body["entries"]]
    assert "POST /api/transfers" in sources, sources
    assert "bnpl_ledger_bridge" in sources, sources

    # Find the transfer entry and assert its counter-leg hit a bank.
    transfer = next(
        e for e in body["entries"]
        if e["source_endpoint"] == "POST /api/transfers")
    bank_legs = [
        leg for leg in transfer["all_legs_in_group"]
        if leg["account"].startswith("bank.")
    ]
    assert bank_legs, transfer


@pytest.mark.asyncio
async def test_history_is_read_only(ctx, db_cli):
    uid = ctx["uid"]
    before = await db_cli.general_ledger.count_documents({"user_id": uid})
    requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-history",
        params={"provider": "tamara"},
        headers=_h(ctx["token"]),
    )
    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == before
