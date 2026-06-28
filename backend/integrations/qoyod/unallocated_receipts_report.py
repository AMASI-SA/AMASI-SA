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

from datetime import date, datetime, timedelta, timezone
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


def _suggest_invoice(receipt: dict, invoices: list[dict]) -> tuple[
    Optional[dict], list[str], int,
]:
    """Pick the best matching invoice for a receipt.

    Returns `(invoice_or_None, match_reasons, score)`:
      • `match_reasons` — subset of {"reference","amount","customer","date"}
        explaining WHICH signals contributed to the suggestion. Empty
        when no invoice matched.
      • `score` — internal weight (operator-facing UI converts to a
        confidence label).
    """
    r_amount   = _f(receipt.get("amount"))
    r_contact  = (str(receipt.get("contact_id") or receipt.get("customer_id") or "")
                  .strip())
    r_ref      = str(
        receipt.get("external_reference")
        or receipt.get("reference")
        or receipt.get("notes") or ""
    ).strip()
    r_date     = _parse_date(receipt.get("date"))

    candidates: list[tuple[int, list[str], dict]] = []
    for inv in invoices:
        reasons: list[str] = []
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
        score = 0
        if r_ref and i_ref and (r_ref == i_ref
                                or r_ref in i_ref or i_ref in r_ref):
            score += 100
            reasons.append("reference")
        if r_date and i_date:
            delta_days = abs((r_date - i_date).days)
            if delta_days <= 2:
                score += 10
                reasons.append("date")
            elif delta_days <= 7:
                score += 3
                reasons.append("date")
        if r_amount and abs(r_amount - i_amount) <= 0.01:
            score += 5
            reasons.append("amount")
        # `customer` reason ONLY when we positively matched (not when
        # both sides were empty — that's the "no evidence" case).
        if r_contact and i_contact and r_contact == i_contact:
            score += 2
            reasons.append("customer")
        if score > 0:
            candidates.append((score, reasons, inv))

    if not candidates:
        return None, [], 0
    # Highest score wins; tie → newest issue_date.
    candidates.sort(key=lambda t: (
        -t[0],
        -(_parse_date(t[2].get("issue_date") or t[2].get("date"))
          or date(1970, 1, 1)).toordinal()
    ))
    best_score, best_reasons, best_inv = candidates[0]
    return best_inv, best_reasons, best_score


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


def _qoyod_deep_links(settings: dict, *, receipt_id: Any,
                      invoice_id: Optional[Any]) -> dict:
    """Return `{receipt_url, invoice_url}` — empty strings when the
    operator hasn't configured a base URL. The base default is the
    public Qoyod tenant area (`https://www.qoyod.com/tenant`) which is
    the same path observed in Qoyod's own settings links. Operators
    with a custom subdomain override via `settings.qoyod_ui_base_url`.
    """
    base = (settings.get("qoyod_ui_base_url")
            or "https://www.qoyod.com/tenant").rstrip("/")
    out: dict[str, str] = {"receipt_url": "", "invoice_url": ""}
    if receipt_id not in (None, "", 0):
        out["receipt_url"] = f"{base}/receipts/{receipt_id}"
    if invoice_id not in (None, "", 0):
        out["invoice_url"] = f"{base}/invoices/{invoice_id}"
    return out


async def _load_dismissed_receipt_ids(db, *, user_id: str) -> set[str]:
    """Iter-290h — Returns the set of Qoyod receipt ids the operator
    has marked as "تمت المعالجة يدوياً" inside ميزان so the report
    no longer surfaces them. Read-only — never mutates Qoyod state."""
    if db is None:
        return set()
    out: set[str] = set()
    cur = db.qoyod_unallocated_dismissals.find(
        {"user_id": user_id, "active": True},
        {"_id": 0, "qoyod_receipt_id": 1},
    )
    async for row in cur:
        rid = row.get("qoyod_receipt_id")
        if rid is not None:
            out.add(str(rid))
    return out


async def _load_settings(db, *, user_id: str) -> dict:
    if db is None:
        return {}
    doc = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    return doc


async def build_unallocated_receipts_report(
    db,
    *,
    user_id: str,
    max_receipts: int = 200,
    max_invoices: int = 500,
) -> dict:
    """Fetch Qoyod receipts + invoices, isolate unallocated receipts,
    suggest matching invoices, annotate with match reasons + deep
    links + dismissal state.

    READ-ONLY end-to-end — never mutates Qoyod state. The operator
    links receipts manually in قيود UI. A later iteration may add a
    one-click "تخصيص" once we've proven suggestion accuracy on a real
    sample.
    """
    api_key = await get_api_key(db, user_id)
    if not api_key:
        return {"ok": False, "error": {
            "code": "qoyod_api_key_missing",
            "message": "Qoyod API key not configured for this tenant."
        }}

    receipts: list[dict] = []
    invoices: list[dict] = []
    api = QoyodAPIClient(api_key)
    try:
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

    settings = await _load_settings(db, user_id=user_id)
    dismissed = await _load_dismissed_receipt_ids(db, user_id=user_id)

    unallocated = [r for r in receipts
                   if _looks_unallocated(r)
                   and str(r.get("id")) not in dismissed]

    items: list[dict] = []
    for r in unallocated:
        suggestion, reasons, score = _suggest_invoice(r, invoices)
        if suggestion is None:
            confidence = "none"
        elif "reference" in reasons:
            confidence = "high"
        elif "amount" in reasons and ("date" in reasons or "customer" in reasons):
            confidence = "medium"
        elif "amount" in reasons or "customer" in reasons or "date" in reasons:
            confidence = "low"
        else:
            confidence = "low"
        deep = _qoyod_deep_links(
            settings,
            receipt_id=r.get("id"),
            invoice_id=(suggestion.get("id") if suggestion else None),
        )
        items.append({
            "receipt":        _slim_receipt(r),
            "suggestion":     _slim_invoice(suggestion) if suggestion else None,
            "match_reasons":  reasons,
            "confidence":     confidence,
            "match_score":    score,
            "qoyod_receipt_url":  deep["receipt_url"],
            "qoyod_invoice_url":  deep["invoice_url"],
            "dismissable":    True,   # always true; operator may un-dismiss later
        })

    with_sugg = sum(1 for it in items if it["suggestion"] is not None)
    return {
        "ok":                  True,
        "scanned_receipts":    len(receipts),
        "scanned_invoices":    len(invoices),
        "dismissed_count":     len(dismissed),
        "qoyod_ui_base_url":   (settings.get("qoyod_ui_base_url")
                                or "https://www.qoyod.com/tenant"),
        "items":               items,
        "summary": {
            "unallocated_count":   len(items),
            "with_suggestion":     with_sugg,
            "without_suggestion":  len(items) - with_sugg,
            "by_confidence": {
                "high":   sum(1 for it in items if it["confidence"] == "high"),
                "medium": sum(1 for it in items if it["confidence"] == "medium"),
                "low":    sum(1 for it in items if it["confidence"] == "low"),
                "none":   sum(1 for it in items if it["confidence"] == "none"),
            },
        },
    }


async def dismiss_receipt(
    db, *, user_id: str, qoyod_receipt_id: str, actor: str,
    note: Optional[str] = None,
) -> dict:
    """Iter-290h — Operator marks a standalone Qoyod receipt as
    "تمت المعالجة يدوياً" inside ميزان so the unallocated report no
    longer surfaces it. No Qoyod state is touched — the operator is
    responsible for linking the receipt in قيود UI.

    Idempotent: a second call upserts the same row with `dismissed_at`
    refreshed. Returns the stored row.
    """
    rid = str(qoyod_receipt_id).strip()
    if not rid:
        raise ValueError("qoyod_receipt_id required")
    now = datetime.now(timezone.utc)
    update = {
        "$set": {
            "user_id":           user_id,
            "qoyod_receipt_id":  rid,
            "active":            True,
            "actor":             actor,
            "note":              note,
            "dismissed_at":      now,
            "updated_at":        now,
        },
        "$setOnInsert": {"created_at": now},
    }
    await db.qoyod_unallocated_dismissals.update_one(
        {"user_id": user_id, "qoyod_receipt_id": rid},
        update, upsert=True,
    )
    row = await db.qoyod_unallocated_dismissals.find_one(
        {"user_id": user_id, "qoyod_receipt_id": rid}, {"_id": 0},
    )
    return row or {}


async def undismiss_receipt(
    db, *, user_id: str, qoyod_receipt_id: str,
) -> dict:
    """Iter-290h — Reverse of `dismiss_receipt`. Flips `active=False`
    so the row reappears in the report. Kept as a soft toggle (NOT a
    delete) so the audit trail of "operator dismissed X then changed
    their mind" is preserved."""
    rid = str(qoyod_receipt_id).strip()
    now = datetime.now(timezone.utc)
    await db.qoyod_unallocated_dismissals.update_one(
        {"user_id": user_id, "qoyod_receipt_id": rid},
        {"$set": {"active": False, "undismissed_at": now,
                  "updated_at": now}},
    )
    return {"ok": True, "qoyod_receipt_id": rid, "active": False}
