"""Iter-293.5-rev3 — Unified eligible statuses + BNPL classification.

Guards the fix for order 268307955 (Tabby / delivered) where the
pending queue surfaced the row as a Candidate but preflight rejected
it with `status_not_in_triggers`, and tabby_installment was routed
to the "Unsupported Method" tab even though the mapping existed.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.eligible_statuses import (
    ELIGIBLE_ORDER_STATUSES,
    is_eligible_status,
    resolve_trigger_statuses,
)


# ── Unified eligible-status set ────────────────────────────────────
class TestEligibleStatusUnification:
    def test_completed_delivered_shipped_shipping_processing_all_eligible(self):
        for s in ("completed", "delivered", "shipped", "shipping",
                  "processing", "in_progress"):
            assert s in ELIGIBLE_ORDER_STATUSES, s

    def test_arabic_natives_are_members(self):
        for s in ("تم التنفيذ", "تم التوصيل", "تم الشحن", "جاري التوصيل"):
            assert s in ELIGIBLE_ORDER_STATUSES, s

    def test_terminal_statuses_are_not_eligible(self):
        for s in ("cancelled", "canceled", "refunded", "deleted"):
            assert s not in ELIGIBLE_ORDER_STATUSES

    def test_resolve_honours_explicit_completed_only(self):
        # Explicit narrow list [`completed`] MUST be honoured
        # verbatim — merchants who want the stricter policy keep it.
        # Widening only kicks in when the field is missing/empty.
        narrowed = resolve_trigger_statuses({"invoice_trigger_statuses":
                                             ["completed"]})
        assert narrowed == ["completed"]

    def test_resolve_widens_missing_setting(self):
        widened = resolve_trigger_statuses({})
        assert set(widened) == set(ELIGIBLE_ORDER_STATUSES)

    def test_resolve_honours_explicit_narrowed_list(self):
        # Explicit override with 2 statuses must be preserved.
        narrowed = resolve_trigger_statuses(
            {"invoice_trigger_statuses": ["completed", "delivered"]})
        assert set(narrowed) == {"completed", "delivered"}

    def test_is_eligible_status_ignores_case(self):
        assert is_eligible_status("Completed")
        assert is_eligible_status("DELIVERED")
        assert is_eligible_status("Shipped")

    def test_is_eligible_status_respects_explicit_triggers(self):
        # Explicit narrow list — shipped is NOT allowed.
        assert not is_eligible_status(
            "shipped", triggers=["completed", "delivered"])
        assert is_eligible_status(
            "delivered", triggers=["completed", "delivered"])


# ── Preflight status gate ─────────────────────────────────────────
class TestPreflightStatusGate:
    """`delivered` on a tenant without an explicit invoice_trigger_statuses
    setting MUST pass. Regression for order 268307955."""

    def _run(self, order_status: str, extra_settings=None):
        from integrations.qoyod.preflight import run
        settings = {
            "tax_mode":                     "customer_first",
            "invoice_total_policy":         "match_salla_total",
            "default_inventory_id":         "42",
            "default_shipping_product_id":  "99",
        }
        if extra_settings:
            settings.update(extra_settings)
        return run(
            dto_dict={
                "order_status":   order_status,
                "payment_method": "tabby_installment",
                "items":          [{"sku": "X", "tax_amount": 0}],
                "shipping_amount": 0,
                "total_amount":   0,
            },
            settings=settings,
            qoyod_customer_id="7",
            product_resolutions=[{"sku": "X", "qoyod_product_id": "1"}],
        )

    def test_delivered_passes_when_no_explicit_triggers(self):
        result = self._run("delivered")
        # No status_not_in_triggers failure.
        codes = {f["code"] for f in result.failures}
        assert "status_not_in_triggers" not in codes, result.failures

    def test_shipped_passes_when_no_explicit_triggers(self):
        result = self._run("shipped")
        codes = {f["code"] for f in result.failures}
        assert "status_not_in_triggers" not in codes

    def test_shipping_and_processing_pass(self):
        for s in ("shipping", "processing", "in_progress"):
            result = self._run(s)
            codes = {f["code"] for f in result.failures}
            assert "status_not_in_triggers" not in codes, (s, result.failures)

    def test_completed_still_passes(self):
        result = self._run("completed")
        codes = {f["code"] for f in result.failures}
        assert "status_not_in_triggers" not in codes

    def test_cancelled_still_rejected(self):
        result = self._run("cancelled")
        codes = {f["code"] for f in result.failures}
        assert "status_not_in_triggers" in codes

    def test_explicit_narrower_list_still_rejects_shipped(self):
        result = self._run(
            "shipped",
            extra_settings={"invoice_trigger_statuses":
                            ["completed", "delivered"]})
        codes = {f["code"] for f in result.failures}
        assert "status_not_in_triggers" in codes


# ── Business rules status gate ────────────────────────────────────
class TestBusinessRulesStatusGate:
    def _evaluate(self, order_status: str):
        from datetime import datetime, timezone
        from integrations.qoyod.business_rules import evaluate
        from integrations.qoyod.dto import SalesOrderDTO, CustomerDTO

        dto = SalesOrderDTO(
            order_id="1",
            order_number="1",
            order_status=order_status,
            order_status_native=order_status,
            order_date=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            currency="SAR",
            total_amount=100.0,
            customer=CustomerDTO(name="Test"),
            items=[],
        )
        return evaluate(dto, {})

    def test_delivered_is_eligible(self):
        d = self._evaluate("delivered")
        assert d.eligible, d.notes

    def test_shipped_is_eligible(self):
        d = self._evaluate("shipped")
        assert d.eligible, d.notes

    def test_completed_is_eligible(self):
        d = self._evaluate("completed")
        assert d.eligible, d.notes

    def test_cancelled_not_eligible(self):
        d = self._evaluate("cancelled")
        assert not d.eligible


# ── Live send gate — BNPL allow-list ──────────────────────────────
class TestLiveSendGateBNPL:
    def _row(self, pm: str, status: str = "delivered"):
        return {
            "trace_id":           "T1",
            "salla_order_number": "268307955",
            "pipeline_stage":     "RULES_APPLIED",
            "qoyod_invoice_id":   None,
            "canonical_payload": {
                "order_status_native": status,
                "order_status":        status,
                "payment_method":      pm,
            },
        }

    def _run(self, pm: str, sendable: bool = True,
             invoice_payload=None, posting_mode="paid_receipt"):
        from integrations.qoyod import live_send_gate as gate
        return gate.evaluate(
            row=self._row(pm),
            settings={"selective_live_send_enabled": True},
            dependency_status={"sendable": sendable,
                               "request_body_unresolved": []},
            invoice_payload=(invoice_payload or {
                "contact_id": 1,
                "line_items": [{"product_id": 2, "quantity": 1}],
            }),
            posting_mode=posting_mode,
            is_current_trace=True,
        )

    def test_tabby_installment_is_allowed_not_unsupported(self):
        d = self._run("tabby_installment")
        assert d.outcome.value == "ALLOWED", d.to_json()
        assert d.category.value == "ready_to_send"

    def test_tamara_installment_is_allowed(self):
        d = self._run("tamara_installment")
        assert d.outcome.value == "ALLOWED", d.to_json()

    def test_emkan_is_allowed(self):
        d = self._run("emkan")
        assert d.outcome.value == "ALLOWED", d.to_json()

    def test_bnpl_produces_both_invoice_and_payment_scope(self):
        d = self._run("tabby")
        assert d.bypass is not None
        assert "create_invoice" in d.bypass.allowed_actions
        assert "create_invoice_payment" in d.bypass.allowed_actions

    def test_unknown_method_still_unsupported(self):
        d = self._run("some_new_wallet")
        assert d.outcome.value == "BLOCKED"
        assert d.category.value == "unsupported_method"

    def test_bnpl_with_delivered_status_passes_g1(self):
        # Delivered is now on the unified eligible list.
        d = self._run("tabby_installment")
        assert d.outcome.value == "ALLOWED"

    def test_shipped_status_now_eligible(self):
        from integrations.qoyod import live_send_gate as gate
        row = self._row("tabby", status="shipped")
        d = gate.evaluate(
            row=row,
            settings={"selective_live_send_enabled": True},
            dependency_status={"sendable": True,
                               "request_body_unresolved": []},
            invoice_payload={"contact_id": 1,
                             "line_items": [{"product_id": 2}]},
            posting_mode="paid_receipt",
            is_current_trace=True,
        )
        assert d.outcome.value == "ALLOWED", d.to_json()


# ── Pending-orders classifier — BNPL routing (real classifier) ───
class TestPendingOrdersBNPLClassification:
    """Guards `pending_classifier.categorise_row` — BNPL rows must
    route to `ready_to_send` when clean, `needs_mapping` when the
    payload has a leak, and NEVER to `unsupported_method`.

    Regression for order 268307955 (Tabby / delivered / contact_id=null
    / AMS11542 dry_run_only=true).
    """

    def _row(self, pm: str, *, stage: str = "RULES_APPLIED",
             inv_payload=None, status: str = "delivered"):
        return {
            "pipeline_stage":  stage,
            "canonical_payload": {
                "order_status":        status,
                "order_status_native": status,
                "payment_method":      pm,
            },
            "qoyod_payloads": {"invoice": inv_payload or {
                "contact_id":  1,
                "line_items":  [{"product_id": 2, "quantity": 1}],
            }},
        }

    def test_tabby_clean_payload_ready_to_send(self):
        from integrations.qoyod.pending_classifier import categorise_row
        assert categorise_row(self._row("tabby_installment")) == \
            "ready_to_send"

    def test_tamara_clean_payload_ready_to_send(self):
        from integrations.qoyod.pending_classifier import categorise_row
        assert categorise_row(self._row("tamara_installment")) == \
            "ready_to_send"

    def test_emkan_clean_payload_ready_to_send(self):
        from integrations.qoyod.pending_classifier import categorise_row
        assert categorise_row(self._row("emkan")) == "ready_to_send"

    def test_bnpl_with_null_contact_id_needs_mapping(self):
        """Order 268307955 profile — Tabby + contact_id=null."""
        from integrations.qoyod.pending_classifier import categorise_row
        row = self._row("tabby_installment", inv_payload={
            "contact_id":  None,
            "line_items":  [{"product_id": 2}],
        })
        assert categorise_row(row) == "needs_mapping"

    def test_bnpl_with_dry_product_id_needs_mapping(self):
        """AMS11542 dry_run_only=true → product_id='DRY:AMS11542'."""
        from integrations.qoyod.pending_classifier import categorise_row
        row = self._row("tabby_installment", inv_payload={
            "contact_id":  7,
            "line_items":  [{"product_id": "DRY:AMS11542"}],
        })
        assert categorise_row(row) == "needs_mapping"

    def test_bnpl_with_both_leaks_needs_mapping(self):
        """Exact profile of order 268307955: contact_id=null AND
        product_id contains a DRY: prefix."""
        from integrations.qoyod.pending_classifier import categorise_row
        row = self._row("tabby_installment", inv_payload={
            "contact_id":  None,
            "line_items":  [
                {"product_id": 5,                   "quantity": 1},
                {"product_id": 6,                   "quantity": 2},
                {"product_id": "DRY:AMS11542",      "quantity": 1},
            ],
        })
        assert categorise_row(row) == "needs_mapping", (
            "Regression for order 268307955 — Tabby row with null "
            "contact + DRY product MUST land in needs_mapping.")

    def test_explicit_unresolved_stage_beats_bnpl_derivation(self):
        """When pipeline_stage=UNRESOLVED_QOYOD_DEPENDENCY the
        explicit stage wins — the row is ALWAYS needs_mapping
        regardless of BNPL classification."""
        from integrations.qoyod.pending_classifier import categorise_row
        row = self._row("tabby_installment",
                        stage="UNRESOLVED_QOYOD_DEPENDENCY",
                        inv_payload={"contact_id": 1,
                                     "line_items": [{"product_id": 2}]})
        assert categorise_row(row) == "needs_mapping"

    def test_bnpl_never_lands_in_unsupported(self):
        from integrations.qoyod.pending_classifier import categorise_row
        for pm in ("tabby", "tabby_installment", "tabby_installments",
                   "tamara", "tamara_installment",
                   "emkan", "emkan_installment"):
            cat = categorise_row(self._row(pm))
            assert cat != "unsupported_method", (pm, cat)

    def test_unknown_wallet_still_unsupported(self):
        from integrations.qoyod.pending_classifier import categorise_row
        assert categorise_row(self._row("crypto_wallet_x")) == \
            "unsupported_method"

    def test_cod_with_leak_needs_mapping(self):
        from integrations.qoyod.pending_classifier import categorise_row
        row = self._row("cod", inv_payload={
            "contact_id": None,
            "line_items": [{"product_id": 1}],
        })
        assert categorise_row(row) == "needs_mapping"

    def test_cod_clean_stays_cod(self):
        from integrations.qoyod.pending_classifier import categorise_row
        assert categorise_row(self._row("cod")) == "cod"

    def test_bank_transfer_always_hold(self):
        from integrations.qoyod.pending_classifier import categorise_row
        assert categorise_row(self._row("bank_transfer")) == \
            "bank_transfer_hold"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
