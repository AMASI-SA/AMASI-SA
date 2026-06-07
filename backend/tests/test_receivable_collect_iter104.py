"""Iter-104 — Receivable collection endpoint.

`POST /api/liabilities/{id}/collect` mirrors `/pay` but for receivables:
  • Bank balance INCREASES by the collected amount.
  • The receivable's `paid_amount` increases (logical inversion).
  • Status flips unpaid → partial → paid as more is collected.

Verifies that money DOES land in the bank (single ledger via
`account_transactions`) and the financial-position numbers stay
consistent.
"""
import os
import uuid

import requests


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _new_user_with_bank():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter104-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#104t", "name": "Recv"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#104t"},
        timeout=10,
    )
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    bank = requests.post(
        f"{BASE_URL}/api/accounts",
        json={"name": "بنك Iter-104", "account_type": "bank",
              "currency": "SAR", "opening_balance": 0,
              "opening_balance_date": "2026-01-01"},
        headers=h, timeout=10,
    ).json()
    return {"headers": h, "bank_id": bank["id"]}


def test_collect_increments_bank_and_reduces_receivable():
    ctx = _new_user_with_bank()
    # Create receivable: 1000 owed by "أحمد"
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "receivable",
            "counterparty_name": "أحمد",
            "counterparty_type": "person",
            "expected_amount": 1000,
            "due_date": "2026-07-01",
            "description": "بيع نقدي مؤجل",
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["kind"] == "receivable"
    assert rec["status"] == "unpaid"
    assert rec["remaining_amount"] == 1000.0

    # Collect 400
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{rec['id']}/collect",
        json={
            "amount": 400,
            "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-06-10",
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["liability"]["paid_amount"] == 400.0
    assert body["liability"]["remaining_amount"] == 600.0
    assert body["liability"]["status"] == "partial"

    # Bank must have grown by 400
    bank = requests.get(
        f"{BASE_URL}/api/accounts/{ctx['bank_id']}",
        headers=ctx["headers"], timeout=10,
    ).json()
    assert bank["current_balance"] == 400.0

    # Collect the rest (600)
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{rec['id']}/collect",
        json={
            "amount": 600,
            "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-06-20",
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["liability"]["status"] == "paid"
    assert body["liability"]["remaining_amount"] == 0.0

    bank = requests.get(
        f"{BASE_URL}/api/accounts/{ctx['bank_id']}",
        headers=ctx["headers"], timeout=10,
    ).json()
    assert bank["current_balance"] == 1000.0


def test_collect_rejected_on_non_receivable():
    ctx = _new_user_with_bank()
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "supplier", "supplier_name": "مورد X",
            "expected_amount": 500, "due_date": "2026-07-01",
        },
        headers=ctx["headers"], timeout=10,
    )
    liab_id = r.json()["id"]
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{liab_id}/collect",
        json={"amount": 100, "paid_from_account_id": ctx["bank_id"],
              "payment_date": "2026-06-10"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400
    assert "receivable" in r.json()["detail"].lower() or "ذمم" in r.json()["detail"]


def test_overcollection_rejected():
    ctx = _new_user_with_bank()
    rec = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "receivable", "counterparty_name": "س",
            "counterparty_type": "person",
            "expected_amount": 200, "due_date": "2026-07-01",
        },
        headers=ctx["headers"], timeout=10,
    ).json()
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{rec['id']}/collect",
        json={"amount": 250, "paid_from_account_id": ctx["bank_id"],
              "payment_date": "2026-06-10"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400
    assert "أكبر من المتبقي" in r.json()["detail"]
