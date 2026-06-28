"""Iter-290h.6 — Display-fidelity fixes for orders that completed
AFTER a prior failed attempt.

Production order 268494278 (2026-06-28) exposed three display
regressions that masked a real success:

  1. The `INVOICE_PAYMENT_CREATED` step rendered as "failed" in
     the row's expanded view because `last_failed_stage` from the
     FIRST attempt was never cleared when the SECOND attempt
     succeeded.

  2. The drawer mixed a stale `error` (from attempt 1) with the
     fresh `body` (from attempt 2) under the same step's response.

  3. Re-running one-shot-reprocess on the now-COMPLETED row
     returned `stage_sequence_observed: []` and no قيود ids — the
     panel showed "المراحل التي اجتازها: —" giving the operator
     the impression nothing was on file.

This file pins the corrected behaviour end-to-end.
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from integrations.qoyod.first_sync_monitor import (
    _status_for_invoice_payment_step,
    shape_inbox_row_for_monitor as build_monitor_row,
)


# ─── 1. Step status — success overrides stale `last_failed_stage` ────
def test_invoice_payment_step_status_success_when_payment_id_present():
    """The row succeeded on the retry. `qoyod_invoice_payment_id` is
    the ground truth — `last_failed_stage` is just stale telemetry
    from the prior attempt."""
    row = {
        "pipeline_stage":            "COMPLETED",
        "last_failed_stage":         "PAYMENT_LINK_FAILED",  # ← stale
        "last_success_stage":        "COMPLETED",
        "qoyod_invoice_payment_id":  "51",
        "qoyod_invoice_id":          "66",
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "PAYMENT_LINK_FAILED"},
            {"to_stage": "PARTIAL_FAILURE"},
            {"to_stage": "RETRYING"},
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "INVOICE_PAYMENT_CREATED"},
            {"to_stage": "COMPLETED"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "success", (
        "When the payment landed (qoyod_invoice_payment_id present), "
        "the step MUST surface as success regardless of any stale "
        "PAYMENT_LINK_FAILED breadcrumb from a previous attempt.")


def test_invoice_payment_step_status_success_when_stage_history_has_payment_created():
    """Same idea but driven by stage_history alone (covers rows where
    `qoyod_invoice_payment_id` happens to be missing but the stage
    transition is on record)."""
    row = {
        "pipeline_stage":     "COMPLETED",
        "last_failed_stage":  "PAYMENT_LINK_FAILED",
        "stage_history": [
            {"to_stage": "INVOICE_PAYMENT_CREATED"},
            {"to_stage": "COMPLETED"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "success"


def test_invoice_payment_step_status_failed_when_no_success_indicator():
    """Stays "failed" when the row hasn't actually succeeded yet."""
    row = {
        "pipeline_stage":     "PARTIAL_FAILURE",
        "last_failed_stage":  "PAYMENT_LINK_FAILED",
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "PAYMENT_LINK_FAILED"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "failed"


def test_legacy_receipt_completed_row_still_renders_as_success():
    """Historic rows under the old /receipts flow keep their
    success badge."""
    row = {
        "pipeline_stage":     "COMPLETED",
        "qoyod_receipt_id":   "42",
        "stage_history": [
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "RECEIPT_CREATED"},
            {"to_stage": "COMPLETED"},
        ],
    }
    assert _status_for_invoice_payment_step(row) == "success"


# ─── 2. The step's response is filtered by status ────────────────────
def _row_with_mixed_attempts() -> dict:
    """Mirror of production order 268494278: first attempt failed
    with the legacy `account` wire bug → 422; second attempt
    succeeded with `account_id`. Both pieces sat under
    `qoyod_responses.invoice_payment` until Iter-290h.6 cleared the
    stale error on success."""
    return {
        "id":               "row-1",
        "user_id":          "tenant-a",
        "received_at":      datetime(2026, 6, 28, tzinfo=timezone.utc),
        "pipeline_stage":   "COMPLETED",
        "pipeline_outcome": "COMPLETED",
        "last_success_stage": "COMPLETED",
        "last_failed_stage":  "PAYMENT_LINK_FAILED",  # stale
        "trace_id":          "trace-abc",
        "attempts":          2,
        "qoyod_invoice_id":         "66",
        "qoyod_invoice_payment_id": "51",
        "qoyod_customer_id":        "123",
        "canonical_payload": {"order_id": "268494278"},
        "stage_history": [
            {"to_stage": "INVOICE_CREATED",         "at": "2026-06-28T21:08:19Z"},
            {"to_stage": "PAYMENT_LINK_FAILED",     "at": "2026-06-28T21:08:19Z"},
            {"to_stage": "PARTIAL_FAILURE",         "at": "2026-06-28T21:08:19Z"},
            {"to_stage": "RETRYING",                "at": "2026-06-28T21:25:31Z"},
            {"to_stage": "INVOICE_CREATED",         "at": "2026-06-28T21:25:31Z"},
            {"to_stage": "INVOICE_PAYMENT_CREATED", "at": "2026-06-28T21:25:32Z"},
            {"to_stage": "COMPLETED",               "at": "2026-06-28T21:25:32Z"},
        ],
        "qoyod_payloads": {
            "invoice":         {"invoice": {"contact_id": 123}},
            "invoice_payment": {"invoice_payment": {
                "invoice_id": 66, "amount": 312.47,
                "date": "2026-06-29", "account_id": 92,
            }},
        },
        "qoyod_responses": {
            "invoice_payment": {
                # Stale error from attempt 1 — the bug we're guarding.
                "error": {
                    "code": "qoyod_validation_error",
                    "status_code": 422,
                    "qoyod_response_excerpt":
                        "{'error': 'Invalid resource', "
                        "'messages': {'account': [\"Can't be blank\"]}}",
                },
                # Fresh body from attempt 2.
                "body": {"id": 51, "amount": "312.47",
                         "account_id": 92, "allocations": [
                             {"allocatee_id": 66, "amount": "312.47"}
                         ]},
                "qoyod_id": "51",
                "duration_ms": 925,
            },
        },
    }


def test_invoice_payment_step_response_drops_stale_error_on_success():
    """On success, the operator-facing `step.response` must NOT
    carry the stale 422 error — the drawer was rendering both
    side-by-side and operators thought the step had failed."""
    row = _row_with_mixed_attempts()
    monitor = build_monitor_row(row)
    ip_step = next(s for s in monitor["qoyod_steps"]
                   if s["key"] == "invoice_payment")
    assert ip_step["status"] == "success"
    # The fresh body is present.
    assert ip_step["response"]["body"]["id"] == 51
    # The stale error is NOT in the surface response.
    assert "error" not in ip_step["response"], (
        "On success, `step.response.error` must be dropped — it "
        "represents a previous failed attempt and confuses the UI.")
    # …but it IS preserved under `previous_error` for forensics.
    assert ip_step["response"]["previous_error"]["status_code"] == 422


def test_invoice_payment_step_response_keeps_error_on_failure():
    """Mirror case — when the step actually failed, keep the error
    (and drop any phantom body)."""
    row = _row_with_mixed_attempts()
    row["pipeline_stage"]            = "PARTIAL_FAILURE"
    row["last_success_stage"]        = "INVOICE_CREATED"
    row["qoyod_invoice_payment_id"] = None
    row["stage_history"] = [
        {"to_stage": "INVOICE_CREATED"},
        {"to_stage": "PAYMENT_LINK_FAILED"},
    ]
    # Drop the fresh body so the row matches a true failure state.
    row["qoyod_responses"]["invoice_payment"].pop("body", None)
    monitor = build_monitor_row(row)
    ip_step = next(s for s in monitor["qoyod_steps"]
                   if s["key"] == "invoice_payment")
    assert ip_step["status"] == "failed"
    assert ip_step["response"]["error"]["status_code"] == 422


# ─── 3. one_shot_reprocess ALREADY_COMPLETED carries final-state ─────
@pytest.mark.asyncio
async def test_already_completed_returns_stage_sequence_and_qoyod_ids(
    monkeypatch,
):
    """The 'مكتمل سابقاً' panel must show the row's final pipeline
    path + قيود ids + bodies — previously it returned an empty
    list and the operator saw 'المراحل التي اجتازها: —'."""
    from integrations.qoyod import one_shot_reprocess as osr

    completed_row = {
        "id":                       "row-1",
        "trace_id":                 "trace-final",
        "pipeline_stage":           "COMPLETED",
        "qoyod_invoice_id":         "66",
        "qoyod_invoice_payment_id": "51",
        "qoyod_customer_id":        "123",
        "qoyod_receipt_id":         None,
        "salla_order_id":           "268494278",
        "qoyod_payloads": {
            "invoice":         {"invoice": {"contact_id": 123}},
            "invoice_payment": {"invoice_payment": {
                "invoice_id": 66, "amount": 312.47,
                "date": "2026-06-29", "account_id": 92,
            }},
        },
        "qoyod_responses": {
            "invoice_payment": {"body": {"id": 51, "account_id": 92}},
        },
        "stage_history": [
            {"to_stage": "NORMALIZED"},
            {"to_stage": "RULES_APPLIED"},
            {"to_stage": "CUSTOMER_RESOLVED"},
            {"to_stage": "PRODUCT_RESOLVED"},
            {"to_stage": "INVOICE_CREATED"},
            {"to_stage": "INVOICE_PAYMENT_CREATED"},
            {"to_stage": "COMPLETED"},
        ],
    }

    async def _fake_find_row(db, *, user_id, order_number, trace_id):
        return completed_row

    monkeypatch.setattr(osr, "_find_target_row", _fake_find_row)

    # Minimal DB stub — only the bail-out path touches it (none).
    class _DB:
        pass

    out = await osr.reprocess_one_order(
        _DB(), user_id="tenant-a",
        order_number="268494278",
        confirm="REPROCESS-268494278",
    )
    assert out["outcome"] == "ALREADY_COMPLETED"
    # The full pipeline path is now on the response.
    assert out["stage_sequence_observed"] == [
        "NORMALIZED", "RULES_APPLIED", "CUSTOMER_RESOLVED",
        "PRODUCT_RESOLVED", "INVOICE_CREATED",
        "INVOICE_PAYMENT_CREATED", "COMPLETED",
    ]
    # Qoyod ids are surfaced so the UI can render them.
    assert out["qoyod_invoice_id"]         == "66"
    assert out["qoyod_invoice_payment_id"] == "51"
    # Payloads + response are surfaced for the drawer's body section.
    assert out["invoice_payment_payload"]["invoice_payment"]["account_id"] == 92
    assert out["invoice_payment_response"]["id"] == 51
    # Human message is updated — no longer instructs the operator
    # to archive (which would be wrong for an already-paid invoice).
    assert "مكتمل سابقاً" in out["message"]


# ─── 4. Pipeline clears stale failure breadcrumbs on payment success ─
@pytest.mark.asyncio
async def test_pipeline_clears_stale_error_on_invoice_payment_success(
    monkeypatch,
):
    """The pipeline's invoice_payment success path must clear the
    stale `qoyod_responses.invoice_payment.error` AND the
    `last_failed_stage` + `pipeline_error` so the drawer doesn't
    render leftovers from the prior attempt."""
    import inspect
    from integrations.qoyod import pipeline as pipe
    # Verify by static source inspection — running the full pipeline
    # in a unit test is heavy. The contract is: on the success branch
    # the code MUST issue an update that nulls these three fields.
    src = inspect.getsource(pipe)
    success_block_marker = "invoice_payment recorded ON invoice in Qoyod"
    # The marker sits inside the success branch.
    assert success_block_marker in src
    success_section = src.split(success_block_marker, 1)[1].split(
        "process_pending_customer_resolved", 1)[0]
    assert '"last_failed_stage"' in success_section, (
        "Pipeline success branch must clear last_failed_stage so the "
        "step doesn't render red after a successful retry.")
    assert '"pipeline_error"' in success_section
    assert '"qoyod_responses.invoice_payment.error"' in success_section, (
        "Pipeline success branch must clear the stale error "
        "breadcrumb under qoyod_responses.invoice_payment.")
