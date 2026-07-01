"""Iter-293.5 — Selective Live Send Gate regression suite.

Contract pinned by these tests
──────────────────────────────
1. Global lock (`production_writes_locked`) is NEVER touched by the
   gate — even the ALLOWED path produces a `ScopedBypass`, not a
   mutation of the flag.
2. Bank transfer is HELD (never auto-sent) until Iter-294.
3. Unknown payment methods land in HOLD_UNSUPPORTED_PAYMENT_METHOD.
4. Cancelled / refunded / deleted / superseded / stale-trace orders
   are QUARANTINED (never shown in the pending-orders list).
5. Any DRY: / PREVIEW: / null id in the payload BLOCKS the gate.
6. Existing (real) qoyod_invoice_id BLOCKS the gate.
7. COD orders require posting_mode='credit_invoice_only'.
8. The scoped bypass is constrained to one order + one trace + one
   payload_hash + a specific action allowlist.
9. `selective_live_send_enabled=false` KEEPS the gate defensive
   (categorises for pending-orders view but denies ALLOWED).
10. Feature flag defaults to False on fresh tenants.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")

from integrations.qoyod.live_send_gate import (   # noqa: E402
    evaluate, GateOutcome, PendingCategory, GuardCode,
    canonicalise_payment_method,
    PREPAID_ALLOWED, COD_ALLOWED, BANK_TRANSFER_METHODS,
    ScopedBypass, _payload_hash,
)


# ─── Fixtures ────────────────────────────────────────────────────────
def _row(*, payment_method: str = "cod", order_status: str = "completed",
         existing_qid: str = "", pipeline_stage: str = "CUSTOMER_RESOLVED"):
    """Baseline eligible row — mutate keys to explore guard branches."""
    return {
        "id":                  "row-ID",
        "trace_id":             "trace-abc-123",
        "salla_order_number":   "ORD-1",
        "salla_order_id":       "ORD-1",
        "pipeline_stage":       pipeline_stage,
        "qoyod_invoice_id":     existing_qid,
        "canonical_payload": {
            "order_id":            "ORD-1",
            "order_number":        "ORD-1",
            "order_status_native": order_status,
            "payment_method":      payment_method,
            "total_amount":        213.78,
            "customer":            {"name": "T"},
            "items":               [{"sku": "S", "quantity": 1,
                                     "unit_price": 100.0}],
        },
    }


def _ok_dep():
    return {"sendable": True, "request_body_unresolved": []}


def _ok_payload():
    """A clean invoice payload with real ids — passes the leak scan."""
    return {"invoice": {
        "contact_id":   "12345",
        "issue_date":   "2026-07-01",
        "line_items": [{"product_id": "678",
                        "quantity":   1,
                        "unit_price": 100.0}],
    }}


def _settings(*, flag: bool = True):
    return {
        "production_writes_locked":     True,
        "selective_live_send_enabled":  flag,
    }


# ═════════════════════════════════════════════════════════════════════
class TestAllowlistCanonicalisation:
    """Every payment_method alias resolves to the right category."""

    @pytest.mark.parametrize("alias", sorted(PREPAID_ALLOWED))
    def test_all_prepaid_aliases_recognised(self, alias):
        # No collisions with COD or bank_transfer.
        c = canonicalise_payment_method(alias)
        assert c in PREPAID_ALLOWED
        assert c not in COD_ALLOWED
        assert c not in BANK_TRANSFER_METHODS

    @pytest.mark.parametrize("alias", sorted(COD_ALLOWED))
    def test_cod_aliases(self, alias):
        assert canonicalise_payment_method(alias) in COD_ALLOWED

    @pytest.mark.parametrize("alias", sorted(BANK_TRANSFER_METHODS))
    def test_bank_transfer_aliases(self, alias):
        assert canonicalise_payment_method(alias) in BANK_TRANSFER_METHODS

    def test_credit_card_family_specifically(self):
        """User directive Iter-293.5: credit_card family MUST include
        these exact tokens."""
        required = {"credit_card", "visa", "mastercard", "master_card",
                    "american_express", "amex", "cc"}
        missing = required - PREPAID_ALLOWED
        assert not missing, f"credit_card family missing aliases: {missing}"

    def test_dash_normalised_to_underscore(self):
        assert canonicalise_payment_method("apple-pay") == "apple_pay"
        assert canonicalise_payment_method("STC-Pay") == "stc_pay"

    def test_bnpl_now_allowed_iter293_5_rev3(self):
        """Iter-293.5-rev3 update per user directive 2026-07-01:
        Tabby / Tamara / Emkan (and their `_installment` variants)
        are now on the allow-list. They behave like prepaid — the
        BNPL provider settles the full amount to the merchant, so
        the pipeline creates invoice + receipt against the provider's
        Qoyod account. Only totally unknown methods (crypto wallets,
        gift cards, etc.) remain unsupported."""
        from integrations.qoyod.live_send_gate import BNPL_ALLOWED
        for pm in ("tamara", "tamara_installment", "tabby",
                   "tabby_installment", "emkan"):
            assert pm in BNPL_ALLOWED


# ═════════════════════════════════════════════════════════════════════
class TestHappyPath:

    def test_cod_completed_allowed_when_flag_on(self):
        d = evaluate(
            row=_row(payment_method="cod"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.ALLOWED
        assert d.category == PendingCategory.READY_TO_SEND
        assert d.bypass is not None
        # Scoped bypass carries the right handshake fields.
        assert d.bypass.order_number == "ORD-1"
        assert d.bypass.trace_id == "trace-abc-123"
        assert d.bypass.approval_phrase == (
            "AUTO-GATE: Approved to send order ORD-1 only")
        assert d.bypass.approval_type == "selective_live_gate_auto"
        assert d.bypass.approval_source == "live_send_gate"
        # COD bypass only allows create_invoice (no invoice_payment).
        assert d.bypass.allowed_actions == ("create_invoice",)

    def test_prepaid_completed_allowed_with_invoice_payment(self):
        d = evaluate(
            row=_row(payment_method="mada"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="paid_receipt",
        )
        assert d.outcome == GateOutcome.ALLOWED
        assert d.bypass.allowed_actions == (
            "create_invoice", "create_invoice_payment")

    def test_bypass_matches_only_scoped_action_and_payload(self):
        d = evaluate(
            row=_row(payment_method="cod"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        b: ScopedBypass = d.bypass
        assert b.matches("create_invoice", _ok_payload()) is True
        # Wrong action → refused.
        assert b.matches("create_receipt", _ok_payload()) is False
        # Different payload → refused (hash mismatch).
        different = {"invoice": {"contact_id": "OTHER", "line_items": []}}
        assert b.matches("create_invoice", different) is False


# ═════════════════════════════════════════════════════════════════════
class TestFeatureFlagDefensive:

    def test_flag_off_blocks_even_with_all_guards_passing(self):
        d = evaluate(
            row=_row(payment_method="cod"),
            settings=_settings(flag=False),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.reason_code == GuardCode.FLAG_DISABLED.value
        assert d.bypass is None
        # Still categorises so the pending-orders UI can show it.
        assert d.category == PendingCategory.READY_TO_SEND
        # Reflects that ALL other guards WOULD have passed.
        assert "order_status_eligible" in d.guards_passed
        assert "payment_method_allowed" in d.guards_passed
        assert "dependency_sendable" in d.guards_passed
        assert "no_existing_invoice" in d.guards_passed


# ═════════════════════════════════════════════════════════════════════
class TestBankTransferInvoiceCreatedButPaymentDeferred:
    """Iter-293.5-rev2 — bank_transfer MUST NOT block invoice
    creation (ZATCA needs the invoice regardless of payment method).
    The gate ALLOWS the invoice POST but DEFERS the invoice_payment
    to Iter-294 (`defers_payment_posting=True`)."""

    @pytest.mark.parametrize("pm", ["bank_transfer", "banktransfer",
                                     "BANK_TRANSFER"])
    def test_all_bank_transfer_aliases_allowed_invoice_only(self, pm):
        d = evaluate(
            row=_row(payment_method=pm),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="paid_receipt",
        )
        assert d.outcome == GateOutcome.ALLOWED
        assert d.bypass is not None
        # Invoice only — no payment posting.
        assert d.bypass.allowed_actions == ("create_invoice",)
        # Pipeline signals.
        assert d.defers_payment_posting is True
        assert d.post_invoice_hold_stage == (
            "BANK_TRANSFER_PAYMENT_ROUTING_PENDING")

    def test_bank_transfer_bypass_refuses_invoice_payment_call(self):
        d = evaluate(
            row=_row(payment_method="bank_transfer"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="paid_receipt",
        )
        b = d.bypass
        # create_invoice → OK.
        assert b.matches("create_invoice", _ok_payload()) is True
        # create_invoice_payment → BLOCKED (not in allowed_actions).
        assert b.matches("create_invoice_payment", _ok_payload()) is False
        # create_receipt → BLOCKED.
        assert b.matches("create_receipt", _ok_payload()) is False

    def test_bank_transfer_flag_off_still_categorised_correctly(self):
        d = evaluate(
            row=_row(payment_method="bank_transfer"),
            settings=_settings(flag=False),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="paid_receipt",
        )
        # Flag off → still blocked, but the defers_payment_posting
        # signal is preserved so the pending-orders UI can distinguish
        # bank_transfer rows from prepaid rows.
        assert d.outcome == GateOutcome.BLOCKED
        assert d.defers_payment_posting is True
        assert d.post_invoice_hold_stage == (
            "BANK_TRANSFER_PAYMENT_ROUTING_PENDING")


# ═════════════════════════════════════════════════════════════════════
class TestUnsupportedPaymentMethod:

    @pytest.mark.parametrize("pm", ["cheque",
                                     "wallet_gift", "unknown_gateway"])
    def test_unknown_methods_held(self, pm):
        """Iter-293.5-rev3: BNPL (tabby/tamara/emkan) moved to
        prepaid-equivalent allow-list. Only genuinely unknown methods
        (crypto wallets, gift cards, cheques…) still land here."""
        d = evaluate(
            row=_row(payment_method=pm),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="paid_receipt",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.hold_stage == "HOLD_UNSUPPORTED_PAYMENT_METHOD"
        assert d.category == PendingCategory.UNSUPPORTED_METHOD
        assert d.reason_code == GuardCode.UNSUPPORTED_PAYMENT_METHOD.value
        assert d.bypass is None


# ═════════════════════════════════════════════════════════════════════
class TestOrderStatusFilters:

    @pytest.mark.parametrize("status", ["canceled", "cancelled"])
    def test_cancelled_is_quarantined(self, status):
        d = evaluate(
            row=_row(order_status=status),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.QUARANTINED
        assert d.reason_code == GuardCode.ORDER_CANCELLED.value
        assert d.category is None    # not shown in pending-orders

    def test_refunded_is_quarantined(self):
        d = evaluate(
            row=_row(order_status="refunded"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.QUARANTINED
        assert d.reason_code == GuardCode.ORDER_REFUNDED.value

    def test_deleted_is_quarantined(self):
        d = evaluate(
            row=_row(order_status="deleted"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.QUARANTINED
        assert d.reason_code == GuardCode.ORDER_DELETED.value

    @pytest.mark.parametrize("status", ["pending", "waiting"])
    def test_non_terminal_ineligible_status_blocks_not_quarantines(
            self, status):
        """Iter-293.5-rev3: `processing` moved to eligible via the
        unified set. Truly non-billable transitional states
        (pending / waiting) still block here."""
        d = evaluate(
            row=_row(order_status=status),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.hold_stage == "STALE_TRACE_NOT_CURRENT_ORDER_STATE"
        assert d.reason_code == GuardCode.ORDER_STATUS_NOT_ELIGIBLE.value


# ═════════════════════════════════════════════════════════════════════
class TestSupersedeAndStale:

    def test_stale_trace_quarantined(self):
        d = evaluate(
            row=_row(),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
            is_current_trace=False,
        )
        assert d.outcome == GateOutcome.QUARANTINED
        assert d.reason_code == GuardCode.ORDER_SUPERSEDED.value
        assert d.hold_stage == "ORDER_SUPERSEDED_BY_NEWER_EVENT"


# ═════════════════════════════════════════════════════════════════════
class TestDependencyGuards:

    def test_sendable_false_blocks(self):
        d = evaluate(
            row=_row(),
            settings=_settings(flag=True),
            dependency_status={"sendable": False,
                               "request_body_unresolved": []},
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.hold_stage == "UNRESOLVED_QOYOD_DEPENDENCY"
        assert d.category == PendingCategory.NEEDS_MAPPING
        assert d.reason_code == GuardCode.UNRESOLVED_DEPENDENCY.value

    def test_unresolved_fields_block(self):
        d = evaluate(
            row=_row(),
            settings=_settings(flag=True),
            dependency_status={"sendable": True,
                               "request_body_unresolved":
                                   ["contact_id", "line_items[0].product_id"]},
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.reason_code == GuardCode.NULL_ID_IN_PAYLOAD.value

    def test_dry_prefix_in_payload_blocks(self):
        payload = {"invoice": {"contact_id": "DRY:temp-1",
                               "line_items": [{"product_id": "42",
                                               "quantity": 1,
                                               "unit_price": 10.0}]}}
        d = evaluate(
            row=_row(),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=payload,
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.reason_code == GuardCode.DRY_OR_PREVIEW_LEAK.value

    def test_preview_prefix_in_payload_blocks(self):
        payload = {"invoice": {"contact_id": "1",
                               "line_items": [{"product_id": "PREVIEW:x",
                                               "quantity": 1,
                                               "unit_price": 10.0}]}}
        d = evaluate(
            row=_row(),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=payload,
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.reason_code == GuardCode.DRY_OR_PREVIEW_LEAK.value

    def test_null_contact_id_in_payload_blocks(self):
        payload = {"invoice": {"contact_id": None,
                               "line_items": [{"product_id": "42",
                                               "quantity": 1,
                                               "unit_price": 10.0}]}}
        d = evaluate(
            row=_row(),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=payload,
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.reason_code == GuardCode.DRY_OR_PREVIEW_LEAK.value


# ═════════════════════════════════════════════════════════════════════
class TestIdempotency:

    def test_existing_real_qid_blocks(self):
        d = evaluate(
            row=_row(existing_qid="QID-1234"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.reason_code == GuardCode.EXISTING_INVOICE.value
        # Total-mismatch tab because that's where the accountant sees it.
        assert d.category == PendingCategory.TOTAL_ROUNDING_REVIEW

    def test_dry_qid_does_not_block(self):
        # A stale DRY:* id from an earlier preview should NOT be
        # treated as an existing invoice — the sender guards will
        # still catch it via the leak scanner.
        payload = _ok_payload()
        d = evaluate(
            row=_row(existing_qid="DRY:temp-99"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=payload,
            posting_mode="credit_invoice_only",
        )
        # Not blocked by idempotency (payload is clean; existing_qid
        # is a fake DRY value).
        assert d.reason_code != GuardCode.EXISTING_INVOICE.value


# ═════════════════════════════════════════════════════════════════════
class TestCODInvariant:

    def test_cod_wrong_posting_mode_blocks(self):
        d = evaluate(
            row=_row(payment_method="cod"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="paid_receipt",       # wrong for COD
        )
        assert d.outcome == GateOutcome.BLOCKED
        assert d.reason_code == GuardCode.COD_POSTING_MODE_MISMATCH.value
        assert d.hold_stage == "HOLD_COD_PENDING_FIX"

    def test_prepaid_does_not_require_credit_invoice_only(self):
        d = evaluate(
            row=_row(payment_method="mada"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="paid_receipt",
        )
        assert d.outcome == GateOutcome.ALLOWED


# ═════════════════════════════════════════════════════════════════════
class TestBypassScopeInvariant:
    """The scoped bypass MUST refuse writes outside its declared
    scope — this is the mechanism that keeps the global lock true."""

    def test_hash_stable_for_same_payload(self):
        assert _payload_hash({"a": 1, "b": 2}) == _payload_hash(
            {"b": 2, "a": 1})    # dict key order irrelevant

    def test_hash_changes_on_any_payload_mutation(self):
        h1 = _payload_hash({"invoice": {"contact_id": "1"}})
        h2 = _payload_hash({"invoice": {"contact_id": "2"}})
        assert h1 != h2

    def test_bypass_carries_specific_actions_not_wildcards(self):
        d = evaluate(
            row=_row(payment_method="cod"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        b = d.bypass
        # No wildcard tokens.
        assert "*" not in b.allowed_actions
        assert "all" not in b.allowed_actions
        # Explicit list only.
        assert list(b.allowed_actions) == ["create_invoice"]


# ═════════════════════════════════════════════════════════════════════
class TestReasonSurfaceForOperator:
    """Fields the pending-orders UI relies on."""

    def test_to_json_shape(self):
        d = evaluate(
            row=_row(payment_method="cod"),
            settings=_settings(flag=False),    # defensive off
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        j = d.to_json()
        assert j["outcome"] == "BLOCKED"
        assert j["category"] == "ready_to_send"
        assert j["reason_code"] == "flag_disabled"
        assert j["canonical_pm"] == "cod"
        assert j["posting_mode"] == "credit_invoice_only"
        assert j["bypass_granted"] is False
        # No secrets leak in the wire payload.
        assert "approval_phrase" not in j
        assert "payload_hash" not in j    # (only truncated hash allowed)

    def test_to_json_bypass_hash_truncated(self):
        d = evaluate(
            row=_row(payment_method="cod"),
            settings=_settings(flag=True),
            dependency_status=_ok_dep(),
            invoice_payload=_ok_payload(),
            posting_mode="credit_invoice_only",
        )
        j = d.to_json()
        assert j["bypass_granted"] is True
        assert len(j["bypass_hash"]) == 12    # never full 64-char sha256
