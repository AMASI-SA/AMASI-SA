"""Iter-246l — Per-account Snapchat card applies FX + bank fees.

Merchant reported: aggregated card converted USD→SAR but the per-
account card showed «0.00 ر.س ≈ 420.65 USD».

Fixed in `/dashboard/snapchat-accounts-summary` by reading
`ads_currency_settings` + each counterparty's `currency` /
`apply_bank_commission` then converting per row.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def ctx():
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "t", "email": f"iter246l-{suf}@x.com",
              "password": "pw1234567"})
    h = _h(r.json()["access_token"])
    uid = r.json()["id"]
    return {"h": h, "uid": uid}


@pytest.mark.asyncio
async def test_per_account_card_applies_fx_and_bank(ctx):
    """Seed two snap accounts (USD + SAR) with 100 spend each and a
    bank commission of 2% — assert the per-account `spend` is now in
    SAR with fees applied, and totals match the sum."""
    h = ctx["h"]
    uid = ctx["uid"]

    # Set FX + bank fee through the public API so the production code
    # path is exercised.
    r = requests.put(
        f"{BASE_URL}/api/ads-currency-settings",
        headers=h,
        json={"usd_to_sar_rate": 3.75, "bank_commission_pct": 2.0})
    assert r.status_code == 200, r.text

    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DB_NAME]
        usd_id = str(uuid.uuid4())
        sar_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await db.counterparties.insert_many([
            {"id": usd_id, "user_id": uid, "name": "متجر USD",
             "kind": "ad_account", "ad_provider": "snapchat",
             "external_account_id": "snap-usd",
             "name_lower": "متجر usd",
             "currency": "USD", "apply_bank_commission": True,
             "created_at": now, "updated_at": now},
            {"id": sar_id, "user_id": uid, "name": "متجر SAR",
             "kind": "ad_account", "ad_provider": "snapchat",
             "external_account_id": "snap-sar",
             "name_lower": "متجر sar",
             "currency": "SAR", "apply_bank_commission": False,
             "created_at": now, "updated_at": now},
        ])
        today = datetime.now(timezone.utc).date().isoformat()
        await db.ad_account_ledger.insert_many([
            {"id": str(uuid.uuid4()), "user_id": uid,
             "counterparty_id": usd_id, "type": "spend",
             "amount": 100.0, "date": today, "created_at": now},
            {"id": str(uuid.uuid4()), "user_id": uid,
             "counterparty_id": sar_id, "type": "spend",
             "amount": 100.0, "date": today, "created_at": now},
        ])
    finally:
        client.close()

    r = requests.get(
        f"{BASE_URL}/api/dashboard/snapchat-accounts-summary",
        headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {a["id"]: a for a in body["accounts"]}
    usd_row = by_id[usd_id]
    sar_row = by_id[sar_id]

    # USD account: 100 × 3.75 × 1.02 = 382.5
    assert usd_row["spend_currency"] == "USD"
    assert usd_row["fx_rate_used"] == 3.75
    assert usd_row["bank_commission_pct_used"] == 2.0
    assert round(usd_row["spend"], 2) == 382.5
    assert round(usd_row["spend_sar"], 2) == 375.0
    assert round(usd_row["bank_fee_sar"], 2) == 7.5

    # SAR account: 100 (no conversion, no fee)
    assert sar_row["spend_currency"] == "SAR"
    assert sar_row["fx_rate_used"] == 1.0
    assert sar_row["bank_commission_pct_used"] == 0.0
    assert round(sar_row["spend"], 2) == 100.0
    assert round(sar_row["bank_fee_sar"], 2) == 0.0

    # Totals roll up correctly in SAR.
    t = body["totals"]
    assert round(t["spend_sar"], 2) == 482.5
    assert t["fx_rate_used"] == 3.75
