"""Iter-272 — stage-specific FAILED_PRODUCT diagnostics surface
`selling_price` + `is_sold` activation flag visibility.

The user must be able to see in the modal that the live deploy is
sending BOTH:
  • selling_price (not sale_price)
  • is_sold: true
A green verdict ONLY fires when both conditions hold.
"""
from __future__ import annotations

from integrations.qoyod.one_shot_reprocess import _build_failure_response


def _pe_product_create(*, body=None):
    """Synthesise a `pipeline_error` dict shaped like
    `QoyodAPIError.to_log_dict()` from a /products POST 422."""
    return {
        "code":        "qoyod_validation_error",
        "message":     "{'base': ['enter at least a purchase price or a sales price to continue.']}",
        "status_code": 422,
        "endpoint":    "POST /products",
        "qoyod_response_excerpt":
            '{"base":["enter at least a purchase price or a sales price to continue."]}',
        "request_body_json": body,
    }


CANONICAL = {
    "order_number": "268670571",
    "items": [{"sku": "AMS11961", "name": "تغليف", "unit_price": 5.0,
               "quantity": 1, "tax_amount": 0, "total": 5}],
}


# ── FIXED deploy: selling_price + is_sold: true ─────────────────────
def test_failed_product_verdict_is_green_when_full_fix_deployed():
    body = {"product": {
        "sku": "AMS11961", "name": "تغليف", "type": "service",
        "is_non_stock": True, "is_sold": True, "is_bought": False,
        "selling_price": 5.0,
    }}
    resp = _build_failure_response(
        outcome="DEAD_LETTER", row_id="r1", trace_id="t1",
        pipeline_error=_pe_product_create(body=body),
        last_failed_stage="FAILED_PRODUCT",
        canonical_payload=CANONICAL,
        invoice_snapshot=None,
        stage_sequence=["NORMALIZED", "CUSTOMER_RESOLVED", "PRODUCT_RESOLVED"],
        quarantine_summary={},
    )
    pc = resp["product_create"]
    assert pc["selling_price_field_present"] is True
    assert pc["sale_price_field_present"] is False
    assert pc["is_sold_flag"] is True
    assert pc["selling_price_in_request_body"] == 5.0
    assert pc["sku_in_request_body"] == "AMS11961"
    assert pc["deploy_carries_full_fix"] is True
    # Stale invoice snapshot must not appear.
    assert "invoice_payload" not in resp


# ── Partial deploy: selling_price present BUT is_sold missing ───────
def test_failed_product_verdict_is_red_when_is_sold_missing():
    body = {"product": {
        "sku": "X", "selling_price": 5.0,
        # NB: no is_sold flag — exactly the bug that caused order 268670571 to fail.
    }}
    resp = _build_failure_response(
        outcome="DEAD_LETTER", row_id="r2", trace_id="t2",
        pipeline_error=_pe_product_create(body=body),
        last_failed_stage="FAILED_PRODUCT",
        canonical_payload=CANONICAL,
        invoice_snapshot=None,
        stage_sequence=[], quarantine_summary={},
    )
    pc = resp["product_create"]
    assert pc["selling_price_field_present"] is True
    assert pc["is_sold_flag"] is None      # absent in body
    assert pc["deploy_carries_full_fix"] is False, \
        "verdict must be red when is_sold flag is missing — that is the bug"


# ── Old deploy: still using sale_price ──────────────────────────────
def test_failed_product_verdict_is_red_when_using_sale_price():
    body = {"product": {"sku": "X", "sale_price": 5.0, "is_sold": True}}
    resp = _build_failure_response(
        outcome="DEAD_LETTER", row_id="r3", trace_id="t3",
        pipeline_error=_pe_product_create(body=body),
        last_failed_stage="FAILED_PRODUCT",
        canonical_payload=CANONICAL,
        invoice_snapshot=None,
        stage_sequence=[], quarantine_summary={},
    )
    pc = resp["product_create"]
    assert pc["sale_price_field_present"]    is True
    assert pc["selling_price_field_present"] is False
    assert pc["deploy_carries_full_fix"] is False


# ── Error block always carries the full Qoyod context ────────────────
def test_failed_product_error_block_has_full_qoyod_context():
    resp = _build_failure_response(
        outcome="DEAD_LETTER", row_id="r4", trace_id="t4",
        pipeline_error=_pe_product_create(body={"product": {"sku": "X"}}),
        last_failed_stage="FAILED_PRODUCT",
        canonical_payload=CANONICAL,
        invoice_snapshot=None,
        stage_sequence=[], quarantine_summary={},
    )
    err = resp["error"]
    assert err["code"] == "qoyod_validation_error"
    assert err["status_code"] == 422
    assert err["endpoint"] == "POST /products"
    assert "enter at least" in (err["qoyod_response_excerpt"] or "")


# ── FAILED_INVOICE / FAILED_RECEIPT still surfaces invoice_snapshot ──
def test_failed_invoice_still_surfaces_invoice_payload():
    pe = {"code": "qoyod_validation_error", "message": "x",
          "status_code": 422, "endpoint": "POST /invoices"}
    inv = {"invoice": {"contact_id": "109", "line_items": []}}
    resp = _build_failure_response(
        outcome="DEAD_LETTER", row_id="r5", trace_id="t5",
        pipeline_error=pe, last_failed_stage="FAILED_INVOICE",
        canonical_payload=CANONICAL, invoice_snapshot=inv,
        stage_sequence=[], quarantine_summary={},
    )
    assert resp["invoice_payload"] == inv
    assert "product_create" not in resp


# ── Totals Guard refusal (Iter-273) ──────────────────────────────────
def test_totals_guard_refusal_is_surfaced_as_dedicated_block():
    """`line_items_incomplete` / `line_items_total_mismatch` /
    `order_total_mismatch` errors produce a `totals_guard` block in
    the response (not the generic product_create / invoice block)."""
    pe = {
        "code":    "line_items_incomplete",
        "message": "items_sum_excl=5.0 but subtotal=105.0 (shortfall=100.0)",
        "details": {
            "items_count":    1,
            "items_sum_excl": 5.0,
            "subtotal":       105.0,
            "shortfall":      100.0,
            "parsed_items":   [{"sku": "AMS11961", "unit_price": 5.0}],
        },
    }
    resp = _build_failure_response(
        outcome="DEAD_LETTER", row_id="r7", trace_id="t7",
        pipeline_error=pe, last_failed_stage="FAILED_VALIDATION",
        canonical_payload=CANONICAL, invoice_snapshot=None,
        stage_sequence=["NORMALIZED"], quarantine_summary={},
    )
    assert resp["totals_guard"]["code"] == "line_items_incomplete"
    assert resp["totals_guard"]["details"]["shortfall"] == 100.0
    # Stage-specific blocks should NOT appear for a Totals Guard refusal.
    assert "product_create" not in resp
    assert "invoice_payload" not in resp


def test_totals_guard_surface_covers_all_three_codes():
    for code in ("line_items_incomplete",
                 "line_items_total_mismatch",
                 "order_total_mismatch"):
        resp = _build_failure_response(
            outcome="DEAD_LETTER", row_id="r", trace_id="t",
            pipeline_error={"code": code, "message": "x", "details": {}},
            last_failed_stage="FAILED_VALIDATION",
            canonical_payload={}, invoice_snapshot=None,
            stage_sequence=[], quarantine_summary={},
        )
        assert resp.get("totals_guard", {}).get("code") == code, \
            f"missing totals_guard surface for {code}"


def test_expected_from_canonical_uses_selling_price_key():
    """The expected block must mirror the actual field name we send."""
    resp = _build_failure_response(
        outcome="DEAD_LETTER", row_id="r6", trace_id="t6",
        pipeline_error=_pe_product_create(body={"product": {"sku": "X"}}),
        last_failed_stage="FAILED_PRODUCT",
        canonical_payload=CANONICAL, invoice_snapshot=None,
        stage_sequence=[], quarantine_summary={},
    )
    expected = resp["product_create"]["expected_from_canonical"]
    assert expected["sku"] == "AMS11961"
    assert expected["selling_price_we_would_send"] == 5.0
