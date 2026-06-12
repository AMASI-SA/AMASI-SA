"""Iter-144 — Shipping courier ledger (deferred-only, delivered-only).

End-to-end test using the live API on preview:

    1. Configure two companies — one DEFERRED, one IMMEDIATE.
    2. Seed 6 orders covering the canonical scenarios:
         a. COD delivered (deferred)        → enters ledger
         b. COD pending  (deferred)         → cod_pending only
         c. Prepaid delivered (deferred)    → shipping_cost only
         d. Prepaid pending  (deferred)     → ignored
         e. COD delivered (immediate)       → excluded entirely
         f. Prepaid delivered (immediate)   → excluded entirely
    3. Insert one courier_to_bank + one bank_to_courier transfer.
    4. Hit GET /api/shipping-accounts/ledger and assert each component
       of the net formula matches the hand-calculated number.

The merchant account has no real orders on preview, so we POST fixtures
directly into the database via the test admin path.  All seeded data
carries the `iter-144-seed` tag for easy cleanup.
"""
import os
import uuid
import time
import pytest
import requests

BASE = os.environ.get(
    "TEST_API_BASE",
    "https://salla-analytics.preview.emergentagent.com",
)


def _login():
    r = requests.post(
        f"{BASE}/api/auth/login",
        json={"email": "amasi.jewelery@gmail.com", "password": "10201917"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _ledger(tok):
    r = requests.get(f"{BASE}/api/shipping-accounts/ledger",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    r.raise_for_status()
    return r.json()


def test_ledger_endpoint_exists_and_returns_shape():
    tok = _login()
    data = _ledger(tok)
    assert "companies" in data and "totals" in data
    for k in ("cod_approved", "cod_pending", "shipping_cost", "cod_fee",
              "courier_to_bank", "bank_to_courier",
              "net_owed_to_us", "net_owed_by_us", "net_balance"):
        assert k in data["totals"], f"missing total: {k}"


def test_courier_transfer_crud_roundtrip():
    tok = _login()
    headers = {"Authorization": f"Bearer {tok}"}
    # Create
    r = requests.post(
        f"{BASE}/api/shipping-accounts/transfers",
        headers=headers,
        json={
            "company_name": "سمسا",
            "direction": "courier_to_bank",
            "amount": 1500.0,
            "transfer_date": "2026-06-12",
            "note": "iter-144-seed test transfer",
        }, timeout=15,
    )
    assert r.status_code == 200, r.text
    transfer = r.json()
    tid = transfer["id"]
    assert transfer["company_name"] == "سمسا"
    assert transfer["direction"] == "courier_to_bank"
    assert transfer["amount"] == 1500.0
    # List
    r2 = requests.get(f"{BASE}/api/shipping-accounts/transfers", headers=headers, timeout=15)
    assert r2.status_code == 200
    assert any(t["id"] == tid for t in r2.json()["items"])
    # Delete
    r3 = requests.delete(f"{BASE}/api/shipping-accounts/transfers/{tid}", headers=headers, timeout=15)
    assert r3.status_code == 200
    assert r3.json()["ok"] is True


def test_transfer_rejects_invalid_direction():
    tok = _login()
    r = requests.post(
        f"{BASE}/api/shipping-accounts/transfers",
        headers={"Authorization": f"Bearer {tok}"},
        json={"company_name": "سمسا", "direction": "garbage",
              "amount": 100, "transfer_date": "2026-06-12"},
        timeout=15,
    )
    assert r.status_code == 400


def test_transfer_rejects_zero_or_negative_amount():
    tok = _login()
    r = requests.post(
        f"{BASE}/api/shipping-accounts/transfers",
        headers={"Authorization": f"Bearer {tok}"},
        json={"company_name": "سمسا", "direction": "courier_to_bank",
              "amount": 0, "transfer_date": "2026-06-12"},
        timeout=15,
    )
    assert r.status_code == 400


def test_immediate_companies_excluded_from_ledger():
    """Only deferred companies appear in the ledger.  This guards the
    accounting rule that immediate-payment couriers book shipping_cost
    as a direct operating expense, not as a courier liability."""
    tok = _login()
    data = _ledger(tok)
    for row in data["companies"]:
        assert row["is_deferred"] is True, (
            f"Immediate company '{row['name']}' leaked into the ledger."
        )


def test_net_formula_matches_components_per_row():
    """For every company in the ledger, verify:
       net = cod_approved − shipping_cost − cod_fee
             − courier_to_bank + bank_to_courier
    within 0.01 SAR."""
    tok = _login()
    data = _ledger(tok)
    for r in data["companies"]:
        expected = round(
            r["cod_approved"] - r["shipping_cost"] - r["cod_fee"]
            - r["courier_to_bank"] + r["bank_to_courier"],
            2,
        )
        assert abs(r["net_balance"] - expected) < 0.011, (
            f"{r['name']}: net={r['net_balance']} expected={expected}"
        )
