"""Iter-2026-02.rev13 — Canary Reconcile / Adopt existing قيود invoice.

Purpose
───────
When the canary pipeline entered an UNKNOWN WRITE STATE — i.e. قيود
DID create the invoice successfully but the real `qoyod_invoice_id`
never made it back into `integration_inbox` — we MUST NOT create a
duplicate invoice on the next attempt. Instead:

    1. Read قيود back (GET /invoices, paginated).
    2. Find the invoice(s) matching the merchant reference.
    3. Verify totals + vat + customer + product + issue_date all
       match what canary was about to send.
    4. Return the real قيود invoice id so the caller can adopt it.

Contract (STRICT — enforced by tests)
─────────────────────────────────────
  • READ-ONLY. Only calls `api_client.list_invoices(page, limit)`.
    Never POST, never PUT, never DELETE.
  • Refuses ambiguous state: >1 invoice with the same reference →
    `multiple_matches` (no writes).
  • Refuses partial state: reference matched but any field differs
    beyond tolerance → `mismatch` (no writes).
  • Money comparison uses ±0.01 SAR tolerance (قيود stores 2dp).
  • Field name resilience: قيود returns totals/customer under
    multiple keys depending on endpoint version — we try the
    canonical + legacy variants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# قيود stores SAR to 2 decimals — anything ≤0.01 apart is equal.
_MONEY_EPS = 0.011


@dataclass
class AdoptResult:
    """Structured outcome of a reconcile attempt.

    `code` ∈ {"adopted", "no_match", "multiple_matches",
              "mismatch", "api_error"}
    """
    success:            bool
    code:               str
    adopted_invoice_id: Optional[str]  = None
    invoice_snapshot:   Optional[dict] = None
    mismatch_reasons:   list[str]      = field(default_factory=list)
    pages_scanned:      int            = 0
    matches_found:      int            = 0
    api_error:          Optional[str]  = None


def _money_eq(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= _MONEY_EPS
    except (TypeError, ValueError):
        return False


def _extract_reference(inv: dict) -> Optional[str]:
    """قيود reference field can be one of several keys."""
    for k in ("reference", "reference_number",
              "external_reference", "ref"):
        v = inv.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def _extract_customer_id(inv: dict) -> Optional[str]:
    """قيود customer id: flat `contact_id`, `customer_id`, or nested."""
    for k in ("contact_id", "customer_id"):
        v = inv.get(k)
        if v not in (None, ""):
            return str(v)
    for parent_key in ("contact", "customer"):
        parent = inv.get(parent_key)
        if isinstance(parent, dict):
            v = parent.get("id")
            if v not in (None, ""):
                return str(v)
    return None


def _extract_totals(inv: dict) -> dict:
    """قيود totals appear under flat keys OR a nested `totals` dict."""
    def _get(*keys) -> Any:
        for k in keys:
            v = inv.get(k)
            if v is not None:
                return v
        nested = inv.get("totals") or {}
        if isinstance(nested, dict):
            for k in keys:
                v = nested.get(k)
                if v is not None:
                    return v
        return None
    return {
        "total":            _get("total", "grand_total", "amount",
                                 "total_amount"),
        "vat":              _get("vat", "tax", "tax_amount",
                                 "vat_amount"),
        "total_before_tax": _get("total_before_tax", "subtotal",
                                 "sub_total", "total_before_vat"),
    }


def _extract_line_product_ids(inv: dict) -> list[str]:
    lines = inv.get("line_items") or inv.get("items") or []
    if not isinstance(lines, list):
        return []
    out: list[str] = []
    for li in lines:
        if not isinstance(li, dict):
            continue
        for k in ("product_id", "inventory_id", "id"):
            v = li.get(k)
            if v not in (None, ""):
                out.append(str(v))
                break
    return out


def _extract_issue_date(inv: dict) -> Optional[str]:
    for k in ("issue_date", "date", "invoice_date"):
        v = inv.get(k)
        if v:
            return str(v)[:10]
    return None


async def find_and_adopt_existing_invoice(
    api_client,
    *,
    order_number:              str,
    expected_total:            float,
    expected_vat:              float,
    expected_total_before_tax: float,
    expected_customer_id:      Optional[str],
    expected_product_id:       Optional[str],
    expected_issue_date:       Optional[str],
    max_pages:                 int = 5,
    page_limit:                int = 50,
) -> AdoptResult:
    """Search قيود for an invoice with `reference == order_number`
    and verify EVERY expected field matches.

    See module docstring for the invariants. Read-only.
    """
    candidates:    list[dict] = []
    pages_scanned: int        = 0
    try:
        for page in range(1, max_pages + 1):
            pages_scanned = page
            data = await api_client.list_invoices(
                page=page, limit=page_limit)
            invs = (data.get("invoices")
                    if isinstance(data, dict) else data)
            if not isinstance(invs, list):
                break
            if not invs:
                break
            for inv in invs:
                if not isinstance(inv, dict):
                    continue
                if _extract_reference(inv) == str(order_number):
                    candidates.append(inv)
            # Short-circuit when قيود returns a partial page.
            if len(invs) < page_limit:
                break
    except Exception as exc:
        return AdoptResult(
            success=False, code="api_error",
            api_error=(f"{exc.__class__.__name__}: "
                       f"{exc}")[:400],
            pages_scanned=pages_scanned,
        )

    if not candidates:
        return AdoptResult(
            success=False, code="no_match",
            pages_scanned=pages_scanned, matches_found=0,
        )
    if len(candidates) > 1:
        return AdoptResult(
            success=False, code="multiple_matches",
            pages_scanned=pages_scanned,
            matches_found=len(candidates),
        )

    inv        = candidates[0]
    totals     = _extract_totals(inv)
    mismatches: list[str] = []

    if not _money_eq(totals["total"], expected_total):
        mismatches.append(
            f"total: got={totals['total']!r} "
            f"expected={expected_total!r}")
    if not _money_eq(totals["vat"], expected_vat):
        mismatches.append(
            f"vat: got={totals['vat']!r} "
            f"expected={expected_vat!r}")
    if not _money_eq(totals["total_before_tax"],
                     expected_total_before_tax):
        mismatches.append(
            f"total_before_tax: got={totals['total_before_tax']!r} "
            f"expected={expected_total_before_tax!r}")

    got_cust = _extract_customer_id(inv)
    if expected_customer_id and got_cust and \
            str(got_cust) != str(expected_customer_id):
        mismatches.append(
            f"customer_id: got={got_cust!r} "
            f"expected={expected_customer_id!r}")

    got_products = _extract_line_product_ids(inv)
    if expected_product_id and got_products and \
            str(expected_product_id) not in got_products:
        mismatches.append(
            f"product_id: expected={expected_product_id!r} "
            f"not in line items {got_products!r}")

    got_date = _extract_issue_date(inv)
    if expected_issue_date and got_date and \
            got_date != expected_issue_date:
        mismatches.append(
            f"issue_date: got={got_date!r} "
            f"expected={expected_issue_date!r}")

    if mismatches:
        return AdoptResult(
            success=False, code="mismatch",
            invoice_snapshot=inv,
            mismatch_reasons=mismatches,
            pages_scanned=pages_scanned, matches_found=1,
        )

    inv_id = inv.get("id") or inv.get("invoice_id")
    if inv_id in (None, ""):
        # Ambiguous — matched everything except we can't identify
        # the id. Refuse rather than adopt something we can't
        # reference later.
        return AdoptResult(
            success=False, code="mismatch",
            invoice_snapshot=inv,
            mismatch_reasons=[
                "invoice_id: قيود response carried no `id` "
                "or `invoice_id` field"],
            pages_scanned=pages_scanned, matches_found=1,
        )
    return AdoptResult(
        success=True, code="adopted",
        adopted_invoice_id=str(inv_id),
        invoice_snapshot=inv,
        pages_scanned=pages_scanned, matches_found=1,
    )
