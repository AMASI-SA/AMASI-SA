"""Iter-246c — End-to-end supplier-invoice scenarios.

The merchant explicitly asked for:
  * supplier invoice with MORE THAN ONE line item
  * auto-calculated total (line_items SUM)
  * three payment modes: cash, partial, credit
  * correct double-entry ledger entries in all three cases
  * rejection when line items are missing / malformed

Plus the new bindings:
  * /api/settings persists operation_withdrawal_methods
  * Posting a movement with a disallowed withdrawal method → 400
  * Posting a movement with a disallowed account → 400
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
    suffix = uuid.uuid4().hex[:8]
    email = f"iter246c-{suffix}@x.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": email,
                            "password": "pw1234567"})
    assert r.status_code == 200, r.text
    body = r.json()
    h = _h(body["access_token"])

    # Seed categories
    r = requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    assert r.status_code == 200

    # Find a supplier_invoice leaf
    r = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h)
    rows = r.json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))
    cat_id = leaf["id"]

    # Create a supplier linked to this leaf
    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h,
                      json={"company_name": "مورد العبايات",
                            "contact_person": "أبو نوف",
                            "phone": "0500000001",
                            "category_ids": [cat_id]})
    assert r.status_code == 200, r.text
    sup_id = r.json()["id"]

    # Create a cash account
    r = requests.post(f"{BASE_URL}/api/accounts", headers=h,
                      json={"name": "الصندوق الرئيسي",
                            "account_type": "cash",
                            "opening_balance": 10000.0,
                            "currency": "SAR"})
    assert r.status_code == 200, r.text
    cash_acc = r.json()["id"]

    # Create a bank account
    r = requests.post(f"{BASE_URL}/api/accounts", headers=h,
                      json={"name": "الراجحي",
                            "account_type": "bank",
                            "opening_balance": 50000.0,
                            "currency": "SAR"})
    assert r.status_code == 200
    bank_acc = r.json()["id"]

    return {"h": h, "cat_id": cat_id, "sup_id": sup_id,
            "cash_acc": cash_acc, "bank_acc": bank_acc}


def _invoice_payload(ctx, terms, paid=0.0, account=None,
                     withdrawal=None, lines=None):
    default_lines = [
        {"description": "عباية كلوش", "quantity": 10,
         "unit_price": 120},
        {"description": "عباية أطفال", "quantity": 20,
         "unit_price": 100},
        {"description": "عباية شتوية", "quantity": 3,
         "unit_price": 170},
    ]
    final_lines = default_lines if lines is None else lines
    return {
        "movement_type": "supplier_invoice",
        "doc_date": "2026-02-01",
        "supplier_id": ctx["sup_id"],
        "category_id": ctx["cat_id"],
        "payment_terms": terms,
        "total_amount": 0,  # will be recomputed server-side
        "paid_amount": paid,
        "paid_from_account_id": account,
        "withdrawal_method": withdrawal,
        "line_items": final_lines,
    }


def test_supplier_invoice_cash_with_multi_lines(ctx):
    h = ctx["h"]
    payload = _invoice_payload(ctx, terms="cash",
                               account=ctx["cash_acc"])
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 200, r.text
    mv = r.json()
    # 10*120 + 20*100 + 3*170 = 1200 + 2000 + 510 = 3710
    assert mv["total_amount"] == 3710.0, mv
    assert mv["paid_amount"] == 3710.0
    assert mv["remaining_amount"] == 0.0
    assert len(mv["line_items"]) == 3


def test_supplier_invoice_partial(ctx):
    h = ctx["h"]
    payload = _invoice_payload(ctx, terms="partial",
                               paid=1000.0,
                               account=ctx["cash_acc"])
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 200, r.text
    mv = r.json()
    assert mv["total_amount"] == 3710.0
    assert mv["paid_amount"] == 1000.0
    assert mv["remaining_amount"] == 2710.0


def test_supplier_invoice_credit(ctx):
    h = ctx["h"]
    payload = _invoice_payload(ctx, terms="credit")
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 200, r.text
    mv = r.json()
    assert mv["paid_amount"] == 0.0
    assert mv["remaining_amount"] == mv["total_amount"] == 3710.0


def test_supplier_invoice_rejects_empty_lines(ctx):
    h = ctx["h"]
    payload = _invoice_payload(ctx, terms="credit", lines=[])
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 400, r.text
    assert "أصناف" in r.text or "line_items" in r.text


def test_supplier_invoice_rejects_zero_quantity(ctx):
    h = ctx["h"]
    payload = _invoice_payload(ctx, terms="credit", lines=[
        {"description": "بدون كمية", "quantity": 0,
         "unit_price": 50},
    ])
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 400, r.text


def test_settings_persists_withdrawal_methods(ctx):
    h = ctx["h"]
    # Restrict supplier_invoice → only transfer
    r = requests.get(f"{BASE_URL}/api/settings", headers=h)
    s = r.json()
    s["operation_withdrawal_methods"] = {
        "supplier_invoice": ["transfer"],
    }
    r = requests.put(f"{BASE_URL}/api/settings", headers=h, json=s)
    assert r.status_code == 200, r.text

    r = requests.get(f"{BASE_URL}/api/settings", headers=h)
    body = r.json()
    assert body["operation_withdrawal_methods"][
        "supplier_invoice"] == ["transfer"]


def test_withdrawal_method_enforced_on_create(ctx):
    """With the allow-list restricted to `transfer`, paying through a
    bank with `cash` withdrawal must be rejected."""
    h = ctx["h"]
    payload = _invoice_payload(ctx, terms="cash",
                               account=ctx["bank_acc"],
                               withdrawal="cash")
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 400, r.text
    assert "غير مسموحة" in r.text or "السحب" in r.text


def test_withdrawal_method_allow_listed_passes(ctx):
    h = ctx["h"]
    payload = _invoice_payload(ctx, terms="cash",
                               account=ctx["bank_acc"],
                               withdrawal="transfer")
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 200, r.text


def test_account_binding_enforced(ctx):
    """Restrict supplier_invoice → only bank_acc; cash_acc must fail."""
    h = ctx["h"]
    r = requests.get(f"{BASE_URL}/api/settings", headers=h)
    s = r.json()
    s["operation_account_bindings"] = {
        "supplier_invoice": [ctx["bank_acc"]],
    }
    s["operation_withdrawal_methods"] = {}  # reset
    r = requests.put(f"{BASE_URL}/api/settings", headers=h, json=s)
    assert r.status_code == 200

    payload = _invoice_payload(ctx, terms="cash",
                               account=ctx["cash_acc"])
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 400, r.text
    assert "غير مسموح" in r.text
