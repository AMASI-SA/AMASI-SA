"""Iter-246d — Verify the journal for supplier invoices is correct
in all 3 payment modes:

  * cash    (paid=total) → 2 legs : Dr expense, Cr bank
  * credit  (paid=0)     → 2 legs : Dr expense, Cr supplier
  * partial (0<paid<tot) → 3 legs : Dr expense, Cr bank (paid),
                                    Cr supplier (remaining)

The decisive bug: prior to this iteration the system only mirrored
the CASH leg, so a partial invoice for 144 paid 50 produced
Dr 50 / Cr 50 — losing both the full expense recognition and the
supplier liability.
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
    email = f"iter246d-{suffix}@x.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": email,
                            "password": "pw1234567"})
    assert r.status_code == 200, r.text
    body = r.json()
    h = _h(body["access_token"])

    r = requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    assert r.status_code == 200

    r = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h)
    rows = r.json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))
    cat_id = leaf["id"]

    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h,
                      json={"company_name": "مورد E2E",
                            "contact_person": "أبو حذيفة",
                            "phone": "0500000099",
                            "category_ids": [cat_id]})
    assert r.status_code == 200, r.text
    sup_id = r.json()["id"]

    r = requests.post(f"{BASE_URL}/api/accounts", headers=h,
                      json={"name": "بنك الراجحي E2E",
                            "account_type": "bank",
                            "opening_balance": 50000.0,
                            "currency": "SAR"})
    assert r.status_code == 200, r.text
    bank_acc = r.json()["id"]

    return {"h": h, "cat_id": cat_id, "sup_id": sup_id,
            "bank_acc": bank_acc}


def _lines_144():
    """3 lines whose total = 144 (matches the merchant's bug report)."""
    return [
        {"description": "صنف A", "quantity": 2, "unit_price": 25},
        {"description": "صنف B", "quantity": 3, "unit_price": 18},
        {"description": "صنف C", "quantity": 4, "unit_price": 10},
    ]


def _post_invoice(ctx, *, terms, paid=0.0, account=None,
                  withdrawal=None):
    return requests.post(
        f"{BASE_URL}/api/financial-movements",
        headers=ctx["h"], json={
            "movement_type": "supplier_invoice",
            "doc_date": "2026-02-01",
            "supplier_id": ctx["sup_id"],
            "category_id": ctx["cat_id"],
            "payment_terms": terms,
            "total_amount": 0,
            "paid_amount": paid,
            "paid_from_account_id": account,
            "withdrawal_method": withdrawal,
            "line_items": _lines_144(),
        })


def _ledger_for_group(ctx, group_id):
    """Fetch all general_ledger rows for a given txn_group_id by
    paging /api/ledger/entries and filtering client-side."""
    r = requests.get(
        f"{BASE_URL}/api/ledger/entries",
        params={"limit": 500},
        headers=ctx["h"])
    assert r.status_code == 200, r.text
    rows = r.json().get("items", [])
    return [e for e in rows if e.get("txn_group_id") == group_id]


def _legs_by_entity(entries):
    by = {}
    for e in entries:
        by[(e["entity_type"], e.get("side"))] = e
    return by


def test_cash_invoice_posts_two_legs(ctx):
    r = _post_invoice(ctx, terms="cash",
                      account=ctx["bank_acc"], withdrawal="transfer")
    assert r.status_code == 200, r.text
    mv = r.json()
    assert _r(mv["total_amount"]) == 144.0
    assert _r(mv["paid_amount"]) == 144.0
    assert _r(mv["remaining_amount"]) == 0.0
    assert mv["ledger_txn_group_id"], "ledger group id must be set"

    entries = _ledger_for_group(ctx, mv["ledger_txn_group_id"])
    assert len(entries) == 2, entries
    debits = [e for e in entries if e["side"] == "debit"]
    credits = [e for e in entries if e["side"] == "credit"]
    assert sum(_r(e["amount"]) for e in debits) == 144.0
    assert sum(_r(e["amount"]) for e in credits) == 144.0

    by = _legs_by_entity(entries)
    assert by.get(("expense_category", "debit"))["amount"] == 144.0
    assert by.get(("bank", "credit"))["amount"] == 144.0


def test_credit_invoice_posts_supplier_payable(ctx):
    r = _post_invoice(ctx, terms="credit")
    assert r.status_code == 200, r.text
    mv = r.json()
    assert _r(mv["paid_amount"]) == 0.0
    assert _r(mv["remaining_amount"]) == 144.0

    entries = _ledger_for_group(ctx, mv["ledger_txn_group_id"])
    assert len(entries) == 2
    by = _legs_by_entity(entries)
    assert by.get(("expense_category", "debit"))["amount"] == 144.0
    assert by.get(("supplier", "credit"))["amount"] == 144.0
    assert by.get(("supplier", "credit"))["entity_id"] == ctx["sup_id"]


def test_partial_invoice_posts_three_legs(ctx):
    """The exact case the merchant reported: 144 / paid 50 / owe 94."""
    r = _post_invoice(ctx, terms="partial", paid=50.0,
                      account=ctx["bank_acc"], withdrawal="transfer")
    assert r.status_code == 200, r.text
    mv = r.json()
    assert _r(mv["total_amount"]) == 144.0
    assert _r(mv["paid_amount"]) == 50.0
    assert _r(mv["remaining_amount"]) == 94.0

    entries = _ledger_for_group(ctx, mv["ledger_txn_group_id"])
    assert len(entries) == 3, entries
    debits = [e for e in entries if e["side"] == "debit"]
    credits = [e for e in entries if e["side"] == "credit"]
    # Balanced
    assert sum(_r(e["amount"]) for e in debits) == 144.0
    assert sum(_r(e["amount"]) for e in credits) == 144.0
    # Per-leg correctness
    by = _legs_by_entity(entries)
    assert by[("expense_category", "debit")]["amount"] == 144.0
    assert by[("bank", "credit")]["amount"] == 50.0
    assert by[("supplier", "credit")]["amount"] == 94.0
    assert by[("supplier", "credit")]["entity_id"] == ctx["sup_id"]


def test_supplier_balance_reflects_open_invoices(ctx):
    """After credit + partial invoices the supplier should owe us
    144 + 94 = 238 ر.س (we owe THEM, hence a credit-leaning net)."""
    r = requests.get(
        f"{BASE_URL}/api/ledger/balance",
        params={"entity_type": "supplier",
                "entity_id": ctx["sup_id"]},
        headers=ctx["h"])
    assert r.status_code == 200, r.text
    body = r.json()
    # `outstanding_debt` is the merchant-facing positive figure.
    assert _r(body.get("outstanding_debt")) >= 94.0, body
    # `net_balance` is positive when supplier owes us, negative when we
    # owe supplier — for a credit invoice it must be ≤ -94.
    assert _r(body.get("net_balance")) <= -94.0, body
