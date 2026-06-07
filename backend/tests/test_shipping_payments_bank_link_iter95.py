"""Iter-95 — Shipping company debt payments linked to bank accounts.

Mirrors Iter-94 (daily expenses → bank). When a payment is recorded with
`paid_from_account_id`, an out-flowing `account_transactions` row is
posted with type=shipping_debt_payment and the bank balance drops by
the payment amount. Delete rolls it back.

Backward-compatible: payments without `paid_from_account_id` do not
touch any bank account (legacy "paper-only" behaviour).
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
    email = f"iter95-{suffix}@example.com"
    password = "Test#95"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "Shipping F2"},
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
            "name": "بنك F2", "account_type": "bank",
            "currency": "SAR", "opening_balance": 10000.0,
            "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    )
    bank_id = r.json()["id"]
    return {"headers": h, "bank_id": bank_id}


def _bank_balance(headers, bank_id):
    return requests.get(
        f"{BASE_URL}/api/accounts/{bank_id}", headers=headers, timeout=10
    ).json().get("current_balance")


def test_unlinked_payment_does_not_touch_bank():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    before = _bank_balance(h, ctx["bank_id"])

    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/مندوب الرياض/payments",
        json={"amount": 500, "payment_date": "2026-06-01"},
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("paid_from_account_id") in (None, "")
    assert body.get("linked_transaction_id") is None
    assert _bank_balance(h, ctx["bank_id"]) == before


def test_linked_payment_deducts_bank():
    ctx = _new_user_with_bank()
    h = ctx["headers"]

    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/مندوب الرياض/payments",
        json={
            "amount": 750, "payment_date": "2026-06-02",
            "invoice_number": "INV-2026-001",
            "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["paid_from_account_id"] == ctx["bank_id"]
    assert body["linked_transaction_id"] is not None
    assert _bank_balance(h, ctx["bank_id"]) == 9250.0


def test_delete_linked_payment_rolls_back_bank():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/SMSA/payments",
        json={
            "amount": 300, "payment_date": "2026-06-03",
            "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    payment_id = r.json()["id"]
    assert _bank_balance(h, ctx["bank_id"]) == 9700.0

    r = requests.delete(
        f"{BASE_URL}/api/shipping-accounts/payments/{payment_id}",
        headers=h, timeout=10,
    )
    assert r.status_code == 200
    assert _bank_balance(h, ctx["bank_id"]) == 10000.0


def test_invalid_account_id_rejected():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/مندوب الرياض/payments",
        json={
            "amount": 100, "payment_date": "2026-06-04",
            "paid_from_account_id": "non-existent-id",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 404


def test_transaction_type_is_shipping_debt_payment():
    """The new transaction_type must be persisted on the ledger so the
    user can filter shipping payments distinct from daily expenses."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/Aramex/payments",
        json={
            "amount": 200, "payment_date": "2026-06-05",
            "invoice_number": "ARMX-7",
            "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    payment_id = r.json()["id"]

    # Inspect the linked transaction on the account
    txs = requests.get(
        f"{BASE_URL}/api/accounts/{ctx['bank_id']}/transactions",
        headers=h, timeout=10,
    ).json()
    items = txs if isinstance(txs, list) else txs.get("transactions") or txs.get("items", [])
    match = [t for t in items if t.get("peer_shipping_payment_id") == payment_id]
    assert len(match) == 1
    tx = match[0]
    assert tx["transaction_type"] == "shipping_debt_payment"
    assert tx["direction"] == "out"
    assert tx["amount"] == 200.0
    assert "Aramex" in tx["description"]
    assert tx.get("reference") == "ARMX-7"


def test_financial_position_reflects_linked_payment():
    """The whole point of Iter-95: when a shipping payment is linked to
    a bank, assets total and net_position drop by exactly the amount."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    before = requests.get(
        f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10
    ).json()
    requests.post(
        f"{BASE_URL}/api/shipping-accounts/SPL/payments",
        json={
            "amount": 1200, "payment_date": "2026-06-07",
            "paid_from_account_id": ctx["bank_id"],
        },
        headers=h, timeout=10,
    )
    after = requests.get(
        f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10
    ).json()

    assert round(before["assets"]["banks"] - after["assets"]["banks"], 2) == 1200.0
    assert round(before["net_position"] - after["net_position"], 2) == 1200.0
