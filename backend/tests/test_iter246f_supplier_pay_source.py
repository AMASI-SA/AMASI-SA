"""Iter-246f — Unified supplier list for «سداد مورد».

Validates:
  * /api/accounting/suppliers/list returns suppliers from BOTH
    `db.counterparties` AND `db.suppliers` (Iter-244), deduped by id.
  * `?with_debt_only=true` hides suppliers whose outstanding_debt == 0
    (this is what the supplier_pay dropdown uses).
  * After a partial invoice the supplier appears at the top of the
    list ranked by debt desc.
  * A counterparty with kind="supplier" but no debt (the «عرفات» case)
    does NOT appear when with_debt_only=true.
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
                      json={"name": "t", "email": f"iter246f-{suf}@x.com",
                            "password": "pw1234567"})
    h = _h(r.json()["access_token"])

    requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    rows = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h).json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))

    # Phantom counterparty WITHOUT a supplier match in db.suppliers —
    # represents the «عرفات» / legacy mis-categorised user.
    r = requests.post(
        f"{BASE_URL}/api/counterparties", headers=h,
        json={"name": f"عرفات الوهمي {suf}", "kind": "supplier"})
    assert r.status_code in (200, 201), r.text
    phantom_id = r.json().get("id")

    # Iter-244 supplier that WILL get a debt.
    r = requests.post(
        f"{BASE_URL}/api/suppliers", headers=h,
        json={"company_name": f"مورد الفواتير {suf}",
              "contact_person": "أبو مدين", "phone": "0500000060",
              "category_ids": [leaf["id"]]})
    sup_id = r.json()["id"]

    r = requests.post(
        f"{BASE_URL}/api/accounts", headers=h,
        json={"name": "بنك الراجحي", "account_type": "bank",
              "opening_balance": 10000.0, "currency": "SAR"})
    bank_id = r.json()["id"]

    return {"h": h, "cat_id": leaf["id"], "sup_id": sup_id,
            "phantom_id": phantom_id, "bank_id": bank_id}


def test_unified_list_includes_iter244_supplier(ctx):
    h = ctx["h"]
    r = requests.get(f"{BASE_URL}/api/accounting/suppliers/list",
                     headers=h)
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()["suppliers"]]
    assert ctx["sup_id"] in ids
    assert ctx["phantom_id"] in ids  # no filter → both shown


def test_with_debt_only_hides_zero_debt(ctx):
    h = ctx["h"]
    r = requests.get(f"{BASE_URL}/api/accounting/suppliers/list",
                     params={"with_debt_only": "true"}, headers=h)
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["suppliers"]]
    # Both have 0 debt right now → list is empty.
    assert ctx["sup_id"] not in ids
    assert ctx["phantom_id"] not in ids


def test_with_debt_after_partial_invoice(ctx):
    h = ctx["h"]
    # Post a partial invoice: 100 / paid 40 / remaining 60.
    r = requests.post(
        f"{BASE_URL}/api/financial-movements", headers=h, json={
            "movement_type": "supplier_invoice",
            "doc_date": "2026-02-05",
            "supplier_id": ctx["sup_id"],
            "category_id": ctx["cat_id"],
            "payment_terms": "partial",
            "paid_amount": 40,
            "paid_from_account_id": ctx["bank_id"],
            "withdrawal_method": "transfer",
            "total_amount": 0,
            "line_items": [
                {"description": "x", "quantity": 1, "unit_price": 100},
            ],
        })
    assert r.status_code == 200, r.text

    # Now `with_debt_only=true` MUST surface this supplier and HIDE
    # the phantom counterparty.
    r = requests.get(f"{BASE_URL}/api/accounting/suppliers/list",
                     params={"with_debt_only": "true"}, headers=h)
    rows = r.json()["suppliers"]
    ids = [s["id"] for s in rows]
    assert ctx["sup_id"] in ids
    assert ctx["phantom_id"] not in ids, (
        "phantom counterparty (0 debt) leaked into the supplier_pay list")
    # Outstanding shown correctly.
    found = next(s for s in rows if s["id"] == ctx["sup_id"])
    assert round(found["outstanding_debt"], 2) == 60.0
    # Sorted by debt desc → top should be ours.
    assert rows[0]["id"] == ctx["sup_id"]
