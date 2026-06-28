"""Iter-290h.4 — one-shot-reprocess diagnostics for PAYMENT_LINK_FAILED.

User report: After Iter-290h.3 (fixing `date` + `account` field names),
the one-shot-reprocess UI still showed the old "request_body_json تم
إيقافه (لم يُرسَل لقيود)" label even when قيود actually 4xx'd the new
payload. This locked in:

  • `_build_failure_response` carries explicit diagnostic fields for
    PAYMENT_LINK_FAILED + PAYMENT_METHOD_MAPPING_MISSING:
      - `payment_post_attempted`
      - `request_sent_to_qoyod`
      - `qoyod_status_code`
      - `qoyod_response`
      - `skip_reason`
      - `request_body_json`
  • The frontend chooses one of THREE labels based on those fields:
      "أُرسل ونجح" / "أُرسل ورُفض" / "تم إيقافه قبل الإرسال"
"""
from __future__ import annotations

from integrations.qoyod.one_shot_reprocess import _build_failure_response


def test_payment_link_failed_surfaces_sent_to_qoyod_with_response_excerpt():
    """Real قيود 4xx on the new payload — operator must see
    `request_sent_to_qoyod=True` plus the actual Qoyod response."""
    pe = {
        "code":         "qoyod_validation_error",
        "message":      "Invalid resource",
        "status_code":  422,
        "endpoint":     "POST /invoice_payments",
        "qoyod_response_excerpt": '{"error":"Invalid resource"}',
        "request_body_json": {
            "invoice_payment": {
                "invoice_id": 63, "amount": 131.92,
                "date": "2026-06-28", "account": 94,
                "reference": "269048975",
                "description": "Mezan · Salla order 269048975",
            }
        },
    }
    resp = _build_failure_response(
        outcome="PARTIAL_FAILURE", row_id="r1", trace_id="t1",
        pipeline_error=pe, last_failed_stage="PAYMENT_LINK_FAILED",
        canonical_payload={"order_id": "269048975"},
        invoice_snapshot=None,
        stage_sequence=["CUSTOMER_RESOLVED", "PRODUCT_RESOLVED",
                        "INVOICE_CREATED", "PAYMENT_LINK_FAILED"],
        quarantine_summary={},
    )
    assert resp["payment_post_attempted"] is True
    assert resp["request_sent_to_qoyod"]  is True
    assert resp["qoyod_status_code"]      == 422
    assert resp["qoyod_response"]         == '{"error":"Invalid resource"}'
    assert resp["skip_reason"]            is None
    # request_body_json carries the NEW correct payload — not a stale one.
    body = resp["request_body_json"]["invoice_payment"]
    assert body["date"]    == "2026-06-28"
    assert body["account"] == 94
    assert "payment_date" not in body
    assert "payment_method_id" not in body


def test_payment_method_mapping_missing_surfaces_skip_reason_not_sent():
    """Pre-POST guard — request was NEVER sent. UI must label it as
    'halted before send', not 'rejected by قيود'."""
    pe = {
        "code":           "payment_method_mapping_missing",
        "message":        "no mapping for 'mada'",
        "request_body_json": {"invoice_payment": {"account": None}},
    }
    resp = _build_failure_response(
        outcome="PARTIAL_FAILURE", row_id="r1", trace_id="t1",
        pipeline_error=pe,
        last_failed_stage="PAYMENT_METHOD_MAPPING_MISSING",
        canonical_payload={"order_id": "X"},
        invoice_snapshot=None,
        stage_sequence=["INVOICE_CREATED",
                        "PAYMENT_METHOD_MAPPING_MISSING"],
        quarantine_summary={},
    )
    assert resp["payment_post_attempted"] is False
    assert resp["request_sent_to_qoyod"]  is False
    assert resp["qoyod_status_code"]      is None
    assert resp["skip_reason"]
    assert "طرق الدفع" in resp["skip_reason"]
