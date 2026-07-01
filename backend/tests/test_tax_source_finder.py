"""Iter-001k+ — Tax Source Finder & Derived-Tax Reconciliation.

Extends the Read-Only order-totals-breakdown diagnostic with:
    1. A recursive walker that surfaces every candidate tax field
       inside integration_inbox row.
    2. Arithmetic reconciliation that computes the tax value the
       order MUST carry for its totals to balance.
    3. `diff_using_derived_tax` — proves that if the missing tax
       were folded into `_check_totals`, the reconstruction would
       reconcile exactly.

Pins the exact numeric contract for the four Production orders
(269632660, 269604656, 269579732, 269640154) reported on
2026-02-XX with `raw_amounts_subtree={}`.

Zero DB. Zero httpx. Zero side effects.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.order_totals_breakdown import (   # noqa: E402
    _find_tax_source_candidates,
    build_order_totals_breakdown,
)


# ── Production-shape fixtures (empty raw.amounts subtree) ───────────
# These mirror the ACTUAL shape observed on 2026-02-XX where
# `raw_payload.data.amounts` was empty for all four orders, forcing
# the diagnostic to derive tax arithmetically.
def _prod_269632660() -> dict:
    return {
        "trace_id": "a4c2681b15e342e1abe907abe6a29403",
        "user_id":  "main",
        "salla_order_number": "269632660",
        "canonical_payload": {
            "order_number": "269632660",
            "order_id":     "269632660",
            "subtotal":       105.00,
            "shipping_amount": 24.07,
            "tax_amount":       0.00,     # ← Normalizer bug
            "discount_amount": 10.24,
            "total_amount":  128.34,
            "items": [
                {"sku": "AMS11889", "quantity": 1,
                 "unit_price": 60.00, "discount_amount": 9.75},
                {"sku": "AMS11961", "quantity": 1,
                 "unit_price": 45.00, "discount_amount": 0.49},
            ],
        },
        "raw_payload": {"data": {"amounts": {}}},   # empty subtree
    }


def _prod_269604656() -> dict:
    return {
        "trace_id": "85297afa4c9f4d649e3e8a6990707816",
        "user_id":  "main",
        "salla_order_number": "269604656",
        "canonical_payload": {
            "order_number": "269604656",
            "order_id":     "269604656",
            "subtotal":       96.30,
            "shipping_amount": 24.07,
            "tax_amount":       0.00,
            "discount_amount":  0.00,
            "total_amount":  130.00,
            "items": [
                {"sku": "AMS-BT", "quantity": 1,
                 "unit_price": 96.30, "discount_amount": 0.0},
            ],
        },
        "raw_payload": {"data": {"amounts": {}}},
    }


def _prod_269579732() -> dict:
    return {
        "trace_id": "c505247845254c269b5779e16f843abd",
        "user_id":  "main",
        "salla_order_number": "269579732",
        "canonical_payload": {
            "order_number": "269579732",
            "order_id":     "269579732",
            "subtotal":     425.00,
            "shipping_amount": 24.07,
            "tax_amount":       0.00,
            "discount_amount": 63.75,
            "total_amount":  416.15,
            "items": [
                {"sku": "AMS11889", "quantity": 1,
                 "unit_price": 200.00, "discount_amount": 60.00},
                {"sku": "AMS11961", "quantity": 1,
                 "unit_price":  75.00, "discount_amount":  1.50},
                {"sku": "AMS11961", "quantity": 1,
                 "unit_price":  75.00, "discount_amount":  0.75},
                {"sku": "AMS11961", "quantity": 1,
                 "unit_price":  75.00, "discount_amount":  1.50},
            ],
        },
        "raw_payload": {"data": {"amounts": {}}},
    }


def _prod_269640154() -> dict:
    return {
        "trace_id": "8500781c9bd74c35b271aba21e24bd93",
        "user_id":  "main",
        "salla_order_number": "269640154",
        "canonical_payload": {
            "order_number": "269640154",
            "order_id":     "269640154",
            "subtotal":     100.00,
            "shipping_amount": 24.07,
            "tax_amount":       0.00,
            "discount_amount":  0.00,
            "total_amount":  134.00,
            "items": [
                {"sku": "AMS-X", "quantity": 1,
                 "unit_price": 100.00, "discount_amount": 0.0},
            ],
        },
        "raw_payload": {"data": {"amounts": {}}},
    }


# ── Derived tax matches observed residual (4 Production orders) ────
class TestDerivedTaxMatchesResidual:
    """The residual after subtracting all detected discounts should
    equal `derived_tax` within 0.01 for every well-formed order."""

    @pytest.mark.parametrize("fixture, expected_derived_tax", [
        (_prod_269632660,  9.51),
        (_prod_269604656,  9.63),
        (_prod_269579732, 30.83),
        (_prod_269640154,  9.93),
    ])
    def test_derived_tax_arithmetic_matches_operator_observation(
            self, fixture, expected_derived_tax):
        b = build_order_totals_breakdown(inbox_row=fixture())
        assert b["derived_tax_from_reconciliation"] == \
            expected_derived_tax
        assert b["derived_tax_matches_residual"] is True

    @pytest.mark.parametrize("fixture", [
        _prod_269632660, _prod_269604656,
        _prod_269579732, _prod_269640154,
    ])
    def test_diff_using_derived_tax_is_zero(self, fixture):
        b = build_order_totals_breakdown(inbox_row=fixture())
        assert b["diff_using_derived_tax"] == 0.00
        # Corrected expected reconciles to Salla's official total.
        assert b["corrected_expected_using_derived_tax"] == \
            b["salla_official_total"]


# ── Tax Source Finder does NOT leak raw payload ─────────────────────
class TestTaxSourceFinderIsPIISafe:

    def test_finder_never_returns_full_raw_payload(self):
        row = _prod_269632660()
        # Salt the row with a fake PII field that should NEVER appear.
        row["raw_payload"]["data"]["customer"] = {
            "email": "leak@example.com",
            "phone": "+9660000000000",
            "national_id": "1010101010",
        }
        b = build_order_totals_breakdown(inbox_row=row)
        # No candidate carries the tainted string values.
        cand_json = repr(b["tax_source_candidates"])
        assert "leak@example.com" not in cand_json
        assert "+9660000000000" not in cand_json
        assert "1010101010" not in cand_json

    def test_finder_never_emits_string_values(self):
        """Every candidate `value` must be numeric or None — strings
        would be a PII leak vector."""
        row = _prod_269632660()
        row["raw_payload"]["data"]["tax_receipt_url"] = \
            "https://salla.com/receipts/leak"
        b = build_order_totals_breakdown(inbox_row=row)
        for c in b["tax_source_candidates"]:
            assert c["value"] is None or isinstance(
                c["value"], (int, float))

    def test_finder_finds_canonical_tax_amount_zero(self):
        """When canonical.tax_amount=0 the finder must still record
        it as a candidate so the auditor knows the field EXISTS but
        holds a wrong value."""
        row = _prod_269632660()
        # canonical.tax_amount=0 → 0 IS falsy but present. Confirm
        # the probe path records it.
        cands = _find_tax_source_candidates(row, derived_tax=9.51)
        paths = [c["path"] for c in cands]
        assert "canonical_payload.tax_amount" in paths

    def test_finder_ranks_matching_value_as_high_confidence(self):
        """Simulate a snapshot that DOES carry the correct tax
        somewhere unexpected. Finder should flag it as HIGH."""
        row = _prod_269632660()
        # Inject a hidden but plausible location (e.g. eligible orders
        # snapshot subtree).
        row["eligible_orders_snapshot"] = {
            "totals_status": {
                "vat_amount": 9.51,       # ← should be caught
            },
        }
        b = build_order_totals_breakdown(inbox_row=row)
        highs = [c for c in b["tax_source_candidates"]
                 if c["confidence"] == "high"]
        assert any(c["value"] == 9.51 for c in highs)
        assert b["tax_source_summary"]["any_high_confidence_match"] \
            is True

    def test_finder_deep_arabic_key_marker_detected(self):
        row = _prod_269632660()
        row["snapshot"] = {"amounts": {"ضريبة": {"amount": 9.51}}}
        b = build_order_totals_breakdown(inbox_row=row)
        paths = [c["path"] for c in b["tax_source_candidates"]]
        assert any("ضريبة" in p for p in paths)

    def test_finder_summary_when_no_valid_source_found(self):
        """Four Production orders — walker finds `tax_amount=0` and
        `discount_amount` (which is NOT a tax marker) but no
        matching-value candidate. `any_high_confidence_match` must
        be False → informs the operator that no raw source of tax
        exists in the stored inbox row."""
        for fixture in (_prod_269632660, _prod_269604656,
                        _prod_269579732, _prod_269640154):
            b = build_order_totals_breakdown(inbox_row=fixture())
            summary = b["tax_source_summary"]
            assert summary["any_high_confidence_match"] is False, \
                f"Fixture {fixture.__name__} unexpectedly found a " \
                f"high-confidence tax source in stored payload."


# ── Read-Only paranoia guard extends to new code ────────────────────
class TestReadOnlyInvariantsExtended:

    def test_module_still_does_not_import_qoyod_api_client(self):
        import integrations.qoyod.order_totals_breakdown as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "QoyodAPIClient" not in src
        assert "api_client" not in src
        assert "httpx" not in src
        assert "requests.post" not in src

    def test_module_still_does_no_db_writes(self):
        import integrations.qoyod.order_totals_breakdown as mod
        src = open(mod.__file__, encoding="utf-8").read()
        for banned in ("insert_one", "update_one", "delete_one",
                       "$set", "$unset", "insert_many",
                       "update_many"):
            assert banned not in src, \
                f"Read-Only invariant violated: found `{banned}`"

    def test_finder_respects_depth_limit(self):
        """Pathological deeply-nested input must not stack-overflow
        or emit unbounded candidates."""
        deep: dict = {"tax": 1.23}
        for _ in range(50):
            deep = {"nested": deep}
        row = {"canonical_payload": {}, "raw_payload": deep}
        cands = _find_tax_source_candidates(row, derived_tax=None)
        # Bounded output — depth capped by walker.
        assert len(cands) < 100


# ── Regression: the older breakdown fields still work ──────────────
class TestNewFieldsCoexistWithOldFields:

    def test_269632660_still_reports_current_diff_minus_073(self):
        b = build_order_totals_breakdown(
            inbox_row=_prod_269632660())
        assert b["current_expected"] == 129.07
        assert b["current_diff"] == -0.73
        # NEW fields present.
        assert "derived_tax_from_reconciliation" in b
        assert "tax_source_candidates" in b

    def test_269604656_still_reports_current_diff_963(self):
        b = build_order_totals_breakdown(
            inbox_row=_prod_269604656())
        assert b["current_expected"] == 120.37
        assert b["current_diff"] == 9.63
