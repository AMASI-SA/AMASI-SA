"""Iter-246j — Full balance parity after a financial-movement cash leg.

Reproduces the merchant's bug:
  * فاتورة مورد and سداد مورد displayed different balances (Δ=536) for
    the SAME bank, because Iter-245 cash legs were posted WITHOUT the
    `metadata.source=account_transaction_double_write` tag, so SSOT
    double-counted them (once from `current_balance` recompute, once
    from `general_ledger`), while `/cash-accounts-with-balances`
    counted them once.

After Iter-246j the bank-leg metadata is tagged.  All FOUR consumer
surfaces must return the EXACT same number for the same account:
  1. /accounts                                  (الأصول والحسابات)
  2. /accounts/{id}                              (صفحة الحساب)
  3. /financial-movements/accounts-with-availability  (فاتورة مورد)
  4. /accounting/cash-accounts-with-balances     (سداد مورد reload)
  5. /diagnostics/account-balances               (التشخيص)
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
                      json={"name": "t", "email": f"iter246j-{suf}@x.com",
                            "password": "pw1234567"})
    h = _h(r.json()["access_token"])

    requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    rows = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h).json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))
    sup = requests.post(
        f"{BASE_URL}/api/suppliers", headers=h,
        json={"company_name": "مورد المطابقة", "contact_person": "أ",
              "phone": "0500000045",
              "category_ids": [leaf["id"]]}).json()
    bank = requests.post(
        f"{BASE_URL}/api/accounts", headers=h,
        json={"name": "بنك الإنماء", "account_type": "bank",
              "opening_balance": 1000.0, "currency": "SAR"}).json()
    return {"h": h, "cat_id": leaf["id"], "sup_id": sup["id"],
            "bank_id": bank["id"]}


def _all_surfaces(ctx) -> dict[str, float]:
    h = ctx["h"]
    bid = ctx["bank_id"]
    out = {}
    # 1) /accounts list
    items = requests.get(
        f"{BASE_URL}/api/accounts?account_type=bank&limit=200",
        headers=h).json()
    out["/accounts"] = _r(
        next(a for a in items if a["id"] == bid)["current_balance"])

    # 2) /accounts/{id}
    one = requests.get(f"{BASE_URL}/api/accounts/{bid}", headers=h).json()
    out["/accounts/{id}"] = _r(one.get("current_balance"))

    # 3) /financial-movements/accounts-with-availability
    items = requests.get(
        f"{BASE_URL}/api/financial-movements/accounts-with-availability",
        params={"amount": 0}, headers=h).json()["items"]
    out["/accounts-with-availability"] = _r(
        next(a for a in items if a["id"] == bid)["available_balance"])

    # 4) /accounting/cash-accounts-with-balances
    items = requests.get(
        f"{BASE_URL}/api/accounting/cash-accounts-with-balances",
        headers=h).json().get("accounts", [])
    out["/cash-accounts-with-balances"] = _r(
        next(a for a in items if a["id"] == bid).get("live_balance"))

    # 5) /diagnostics/account-balances
    items = requests.get(
        f"{BASE_URL}/api/diagnostics/account-balances",
        headers=h).json()["accounts"]
    row = next(a for a in items if a["account_id"] == bid)
    out["/diagnostics/account-balances"] = _r(row["ssot_balance"])
    return out


def test_baseline_parity_before_any_invoice(ctx):
    b = _all_surfaces(ctx)
    assert len(set(b.values())) == 1, b
    assert next(iter(b.values())) == 1000.0


def test_parity_after_cash_invoice(ctx):
    """The merchant's exact bug: post a cash invoice via the new
    /financial-movements endpoint then assert every surface shows the
    SAME debited balance.  Pre-Iter-246j this failed because the bank
    leg double-counted in SSOT."""
    h = ctx["h"]
    # Cash invoice 250 ر.س.
    r = requests.post(
        f"{BASE_URL}/api/financial-movements", headers=h, json={
            "movement_type": "supplier_invoice",
            "doc_date": "2026-02-15",
            "supplier_id": ctx["sup_id"], "category_id": ctx["cat_id"],
            "payment_terms": "cash",
            "paid_from_account_id": ctx["bank_id"],
            "withdrawal_method": "transfer",
            "total_amount": 0,
            "line_items": [
                {"description": "x", "quantity": 1, "unit_price": 250}
            ],
        })
    assert r.status_code == 200, r.text

    b = _all_surfaces(ctx)
    # 1000 opening − 250 paid = 750.
    assert all(v == 750.0 for v in b.values()), b


def test_parity_after_partial_invoice(ctx):
    """Partial invoice posts BOTH a bank-credit leg (which double-write
    would have double-counted pre-fix) AND a supplier-credit leg."""
    h = ctx["h"]
    r = requests.post(
        f"{BASE_URL}/api/financial-movements", headers=h, json={
            "movement_type": "supplier_invoice",
            "doc_date": "2026-02-16",
            "supplier_id": ctx["sup_id"], "category_id": ctx["cat_id"],
            "payment_terms": "partial",
            "paid_amount": 100,
            "paid_from_account_id": ctx["bank_id"],
            "withdrawal_method": "transfer",
            "total_amount": 0,
            "line_items": [
                {"description": "y", "quantity": 2, "unit_price": 80}
            ],
        })
    assert r.status_code == 200, r.text

    b = _all_surfaces(ctx)
    # 750 − 100 paid = 650.
    assert all(v == 650.0 for v in b.values()), b


def test_debug_fields_in_response(ctx):
    h = ctx["h"]
    items = requests.get(
        f"{BASE_URL}/api/financial-movements/accounts-with-availability",
        params={"amount": 0}, headers=h).json()["items"]
    row = next(a for a in items if a["id"] == ctx["bank_id"])
    for k in ("balance_source", "stored_balance", "ssot_balance",
              "ledger_balance", "last_calculated_at"):
        assert k in row, (k, row)
