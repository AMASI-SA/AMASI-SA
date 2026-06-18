"""Iter-246i — SSOT for account balance across all surfaces.

The merchant reported:
  * /financial-movements/accounts-with-availability  → 73,525.86
  * /accounting/suppliers/list (used by supplier_pay) → 50,986.91
… for the SAME bank account.  Root cause: the Iter-245 form read the
stale `accounts.current_balance` document field, while everything
else used `account_balance_ssot()`.

This test asserts that AFTER Iter-246i:

  1. /accounts                                  ── SSOT
  2. /financial-movements/accounts-with-availability ── SSOT
  3. /diagnostics/account-balances              ── SSOT + drift report

all return the SAME `available_balance` for any given account, and
that the diagnostic endpoint reports `status == "ok"` (no drift) when
the stored value matches.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _r(n) -> float:
    return round(float(n or 0), 2)


@pytest.fixture(scope="module")
def ctx():
    suf = uuid.uuid4().hex[:8]
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": f"iter246i-{suf}@x.com",
                            "password": "pw1234567"})
    h = _h(r.json()["access_token"])

    r = requests.post(
        f"{BASE_URL}/api/accounts", headers=h,
        json={"name": "بنك الإنماء", "account_type": "bank",
              "opening_balance": 7352.86, "currency": "SAR"})
    bank_id = r.json()["id"]

    r = requests.post(
        f"{BASE_URL}/api/accounts", headers=h,
        json={"name": "الصندوق الرئيسي", "account_type": "cash",
              "opening_balance": 1000.0, "currency": "SAR"})
    cash_id = r.json()["id"]

    return {"h": h, "bank_id": bank_id, "cash_id": cash_id}


def _balances_from_each_surface(ctx, account_id):
    """Pull the displayed balance for ONE account from every consumer
    surface that the merchant interacts with."""
    h = ctx["h"]
    out = {}

    # Surface 1: GET /accounts (used by الأصول والحسابات & سداد مورد)
    r = requests.get(
        f"{BASE_URL}/api/accounts?account_type=bank&limit=200",
        headers=h)
    items = r.json() if isinstance(r.json(), list) else r.json().get(
        "items", [])
    items += (
        requests.get(f"{BASE_URL}/api/accounts?account_type=cash&limit=200",
                     headers=h).json()
        if account_id == ctx["cash_id"] else []
    )
    row = next((x for x in items if x["id"] == account_id), None)
    out["/accounts"] = _r(row["current_balance"]) if row else None

    # Surface 2: GET /financial-movements/accounts-with-availability
    r = requests.get(
        f"{BASE_URL}/api/financial-movements/accounts-with-availability",
        params={"amount": 0}, headers=h)
    row = next((x for x in r.json()["items"]
                if x["id"] == account_id), None)
    out["/accounts-with-availability"] = (
        _r(row["available_balance"]) if row else None)

    # Surface 3: /diagnostics/account-balances (audit)
    r = requests.get(
        f"{BASE_URL}/api/diagnostics/account-balances", headers=h)
    row = next((x for x in r.json()["accounts"]
                if x["account_id"] == account_id), None)
    out["/diagnostics/account-balances"] = (
        _r(row["ssot_balance"]) if row else None)
    return out


def test_balances_match_across_surfaces_at_baseline(ctx):
    for acc_id, expected in [
        (ctx["bank_id"], 7352.86),
        (ctx["cash_id"], 1000.00),
    ]:
        b = _balances_from_each_surface(ctx, acc_id)
        # Every surface returns the same value.
        vals = list(b.values())
        assert all(v == expected for v in vals), b


def test_diagnostic_reports_no_drift_after_clean_account(ctx):
    r = requests.get(
        f"{BASE_URL}/api/diagnostics/account-balances",
        headers=ctx["h"])
    body = r.json()
    assert body["ok"] is True
    rows = body["accounts"]
    drifted = [x for x in rows if x["status"] == "drift"]
    assert not drifted, drifted
    assert body["summary"]["drifted"] == 0


def test_diagnostic_endpoint_shape(ctx):
    """The diagnostic endpoint returns the expected shape and includes
    every bank/cash/payment_platform account with stored, ssot,
    ledger, difference, and status fields.  Used by the merchant to
    audit per-account drift."""
    r = requests.get(
        f"{BASE_URL}/api/diagnostics/account-balances",
        headers=ctx["h"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["iter"] == "iter246i"
    assert "summary" in body
    assert "accounts" in body
    assert isinstance(body["accounts"], list)

    expected_keys = {
        "account_id", "account_name", "account_type",
        "currency", "stored_balance", "ssot_balance",
        "ledger_balance", "difference", "status",
    }
    for row in body["accounts"]:
        assert expected_keys.issubset(row.keys()), row
        assert row["status"] in ("ok", "drift")
