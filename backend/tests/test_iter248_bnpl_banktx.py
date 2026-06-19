"""Iter-248 — Bank-statement mirror for BNPL settlements.

Verifies the 5 guarantees the merchant asked for:
  1. No additional ledger entry is created.
  2. No `current_balance` mutation by the mirror.
  3. Re-running Apply is idempotent.
  4. account_transactions row carries direction=in + provider + ref.
  5. Same behaviour for Tamara AND Tabby.
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

_BD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BD, ".env"))
load_dotenv(os.path.join(_BD, "..", "frontend", ".env"))

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO = os.environ["MONGO_URL"]
DB = os.environ["DB_NAME"]


def _h(t): return {"Authorization": f"Bearer {t}"}


@pytest_asyncio.fixture
async def db_cli():
    c = AsyncIOMotorClient(MONGO); yield c[DB]; c.close()


@pytest_asyncio.fixture
async def ctx(db_cli):
    suf = uuid.uuid4().hex[:8]
    r = requests.post(f"{BASE}/api/auth/register", json={
        "name": "iter248", "email": f"i248-{suf}@x.com",
        "password": "pw1234567"})
    uid = r.json()["id"]
    bank_id = f"bank-{suf}"
    now_iso = datetime.now(timezone.utc).isoformat()

    # Bank account
    await db_cli.accounts.insert_one({
        "id": bank_id, "user_id": uid, "name": "بنك الإنماء",
        "account_type": "bank", "currency": "SAR",
        "current_balance": 1000.0, "status": "active",
    })

    # Seed two posted bnpl_settlement txn_groups (one tamara, one
    # tabby) that DO have a bank leg but NO account_transactions row.
    for n, (prov, ref, amt) in enumerate([
        ("tamara", f"TAM-{suf}", 100.0),
        ("tabby",  f"TAB-{suf}", 50.0),
    ]):
        grp = f"grp-{prov}-{suf}"
        await db_cli.general_ledger.insert_many([
            {"id": f"gl-bank-{prov}-{suf}", "user_id": uid,
             "entry_no": 100 + n*2,
             "txn_group_id": grp, "side": "debit", "amount": amt,
             "entity_type": "bank", "entity_id": bank_id,
             "sub_account": "balance",
             "entry_type": "bnpl_settlement", "status": "posted",
             "currency": "SAR",
             "transaction_date": "2026-06-16",
             "created_at": now_iso,
             "metadata": {"provider": prov,
                          "settlement_reference": ref,
                          "transferred_amount": amt,
                          "bank_account_id": bank_id,
                          "bank_account_name": "بنك الإنماء",
                          "settlement_date": "2026-06-16"}},
            {"id": f"gl-r-{prov}-{suf}", "user_id": uid,
             "entry_no": 101 + n*2,
             "txn_group_id": grp, "side": "credit", "amount": amt,
             "entity_type": "payment_gateway", "entity_id": prov,
             "sub_account": "receivable",
             "entry_type": "bnpl_settlement", "status": "posted",
             "currency": "SAR",
             "transaction_date": "2026-06-16",
             "created_at": now_iso,
             "metadata": {"provider": prov,
                          "settlement_reference": ref,
                          "transferred_amount": amt,
                          "bank_account_id": bank_id}},
        ])
    yield {"uid": uid, "token": r.json()["access_token"],
           "suf": suf, "bank_id": bank_id}

    await db_cli.general_ledger.delete_many({"user_id": uid})
    await db_cli.account_transactions.delete_many({"user_id": uid})
    await db_cli.accounts.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_dry_run_finds_both_providers(ctx):
    r = requests.get(
        f"{BASE}/api/audit/bnpl-settlement-banktx-backfill-dry-run",
        headers=_h(ctx["token"]))
    assert r.status_code == 200
    b = r.json()
    assert b["missing_bank_transaction_count"] == 2
    assert _round(b["missing_bank_transaction_sum"]) == 150.0
    provs = {m["provider"] for m in b["missing"]}
    assert provs == {"tamara", "tabby"}


def _round(n): return round(float(n), 2)


@pytest.mark.asyncio
async def test_apply_inserts_only_account_transactions(ctx, db_cli):
    """Guarantees 1, 2, 4, 5: ledger untouched, balance untouched,
    bank txn fields correct, both providers covered."""
    uid = ctx["uid"]
    gl_before = await db_cli.general_ledger.count_documents(
        {"user_id": uid})
    bal_before = (await db_cli.accounts.find_one(
        {"user_id": uid, "id": ctx["bank_id"]}))["current_balance"]

    dr = requests.get(
        f"{BASE}/api/audit/bnpl-settlement-banktx-backfill-dry-run",
        headers=_h(ctx["token"])).json()
    apply = requests.post(
        f"{BASE}/api/admin/bnpl-settlement-banktx-backfill-apply",
        headers={**_h(ctx["token"]),
                 "X-Apply-Token": dr["apply_token"]}).json()
    assert apply["applied_count"] == 2
    assert _round(apply["applied_sum"]) == 150.0

    # Guarantee 1 — ledger count unchanged.
    gl_after = await db_cli.general_ledger.count_documents(
        {"user_id": uid})
    assert gl_after == gl_before

    # Guarantee 2 — current_balance untouched.
    bal_after = (await db_cli.accounts.find_one(
        {"user_id": uid, "id": ctx["bank_id"]}))["current_balance"]
    assert bal_after == bal_before

    # Guarantees 4 + 5 — rows exist for both providers with correct fields.
    for prov in ("tamara", "tabby"):
        row = await db_cli.account_transactions.find_one({
            "user_id": uid,
            "provider": prov,
            "transaction_type": "bnpl_settlement",
        })
        assert row is not None
        assert row["direction"] == "in"
        assert row["account_id"] == ctx["bank_id"]
        assert row["reference"] == (
            f"TAM-{ctx['suf']}" if prov == "tamara"
            else f"TAB-{ctx['suf']}")
        assert "تسوية" in row["description"]


@pytest.mark.asyncio
async def test_apply_is_idempotent(ctx, db_cli):
    """Guarantee 3 — re-running Apply doesn't duplicate."""
    dr = requests.get(
        f"{BASE}/api/audit/bnpl-settlement-banktx-backfill-dry-run",
        headers=_h(ctx["token"])).json()
    requests.post(
        f"{BASE}/api/admin/bnpl-settlement-banktx-backfill-apply",
        headers={**_h(ctx["token"]),
                 "X-Apply-Token": dr["apply_token"]})
    # Second dry-run reports 0 missing.
    dr2 = requests.get(
        f"{BASE}/api/audit/bnpl-settlement-banktx-backfill-dry-run",
        headers=_h(ctx["token"])).json()
    assert dr2["missing_bank_transaction_count"] == 0


@pytest.mark.asyncio
async def test_health_endpoint_summarises_state(ctx):
    dr = requests.get(
        f"{BASE}/api/audit/bnpl-settlement-banktx-backfill-dry-run",
        headers=_h(ctx["token"])).json()
    requests.post(
        f"{BASE}/api/admin/bnpl-settlement-banktx-backfill-apply",
        headers={**_h(ctx["token"]),
                 "X-Apply-Token": dr["apply_token"]})
    h = requests.get(
        f"{BASE}/api/audit/bnpl-settlement-banktx-health",
        headers=_h(ctx["token"])).json()
    assert h["settlements_in_ledger"] == 2
    assert h["settlements_with_bank_transaction"] == 2
    assert h["settlements_missing_bank_transaction"] == 0


@pytest.mark.asyncio
async def test_apply_token_rejected_without_header(ctx):
    r = requests.post(
        f"{BASE}/api/admin/bnpl-settlement-banktx-backfill-apply",
        headers=_h(ctx["token"]))
    assert r.status_code == 401
