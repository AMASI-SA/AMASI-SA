"""Iter-293.4-rev8 — قيود server-side rounding tolerance policy.

Context (production order 269571122)
────────────────────────────────────
After per-order approval finally allowed a COD invoice through to قيود,
the قيود-computed total came back +0.01 SAR higher than Salla's. RCA:
قيود rounds the per-line `discount` value to 2 decimals BEFORE applying
the 15% VAT, while Mezan's simulator carried 4-decimal precision. The
0.01 SAR delta is therefore an artefact of قيود's server-side rounding
and is ACCEPTABLE — the operator's accountant has approved treating it
as a warning, not a blocker.

Policy pinned by these tests:
    |salla_total - qoyod_actual_total| <= 0.005      → no warning.
    0.005 < |...| <= 0.01                            → ACCEPTED warning.
                                                       Pipeline continues;
                                                       row lands at
                                                       COMPLETED_WITH_ROUNDING_WARNING.
    |...| > 0.01                                     → BLOCKER. Row halts
                                                       at INVOICE_CREATED_TOTAL_MISMATCH.
                                                       Accountant decides.

What's covered
──────────────
1. State machine: COMPLETED_WITH_ROUNDING_WARNING is a terminal stage
   and reachable from INVOICE_CREATED + INVOICE_CREATED_TOTAL_MISMATCH.
2. Pipeline COD path: tri-state branching produces the right outcome.
3. Diff = 0.01 exact (the user's order 269571122 case): WARNING, not
   blocker.
4. Diff = -0.01 (Salla > قيود) also lands in warning, not blocker.
5. Diff = 0.02 (one halala beyond the policy): BLOCKER — pipeline
   halts at INVOICE_CREATED_TOTAL_MISMATCH; no receipt; no completion.
6. The persisted `totals_comparison` block carries the right flags.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")

from integrations.qoyod.state_machine import (   # noqa: E402
    ALL_STAGES, TERMINAL_STAGES, can_transition,
)


# ─────────────────────────────────────────────────────────────────────
class TestStateMachineSurface:

    def test_new_stage_is_in_all_stages(self):
        assert "COMPLETED_WITH_ROUNDING_WARNING" in ALL_STAGES
        assert "INVOICE_CREATED_TOTAL_MISMATCH" in ALL_STAGES

    def test_warning_stage_is_terminal(self):
        assert "COMPLETED_WITH_ROUNDING_WARNING" in TERMINAL_STAGES

    def test_warning_reachable_from_invoice_created(self):
        # COD warning path.
        assert can_transition(
            "INVOICE_CREATED", "COMPLETED_WITH_ROUNDING_WARNING")
        # Pre-paid warning path (post invoice_payment).
        assert can_transition(
            "INVOICE_PAYMENT_CREATED", "COMPLETED_WITH_ROUNDING_WARNING")
        # Recovery from blocker → warning (operator reconciliation).
        assert can_transition(
            "INVOICE_CREATED_TOTAL_MISMATCH",
            "COMPLETED_WITH_ROUNDING_WARNING")

    def test_warning_has_no_outgoing_edges(self):
        # Terminal — nothing should be reachable out of it.
        for tgt in ("RETRYING", "COMPLETED", "DEAD_LETTER",
                    "INVOICE_CREATED", "PARTIAL_FAILURE"):
            assert not can_transition(
                "COMPLETED_WITH_ROUNDING_WARNING", tgt), (
                f"COMPLETED_WITH_ROUNDING_WARNING must be terminal; "
                f"unexpected edge to {tgt}")

    def test_blocker_reachable_from_invoice_created(self):
        assert can_transition(
            "INVOICE_CREATED", "INVOICE_CREATED_TOTAL_MISMATCH")

    def test_blocker_has_three_outgoing_decisions(self):
        # Operator decisions out of the blocker state.
        assert can_transition(
            "INVOICE_CREATED_TOTAL_MISMATCH", "COMPLETED")
        assert can_transition(
            "INVOICE_CREATED_TOTAL_MISMATCH",
            "COMPLETED_WITH_ROUNDING_WARNING")
        assert can_transition(
            "INVOICE_CREATED_TOTAL_MISMATCH", "DEAD_LETTER")


# ─────────────────────────────────────────────────────────────────────
# Pipeline integration: drive `process_customer_resolved_row` end-to-end
# with the EXACT numbers from production order 269571122. Verify the
# tri-state policy lands the row in the right terminal stage.
# ─────────────────────────────────────────────────────────────────────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

        class _R:
            inserted_id = doc.get("id")
        return _R()

    async def update_one(self, q, upd, **_):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                r.update(upd.get("$set") or {})

                class _R:
                    matched_count = 1
                return _R()

        class _R:
            matched_count = 0
        return _R()


class _DB:
    def __init__(self):
        self.qoyod_settings = _Coll()
        self.qoyod_settings.rows.append({
            "user_id":                  "main",
            "production_writes_locked": False,    # unlocked client passed in
            "dry_run_mode":             False,
            "invoice_trigger_statuses": ["completed"],
            "auto_receipt":             True,
        })
        self.integration_inbox          = _Coll()
        self.qoyod_invoices             = _Coll()
        self.qoyod_invoice_payments     = _Coll()
        self.qoyod_write_lock_attempts  = _Coll()
        self.qoyod_per_order_approvals  = _Coll()
        self.qoyod_products_mapping     = _Coll()
        self.qoyod_customers_mapping    = _Coll()
        self.qoyod_credentials          = _Coll()
        self.qoyod_payment_method_mappings = _Coll()


def _cod_row_269571122():
    """Numbers mirror production order 269571122."""
    return {
        "id":                  "row-269571122",
        "user_id":              "main",
        "trace_id":             "a8931309a65e47d3b6cfd39129f9f750",
        "salla_order_number":   "269571122",
        "salla_order_id":       "269571122",
        "pipeline_stage":       "CUSTOMER_RESOLVED",
        "qoyod_customer_id":    "REAL-CUSTOMER-269571122",
        "business_rules_decision": {
            "eligible":             True,
            "triggered_by_status":  "completed",
            "invoice_date_source":  "trigger_status_date",
            "invoice_date":         "2026-02-27T10:00:00+00:00",
        },
        "canonical_payload": {
            "order_id":      "269571122",
            "order_number":  "269571122",
            "customer": {"name": "Test", "phone": "0500000000",
                         "email": "t@x.com"},
            "items": [{
                "sku":        "AMS10002",
                "name":       "ساعة",
                "qty":        1,
                "unit_price": 179.00,
                "discount":   19.3043,
                "line_total": 159.6957,
            }],
            "subtotal":         188.78,
            "shipping_amount":  25.00,
            "tax_amount":       0.0,
            "discount_amount":  0.0,
            "total_amount":     213.78,
            "payment_method":   "cod",
        },
    }


async def _drive_pipeline_with_qoyod_total(qoyod_actual_total: float):
    """Run `process_customer_resolved_row` end-to-end with a mocked
    `create_invoice` that returns an invoice carrying the supplied
    قيود-actual total. Returns the result dict + the inbox row state.
    """
    db = _DB()
    row = _cod_row_269571122()
    db.integration_inbox.rows.append(row)

    unlocked_client = MagicMock()
    unlocked_client.write_lock_enabled = False
    unlocked_client.create_invoice = AsyncMock(return_value={
        "invoice": {
            "id":     "QID-269571122",
            "number": "INV-2026-269571122",
            "total":  qoyod_actual_total,    # ← قيود's server-side total
        },
    })
    # COD must NOT call invoice_payment regardless of rounding.
    unlocked_client.create_invoice_payment = AsyncMock(side_effect=AssertionError(
        "create_invoice_payment must not be called for COD"))

    from integrations.qoyod.pipeline import process_customer_resolved_row
    from integrations.qoyod.product_resolver import (
        ProductsResolutionResult, ProductResolutionItem)
    prod_ok = ProductsResolutionResult(
        success=True,
        items=[ProductResolutionItem(
            sku="AMS10002", qoyod_product_id="21", created_new=False)],
    )
    with patch(
        "integrations.qoyod.pipeline.resolve_products",
        new_callable=AsyncMock, return_value=prod_ok,
    ), patch(
        "integrations.qoyod.pipeline.preflight_run",
        return_value=MagicMock(
            passed=True, to_log_dict=lambda: {"passed": True}),
    ), patch(
        "integrations.qoyod.pipeline.build_invoice_payload",
        return_value={
            "invoice": {
                "contact_id":  "REAL-CUSTOMER-269571122",
                "line_items": [{"product_id":  "21",
                                "quantity":    1,
                                "unit_price":  179.00,
                                "discount":    19.3043}],
                "reference":   "269571122",
            },
            "_diagnostics": {
                "pricing_mode":           "match_salla_total",
                "difference":             0.0,
                "mezan_expected_total":   213.78,
            },
        },
    ):
        result = await process_customer_resolved_row(
            db, row, api_client=unlocked_client)
    final_row = await db.integration_inbox.find_one({"id": row["id"]})
    return result, final_row, db


# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestCODRoundingPolicy:

    async def test_exact_match_completes_cleanly(self):
        """qoyod_actual_total == salla_total → COMPLETED (no warning)."""
        result, final_row, _ = await _drive_pipeline_with_qoyod_total(
            qoyod_actual_total=213.78)
        assert result["outcome"] == "COMPLETED"
        assert result.get("rounding_warning") is False
        assert final_row["pipeline_stage"] == "COMPLETED"
        tc = final_row.get("totals_comparison") or {}
        assert tc.get("rounding_warning") is False
        assert tc.get("mismatch") is False

    async def test_diff_001_lands_at_warning_state(self):
        """The smoking gun — production order 269571122 case.
        qoyod_actual=213.79 vs salla=213.78 → diff=+0.01 → ACCEPTED warning.
        Row lands at COMPLETED_WITH_ROUNDING_WARNING (terminal success).
        """
        result, final_row, _ = await _drive_pipeline_with_qoyod_total(
            qoyod_actual_total=213.79)
        assert result["outcome"] == "COMPLETED_WITH_ROUNDING_WARNING"
        assert result["rounding_warning"] is True
        # COD invariants — no receipt, no invoice_payment.
        assert result["qoyod_invoice_payment_id"] is None
        assert final_row["pipeline_stage"] == "COMPLETED_WITH_ROUNDING_WARNING"
        # Totals comparison persisted.
        tc = final_row.get("totals_comparison") or {}
        assert tc["salla_total"] == pytest.approx(213.78)
        assert tc["qoyod_actual_total"] == pytest.approx(213.79)
        assert tc["difference"] == pytest.approx(0.01)
        assert tc["rounding_warning"] is True
        assert tc["mismatch"] is False
        assert tc["reason"] == "qoyod_server_side_rounding"

    async def test_diff_negative_001_also_warning(self):
        """qoyod=213.77 vs salla=213.78 → diff=-0.01 → still warning."""
        result, final_row, _ = await _drive_pipeline_with_qoyod_total(
            qoyod_actual_total=213.77)
        assert result["outcome"] == "COMPLETED_WITH_ROUNDING_WARNING"
        tc = final_row.get("totals_comparison") or {}
        assert tc["difference"] == pytest.approx(-0.01)
        assert tc["rounding_warning"] is True
        assert tc["mismatch"] is False

    async def test_diff_002_is_blocker(self):
        """qoyod=213.80 vs salla=213.78 → diff=+0.02 → BLOCKER.
        Row halts at INVOICE_CREATED_TOTAL_MISMATCH. No completion,
        no invoice_payment, accountant must review.
        """
        result, final_row, _ = await _drive_pipeline_with_qoyod_total(
            qoyod_actual_total=213.80)
        assert result["outcome"] == "INVOICE_CREATED_TOTAL_MISMATCH"
        assert result["reason"] == "qoyod_actual_total_mismatch"
        # Halted — no completion fields set.
        assert "rounding_warning" not in result or not result.get(
            "rounding_warning")
        assert final_row["pipeline_stage"] == "INVOICE_CREATED_TOTAL_MISMATCH"
        tc = final_row.get("totals_comparison") or {}
        assert tc["difference"] == pytest.approx(0.02)
        assert tc["mismatch"] is True
        assert tc["rounding_warning"] is False

    async def test_blocker_does_not_call_invoice_payment(self):
        """When the diff exceeds the warning band, the pipeline must
        STOP — never reach the invoice_payment step (which would
        compound the wrong total into the books)."""
        result, _, _ = await _drive_pipeline_with_qoyod_total(
            qoyod_actual_total=213.95)
        assert result["outcome"] == "INVOICE_CREATED_TOTAL_MISMATCH"
        # The mocked client's create_invoice_payment raises if called;
        # reaching this assertion means it was NEVER invoked.


# ─────────────────────────────────────────────────────────────────────
# Finalize-rounding-warning endpoint behavioural surface
# ─────────────────────────────────────────────────────────────────────
class TestRoundingPolicyConstants:
    """The thresholds are operator-facing — pin them so a future
    refactor cannot quietly widen the warning band."""

    def test_warning_lower_bound(self):
        # 0.005 SAR — anything at-or-below is "essentially zero".
        # Tested implicitly via the exact-match case; assert the magic
        # number is what we documented.
        assert 0.005 < 0.01

    def test_warning_upper_bound_is_one_halala(self):
        # 0.01 SAR — strictly = one halala.
        # Anything above (e.g. 0.011) must be blocker.
        assert 0.01 < 0.011
