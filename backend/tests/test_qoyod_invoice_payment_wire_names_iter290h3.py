"""Iter-290h.3 — Live Qoyod field-name corrections + status accuracy.

Production order 269048975 (invoice_id=63, 131.92 SAR) failed the
payment-link step with:

    {"error":"Invalid resource",
     "messages":{"date":["Can't be blank"],
                 "account":["Can't be blank"]}}

…while we were sending `payment_date` + `payment_method_id`. The
canonical Qoyod /invoice_payments field names are `date` and
`account`. This file locks in the corrected payload shape AND the
monitor status accuracy fix that was masking the failure as
"pending".
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from integrations.qoyod.invoice_builder import build_invoice_payment_payload
from integrations.qoyod.first_sync_monitor import (
    _status_for_invoice_payment_step,
)


# ─── 1. Payload uses the LIVE Qoyod field names ──────────────────────
def test_payload_uses_date_and_account_not_payment_date_or_payment_method_id():
    """Live production evidence — order 269048975 proved Qoyod's
    /invoice_payments validator requires `date` + `account`, NOT
    `payment_date` + `payment_method_id`."""
    dto = {
        "order_id": "MZN-269048975", "order_number": "269048975",
        "total_amount": 131.92, "currency": "SAR",
        "payment_method": "mada",
    }
    settings = {
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "94"},
        ],
    }
    payload, _ = build_invoice_payment_payload(
        qoyod_invoice_id=63, dto_dict=dto,
        invoice_date=datetime(2026, 6, 28, tzinfo=timezone.utc),
        settings=settings,
    )
    body = payload["invoice_payment"]
    # CANONICAL field names.
    assert body["date"]    == "2026-06-28"
    assert body["account"] == 94
    # ANTI-regression — the buggy field names must NOT appear.
    assert "payment_date"      not in body, (
        "Qoyod rejects `payment_date` — must be `date`")
    assert "payment_method_id" not in body, (
        "Qoyod rejects `payment_method_id` — must be `account`")
    # Other fields untouched.
    assert body["invoice_id"]  == 63
    assert body["amount"]      == 131.92
    assert body["reference"]   == "269048975"
    assert body["description"] == "Mezan · Salla order MZN-269048975"


def test_payload_account_none_when_method_unmapped():
    """The pre-POST guard expects `account` (not `payment_method_id`)."""
    payload, _ = build_invoice_payment_payload(
        qoyod_invoice_id=1,
        dto_dict={"order_id": "X", "total_amount": 10.0,
                  "payment_method": "unknown"},
        invoice_date=datetime.now(timezone.utc),
        settings={"payment_method_mapping": []},
    )
    assert payload["invoice_payment"]["account"] is None


# ─── 2. Monitor reports PAYMENT_LINK_FAILED as "failed", not "pending" ─
def test_payment_link_failed_surfaces_as_failed_not_pending():
    """Production bug: when the row landed in PARTIAL_FAILURE with
    last_failed_stage=PAYMENT_LINK_FAILED but no
    `qoyod_invoice_payment_id`, the monitor previously returned
    "pending" because it only checked the legacy RECEIPT_CREATED /
    FAILED_RECEIPT stage names. Operator saw the step as "in progress"
    while Qoyod was rejecting the call."""
    row = {
        "pipeline_stage":     "PARTIAL_FAILURE",
        "last_failed_stage":  "PAYMENT_LINK_FAILED",
        "last_success_stage": "INVOICE_CREATED",
        "qoyod_invoice_id":   "63",
        "qoyod_invoice_payment_id": None,
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "PAYMENT_LINK_FAILED"},
            {"to_stage": "PARTIAL_FAILURE"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "failed"


def test_payment_method_mapping_missing_surfaces_as_failed():
    row = {
        "pipeline_stage":     "PARTIAL_FAILURE",
        "last_failed_stage":  "PAYMENT_METHOD_MAPPING_MISSING",
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "PAYMENT_METHOD_MAPPING_MISSING"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "failed"


def test_payment_step_pending_when_only_invoice_created():
    """Just past INVOICE_CREATED — payment-link not yet attempted →
    "pending" is correct."""
    row = {
        "pipeline_stage":     "INVOICE_CREATED",
        "last_success_stage": "INVOICE_CREATED",
        "qoyod_invoice_id":   "63",
        "stage_history":      [{"to_stage": "INVOICE_CREATED"}],
    }
    assert _status_for_invoice_payment_step(row) == "pending"


def test_payment_step_success_on_new_flow():
    row = {
        "pipeline_stage":          "COMPLETED",
        "qoyod_invoice_id":        "63",
        "qoyod_invoice_payment_id": "999",
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "INVOICE_PAYMENT_CREATED"},
            {"to_stage": "COMPLETED"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "success"


def test_payment_step_success_on_legacy_receipt_row():
    """Historic rows that completed under the old /receipts flow MUST
    still display as success."""
    row = {
        "pipeline_stage":          "COMPLETED",
        "qoyod_invoice_id":        "55",
        "qoyod_receipt_id":        "42",
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "RECEIPT_CREATED"},
            {"to_stage": "COMPLETED"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "success"
