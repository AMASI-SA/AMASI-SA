"""Iter-246k — Suppliers report.

Three scenarios required by the merchant:
  1. فاتورة آجل 400 → outstanding_debt = 400
  2. فاتورة نقدية 480 → outstanding_debt = 0
  3. فاتورة جزئية 50 / مدفوع 30 → outstanding_debt = 20
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
                      json={"name": "t", "email": f"iter246k-{suf}@x.com",
                            "password": "pw1234567"})
    h = _h(r.json()["access_token"])

    requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    rows = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h).json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))

    sup_credit = requests.post(
        f"{BASE_URL}/api/suppliers", headers=h,
        json={"company_name": "مورد آجل",
              "contact_person": "أ", "phone": "0500111111",
              "category_ids": [leaf["id"]]}).json()
    sup_cash = requests.post(
        f"{BASE_URL}/api/suppliers", headers=h,
        json={"company_name": "مورد نقدي",
              "contact_person": "ب", "phone": "0500222222",
              "category_ids": [leaf["id"]]}).json()
    sup_partial = requests.post(
        f"{BASE_URL}/api/suppliers", headers=h,
        json={"company_name": "مورد جزئي",
              "contact_person": "ج", "phone": "0500333333",
              "category_ids": [leaf["id"]]}).json()

    bank = requests.post(
        f"{BASE_URL}/api/accounts", headers=h,
        json={"name": "بنك", "account_type": "bank",
              "opening_balance": 10000.0, "currency": "SAR"}).json()

    return {
        "h": h, "cat_id": leaf["id"], "bank_id": bank["id"],
        "credit": sup_credit["id"],
        "cash": sup_cash["id"],
        "partial": sup_partial["id"],
    }


def _post_invoice(ctx, sup_id, *, terms, total, paid=0,
                  with_account=True):
    h = ctx["h"]
    return requests.post(
        f"{BASE_URL}/api/financial-movements", headers=h, json={
            "movement_type": "supplier_invoice",
            "doc_date": "2026-02-20",
            "supplier_id": sup_id, "category_id": ctx["cat_id"],
            "payment_terms": terms,
            "paid_amount": paid,
            "paid_from_account_id":
                ctx["bank_id"] if with_account and terms != "credit" else None,
            "withdrawal_method":
                "transfer" if with_account and terms != "credit" else None,
            "total_amount": 0,
            "line_items": [
                {"description": "x", "quantity": 1, "unit_price": total},
            ],
        })


def test_three_scenarios(ctx):
    """Post the merchant's exact three test cases then read the
    suppliers report and assert the per-supplier outstanding_debt."""
    assert _post_invoice(ctx, ctx["credit"], terms="credit",
                          total=400).status_code == 200
    assert _post_invoice(ctx, ctx["cash"], terms="cash",
                          total=480).status_code == 200
    assert _post_invoice(ctx, ctx["partial"], terms="partial",
                          total=50, paid=30).status_code == 200

    r = requests.get(f"{BASE_URL}/api/reports/suppliers",
                     headers=ctx["h"])
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {s["id"]: s for s in body["suppliers"]}

    # ── Scenario 1: credit 400 → debt 400 ───────────────────────────
    s1 = by_id[ctx["credit"]]
    assert s1["invoices_count"] == 1
    assert s1["invoices_total"] == 400.0
    assert s1["paid_total"] == 0.0
    assert s1["remaining_total"] == 400.0
    assert s1["outstanding_debt"] == 400.0
    assert s1["last_invoice_date"] == "2026-02-20"
    assert s1["last_activity"] is not None
    assert s1["categories"]                       # category resolved
    assert s1["ledger_url"].endswith(ctx["credit"])

    # ── Scenario 2: cash 480 → debt 0 ───────────────────────────────
    s2 = by_id[ctx["cash"]]
    assert s2["invoices_total"] == 480.0
    assert s2["paid_total"] == 480.0
    assert s2["remaining_total"] == 0.0
    assert s2["outstanding_debt"] == 0.0

    # ── Scenario 3: partial 50, paid 30 → debt 20 ───────────────────
    s3 = by_id[ctx["partial"]]
    assert s3["invoices_total"] == 50.0
    assert s3["paid_total"] == 30.0
    assert s3["remaining_total"] == 20.0
    assert s3["outstanding_debt"] == 20.0


def test_filter_with_debt_only(ctx):
    r = requests.get(
        f"{BASE_URL}/api/reports/suppliers",
        params={"with_debt_only": "true"}, headers=ctx["h"])
    body = r.json()
    ids = {s["id"] for s in body["suppliers"]}
    assert ctx["credit"] in ids
    assert ctx["partial"] in ids
    assert ctx["cash"] not in ids


def test_filter_q_search(ctx):
    r = requests.get(
        f"{BASE_URL}/api/reports/suppliers",
        params={"q": "نقدي"}, headers=ctx["h"])
    body = r.json()
    ids = {s["id"] for s in body["suppliers"]}
    assert ids == {ctx["cash"]}


def test_filter_by_category(ctx):
    """All three suppliers share the same leaf, so a category filter
    on the leaf should return all three (or, with `with_debt_only`,
    the two with debt)."""
    r = requests.get(
        f"{BASE_URL}/api/reports/suppliers",
        params={"category_id": ctx["cat_id"]}, headers=ctx["h"])
    body = r.json()
    ids = {s["id"] for s in body["suppliers"]}
    for k in ("credit", "cash", "partial"):
        assert ctx[k] in ids


def test_totals_aggregate(ctx):
    r = requests.get(f"{BASE_URL}/api/reports/suppliers",
                     headers=ctx["h"])
    t = r.json()["totals"]
    assert t["suppliers_count"] >= 3
    # 400 + 480 + 50 = 930
    assert t["invoices_total"] >= 930.0
    # 0 + 480 + 30 = 510
    assert t["paid_total"] >= 510.0
    # 400 + 0 + 20 = 420
    assert t["outstanding_debt"] >= 420.0
