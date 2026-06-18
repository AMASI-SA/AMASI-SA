"""Iter-246e — Supplier↔counterparties bridge + entity-name enrichment.

After Iter-246d a partial invoice produced the right journal but the
merchant's downstream screens still showed the raw schema-level
`expense_category` string and the supplier didn't appear on
`/api/accounting/suppliers/list` because it lived in `db.suppliers`
(Iter-244) while the legacy ledger reads from `db.counterparties`.

This test proves the bridge is in place and that the ledger enrichment
returns human-readable names.

Scenario (exactly the merchant's last test):
  * total = 50, paid = 30, remaining = 20
  * expected supplier outstanding_debt after invoice = 20
  * supplier appears on /suppliers/list with outstanding_debt = 20
  * ledger entries are enriched with `entity_name`:
      - expense_category leg → full category path
      - supplier leg → company_name
      - bank leg → account name
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
    suffix = uuid.uuid4().hex[:8]
    email = f"iter246e-{suffix}@x.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": email,
                            "password": "pw1234567"})
    h = _h(r.json()["access_token"])

    r = requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    assert r.status_code == 200

    r = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h)
    rows = r.json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))

    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h,
                      json={"company_name": "مورد العبايات الراقية",
                            "contact_person": "أبو محمد",
                            "phone": "0500000050",
                            "category_ids": [leaf["id"]]})
    sup_id = r.json()["id"]

    r = requests.post(f"{BASE_URL}/api/accounts", headers=h,
                      json={"name": "بنك الراجحي - الرئيسي",
                            "account_type": "bank",
                            "opening_balance": 10000.0,
                            "currency": "SAR"})
    bank_acc = r.json()["id"]

    return {"h": h, "cat_id": leaf["id"], "cat_path": leaf["path"],
            "sup_id": sup_id, "sup_name": "مورد العبايات الراقية",
            "bank_acc": bank_acc, "bank_name": "بنك الراجحي - الرئيسي"}


def test_supplier_bridge_creates_counterparty(ctx):
    """The Iter-244 supplier must show up in db.counterparties so the
    legacy /suppliers/list reader sees it."""
    r = requests.get(f"{BASE_URL}/api/accounting/suppliers/list",
                     headers=ctx["h"])
    assert r.status_code == 200, r.text
    rows = r.json().get("suppliers", [])
    found = next((x for x in rows if x["id"] == ctx["sup_id"]), None)
    assert found is not None, (
        f"Supplier {ctx['sup_id']} not bridged to counterparties — "
        f"got rows: {rows}")
    assert found["name"] == ctx["sup_name"]
    assert found["outstanding_debt"] == 0.0  # no invoices yet


def test_partial_invoice_50_30_20_supplier_debt(ctx):
    """The merchant's exact scenario."""
    h = ctx["h"]
    payload = {
        "movement_type": "supplier_invoice",
        "doc_date": "2026-02-02",
        "supplier_id": ctx["sup_id"],
        "category_id": ctx["cat_id"],
        "payment_terms": "partial",
        "total_amount": 0,
        "paid_amount": 30,
        "paid_from_account_id": ctx["bank_acc"],
        "withdrawal_method": "transfer",
        "line_items": [
            {"description": "صنف A", "quantity": 1,
             "unit_price": 50},
        ],
    }
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    assert r.status_code == 200, r.text
    mv = r.json()
    assert _r(mv["total_amount"]) == 50.0
    assert _r(mv["paid_amount"]) == 30.0
    assert _r(mv["remaining_amount"]) == 20.0

    # ── A) /accounting/suppliers/list reflects the 20 ر.س debt ──
    r = requests.get(f"{BASE_URL}/api/accounting/suppliers/list",
                     headers=h)
    rows = r.json().get("suppliers", [])
    found = next(x for x in rows if x["id"] == ctx["sup_id"])
    assert _r(found["outstanding_debt"]) == 20.0, found
    # And the totals roll-up too.
    totals = r.json().get("totals", {})
    assert _r(totals.get("outstanding_debt")) >= 20.0, totals

    # ── B) /api/ledger/balance returns the right outstanding_debt ──
    r = requests.get(
        f"{BASE_URL}/api/ledger/balance",
        params={"entity_type": "supplier",
                "entity_id": ctx["sup_id"]},
        headers=h)
    assert r.status_code == 200
    assert _r(r.json().get("outstanding_debt")) == 20.0

    # ── C) /api/ledger/entries returns enriched names ──
    r = requests.get(f"{BASE_URL}/api/ledger/entries",
                     params={"entity_type": "supplier",
                             "entity_id": ctx["sup_id"],
                             "limit": 50},
                     headers=h)
    assert r.status_code == 200
    items = r.json().get("items", [])
    sup_legs = [e for e in items if e["entity_type"] == "supplier"]
    assert sup_legs, "expected at least one supplier leg"
    assert sup_legs[0]["entity_name"] == ctx["sup_name"]
    assert sup_legs[0]["entity_label_ar"] == "مورد"

    # ── D) Same enrichment for the expense_category & bank legs ──
    r = requests.get(f"{BASE_URL}/api/ledger/entries",
                     params={"limit": 50}, headers=h)
    items = r.json()["items"]
    expense = next(e for e in items
                   if e["entity_type"] == "expense_category"
                   and e["entity_id"] == ctx["cat_id"])
    assert " › " in expense["entity_name"], expense
    assert expense["entity_name"].endswith(ctx["cat_path"][-1])
    assert expense["entity_label_ar"] == "حساب مصروف"

    bank = next(e for e in items
                if e["entity_type"] == "bank"
                and e["entity_id"] == ctx["bank_acc"])
    assert bank["entity_name"] == ctx["bank_name"]
    assert bank["entity_label_ar"] == "حساب بنكي/صندوق"
