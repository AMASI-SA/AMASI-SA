"""Iter-110 — Historical migration & opening balance for ad-accounts.

Hits the live backend (mirrors the convention used by iter108 tests).
Covered behaviour:
  • preview scopes per external_account_id (Snapchat multi-account).
  • account without external_account_id is blocked by default.
  • apply daily-mode writes per-day ledger rows; lump-mode writes one.
  • apply respects debt_mode=manual (no auto liability).
  • apply only touches account_ids passed in payload.
  • opening endpoint creates a dedicated ad_account_opening liability;
    setting opening_debt=0 clears it.
"""
import os
import uuid

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _mdb():
    load_dotenv("/app/backend/.env")
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter110-{suffix}@example.com"
    pwd = "T#110a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "Mig"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb()}


def _make_account(ctx, name, provider="snapchat", external_id=None):
    payload = {"name": name, "ad_provider": provider, "force": True}
    if external_id:
        payload["external_account_id"] = external_id
    r = requests.post(f"{BASE_URL}/api/ad-accounts",
                      json=payload, headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_snap(ctx, ad_account_id, date, spend):
    ctx["db"].snapchat_account_daily.insert_one({
        "user_id": ctx["uid"], "ad_account_id": ad_account_id,
        "date": date, "spend": float(spend), "spend_sar": float(spend),
    })


def test_preview_scopes_per_external_id(ctx):
    a1 = _make_account(ctx, "Snap A", external_id="acc_A")
    a2 = _make_account(ctx, "Snap B", external_id="acc_B")
    _seed_snap(ctx, "acc_A", "2026-06-01", 100)
    _seed_snap(ctx, "acc_A", "2026-06-02", 200)
    _seed_snap(ctx, "acc_B", "2026-06-01", 50)

    r = requests.post(f"{BASE_URL}/api/ad-accounts/migration/preview",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-30"},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    by_id = {a["id"]: a for a in r.json()["accounts"]}
    assert by_id[a1]["period_spend"] == 300.0
    assert by_id[a1]["days_with_data"] == 2
    assert by_id[a2]["period_spend"] == 50.0
    assert by_id[a1]["blocked_by_default"] is False
    assert by_id[a2]["blocked_by_default"] is False


def test_preview_blocks_account_without_external_id(ctx):
    cp = _make_account(ctx, "Snap orphan", external_id=None)
    _seed_snap(ctx, "acc_X", "2026-06-01", 100)
    r = requests.post(f"{BASE_URL}/api/ad-accounts/migration/preview",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-30"},
                      headers=ctx["hdr"], timeout=10)
    item = [a for a in r.json()["accounts"] if a["id"] == cp][0]
    assert item["blocked_by_default"] is True
    assert item["external_account_id"] is None
    assert any("Ad Account ID" in w for w in item["warnings"])


def test_apply_daily_mode_per_day_rows(ctx):
    cp = _make_account(ctx, "Snap D", external_id="acc_D")
    _seed_snap(ctx, "acc_D", "2026-06-01", 100)
    _seed_snap(ctx, "acc_D", "2026-06-02", 200)
    _seed_snap(ctx, "acc_D", "2026-06-03", 50)

    r = requests.post(f"{BASE_URL}/api/ad-accounts/migration/apply",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-30",
                            "mode": "daily", "account_ids": [cp]},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["ok"]
    assert res["rows_posted"] == 3
    assert res["total_spend"] == 350.0
    assert res["debt_created"] == 350.0
    rows = list(ctx["db"].ad_account_ledger.find(
        {"user_id": ctx["uid"], "counterparty_id": cp, "type": "spend"}
    ))
    assert len(rows) == 3
    assert {r["date"] for r in rows} == {"2026-06-01", "2026-06-02", "2026-06-03"}


def test_apply_lump_mode_single_row(ctx):
    cp = _make_account(ctx, "Snap L", external_id="acc_L")
    _seed_snap(ctx, "acc_L", "2026-07-01", 80)
    _seed_snap(ctx, "acc_L", "2026-07-05", 120)

    r = requests.post(f"{BASE_URL}/api/ad-accounts/migration/apply",
                      json={"from_date": "2026-07-01", "to_date": "2026-07-31",
                            "mode": "lump", "account_ids": [cp]},
                      headers=ctx["hdr"], timeout=10)
    res = r.json()["results"][0]
    assert res["ok"]
    assert res["rows_posted"] == 1
    assert res["total_spend"] == 200.0
    rows = list(ctx["db"].ad_account_ledger.find(
        {"user_id": ctx["uid"], "counterparty_id": cp, "type": "spend"}
    ))
    assert len(rows) == 1


def test_apply_respects_manual_mode(ctx):
    cp = _make_account(ctx, "Snap M", external_id="acc_M")
    # Switch to manual
    requests.put(f"{BASE_URL}/api/ad-accounts/{cp}/settings",
                 json={"debt_mode": "manual"}, headers=ctx["hdr"], timeout=10)
    _seed_snap(ctx, "acc_M", "2026-06-10", 150)

    r = requests.post(f"{BASE_URL}/api/ad-accounts/migration/apply",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-30",
                            "mode": "daily", "account_ids": [cp]},
                      headers=ctx["hdr"], timeout=10)
    res = r.json()["results"][0]
    assert res["ok"]
    assert res["total_spend"] == 150.0
    assert res["debt_created"] == 0.0
    liab = ctx["db"].liabilities.count_documents(
        {"user_id": ctx["uid"], "counterparty_id": cp, "kind": "ad_account"}
    )
    assert liab == 0


def test_opening_creates_dedicated_liability(ctx):
    cp = _make_account(ctx, "Snap O", external_id="acc_O")
    r = requests.put(f"{BASE_URL}/api/ad-accounts/{cp}/opening",
                     json={"opening_balance": 500.0, "opening_debt": 1200.0,
                           "start_date": "2026-05-01", "method": "auto",
                           "notes": "Opening from May"},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["balance"] == 500.0
    assert s["open_debt"] == 1200.0
    liabs = list(ctx["db"].liabilities.find(
        {"user_id": ctx["uid"], "counterparty_id": cp}
    ))
    assert len(liabs) == 1
    assert liabs[0]["source"] == "ad_account_opening"
    assert liabs[0]["expected_amount"] == 1200.0
    assert liabs[0]["due_date"] == "2026-05-01"

    # Clear opening_debt
    requests.put(f"{BASE_URL}/api/ad-accounts/{cp}/opening",
                 json={"opening_debt": 0}, headers=ctx["hdr"], timeout=10)
    cleared = ctx["db"].liabilities.count_documents(
        {"user_id": ctx["uid"], "counterparty_id": cp, "source": "ad_account_opening"}
    )
    assert cleared == 0


def test_apply_only_selected_accounts(ctx):
    a = _make_account(ctx, "Snap S1", external_id="acc_S1")
    b = _make_account(ctx, "Snap S2", external_id="acc_S2")
    _seed_snap(ctx, "acc_S1", "2026-06-01", 100)
    _seed_snap(ctx, "acc_S2", "2026-06-01", 999)

    r = requests.post(f"{BASE_URL}/api/ad-accounts/migration/apply",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-30",
                            "mode": "lump", "account_ids": [a]},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200
    res = r.json()["results"]
    assert len(res) == 1
    assert res[0]["id"] == a

    b_ledger = ctx["db"].ad_account_ledger.count_documents(
        {"user_id": ctx["uid"], "counterparty_id": b}
    )
    assert b_ledger == 0
