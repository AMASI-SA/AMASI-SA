"""Iter-97 — New liability kinds: supplier + receivable.

Verifies:
  • supplier   → counted under liabilities.suppliers_unpaid + total
  • receivable → counted under assets.receivables (current asset)
  • Net position math stays consistent: assets − liabilities = net.
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


def _new_user():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter97-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#97test", "name": "Hub"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#97test"},
        timeout=10,
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_supplier_liability_counts_under_liabilities():
    h = _new_user()
    # Iter-165 — counterparty_id is now mandatory; create supplier first.
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"name": "شركة التغليف الذهبي", "kind": "supplier"},
        headers=h, timeout=10,
    ).json()
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "supplier",
            "counterparty_id": cp["id"],
            "expected_amount": 1500,
            "due_date": "2026-07-01",
            "description": "فاتورة كرتون",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "supplier"
    assert body["supplier_name"] == "شركة التغليف الذهبي"
    assert body["status"] == "unpaid"
    assert body["remaining_amount"] == 1500

    s = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    assert s["liabilities"]["suppliers_unpaid"] == 1500
    assert s["liabilities"]["total"] == 1500


def test_supplier_requires_name():
    h = _new_user()
    # Iter-165 — supplier without counterparty_id is rejected.
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={"kind": "supplier", "expected_amount": 100,
              "due_date": "2026-07-01",
              "supplier_name": "بدون مرجع"},
        headers=h, timeout=10,
    )
    assert r.status_code in (400, 422)


def test_receivable_counts_as_asset():
    h = _new_user()
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "receivable",
            "counterparty_name": "خالد العميل",
            "counterparty_type": "customer",
            "expected_amount": 800,
            "due_date": "2026-07-15",
            "description": "بيع آجل",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "receivable"
    assert body["counterparty_name"] == "خالد العميل"

    s = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    assert s["assets"]["receivables"] == 800
    # Net position: receivable is on the ASSETS side, not liabilities
    assert s["liabilities"]["total"] == 0
    assert s["net_position"] == 800


def test_receivable_requires_counterparty_name():
    h = _new_user()
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "receivable", "expected_amount": 100,
            "due_date": "2026-07-01",
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (400, 422)


def test_mixed_kinds_in_summary():
    """Supplier liability + receivable asset → net = receivable − supplier."""
    h = _new_user()
    # Iter-165 — supplier counterparty must exist first.
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"name": "Vendor", "kind": "supplier"},
        headers=h, timeout=10,
    ).json()
    requests.post(
        f"{BASE_URL}/api/liabilities",
        json={"kind": "supplier", "counterparty_id": cp["id"],
              "expected_amount": 1000, "due_date": "2026-07-01"},
        headers=h, timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/liabilities",
        json={"kind": "receivable", "counterparty_name": "Client",
              "expected_amount": 600, "due_date": "2026-07-15"},
        headers=h, timeout=10,
    )
    s = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10).json()
    assert s["assets"]["receivables"] == 600
    assert s["liabilities"]["suppliers_unpaid"] == 1000
    assert s["net_position"] == 600 - 1000  # = -400
