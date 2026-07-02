"""Iter-2026-02.rev13 — Canary Reconcile / Adopt tests.

Locks in the invariants for `canary_reconcile.find_and_adopt_existing_invoice`:

  1. READ-ONLY — never calls create_invoice/customer/product/receipt.
  2. Exact-match adoption: returns real قيود invoice_id.
  3. Refuses on `mismatch` (any of totals/vat/customer/product/date).
  4. Refuses on `multiple_matches` (ambiguous reference).
  5. Returns `no_match` cleanly when reference absent.
  6. Money comparison honours ±0.01 SAR tolerance.
  7. Field-name resilience (contact_id vs contact.id, etc.).
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.canary_reconcile import (   # noqa: E402
    AdoptResult, find_and_adopt_existing_invoice,
)


# ─── Read-only stub Qoyod client — asserts no writes ─────────────
class _ReadOnlyQoyod:
    def __init__(self, pages):
        # pages: list of list[dict] (per page)
        self._pages = pages
        self.calls = []

    async def list_invoices(self, *, page: int, limit: int):
        self.calls.append({"page": page, "limit": limit})
        idx = page - 1
        if idx >= len(self._pages):
            return {"invoices": []}
        return {"invoices": self._pages[idx]}

    # Guard rails — any write attempt fails the test loudly.
    async def create_invoice(self, *a, **kw):
        raise AssertionError("reconcile MUST NOT create_invoice")
    async def create_receipt(self, *a, **kw):
        raise AssertionError("reconcile MUST NOT create_receipt")
    async def create_customer(self, *a, **kw):
        raise AssertionError("reconcile MUST NOT create_customer")
    async def create_product(self, *a, **kw):
        raise AssertionError("reconcile MUST NOT create_product")
    async def create_invoice_payment(self, *a, **kw):
        raise AssertionError(
            "reconcile MUST NOT create_invoice_payment")


def _match_invoice() -> dict:
    """The exact قيود snapshot the operator confirmed visually
    for order 269629400 / invoice_id=186."""
    return {
        "id":               "186",
        "reference":        "269629400",
        "contact_id":       "228",
        "issue_date":       "2026-07-02",
        "total":            178.87,
        "vat":              23.33,
        "total_before_tax": 155.54,
        "line_items": [
            {"product_id": "45", "sku": "AMS11237"},
        ],
    }


# ─── Success path — exact adopt ──────────────────────────────────
@pytest.mark.asyncio
async def test_exact_match_adopts_invoice_id():
    q = _ReadOnlyQoyod([[_match_invoice()]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert isinstance(res, AdoptResult)
    assert res.success is True
    assert res.code == "adopted"
    assert res.adopted_invoice_id == "186"
    assert res.matches_found == 1
    # Only GETs — never a write.
    assert q.calls == [{"page": 1, "limit": 50}]


@pytest.mark.asyncio
async def test_adopt_tolerates_penny_rounding():
    inv = _match_invoice()
    inv["total"] = 178.878     # +0.008 within tolerance
    inv["vat"]   = 23.325      # -0.005 within tolerance
    q = _ReadOnlyQoyod([[inv]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is True


# ─── Refusal — reference not present ─────────────────────────────
@pytest.mark.asyncio
async def test_no_match_returns_no_match():
    q = _ReadOnlyQoyod([[
        {"id": "185", "reference": "SOMETHING_ELSE",
         "contact_id": "228", "total": 178.87},
    ]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is False
    assert res.code == "no_match"
    assert res.matches_found == 0


# ─── Refusal — multiple invoices with same reference ─────────────
@pytest.mark.asyncio
async def test_multiple_matches_refuses():
    dup = _match_invoice()
    dup2 = _match_invoice()
    dup2["id"] = "187"     # duplicate reference — ambiguous
    q = _ReadOnlyQoyod([[dup, dup2]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is False
    assert res.code == "multiple_matches"
    assert res.matches_found == 2


# ─── Refusal — reference matched but totals differ ───────────────
@pytest.mark.asyncio
async def test_mismatch_on_total_refuses():
    inv = _match_invoice()
    inv["total"] = 200.00      # gross mismatch
    q = _ReadOnlyQoyod([[inv]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is False
    assert res.code == "mismatch"
    assert any("total:" in m for m in res.mismatch_reasons)


@pytest.mark.asyncio
async def test_mismatch_on_customer_refuses():
    inv = _match_invoice()
    inv["contact_id"] = "9999"
    q = _ReadOnlyQoyod([[inv]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is False
    assert res.code == "mismatch"
    assert any("customer_id:" in m for m in res.mismatch_reasons)


@pytest.mark.asyncio
async def test_mismatch_on_product_refuses():
    inv = _match_invoice()
    inv["line_items"] = [{"product_id": "99"}]
    q = _ReadOnlyQoyod([[inv]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is False
    assert res.code == "mismatch"
    assert any("product_id:" in m for m in res.mismatch_reasons)


# ─── Field-name resilience: nested contact.id  ───────────────────
@pytest.mark.asyncio
async def test_customer_id_from_nested_contact_object():
    inv = _match_invoice()
    del inv["contact_id"]
    inv["contact"] = {"id": "228", "name": "سوزان عوض الله"}
    q = _ReadOnlyQoyod([[inv]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is True


# ─── Field-name resilience: totals nested under `totals` ─────────
@pytest.mark.asyncio
async def test_totals_from_nested_totals_dict():
    inv = _match_invoice()
    del inv["total"]
    del inv["vat"]
    del inv["total_before_tax"]
    inv["totals"] = {
        "total":            178.87,
        "vat":              23.33,
        "total_before_tax": 155.54,
    }
    q = _ReadOnlyQoyod([[inv]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is True


# ─── Pagination — walks pages until match ────────────────────────
@pytest.mark.asyncio
async def test_finds_match_on_second_page():
    other = {"id": "1", "reference": "OTHER"}
    q = _ReadOnlyQoyod([[other] * 50, [_match_invoice()]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is True
    assert q.calls[0]["page"] == 1
    assert q.calls[1]["page"] == 2


# ─── API error surfaces cleanly ──────────────────────────────────
@pytest.mark.asyncio
async def test_api_error_surfaces_cleanly():
    class _BadQoyod:
        async def list_invoices(self, *, page, limit):
            raise RuntimeError("simulated قيود outage")
    res = await find_and_adopt_existing_invoice(
        _BadQoyod(), order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is False
    assert res.code == "api_error"
    assert "simulated" in (res.api_error or "")


# ─── Production case docstring lock — invoice 186 shape ──────────
@pytest.mark.asyncio
async def test_production_case_269629400_invoice_186():
    """Documents the EXACT قيود snapshot for the production
    269629400/186 case so future refactors of the extractors don't
    accidentally break adoption."""
    inv = {
        "id":                "186",
        "invoice_number":    "INV-000186",
        "reference":         "269629400",
        "contact_id":        "228",
        "contact":           {"id": "228",
                              "name": "سوزان عوض الله"},
        "issue_date":        "2026-07-02",
        "due_date":          "2026-07-02",
        "status":            "approved",
        "total":             178.87,
        "vat":               23.33,
        "total_before_tax":  155.54,
        "line_items": [{"product_id": "45", "sku": "AMS11237",
                        "quantity": 1, "unit_price": 155.54}],
        "payment_method":    "tabby",
    }
    q = _ReadOnlyQoyod([[inv]])
    res = await find_and_adopt_existing_invoice(
        q, order_number="269629400",
        expected_total=178.87, expected_vat=23.33,
        expected_total_before_tax=155.54,
        expected_customer_id="228",
        expected_product_id="45",
        expected_issue_date="2026-07-02",
    )
    assert res.success is True
    assert res.adopted_invoice_id == "186"
