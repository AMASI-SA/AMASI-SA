"""Iter-110 — Ad-account delete button + visibility toggle.

Behaviour verified:
  • DELETE /api/ad-accounts/{cp_id} succeeds for an account with zero
    balance and zero open debt.
  • DELETE refuses when the account has open debt > 0.
  • DELETE refuses when the account has balance > 0.
  • Settings GET/PUT round-trip the new `ad_account_allow_delete` flag.
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
    email = f"del110-{suffix}@example.com"
    pwd = "T#110a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "Del"}, timeout=10)
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


def test_settings_toggle_round_trip(ctx):
    """Setting `ad_account_allow_delete=True` persists and is returned."""
    s = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10).json()
    assert s.get("ad_account_allow_delete") is False  # default

    # Save with the toggle on (we MUST include payment_methods + shipping_companies
    # because SettingsIn requires them — pull them from the current settings).
    requests.put(f"{BASE_URL}/api/settings",
                 json={"payment_methods": s["payment_methods"],
                       "shipping_companies": s["shipping_companies"],
                       "ad_account_allow_delete": True},
                 headers=ctx["hdr"], timeout=10)
    s2 = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10).json()
    assert s2["ad_account_allow_delete"] is True

    # Other unrelated flags must not flip
    assert s2["settlements_allow_delete"] == s["settlements_allow_delete"]


def test_delete_zero_balance_zero_debt_account(ctx):
    """A fresh ad-account with no spend can be deleted."""
    cp = _make_account(ctx, "Snap to delete", external_id="acc_DEL")
    r = requests.delete(f"{BASE_URL}/api/ad-accounts/{cp}",
                        headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    # Confirm removal
    listing = requests.get(f"{BASE_URL}/api/ad-accounts",
                           headers=ctx["hdr"], timeout=10).json()
    assert all(it["id"] != cp for it in listing["items"])


def test_delete_blocked_when_balance_positive(ctx):
    """Top up the account → DELETE must be rejected by the server."""
    cp = _make_account(ctx, "Snap with balance", external_id="acc_BAL")
    # We need a bank account to top up from
    bank_r = requests.post(f"{BASE_URL}/api/accounts",
                           json={"account_type": "bank", "name": "Test Bank",
                                 "current_balance": 1000},
                           headers=ctx["hdr"], timeout=10)
    assert bank_r.status_code == 200, bank_r.text
    bank_id = bank_r.json()["id"]
    tu = requests.post(f"{BASE_URL}/api/ad-accounts/{cp}/topup",
                       json={"amount": 500, "paid_from_account_id": bank_id,
                             "transaction_date": "2026-06-08",
                             "description": "test topup"},
                       headers=ctx["hdr"], timeout=10)
    assert tu.status_code == 200, tu.text

    r = requests.delete(f"{BASE_URL}/api/ad-accounts/{cp}",
                        headers=ctx["hdr"], timeout=10)
    assert r.status_code == 400
    assert "رصيد" in r.json()["detail"]


def test_delete_blocked_when_open_debt(ctx):
    """A spend that exceeds balance creates open debt → DELETE refused."""
    cp = _make_account(ctx, "Snap with debt", external_id="acc_DEBT")
    sp = requests.post(f"{BASE_URL}/api/ad-accounts/{cp}/spend",
                       json={"amount": 200, "spend_date": "2026-06-08",
                             "description": "creates debt"},
                       headers=ctx["hdr"], timeout=10)
    assert sp.status_code == 200, sp.text
    r = requests.delete(f"{BASE_URL}/api/ad-accounts/{cp}",
                        headers=ctx["hdr"], timeout=10)
    assert r.status_code == 400
    assert "مديونية" in r.json()["detail"]
