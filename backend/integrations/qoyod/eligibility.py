"""Invoice eligibility checks — Contract v1.0 enforcement.

These rules sit BETWEEN the Legacy Adapter and the validator. They
encode the user-locked policy 2026-06-26:

    • Every line item MUST have a non-empty SKU.
    • The order grand total MUST be strictly positive.

Failures here yield `FAILED_VALIDATION → DEAD_LETTER` — never an invoice.
Distinct from validate() (structural) and business_rules (status-based).

Pure function — no DB, no I/O. Tested at unit level for every code
path; integration-tested at the webhook level.
"""
from __future__ import annotations

from typing import Any, Optional


def check_invoice_eligibility(adapted_payload: Any) -> Optional[dict]:
    """Return None when the payload satisfies all v1.0 eligibility rules,
    or an `{"code": ..., "message": ..., ...}` error dict on the first
    rule violated. Caller is responsible for transitioning the inbox
    row to FAILED_VALIDATION → DEAD_LETTER on a non-None return.

    Rules (checked in this order):
        1. payload has a `data` envelope (defensive — adapt() always
           builds one for legacy payloads).
        2. every item in `data.items[]` has a non-empty `sku`.
        3. `data.amounts.total.amount` (or root-level fallback) is
           a number strictly greater than zero.
    """
    if not isinstance(adapted_payload, dict):
        return _err("invalid_payload_shape",
                    "adapted payload is not a JSON object")

    data = adapted_payload.get("data") \
        if isinstance(adapted_payload.get("data"), dict) \
        else adapted_payload

    # Rule 2 — SKU on every line item ─────────────────────────────────
    # Non-dict items are silently skipped — they're a STRUCTURAL bug
    # caught by `normalize()` (→ FAILED_NORMALIZATION). Eligibility is
    # SEMANTIC: among well-shaped items, does each have a real SKU?
    items = data.get("items") if isinstance(data.get("items"), list) else []
    offenders: list[dict] = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue   # let normalize() raise FAILED_NORMALIZATION
        sku = (it.get("sku") or "").strip()
        if not sku:
            offenders.append({
                "index": idx,
                "name":  (it.get("name") or "").strip() or None,
                "reason": "empty_sku",
            })
    if offenders:
        return _err(
            "items_missing_sku",
            f"{len(offenders)} line item(s) without a SKU — "
            "invoice cannot be created.",
            offenders=offenders[:5],   # cap echoed offenders for log hygiene
            total_offenders=len(offenders),
        )

    # Rule 4 — positive grand total ────────────────────────────────────
    amounts = data.get("amounts") if isinstance(data.get("amounts"), dict) else {}
    total_node = amounts.get("total")
    if isinstance(total_node, dict):
        total_value = total_node.get("amount")
    else:
        total_value = total_node
    # Fallback to root-level `total` / `total_amount` (legacy shape).
    if total_value in (None, ""):
        total_value = data.get("total_amount") or data.get("total")

    try:
        total_float = float(total_value) if total_value not in (None, "") else None
    except (TypeError, ValueError):
        return _err(
            "total_invalid",
            f"order total must be a number, got {type(total_value).__name__}",
            received=str(total_value)[:80],
        )

    if total_float is None:
        return _err("total_missing", "order total is missing")
    if total_float <= 0:
        return _err(
            "total_must_be_positive",
            f"order total must be > 0, got {total_float}",
            received=total_float,
        )

    return None


def _err(code: str, message: str, **extra: Any) -> dict:
    out = {"code": code, "message": message}
    out.update(extra)
    return out
