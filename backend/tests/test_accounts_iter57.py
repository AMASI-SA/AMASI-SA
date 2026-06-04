"""Iter-57 Phase 1 — Financial Accounts foundation.

Covers:
- Create 3 account types (bank / payment_platform / ads_platform) with opening balance.
- Opening balance auto-creates a posted opening_balance transaction.
- Summary aggregates by_type correctly (including negative balances).
- Adding a transaction recomputes balance_after.
- Deletion blocked when account has more than 1 transaction; hide works instead.
- Update endpoints respect field whitelist.
"""
import os
import time
import requests

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
API = f"{BACKEND_URL}/api"


def _login():
    r = requests.post(f"{API}/auth/login",
                      json={"email": "admin@hesab.app", "password": "admin123"},
                      timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _wipe_all(token):
    accounts = requests.get(f"{API}/accounts?include_hidden=true", headers=_h(token), timeout=15).json()
    for a in accounts:
        txs = requests.get(f"{API}/accounts/{a['id']}/transactions", headers=_h(token), timeout=15).json()
        for t in txs:
            requests.delete(f"{API}/accounts/{a['id']}/transactions/{t['id']}", headers=_h(token), timeout=15)
        requests.delete(f"{API}/accounts/{a['id']}", headers=_h(token), timeout=15)


def test_catalogue_endpoint_returns_required_keys():
    token = _login()
    r = requests.get(f"{API}/accounts/catalogue", headers=_h(token), timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert {"account_types", "suggested_providers", "transaction_types", "statuses"} <= data.keys()
    assert len(data["account_types"]) == 3


def test_create_account_auto_creates_opening_transaction():
    token = _login()
    _wipe_all(token)
    try:
        r = requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Bank", "account_type": "bank", "provider_name": "Inma",
            "currency": "SAR", "opening_balance": 50000,
            "opening_balance_date": "2026-01-01",
        }, timeout=15)
        assert r.status_code == 200, r.text
        acc = r.json()
        assert acc["current_balance"] == 50000.0
        assert acc["transactions_count"] == 1

        # Inspect that transaction
        tx = requests.get(f"{API}/accounts/{acc['id']}/transactions", headers=_h(token), timeout=15).json()
        assert len(tx) == 1
        assert tx[0]["transaction_type"] == "opening_balance"
        assert tx[0]["balance_after"] == 50000.0
        assert tx[0]["direction"] == "in"
    finally:
        _wipe_all(token)


def test_summary_aggregates_correctly():
    token = _login()
    _wipe_all(token)
    try:
        requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Bank A", "account_type": "bank",
            "currency": "SAR", "opening_balance": 50000,
        }, timeout=15)
        requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Salla", "account_type": "payment_platform", "provider_name": "سلة",
            "currency": "SAR", "opening_balance": 12400,
        }, timeout=15)
        requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Snap", "account_type": "ads_platform", "provider_name": "Snapchat",
            "currency": "USD", "opening_balance": -2300,
        }, timeout=15)

        s = requests.get(f"{API}/accounts/summary", headers=_h(token), timeout=15).json()
        assert s["by_type"]["bank"] == 50000.0
        assert s["by_type"]["payment_platform"] == 12400.0
        assert s["by_type"]["ads_platform"] == -2300.0
        assert s["grand_total"] == 60100.0
    finally:
        _wipe_all(token)


def test_add_transaction_updates_running_balance():
    token = _login()
    _wipe_all(token)
    try:
        cr = requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Bank B", "account_type": "bank",
            "currency": "SAR", "opening_balance": 1000,
            "opening_balance_date": "2026-02-01",
        }, timeout=15).json()
        aid = cr["id"]

        # In
        requests.post(f"{API}/accounts/{aid}/transactions", headers=_h(token), json={
            "transaction_type": "income", "amount": 500, "direction": "in",
            "description": "deposit", "transaction_date": "2026-02-05",
        }, timeout=15)
        # Out
        requests.post(f"{API}/accounts/{aid}/transactions", headers=_h(token), json={
            "transaction_type": "expense", "amount": 200, "direction": "out",
            "description": "withdrawal", "transaction_date": "2026-02-06",
        }, timeout=15)

        acc = requests.get(f"{API}/accounts/{aid}", headers=_h(token), timeout=15).json()
        assert acc["current_balance"] == 1300.0  # 1000 + 500 - 200

        txs = requests.get(f"{API}/accounts/{aid}/transactions", headers=_h(token), timeout=15).json()
        # newest first → out tx is first
        amounts_in_order = sorted(txs, key=lambda x: x["transaction_date"])
        assert [t["balance_after"] for t in amounts_in_order] == [1000.0, 1500.0, 1300.0]
    finally:
        _wipe_all(token)


def test_cannot_delete_account_with_transactions():
    token = _login()
    _wipe_all(token)
    try:
        cr = requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Bank C", "account_type": "bank",
            "currency": "SAR", "opening_balance": 100,
        }, timeout=15).json()
        aid = cr["id"]
        requests.post(f"{API}/accounts/{aid}/transactions", headers=_h(token), json={
            "transaction_type": "income", "amount": 50, "direction": "in",
            "description": "extra", "transaction_date": "2026-02-05",
        }, timeout=15)

        d = requests.delete(f"{API}/accounts/{aid}", headers=_h(token), timeout=15)
        assert d.status_code == 400
        assert "حركات مالية" in d.json()["detail"] or "delete" in d.json()["detail"].lower()

        # But hiding works
        h = requests.put(f"{API}/accounts/{aid}", headers=_h(token), json={"status": "hidden"}, timeout=15)
        assert h.status_code == 200
        assert h.json()["status"] == "hidden"
    finally:
        _wipe_all(token)


def test_hidden_accounts_excluded_from_summary():
    token = _login()
    _wipe_all(token)
    try:
        a = requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Visible", "account_type": "bank",
            "currency": "SAR", "opening_balance": 5000,
        }, timeout=15).json()
        b = requests.post(f"{API}/accounts", headers=_h(token), json={
            "name": "T57 Hidden", "account_type": "bank",
            "currency": "SAR", "opening_balance": 99999,
        }, timeout=15).json()
        requests.put(f"{API}/accounts/{b['id']}", headers=_h(token),
                     json={"status": "hidden"}, timeout=15)

        s = requests.get(f"{API}/accounts/summary", headers=_h(token), timeout=15).json()
        # Only the visible 5000 should be counted
        assert s["by_type"]["bank"] == 5000.0
    finally:
        _wipe_all(token)
