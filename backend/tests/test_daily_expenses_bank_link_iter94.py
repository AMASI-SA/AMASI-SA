"""Iter-94 — Daily expenses linked to bank accounts.

Verifies the F1 gap is closed: when a daily operating expense is created
with `paid_from_account_id`, an out-flowing `account_transactions` row is
posted and the bank's `current_balance` decreases by exactly the amount.
Update reposts the tx; delete rolls it back.
"""
import os
import uuid

import pytest
import requests


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _new_user_with_bank():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter94-{suffix}@example.com"
    password = "Test#94"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "F1 Test"},
        timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    token = r.json().get("access_token") or r.json().get("token")
    h = {"Authorization": f"Bearer {token}"}

    r = requests.post(
        f"{BASE_URL}/api/accounts",
        json={
            "name": "بنك F1", "account_type": "bank",
            "currency": "SAR", "opening_balance": 10000.0,
            "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    )
    bank_id = r.json()["id"]
    return {"headers": h, "bank_id": bank_id}


def _bank_balance(headers, bank_id):
    r = requests.get(
        f"{BASE_URL}/api/accounts/{bank_id}", headers=headers, timeout=10
    )
    return r.json().get("current_balance")


def test_unlinked_expense_does_not_touch_bank():
    """Backward-compatibility: cash expense (no account) leaves bank alone."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    before = _bank_balance(h, ctx["bank_id"])

    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-01", "expense_type": "نقدي",
            "description": "بدون حساب", "amount": 50,
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("paid_from_account_id") is None
    assert body.get("linked_transaction_id") is None
    assert _bank_balance(h, ctx["bank_id"]) == before


def test_linked_expense_deducts_bank():
    ctx = _new_user_with_bank()
    h = ctx["headers"]

    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-02", "expense_type": "وقود",
            "description": "تعبئة", "amount": 200,
            "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["paid_from_account_id"] == ctx["bank_id"]
    assert body["linked_transaction_id"] is not None
    assert _bank_balance(h, ctx["bank_id"]) == 9800.0


def test_update_amount_reposts_tx():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-03", "expense_type": "صيانة",
            "amount": 100, "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    eid = r.json()["id"]
    assert _bank_balance(h, ctx["bank_id"]) == 9900.0

    # Update amount → bank reflects the new amount only
    r = requests.put(
        f"{BASE_URL}/api/operating-expenses/daily/{eid}",
        json={"amount": 300},
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 300
    assert _bank_balance(h, ctx["bank_id"]) == 9700.0


def test_update_unlink_account_restores_bank():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-04", "expense_type": "اشتراك",
            "amount": 75, "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    eid = r.json()["id"]
    assert _bank_balance(h, ctx["bank_id"]) == 9925.0

    # Unlink (paid_from_account_id = null) → bank should restore
    r = requests.put(
        f"{BASE_URL}/api/operating-expenses/daily/{eid}",
        json={"paid_from_account_id": None},
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert fresh.get("paid_from_account_id") in (None, "")
    assert _bank_balance(h, ctx["bank_id"]) == 10000.0


def test_update_switches_account():
    """Move the expense from bank A to bank B → both balances must adjust."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    # Add a second bank
    r = requests.post(
        f"{BASE_URL}/api/accounts",
        json={
            "name": "بنك آخر", "account_type": "bank", "currency": "SAR",
            "opening_balance": 5000.0, "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    )
    bank_b = r.json()["id"]

    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-05", "expense_type": "نقل",
            "amount": 250, "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    eid = r.json()["id"]
    assert _bank_balance(h, ctx["bank_id"]) == 9750.0
    assert _bank_balance(h, bank_b) == 5000.0

    # Switch to bank B
    r = requests.put(
        f"{BASE_URL}/api/operating-expenses/daily/{eid}",
        json={"paid_from_account_id": bank_b},
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    assert _bank_balance(h, ctx["bank_id"]) == 10000.0    # restored
    assert _bank_balance(h, bank_b) == 4750.0             # debited


def test_delete_rolls_back_bank():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-06", "expense_type": "طعام",
            "amount": 60, "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    eid = r.json()["id"]
    assert _bank_balance(h, ctx["bank_id"]) == 9940.0

    r = requests.delete(
        f"{BASE_URL}/api/operating-expenses/daily/{eid}",
        headers=h, timeout=10,
    )
    assert r.status_code == 200
    assert _bank_balance(h, ctx["bank_id"]) == 10000.0


def test_financial_position_reflects_linked_expense():
    """The whole point of Iter-94: assets total drops by the expense
    amount when paid from a linked bank."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    before = requests.get(
        f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10
    ).json()
    requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-07", "expense_type": "إنترنت",
            "amount": 400, "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    after = requests.get(
        f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10
    ).json()

    # Bank balance dropped by 400 → assets total dropped by 400
    assert round(before["assets"]["banks"] - after["assets"]["banks"], 2) == 400.0
    assert round(before["net_position"] - after["net_position"], 2) == 400.0


def test_invalid_account_id_rejected():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/daily",
        json={
            "date": "2026-06-08", "expense_type": "اختبار",
            "amount": 10, "paid_from_account_id": "non-existent-id",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 404
