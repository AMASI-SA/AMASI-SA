"""Iter-001k+ — Order Totals Breakdown (Read-Only Diagnostic).

Pins the exact numeric contract for orders 269632660 and 269604656
(as reported by the operator on 2026-02-XX).

Zero DB. Zero httpx. Zero side effects.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.order_totals_breakdown import (   # noqa: E402
    build_order_totals_breakdown,
    fetch_order_totals_breakdown,
)


# ── Order #1: 269632660 — coupon + promotion pattern ───────────────
def _order_269632660_inbox_row() -> dict:
    """Reconstructs what the integration_inbox row would look like
    for order 269632660 per the operator's Salla screenshot:

        subtotal        = 105.00
        coupon          = 5.25   (subtracted)
        promotion       = 4.99   (subtracted)
        shipping        = 24.07  (added)
        tax             = 9.51   (added)
        official_total  = 128.34

    Salla's raw payload carries `coupon` and `promotion` as separate
    top-level nodes AND their SUM in `amounts.discount`. The
    reconstruction must not double-subtract.

    NOTE on the current bug pattern (Production observation for
    order 269632660): the canonical payload has `tax_amount=0`
    because the normalizer didn't lift `amounts.tax` into it. The
    current `_check_totals` reads `canonical.tax_amount` and therefore
    computes `expected = items_sum(105) + shipping(24.07) + tax(0) =
    129.07` — matching the operator's observed `current_diff=-0.73`.

    The breakdown module MUST prefer the raw amounts subtree so the
    9.51 tax is picked up correctly.
    """
    return {
        "trace_id": "trace-269632660",
        "user_id": "main",
        "salla_order_number": "269632660",
        "canonical_payload": {
            "order_number": "269632660",
            "order_id":     "269632660",
            "subtotal":       105.00,
            "shipping_amount": 24.07,
            # Reflects the Production bug: tax not lifted into
            # canonical, so current_expected drops the 9.51.
            "tax_amount":       0.00,
            "discount_amount": 10.24,   # coupon+promo aggregate
            "total_amount":  128.34,
            "items": [
                {"sku": "SKU-A", "quantity": 1, "unit_price": 60.00,
                 "discount_amount": 0.0},
                {"sku": "SKU-B", "quantity": 1, "unit_price": 45.00,
                 "discount_amount": 0.0},
            ],
        },
        "raw_payload": {
            "data": {
                "id": 269632660,
                "reference_id": "269632660",
                "amounts": {
                    "total":    {"amount": 128.34, "currency": "SAR"},
                    "sub_total": {"amount": 105.00, "currency": "SAR"},
                    "shipping":  {"amount": 24.07, "currency": "SAR"},
                    "tax":       {"amount":  9.51, "currency": "SAR"},
                    "discount":  {"amount": 10.24, "currency": "SAR"},
                },
                "coupon":    {"amount": 5.25, "code": "SAVE5"},
                "promotion": {"amount": 4.99,
                              "label":  "Special Offer"},
            },
        },
    }


class TestOrder269632660:
    """The reconstruction must reconcile EXACTLY to 128.34, and the
    coupon+promotion sum (10.24) must equal the order-level
    discount so we don't double-subtract."""

    def test_official_total_and_current_diff(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269632660_inbox_row())
        assert b["salla_official_total"] == 128.34
        # Current (buggy) reconstruction: items+shipping+tax = 129.07.
        assert b["items_sum_from_canonical"] == 105.00
        assert b["current_expected"] == 129.07
        assert b["current_diff"] == -0.73

    def test_reconstruction_with_adjustments_reaches_official_total(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269632660_inbox_row())
        assert b["coupon_discount_amount"]    == 5.25
        assert b["promotion_discount_amount"] == 4.99
        assert b["order_level_discount_amount"] == 10.24
        # subtotal − coupon − promotion + shipping + tax = 128.34.
        assert b["reconstructed_with_adjustments"] == 128.34
        assert b["diff_after_adjustments"] == 0.00
        assert b["residual_unexplained"] == 0.00
        assert b["would_pass_totals_guard"] is True

    def test_dedup_note_is_emitted_when_aggregate_matches_sum(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269632660_inbox_row())
        notes = " ".join(b["formula_notes"])
        assert "double-subtraction" in notes

    def test_field_paths_used_include_raw_amounts(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269632660_inbox_row())
        used = b["field_paths_used"]
        assert used["salla_official_total"] == \
            "raw_payload.data.amounts.total.amount"
        assert used["coupon_discount_amount"].startswith(
            "raw_payload.data.coupon")
        assert used["promotion_discount_amount"].startswith(
            "raw_payload.data.promotion")

    def test_read_only_flags_present(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269632660_inbox_row())
        assert b["read_only"] is True
        assert b["no_qoyod_api_calls"] is True
        assert b["no_db_writes"] is True

    def test_raw_debug_off_by_default(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269632660_inbox_row())
        assert "raw_debug" not in b

    def test_raw_debug_on_returns_trimmed_subtree(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269632660_inbox_row(),
            include_raw_debug=True)
        assert "raw_debug" in b
        rd = b["raw_debug"]
        assert "raw_amounts_subtree" in rd
        assert rd["raw_amounts_subtree"]["total"]["amount"] == 128.34
        top = rd["raw_top_level_discount_keys"]
        assert top["coupon"]["amount"] == 5.25
        assert top["promotion"]["amount"] == 4.99


# ── Order #2: 269604656 — clean tax breakdown (bank_transfer) ──────
def _order_269604656_inbox_row() -> dict:
    """Diagnostic-only fixture for the tax-clean case.

        subtotal       = 96.30
        shipping       = 24.07
        tax            = 9.63
        official_total = 130.00

    Payment method is `bank_transfer` so this order remains blocked
    on `bank_transfer_on_hold_iter_294` regardless — we only use it
    to prove the reconstruction doesn't over-adjust when NO discounts
    are present.
    """
    return {
        "trace_id": "trace-269604656",
        "user_id": "main",
        "salla_order_number": "269604656",
        "canonical_payload": {
            "order_number": "269604656",
            "order_id":     "269604656",
            "subtotal":       96.30,
            "shipping_amount": 24.07,
            "tax_amount":       9.63,
            "discount_amount":  0.00,
            "total_amount":  130.00,
            "items": [
                {"sku": "SKU-X", "quantity": 1,
                 "unit_price": 96.30, "discount_amount": 0.0},
            ],
        },
        "raw_payload": {
            "data": {
                "id": 269604656,
                "reference_id": "269604656",
                "amounts": {
                    "total":     {"amount": 130.00, "currency": "SAR"},
                    "sub_total": {"amount":  96.30, "currency": "SAR"},
                    "shipping":  {"amount":  24.07, "currency": "SAR"},
                    "tax":       {"amount":   9.63, "currency": "SAR"},
                },
                "payment_method": "bank_transfer",
            },
        },
    }


class TestOrder269604656:
    """No discounts anywhere. Both reconstructions should reconcile."""

    def test_no_adjustments_needed(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269604656_inbox_row())
        assert b["salla_official_total"] == 130.00
        assert b["items_sum_from_canonical"] == 96.30
        assert b["current_expected"] == 130.00
        assert b["current_diff"] == 0.00
        # Adjustment-aware path also reconciles exactly.
        assert b["reconstructed_with_adjustments"] == 130.00
        assert b["diff_after_adjustments"] == 0.00
        assert b["residual_unexplained"] == 0.00

    def test_no_discount_fields_populated(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269604656_inbox_row())
        assert b["coupon_discount_amount"] is None
        assert b["promotion_discount_amount"] is None
        assert b["wallet_amount"] is None
        assert b["manual_discount_amount"] is None
        assert b["gift_card_amount"] is None
        assert b["reward_points_amount"] is None
        # order_level_discount is 0 (or None) — either way, non-blocking.
        assert (b["order_level_discount_amount"] or 0) == 0

    def test_adjustments_applied_list_is_empty(self):
        b = build_order_totals_breakdown(
            inbox_row=_order_269604656_inbox_row())
        assert b["adjustments_applied"] == []


# ── Fetch wrapper (Read-Only, no writes) ────────────────────────────
class _FakeInboxColl:
    def __init__(self, rows):
        self._rows = rows

    def find(self, q, projection=None):
        return _Cursor([
            r for r in self._rows if self._match(r, q)
        ])

    @staticmethod
    def _match(row, q):
        if not isinstance(q, dict):
            return False
        for k, v in q.items():
            if k == "$or":
                if not any(_FakeInboxColl._match(row, sub) for sub in v):
                    return False
                continue
            if row.get(k) != v:
                return False
        return True


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **kw):
        return self

    async def to_list(self, length=None):
        return list(self._rows[: (length or len(self._rows))])


class _FakeDB:
    def __init__(self, rows):
        self.integration_inbox = _FakeInboxColl(rows)


@pytest.mark.asyncio
class TestFetchWrapper:

    async def test_found_returns_breakdown(self):
        db = _FakeDB([_order_269632660_inbox_row()])
        out = await fetch_order_totals_breakdown(
            db, user_id="main", order_number="269632660")
        assert out["found"] is True
        assert out["reconstructed_with_adjustments"] == 128.34

    async def test_not_found_returns_stub(self):
        db = _FakeDB([])
        out = await fetch_order_totals_breakdown(
            db, user_id="main", order_number="000000000")
        assert out["found"] is False
        assert out["read_only"] is True
        assert out["no_qoyod_api_calls"] is True

    async def test_include_raw_debug_flag_propagates(self):
        db = _FakeDB([_order_269632660_inbox_row()])
        out = await fetch_order_totals_breakdown(
            db, user_id="main", order_number="269632660",
            include_raw_debug=True)
        assert "raw_debug" in out
        assert out["raw_debug"]["raw_top_level_discount_keys"][
            "coupon"]["amount"] == 5.25


# ── Read-Only invariants (paranoia guard) ───────────────────────────
class TestReadOnlyInvariants:

    def test_module_does_not_import_qoyod_api_client(self):
        """The breakdown module must NEVER pull in the Qoyod HTTP
        client — that's the strongest static signal that it can't
        accidentally send."""
        import integrations.qoyod.order_totals_breakdown as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "QoyodAPIClient" not in src
        assert "api_client" not in src
        assert "httpx" not in src

    def test_module_does_not_write_to_db(self):
        import integrations.qoyod.order_totals_breakdown as mod
        src = open(mod.__file__, encoding="utf-8").read()
        # No writes.
        assert "insert_one" not in src
        assert "update_one" not in src
        assert "delete_one" not in src
        assert "$set" not in src
