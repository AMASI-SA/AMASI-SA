"""Iter-246h — Supplier_pay over-payment & re-payment guards.

Validates the merchant's exact bug report:
  * outstanding_debt = 458 → attempting to pay 550 must be REJECTED.
  * paying the exact outstanding_debt clears the balance.
  * a second payment after the balance is zero is REJECTED by the
    backend (defence-in-depth against a stale frontend).
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


@pytest.fixture(scope="module")
def ctx():
    suf = uuid.uuid4().hex[:8]
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": f"iter246h-{suf}@x.com",
                            "password": "pw1234567"})
    h = _h(r.json()["access_token"])

    requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    rows = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h).json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))

    r = requests.post(
        f"{BASE_URL}/api/suppliers", headers=h,
        json={"company_name": "مورد الاختبار",
              "contact_person": "مدير", "phone": "0500000033",
              "category_ids": [leaf["id"]]})
    sup_id = r.json()["id"]

    r = requests.post(
        f"{BASE_URL}/api/accounts", headers=h,
        json={"name": "بنك", "account_type": "bank",
              "opening_balance": 10000.0, "currency": "SAR"})
    bank_id = r.json()["id"]

    # Credit invoice 458 ر.س → outstanding_debt = 458.
    r = requests.post(
        f"{BASE_URL}/api/financial-movements", headers=h, json={
            "movement_type": "supplier_invoice",
            "doc_date": "2026-02-10",
            "supplier_id": sup_id, "category_id": leaf["id"],
            "payment_terms": "credit", "total_amount": 0,
            "line_items": [
                {"description": "x", "quantity": 1, "unit_price": 458}
            ],
        })
    assert r.status_code == 200, r.text

    return {"h": h, "sup_id": sup_id, "bank_id": bank_id}


def _pay(ctx, amount):
    return requests.post(
        f"{BASE_URL}/api/accounting/suppliers/{ctx['sup_id']}/pay",
        headers=ctx["h"], json={
            "amount": amount,
            "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-02-10",
            "notes": "test",
        })


def _outstanding(ctx) -> float:
    r = requests.get(
        f"{BASE_URL}/api/ledger/balance",
        params={"entity_type": "supplier",
                "entity_id": ctx["sup_id"]},
        headers=ctx["h"])
    return round(float(r.json().get("outstanding_debt") or 0), 2)


def test_baseline_debt_is_458(ctx):
    assert _outstanding(ctx) == 458.0


def test_overpay_rejected_550(ctx):
    """The merchant's exact case: debt 458 → pay 550 must fail."""
    r = _pay(ctx, 550)
    assert r.status_code == 400, r.text
    assert "أكبر من الرصيد" in r.text
    # Balance unchanged.
    assert _outstanding(ctx) == 458.0


def test_exact_payment_clears_debt(ctx):
    r = _pay(ctx, 458)
    assert r.status_code == 200, r.text
    assert _outstanding(ctx) == 0.0


def test_second_payment_after_zero_balance_rejected(ctx):
    """Defence-in-depth: even if the frontend hasn't refreshed and the
    merchant clicks pay again, backend must reject."""
    r = _pay(ctx, 100)
    assert r.status_code == 400, r.text
    assert ("لا يوجد رصيد مستحق" in r.text
            or "تم تسوية الدين" in r.text)
