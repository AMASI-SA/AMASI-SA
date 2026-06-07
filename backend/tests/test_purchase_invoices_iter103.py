"""Iter-103 — Purchase invoices with supplier linkage (no inventory).

What's covered:
  • Create invoice → auto-creates linked supplier liability with total
    = sum(qty × price) + tax_amount. Supplier name sourced from
    counterparties (single source of truth).
  • Line totals computed server-side (immutable).
  • Edit refused if any payment was recorded.
  • Edit (lines / tax) resyncs the liability's expected_amount.
  • Delete refused if any payment was recorded; otherwise both invoice
    and unpaid liability are removed.
  • Paying the linked liability reduces the invoice's `remaining_amount`
    and flips `status` to partial / paid.
  • Supplier statement aggregates total_invoiced / total_paid /
    balance_owed correctly across multiple invoices and payments.
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


def _new_user_with_supplier_and_bank():
    """Fresh user + a supplier counterparty + a bank account."""
    suffix = uuid.uuid4().hex[:8]
    email = f"iter103-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#103t", "name": "PInv"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#103t"},
        timeout=10,
    )
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "شركة الكرتون الذهبي"},
        headers=h, timeout=10,
    ).json()
    bank = requests.post(
        f"{BASE_URL}/api/accounts",
        json={"name": "بنك Iter-103", "account_type": "bank",
              "currency": "SAR", "opening_balance": 200000,
              "opening_balance_date": "2026-01-01"},
        headers=h, timeout=10,
    ).json()
    return {"headers": h, "cp_id": cp["id"], "cp_name": cp["name"],
            "bank_id": bank["id"]}


def _line(name, qty, price, sku=None):
    out = {"product_name": name, "quantity": qty, "unit_price": price}
    if sku:
        out["sku"] = sku
    return out


# ── 1) Create invoice → auto-create supplier liability ──────────────
def test_create_invoice_creates_linked_liability():
    ctx = _new_user_with_supplier_and_bank()
    r = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": ctx["cp_id"],
            "invoice_number": "PO-1001",
            "invoice_date": "2026-06-01",
            "due_date": "2026-07-01",
            "lines": [
                _line("كرتون 30×30",  100, 5.0,  sku="K3030"),
                _line("شريط لاصق",     20, 12.5, sku="TAPE"),
            ],
            "tax_amount": 25.0,
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    inv = r.json()
    # 100 × 5 + 20 × 12.5 = 500 + 250 = 750. + 25 tax = 775.
    assert inv["subtotal"] == 750.0
    assert inv["tax_amount"] == 25.0
    assert inv["total"] == 775.0
    assert inv["supplier_name"] == ctx["cp_name"]
    assert inv["status"] == "unpaid"
    assert inv["paid_amount"] == 0.0
    assert inv["remaining_amount"] == 775.0
    assert inv["liability_id"]

    # Each line has its own id + line_total
    assert all(ln.get("id") and ln["line_total"] for ln in inv["lines"])
    assert inv["lines"][0]["line_total"] == 500.0
    assert inv["lines"][1]["line_total"] == 250.0

    # Verify the liability really exists with the same amount.
    r = requests.get(
        f"{BASE_URL}/api/liabilities/{inv['liability_id']}",
        headers=ctx["headers"], timeout=10,
    )
    # liabilities_routes doesn't expose GET single, so list and filter:
    r = requests.get(
        f"{BASE_URL}/api/liabilities?limit=500",
        headers=ctx["headers"], timeout=10,
    )
    liab = next(
        x for x in r.json()["items"]
        if x["id"] == inv["liability_id"]
    )
    assert liab["kind"] == "supplier"
    assert liab["expected_amount"] == 775.0
    assert liab["counterparty_id"] == ctx["cp_id"]


# ── 2) Wrong / missing supplier → 404 ───────────────────────────────
def test_create_rejects_unknown_supplier():
    ctx = _new_user_with_supplier_and_bank()
    r = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": "no-such-id",
            "invoice_date": "2026-06-01",
            "lines": [_line("X", 1, 1)],
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 404


# ── 3) Zero-total invoice rejected ──────────────────────────────────
def test_zero_total_rejected():
    ctx = _new_user_with_supplier_and_bank()
    r = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": ctx["cp_id"],
            "invoice_date": "2026-06-01",
            "lines": [_line("Free sample", 1, 0)],
            "tax_amount": 0,
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400


# ── 4) Paying the linked liability reflects on the invoice ──────────
def test_paying_liability_reflects_on_invoice():
    ctx = _new_user_with_supplier_and_bank()
    inv = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": ctx["cp_id"],
            "invoice_date": "2026-06-01",
            "lines": [_line("منتج أ", 10, 50)],   # total = 500
        },
        headers=ctx["headers"], timeout=10,
    ).json()

    # Pay 200 of the 500
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{inv['liability_id']}/pay",
        json={
            "amount": 200,
            "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-06-15",
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text

    # Read invoice — should now show partial state
    r = requests.get(
        f"{BASE_URL}/api/purchase-invoices/{inv['id']}",
        headers=ctx["headers"], timeout=10,
    )
    body = r.json()
    assert body["status"] == "partial"
    assert body["paid_amount"] == 200.0
    assert body["remaining_amount"] == 300.0

    # Pay the rest
    requests.post(
        f"{BASE_URL}/api/liabilities/{inv['liability_id']}/pay",
        json={
            "amount": 300,
            "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-06-20",
        },
        headers=ctx["headers"], timeout=10,
    )
    r = requests.get(
        f"{BASE_URL}/api/purchase-invoices/{inv['id']}",
        headers=ctx["headers"], timeout=10,
    )
    body = r.json()
    assert body["status"] == "paid"
    assert body["paid_amount"] == 500.0
    assert body["remaining_amount"] == 0.0


# ── 5) Edit refused after any payment ───────────────────────────────
def test_edit_refused_after_payment():
    ctx = _new_user_with_supplier_and_bank()
    inv = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": ctx["cp_id"],
            "invoice_date": "2026-06-01",
            "lines": [_line("منتج", 5, 100)],
        },
        headers=ctx["headers"], timeout=10,
    ).json()
    requests.post(
        f"{BASE_URL}/api/liabilities/{inv['liability_id']}/pay",
        json={"amount": 100, "paid_from_account_id": ctx["bank_id"],
              "payment_date": "2026-06-10"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.put(
        f"{BASE_URL}/api/purchase-invoices/{inv['id']}",
        json={"lines": [_line("منتج", 6, 100)], "tax_amount": 0},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400
    assert "سُدِّد" in r.json()["detail"] or "سدد" in r.json()["detail"]


# ── 6) Edit resyncs the liability's expected_amount ─────────────────
def test_edit_resyncs_liability_amount():
    ctx = _new_user_with_supplier_and_bank()
    inv = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": ctx["cp_id"],
            "invoice_date": "2026-06-01",
            "lines": [_line("منتج", 5, 100)],   # total = 500
        },
        headers=ctx["headers"], timeout=10,
    ).json()

    r = requests.put(
        f"{BASE_URL}/api/purchase-invoices/{inv['id']}",
        json={
            "lines": [_line("منتج", 5, 100), _line("منتج آخر", 2, 200)],
            "tax_amount": 50,
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 500 + 400 = 900 + 50 tax = 950
    assert body["total"] == 950.0
    assert body["remaining_amount"] == 950.0

    # The linked liability also bumped
    r = requests.get(
        f"{BASE_URL}/api/liabilities?limit=500",
        headers=ctx["headers"], timeout=10,
    )
    liab = next(x for x in r.json()["items"] if x["id"] == inv["liability_id"])
    assert liab["expected_amount"] == 950.0


# ── 7) Delete refused after payment; otherwise removes both rows ────
def test_delete_lifecycle():
    ctx = _new_user_with_supplier_and_bank()
    inv = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": ctx["cp_id"],
            "invoice_date": "2026-06-01",
            "lines": [_line("X", 1, 10)],
        },
        headers=ctx["headers"], timeout=10,
    ).json()
    # Delete OK while unpaid
    r = requests.delete(
        f"{BASE_URL}/api/purchase-invoices/{inv['id']}",
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200
    # Liability removed too
    r = requests.get(
        f"{BASE_URL}/api/liabilities?limit=500",
        headers=ctx["headers"], timeout=10,
    )
    assert all(x["id"] != inv["liability_id"] for x in r.json()["items"])

    # Now create another, pay it, try delete → 400
    inv2 = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={
            "supplier_counterparty_id": ctx["cp_id"],
            "invoice_date": "2026-06-02",
            "lines": [_line("Y", 2, 50)],
        },
        headers=ctx["headers"], timeout=10,
    ).json()
    requests.post(
        f"{BASE_URL}/api/liabilities/{inv2['liability_id']}/pay",
        json={"amount": 10, "paid_from_account_id": ctx["bank_id"],
              "payment_date": "2026-06-05"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.delete(
        f"{BASE_URL}/api/purchase-invoices/{inv2['id']}",
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400


# ── 8) Supplier statement aggregates correctly ──────────────────────
def test_supplier_statement():
    ctx = _new_user_with_supplier_and_bank()
    # Create 3 invoices
    inv_a = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={"supplier_counterparty_id": ctx["cp_id"],
              "invoice_date": "2026-06-01",
              "lines": [_line("A", 10, 50)]},   # 500
        headers=ctx["headers"], timeout=10,
    ).json()
    inv_b = requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={"supplier_counterparty_id": ctx["cp_id"],
              "invoice_date": "2026-06-15",
              "lines": [_line("B", 5, 200)],    # 1000
              "tax_amount": 150},                # +150 → 1150
        headers=ctx["headers"], timeout=10,
    ).json()
    requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={"supplier_counterparty_id": ctx["cp_id"],
              "invoice_date": "2026-06-20",
              "lines": [_line("C", 1, 300)]},   # 300
        headers=ctx["headers"], timeout=10,
    )
    # Pay 200 on A, 1150 on B (full)
    requests.post(
        f"{BASE_URL}/api/liabilities/{inv_a['liability_id']}/pay",
        json={"amount": 200, "paid_from_account_id": ctx["bank_id"],
              "payment_date": "2026-06-05"},
        headers=ctx["headers"], timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/liabilities/{inv_b['liability_id']}/pay",
        json={"amount": 1150, "paid_from_account_id": ctx["bank_id"],
              "payment_date": "2026-06-18"},
        headers=ctx["headers"], timeout=10,
    )

    r = requests.get(
        f"{BASE_URL}/api/purchase-invoices/supplier/{ctx['cp_id']}/statement",
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 500 + 1150 + 300 = 1950 invoiced
    assert body["totals"]["total_invoiced"] == 1950.0
    # 200 + 1150 = 1350 paid
    assert body["totals"]["total_paid"] == 1350.0
    # 1950 − 1350 = 600 owed (300 of A + 300 of C)
    assert body["totals"]["balance_owed"] == 600.0
    assert len(body["invoices"]) == 3


# ── 9) List filters by supplier_id + status ─────────────────────────
def test_list_filters():
    ctx = _new_user_with_supplier_and_bank()
    # Other supplier (force=True to bypass fuzzy warning if any)
    cp2 = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "مورد ثاني", "force": True},
        headers=ctx["headers"], timeout=10,
    ).json()
    # 2 invoices for cp1, 1 for cp2
    for _ in range(2):
        requests.post(
            f"{BASE_URL}/api/purchase-invoices",
            json={"supplier_counterparty_id": ctx["cp_id"],
                  "invoice_date": "2026-06-01",
                  "lines": [_line("X", 1, 100)]},
            headers=ctx["headers"], timeout=10,
        )
    requests.post(
        f"{BASE_URL}/api/purchase-invoices",
        json={"supplier_counterparty_id": cp2["id"],
              "invoice_date": "2026-06-01",
              "lines": [_line("Y", 1, 100)]},
        headers=ctx["headers"], timeout=10,
    )

    r = requests.get(
        f"{BASE_URL}/api/purchase-invoices?supplier_id={ctx['cp_id']}",
        headers=ctx["headers"], timeout=10,
    )
    assert r.json()["total"] == 2
    assert all(
        x["supplier_counterparty_id"] == ctx["cp_id"]
        for x in r.json()["items"]
    )
