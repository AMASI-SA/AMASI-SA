"""Pending-Orders Classifier — Iter-293.5-rev3.

Pure helpers extracted from `routes.py` so both the endpoint AND
tests exercise the exact same logic. No DB access, no IO.

Contract
────────
`categorise_row(row)` returns one of the seven category tokens
surfaced by `GET /admin/qoyod/pending-orders`:

    ready_to_send | needs_mapping | bank_transfer_hold | cod
    | unsupported_method | total_rounding_review | stale_or_cancelled

Rules (in evaluation order):
  1. If `pipeline_stage` is one of the explicit HOLD/failure stages,
     that stage wins — it's the most reliable signal.
  2. Otherwise derive from `canonical_payload.payment_method`:
     • bank_transfer          → bank_transfer_hold
     • cod / cash_on_delivery → cod (unless payload leaks → needs_mapping)
     • BNPL family (tabby / tamara / emkan + `_installment` variants)
       → leak? needs_mapping : ready_to_send  (Iter-293.5-rev3)
     • prepaid family (mada / apple_pay / stc_pay / credit cards)
       → leak? needs_mapping : ready_to_send
     • unknown non-empty pm  → unsupported_method
     • empty / None pm        → leak? needs_mapping : ready_to_send

`payload_has_leak(payload)` — deep scan for DRY: / PREVIEW: string
prefixes AND null-valued `contact_id` / `product_id` keys.
"""
from __future__ import annotations

from typing import Any


_STAGE_TO_CATEGORY: dict[str, str] = {
    "LOCKED_AWAITING_APPROVAL":              "ready_to_send",
    "UNRESOLVED_QOYOD_DEPENDENCY":           "needs_mapping",
    "BANK_TRANSFER_PAYMENT_ROUTING_PENDING": "bank_transfer_hold",
    "HOLD_COD_PENDING_FIX":                  "cod",
    "HOLD_UNSUPPORTED_PAYMENT_METHOD":       "unsupported_method",
    "INVOICE_CREATED_TOTAL_MISMATCH":        "total_rounding_review",
    "STALE_TRACE_NOT_CURRENT_ORDER_STATE":   "stale_or_cancelled",
    "FAILED_INVOICE":                        "needs_mapping",
    "DEAD_LETTER":                           "stale_or_cancelled",
}

EXPLICIT_HOLD_STAGES: frozenset[str] = frozenset(_STAGE_TO_CATEGORY.keys())

BANK_TRANSFER_METHODS: frozenset[str] = frozenset({
    "bank_transfer", "banktransfer",
})
COD_METHODS: frozenset[str] = frozenset({
    "cod", "cash_on_delivery", "cashondelivery",
})
PREPAID_METHODS: frozenset[str] = frozenset({
    "mada", "apple_pay", "applepay", "stc_pay", "stcpay",
    "credit_card", "creditcard", "cc", "visa",
    "mastercard", "master_card", "american_express",
    "americanexpress", "amex",
})
# Iter-293.5-rev3 — BNPL variants (Tabby / Tamara / Emkan). Kept in
# lock-step with `live_send_gate.BNPL_ALLOWED`.
BNPL_METHODS: frozenset[str] = frozenset({
    "tabby", "tabby_installment", "tabby_installments",
    "tabby_pay", "tabby_payment",
    "tamara", "tamara_installment", "tamara_installments",
    "tamara_pay", "tamara_payment",
    "emkan", "emkan_installment", "emkan_installments",
})


def stage_to_category(stage: str) -> str:
    """Explicit HOLD/failure stage → category. Unknown stages fall
    back to `needs_mapping` (the safest bucket)."""
    return _STAGE_TO_CATEGORY.get(stage, "needs_mapping")


def payload_has_leak(payload: Any) -> bool:
    """Deep scan for DRY: / PREVIEW: string prefixes and null
    `contact_id` / `product_id` values. A True return means the row
    has an unresolved dependency and MUST NOT be sent."""
    if isinstance(payload, str):
        return payload.startswith("DRY:") or payload.startswith("PREVIEW:")
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in ("contact_id", "product_id") and v is None:
                return True
            if payload_has_leak(v):
                return True
        return False
    if isinstance(payload, list):
        return any(payload_has_leak(x) for x in payload)
    return False


def categorise_row(row: dict) -> str:
    """Iter-293.5-rev3 — Category for a pending-orders row.

    Rules — see module docstring.
    """
    stage = row.get("pipeline_stage") or ""
    if stage in EXPLICIT_HOLD_STAGES:
        return stage_to_category(stage)
    canonical = row.get("canonical_payload") or {}
    pm = str(canonical.get("payment_method") or "").strip().lower()
    payloads = row.get("qoyod_payloads") or {}
    inv_payload = payloads.get("invoice") or {}
    if pm in BANK_TRANSFER_METHODS:
        return "bank_transfer_hold"
    if pm in COD_METHODS:
        return "needs_mapping" if payload_has_leak(inv_payload) else "cod"
    if pm in BNPL_METHODS:
        return (
            "needs_mapping" if payload_has_leak(inv_payload)
            else "ready_to_send"
        )
    if pm and pm not in PREPAID_METHODS:
        return "unsupported_method"
    if payload_has_leak(inv_payload):
        return "needs_mapping"
    return "ready_to_send"
