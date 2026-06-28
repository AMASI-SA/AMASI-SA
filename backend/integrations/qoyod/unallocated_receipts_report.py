"""Iter-290h — Unallocated Receipts Report (admin / manual reconciliation).

Purpose
───────
Before Iter-290h shipped, the integration pipeline called `POST /receipts`
which produced STANDALONE Qoyod receipts (no link to the invoice). The
operator saw these in Qoyod's "غير مستعمل" (unallocated) bin and the
invoice balance stayed > 0.

This module surfaces those orphan receipts (PYT1 … PYT8 in the user's
audit) alongside a SUGGESTED matching invoice — so the operator can
link them manually in the Qoyod UI. Per user spec we DO NOT mutate Qoyod
state from this report (no DELETE, no auto-CREATE invoice_payment).
Pure read + suggest.

Match algorithm
───────────────
Receipt R is suggested against Invoice I when ALL of:
    • R.contact_id == I.contact_id        (same customer)
    • abs(R.amount − I.amount) ≤ 0.05     (5 halalat tolerance)
    • R.external_reference equals I.reference (order number) OR
      R.date within ±2 days of I.issue_date  (fuzzy fallback)

A receipt with NO matching invoice → returned with `suggestion=None`
so the operator can scroll to it on the manual report page.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.credentials import get_api_key


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    s = str(v)[:10]   # tolerate ISO datetimes
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _looks_unallocated(receipt: dict) -> bool:
    """Heuristic for "غير مستعمل" — Qoyod's API surfaces an explicit
    flag when available, otherwise we infer from a missing/empty
    allocation field. Errs on the side of INCLUDING the row so the
    operator can manually rule it out."""
    # 1. Direct fields if Qoyod exposes them on the receipt resource.
    for k in ("allocated", "is_allocated", "linked", "invoice_id"):
        v = receipt.get(k)
        if k in ("allocated", "is_allocated", "linked") and v is True:
            return False
        if k == "invoice_id" and v not in (None, "", 0):
            return False
    # 2. Allocations array shape: `allocations: [{invoice_id, amount}, …]`.
    allocs = receipt.get("allocations") or receipt.get("invoice_allocations")
    if isinstance(allocs, list) and any(
        isinstance(a, dict) and a.get("invoice_id") for a in allocs
    ):
        return False
    # 3. Default: assume unallocated.
    return True


def _suggest_invoice(receipt: dict, invoices: list[dict]) -> Optional[dict]:
    """Pick the best matching invoice for a receipt. Returns the invoice
    dict (or None). Match priority — reference > amount + customer +
    date proximity."""
    r_amount   = _f(receipt.get("amount"))
    r_contact  = (str(receipt.get("contact_id") or receipt.get("customer_id") or "")
                  .strip())
    r_ref      = str(
        receipt.get("external_reference")
        or receipt.get("reference")
        or receipt.get("notes") or ""
    ).strip()
    r_date     = _parse_date(receipt.get("date"))

    candidates: list[tuple[int, dict]] = []   # (score, invoice)
    for inv in invoices:
        i_amount  = _f(inv.get("total") or inv.get("amount")
                       or inv.get("total_amount"))
        i_contact = str(
            inv.get("contact_id") or inv.get("customer_id") or ""
        ).strip()
        i_ref     = str(inv.get("reference") or "").strip()
        i_date    = _parse_date(
            inv.get("issue_date") or inv.get("date")
            or inv.get("created_at"))
        # Reject hard: different customer.
        if r_contact and i_contact and r_contact != i_contact:
            continue
        # Reject hard: amounts differ by more than 5 halalat.
        if r_amount and abs(r_amount - i_amount) > 0.05:
            continue
        # Score: reference match = +100, date within 2 days = +10,
        # exact-amount match (≤0.01) = +5.
        score = 0
        if r_ref and i_ref and (r_ref == i_ref
                                or r_ref in i_ref or i_ref in r_ref):
            score += 100
        if r_date and i_date:
            delta_days = abs((r_date - i_date).days)
            if delta_days <= 2:
                score += 10
            elif delta_days <= 7:
                score += 3
        if r_amount and abs(r_amount - i_amount) <= 0.01:
            score += 5
        if score > 0:
            candidates.append((score, inv))

    if not candidates:
        return None
    # Highest score wins; tie → newest issue_date.
    candidates.sort(key=lambda t: (
        -t[0],
        # Newest first on tie.
        -(_parse_date(t[1].get("issue_date") or t[1].get("date"))
          or date(1970, 1, 1)).toordinal()
    ))
    return candidates[0][1]


def _slim_invoice(inv: dict) -> dict:
    return {
        "id":          inv.get("id"),
        "reference":   inv.get("reference"),
        "issue_date":  inv.get("issue_date") or inv.get("date"),
        "total":       inv.get("total") or inv.get("amount")
                       or inv.get("total_amount"),
        "contact_id":  inv.get("contact_id") or inv.get("customer_id"),
        "status":      inv.get("status"),
        "balance":     inv.get("balance"),
    }


def _slim_receipt(r: dict) -> dict:
    return {
        "id":           r.get("id"),
        "number":       r.get("number") or r.get("reference"),
        "date":         r.get("date"),
        "amount":       r.get("amount"),
        "contact_id":   r.get("contact_id") or r.get("customer_id"),
        "external_reference": r.get("external_reference"),
        "notes":        r.get("notes"),
    }


async def build_unallocated_receipts_report(
    db,
    *,
    user_id: str,
    max_receipts: int = 200,
    max_invoices: int = 500,
) -> dict:
    """Fetch Qoyod receipts + invoices, isolate unallocated receipts,
    suggest invoices.

    Returns:
        {
            "ok":               True,
            "scanned_receipts": N,
            "scanned_invoices": M,
            "items": [
                {
                    "receipt":    {…slim…},
                    "suggestion": {…slim invoice…} | None,
                    "confidence": "high" | "medium" | "low" | "none",
                },
                …
            ],
            "summary": {
                "unallocated_count": N,
                "with_suggestion":   K,
                "without_suggestion": N − K,
            }
        }

    On Qoyod API failure returns `{"ok": False, "error": {…}}`.
    """
    api_key = await get_api_key(db, user_id)
    if not api_key:
        return {"ok": False, "error": {
            "code": "qoyod_api_key_missing",
            "message": "Qoyod API key not configured for this tenant."
        }}

    receipts: list[dict] = []
    invoices: list[dict] = []
    async with QoyodAPIClient(api_key) as api:
        try:
            # Pull receipts (paginated; we cap at max_receipts).
            page = 1
            while len(receipts) < max_receipts:
                resp = await api.list_receipts(page=page, limit=50)
                rows = (resp.get("receipts") if isinstance(resp, dict)
                        else resp) or []
                if not rows:
                    break
                receipts.extend(rows)
                if len(rows) < 50:
                    break
                page += 1
            # Pull invoices.
            page = 1
            while len(invoices) < max_invoices:
                resp = await api.list_invoices(page=page, limit=50)
                rows = (resp.get("invoices") if isinstance(resp, dict)
                        else resp) or []
                if not rows:
                    break
                invoices.extend(rows)
                if len(rows) < 50:
                    break
                page += 1
        except QoyodAPIError as exc:
            return {"ok": False, "error": exc.to_log_dict()}

    receipts = receipts[:max_receipts]
    invoices = invoices[:max_invoices]

    unallocated = [r for r in receipts if _looks_unallocated(r)]
    items: list[dict] = []
    for r in unallocated:
        suggestion = _suggest_invoice(r, invoices)
        if suggestion is None:
            confidence = "none"
        else:
            # Recompute the match score for the confidence label.
            r_ref = str(r.get("external_reference") or r.get("reference")
                        or "").strip()
            i_ref = str(suggestion.get("reference") or "").strip()
            r_amt = _f(r.get("amount"))
            i_amt = _f(suggestion.get("total") or suggestion.get("amount")
                       or suggestion.get("total_amount"))
            if r_ref and i_ref and (r_ref == i_ref
                                    or r_ref in i_ref or i_ref in r_ref):
                confidence = "high"
            elif r_amt and abs(r_amt - i_amt) <= 0.01:
                confidence = "medium"
            else:
                confidence = "low"
        items.append({
            "receipt":    _slim_receipt(r),
            "suggestion": _slim_invoice(suggestion) if suggestion else None,
            "confidence": confidence,
        })

    with_sugg = sum(1 for it in items if it["suggestion"] is not None)
    return {
        "ok":               True,
        "scanned_receipts": len(receipts),
        "scanned_invoices": len(invoices),
        "items":            items,
        "summary": {
            "unallocated_count":   len(items),
            "with_suggestion":     with_sugg,
            "without_suggestion":  len(items) - with_sugg,
        },
    }
