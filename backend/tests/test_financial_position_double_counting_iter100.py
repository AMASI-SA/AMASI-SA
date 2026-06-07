"""Iter-100 — Financial-Position double-counting fix.

Bug: `/api/liabilities/summary` was returning the GROSS historical order
amount (`expected_orders_balance`) as the platform asset, while bank
transfers OUT of the platform already credited the bank. This made the
total assets balloon by every transferred amount.

Fix: switch platforms to `current_balance` (the running ledger balance
after all transfers/refunds/settlements). Backward-compat: the legacy
key `payment_platforms_expected` is kept with the SAME (new) value.

This test reproduces the user's example:
  Tamara sales = 100,000
  Transfer    =  90,000 to bank
  Expected → platforms_remaining = 10,000 (NOT 100,000)
  Expected → assets.total == accounts/summary grand_total (no dup)
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _env(key: str) -> str:
    line = [ln for ln in open("/app/backend/.env").read().splitlines() if ln.startswith(f"{key}=")][0]
    return line.split("=", 1)[1].strip().strip('"')


@pytest.fixture
def mongo_db():
    return MongoClient(_env("MONGO_URL"))[_env("DB_NAME")]


def _new_user_with_bank():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter100-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#100test", "name": "FP Fix"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#100test"},
        timeout=10,
    )
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    bank = requests.post(
        f"{BASE_URL}/api/accounts",
        json={
            "name": "بنك التجريب", "account_type": "bank",
            "currency": "SAR", "opening_balance": 0,
            "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    ).json()
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=h, timeout=10).json()
    return {"headers": h, "bank_id": bank["id"], "uid": me["id"]}


def _seed_platform(uid, mongo, name="تمارا", balance=100000.0, payment_method="tamara"):
    """Seed a payment_platform account with gross order balance."""
    acc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mongo.accounts.insert_one({
        "id": acc_id, "user_id": uid,
        "name": name,
        "account_type": "payment_platform",
        "currency": "SAR",
        "opening_balance": 0.0,
        # Seeded as if 100k of orders flowed into the platform:
        "current_balance": balance,
        "expected_orders_balance": balance,
        "status": "active",
        "normalized_payment_method": payment_method,
        "created_at": now, "updated_at": now,
    })
    return acc_id


# ── 1) Single-platform reproduction of user's example ───────────────
def test_tamara_after_transfer_shows_only_remaining(mongo_db):
    """Tamara sales=100k, transferred 90k to bank.
       Platform remaining must be 10k, NOT 100k."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    platform_id = _seed_platform(ctx["uid"], mongo_db, name="تمارا", balance=100000.0)

    # Transfer 90k out of platform → bank
    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": platform_id,
            "to_account_id": ctx["bank_id"],
            "amount": 90000,
            "transfer_date": "2026-06-10",
            "reference": "TAMARA-OUT-1",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text

    s = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    a = s["assets"]
    # The NEW field shows the correct, post-transfer remaining
    assert a["payment_platforms_remaining"] == 10000.0, a
    # Legacy field keeps same (corrected) value for backward compat
    assert a["payment_platforms_expected"] == 10000.0, a
    # Bank received 90k
    assert a["banks"] == 90000.0, a
    # Total assets equals exactly 100k — no double-counting.
    assert a["total"] == 100000.0, a


# ── 2) Cross-check: totals match /accounts/summary ──────────────────
def test_assets_total_matches_accounts_summary(mongo_db):
    """The financial-position assets total MUST equal the accounts
    summary grand_total (no other moving parts in this isolated user)."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    _seed_platform(ctx["uid"], mongo_db, name="تمارا", balance=50000.0)
    _seed_platform(ctx["uid"], mongo_db, name="تابي",
                    balance=30000.0, payment_method="tabby")

    # Make one transfer
    tamara_id = mongo_db.accounts.find_one(
        {"user_id": ctx["uid"], "name": "تمارا"}, {"_id": 0, "id": 1}
    )["id"]
    requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": tamara_id,
            "to_account_id": ctx["bank_id"],
            "amount": 40000,
            "transfer_date": "2026-06-10",
            "reference": "TAM-MATCH",
        },
        headers=h, timeout=10,
    )

    fp = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    acc = requests.get(f"{BASE_URL}/api/accounts/summary", headers=h, timeout=10).json()

    # /accounts/summary uses current_balance everywhere.
    # fp.assets.total must equal acc.grand_total (no receivables here).
    assert fp["assets"]["receivables"] == 0
    assert fp["assets"]["total"] == acc["grand_total"], (fp, acc)

    # And the breakdown matches:
    assert fp["assets"]["banks"] == acc["by_type"]["bank"]
    assert fp["assets"]["payment_platforms_remaining"] == acc["by_type"]["payment_platform"]


# ── 3) Net position is no longer inflated ───────────────────────────
def test_net_position_is_not_inflated_by_transfers(mongo_db):
    """Before fix: every transfer inflated the net position by `amount`.
    After fix: net position is invariant under bank↔platform transfers."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    platform_id = _seed_platform(ctx["uid"], mongo_db, balance=80000.0)

    s0 = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    net_before = s0["net_position"]

    requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": platform_id,
            "to_account_id": ctx["bank_id"],
            "amount": 60000,
            "transfer_date": "2026-06-10",
            "reference": "INV-1",
        },
        headers=h, timeout=10,
    )

    s1 = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    net_after = s1["net_position"]

    # Transferring money between owned accounts must NOT change net position
    assert net_before == 80000.0
    assert net_after == 80000.0
    assert net_after == net_before, (s0, s1)


# ── 4) Reconciliation-page agreement (Iter-93 promise restored) ─────
def test_platform_remaining_matches_reconciliation_expected(mongo_db):
    """Platform remaining on financial-position must equal the
    `expected` value used on the reconciliation page for the same
    platform after the transfer is applied."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    platform_id = _seed_platform(ctx["uid"], mongo_db, name="تمارا", balance=100000.0)

    requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": platform_id,
            "to_account_id": ctx["bank_id"],
            "amount": 90000,
            "transfer_date": "2026-06-10",
            "reference": "RECON-1",
        },
        headers=h, timeout=10,
    )

    # Re-read account to confirm current_balance is 10k
    acc = requests.get(
        f"{BASE_URL}/api/accounts/{platform_id}", headers=h, timeout=10
    ).json()
    assert acc["current_balance"] == 10000.0

    # Financial-position platforms remaining must agree.
    s = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    assert s["assets"]["payment_platforms_remaining"] == acc["current_balance"]
