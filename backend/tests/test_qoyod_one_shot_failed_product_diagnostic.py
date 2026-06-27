"""Iter-271 — stage-specific FAILED_PRODUCT diagnostics on one-shot reprocess.

The user wants the one-shot reprocess modal to surface the EXACT product
create payload (not a stale invoice snapshot from a previous attempt) so
they can verify the live deploy actually carries the `sale_price` fix.

Coverage:
  • `_build_failure_response` for FAILED_PRODUCT carries:
      - `product_create.endpoint`, `status_code`, `request_body`,
        `response_excerpt`, `sale_price_field_present`,
        `selling_price_field_present`, `sale_price_in_request_body`,
        `sku_in_request_body`, `deploy_carries_sale_price_fix`.
  • The stale invoice snapshot is NOT surfaced for FAILED_PRODUCT.
  • The verdict flag flips correctly between fixed / unfixed deploys.
"""
from __future__ import annotations

from integrations.qoyod.one_shot_reprocess import _build_failure_response


def _pe_for_product_create(*, sale_price_value=5.0,
                           field_name="sale_price",
                           sku="AMS11961"):
    """Synthesise a `pipeline_error` dict shaped like what
    `QoyodAPIError.to_log_dict()` produces when /products POST fails."""
    body = {"product": {
        "name": "تغليف",
        "sku":  sku,
        "type": "service",
        "is_non_stock": True,
        field_name: sale_price_value,
    }}
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


# ── FIXED deploy (sale_price present, selling_price absent) ──────────
def test_failed_product_surfaces_product_create_diagnostic_when_fixed():
    pe = _pe_for_product_create()
    resp = _build_failure_response(
        outcome="DEAD_LETTER",
        row_id="r1", trace_id="t1",
        pipeline_error=pe,
        last_failed_stage="FAILED_PRODUCT",
        canonical_payload=CANONICAL,
        invoice_snapshot={"invoice": {"contact_id": "109",
                                      "line_items": [{"product_id": "DRY:product:STALE"}]}},
        stage_sequence=["NORMALIZED", "CUSTOMER_RESOLVED", "PRODUCT_RESOLVED"],
        quarantine_summary={"product_mappings_quarantined": []},
    )

    assert resp["failed_at_stage"] == "FAILED_PRODUCT"
    pc = resp["product_create"]
    assert pc["endpoint"] == "POST /products"
    assert pc["status_code"] == 422
    assert pc["sku_in_request_body"] == "AMS11961"
    assert pc["sale_price_field_present"] is True
    assert pc["selling_price_field_present"] is False
    assert pc["sale_price_in_request_body"] == 5.0
    assert pc["deploy_carries_sale_price_fix"] is True
    assert pc["expected_from_canonical"]["sale_price_we_would_send"] == 5.0
    # The stale invoice snapshot MUST NOT be surfaced — it confuses
    # the diagnosis (it's from a pre-fix attempt).
    assert "invoice_payload" not in resp


# ── UNFIXED deploy (still using `selling_price`) ─────────────────────
def test_failed_product_diagnostic_detects_unfixed_deploy():
    pe = _pe_for_product_create(field_name="selling_price")
    resp = _build_failure_response(
        outcome="DEAD_LETTER",
        row_id="r2", trace_id="t2",
        pipeline_error=pe,
        last_failed_stage="FAILED_PRODUCT",
        canonical_payload=CANONICAL,
        invoice_snapshot=None,
        stage_sequence=["NORMALIZED", "CUSTOMER_RESOLVED"],
        quarantine_summary={},
    )
    pc = resp["product_create"]
    assert pc["sale_price_field_present"] is False
    assert pc["selling_price_field_present"] is True
    assert pc["deploy_carries_sale_price_fix"] is False, \
        "verdict must flip when the live deploy still ships `selling_price`"


# ── Generic error block (status, endpoint) preserved ─────────────────
def test_failed_product_error_block_has_full_qoyod_context():
    pe = _pe_for_product_create()
    resp = _build_failure_response(
        outcome="DEAD_LETTER",
        row_id="r3", trace_id="t3",
        pipeline_error=pe,
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


# ── FAILED_INVOICE still surfaces invoice_snapshot ───────────────────
def test_failed_invoice_still_surfaces_invoice_payload():
    pe = {"code": "qoyod_validation_error", "message": "x",
          "status_code": 422, "endpoint": "POST /invoices"}
    inv = {"invoice": {"contact_id": "109", "line_items": []}}
    resp = _build_failure_response(
        outcome="DEAD_LETTER",
        row_id="r4", trace_id="t4",
        pipeline_error=pe,
        last_failed_stage="FAILED_INVOICE",
        canonical_payload=CANONICAL,
        invoice_snapshot=inv,
        stage_sequence=[], quarantine_summary={},
    )
    assert resp["invoice_payload"] == inv
    assert "product_create" not in resp
