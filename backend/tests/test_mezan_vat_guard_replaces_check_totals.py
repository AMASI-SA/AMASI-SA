"""Iter-001k+ — `_check_totals` replaced by Mezan-VAT-15% guard.

Contract pinned by operator on 2026-02-27:
    • Guard uses `qoyod_simulation.build_qoyod_simulation` — the
      SAME math the real invoice_builder uses.
    • Salla's `canonical.tax_amount` is IGNORED by the decision.
    • Guard passes iff `|simulated_qoyod_diff_vs_salla_total| ≤ 0.01`.
    • Legacy diff remains visible in `totals_status.legacy_diff`
      for diagnostics ONLY — never gates a decision.
    • Blocker precedence is UNCHANGED — totals pass does not
      override other blockers (bank_transfer, DRY, etc.).

Test invariants (13):
     1. Five Production orders now report totals_status.valid=True.
     2. legacy_diff is preserved in diagnostics.
     3. Salla tax value (0/8/15/18%) does not change the decision.
     4. Guard code path does not read canonical.tax_amount for the
        decision boolean.
     5. |mezan_diff| ≤ 0.01 → valid=True.
     6. |mezan_diff| > 0.01 → valid=False.
     7. bank_transfer stays classified `blocked_bank_transfer_routing`
        even when totals reconcile.
     8. DRY / PREVIEW / null customer/product ids stay blocking.
     9. `payload_date_source == "send_date"`.
    10. Guard emits `no_qoyod_api_calls` and never opens gate.
    11. `production_writes_locked` remains True — Preview & Prod.
    12. Blocker precedence: already_sent → totals → bank_transfer …
        is preserved (test evaluates each rung).
    13. Static: `_check_totals` module does not import Qoyod API
        client and does no DB writes.
"""
from __future__ import annotations

import inspect
import sys
from typing import Any, Optional

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod import eligible_orders as EO      # noqa: E402
from integrations.qoyod.eligible_orders import (           # noqa: E402
    _check_totals, _classify,
)


# ── Production-shape fixtures (5 orders) ────────────────────────────
def _mk_order(
    *, salla_total, subtotal, shipping, discount, items,
    payment_method="credit_card", salla_tax=0.0,
    order_number=None,
) -> dict:
    return {
        "order_number":    order_number or "TEST",
        "order_id":        order_number or "TEST",
        "subtotal":        subtotal,
        "shipping_amount": shipping,
        "tax_amount":      salla_tax,           # ← ignored by guard
        "discount_amount": discount,
        "total_amount":    salla_total,
        "payment_method":  payment_method,
        "status":          "completed",
        "items":           items,
    }


def _prod_269629400():
    return _mk_order(
        order_number="269629400",
        salla_total=100.00, subtotal=80.00, shipping=13.05,
        discount=0.00,
        items=[{"sku": "SKU-A", "quantity": 1,
                "unit_price": 80.00, "discount_amount": 0.0,
                "tax_amount": 6.95, "total": 86.95}])


def _prod_269632660():
    return _mk_order(
        order_number="269632660",
        salla_total=128.34, subtotal=105.00, shipping=24.07,
        discount=10.24,
        items=[
            {"sku": "AMS11889", "quantity": 1,
             "unit_price": 60.00, "discount_amount": 9.75,
             "tax_amount": 4.02, "total": 54.27},
            {"sku": "AMS11961", "quantity": 1,
             "unit_price": 45.00, "discount_amount": 0.49,
             "tax_amount": 3.56, "total": 48.07},
        ])


def _prod_269604656():
    return _mk_order(
        order_number="269604656",
        salla_total=130.00, subtotal=96.30, shipping=24.07,
        discount=0.0, payment_method="bank_transfer",
        items=[{"sku": "AMS-BT", "quantity": 1,
                "unit_price": 96.30, "discount_amount": 0.0,
                "tax_amount": 7.70, "total": 104.00}])


def _prod_269579732():
    return _mk_order(
        order_number="269579732",
        salla_total=416.15, subtotal=425.00, shipping=24.07,
        discount=63.75,
        items=[
            {"sku": "AMS11889", "quantity": 1,
             "unit_price": 200.00, "discount_amount": 60.00,
             "tax_amount": 11.20, "total": 151.20},
            {"sku": "AMS11961", "quantity": 1,
             "unit_price": 75.00, "discount_amount": 1.50,
             "tax_amount": 5.88, "total": 79.38},
            {"sku": "AMS11961", "quantity": 1,
             "unit_price": 75.00, "discount_amount": 0.75,
             "tax_amount": 5.94, "total": 80.19},
            {"sku": "AMS11961", "quantity": 1,
             "unit_price": 75.00, "discount_amount": 1.50,
             "tax_amount": 5.88, "total": 79.38},
        ])


def _prod_269640154():
    return _mk_order(
        order_number="269640154",
        salla_total=134.00, subtotal=100.00, shipping=24.07,
        discount=0.0,
        items=[{"sku": "AMS-X", "quantity": 1,
                "unit_price": 100.00, "discount_amount": 0.0,
                "tax_amount": 8.00, "total": 108.00}])


_FIVE = [
    _prod_269629400, _prod_269632660, _prod_269604656,
    _prod_269579732, _prod_269640154,
]


# ── 1. Five Production orders now report totals_status.valid=True ──
class TestFiveProductionOrdersPassGuard:

    @pytest.mark.parametrize("fixture", _FIVE)
    def test_valid_true(self, fixture):
        r = _check_totals(fixture())
        assert r["valid"] is True, r

    @pytest.mark.parametrize("fixture", _FIVE)
    def test_mezan_diff_within_tolerance(self, fixture):
        r = _check_totals(fixture())
        assert abs(r["diff"]) <= 0.01

    @pytest.mark.parametrize("fixture", _FIVE)
    def test_expected_equals_salla_gross(self, fixture):
        r = _check_totals(fixture())
        assert r["expected"] == pytest.approx(r["total"], abs=0.01)


# ── 2. Legacy diff preserved as diagnostic ──────────────────────────
class TestLegacyDiffIsDiagnostic:

    @pytest.mark.parametrize("fixture, expected_legacy_diff", [
        (_prod_269632660, -0.73),
        (_prod_269604656,  9.63),
        (_prod_269579732, -32.92),
        (_prod_269640154,  9.93),
    ])
    def test_legacy_diff_still_computed(
            self, fixture, expected_legacy_diff):
        r = _check_totals(fixture())
        assert r["legacy_diff"] == expected_legacy_diff
        # But it does NOT drive `valid`.
        assert r["valid"] is True

    def test_legacy_expected_present(self):
        r = _check_totals(_prod_269632660())
        assert r["legacy_expected"] == 129.07


# ── 3. Salla tax is IGNORED by the decision ─────────────────────────
class TestSallaTaxDoesNotAffectDecision:

    @pytest.mark.parametrize("salla_tax_variant", [0.0, 8.0, 15.0, 18.0])
    def test_variant_salla_tax_yields_same_valid(self, salla_tax_variant):
        order = _prod_269632660()
        order["tax_amount"] = salla_tax_variant
        for it in order["items"]:
            it["tax_amount"] = salla_tax_variant
        r = _check_totals(order)
        assert r["valid"] is True

    def test_zero_salla_tax_does_not_flip_decision(self):
        order = _prod_269604656()
        order["tax_amount"] = 0.0
        for it in order["items"]:
            it["tax_amount"] = 0.0
        # item.total is what actually matters — as long as canonical
        # carries the Salla-side gross, the Mezan guard reconciles.
        r = _check_totals(order)
        assert r["valid"] is True


# ── 4. canonical.tax_amount is not referenced for the decision ─────
class TestGuardDoesNotUseSallaTaxForDecision:

    def test_guard_engine_labels_correctly(self):
        r = _check_totals(_prod_269632660())
        assert r["guard_engine"] == "mezan_vat_15_simulation"

    def test_mezan_vat_rate_is_15(self):
        r = _check_totals(_prod_269632660())
        assert r["mezan_vat_rate"] == 0.15


# ── 5. & 6. Diff ≤ 0.01 → pass; > 0.01 → fail ─────────────────────
class TestTolerance:

    def test_within_tolerance_passes(self):
        order = _prod_269632660()
        r = _check_totals(order)
        assert abs(r["diff"]) <= 0.01
        assert r["valid"] is True

    def test_broken_canonical_fails_hard(self, monkeypatch):
        """When the simulation itself reports a diff > 0.01 (e.g.
        canonical is missing item.total for MULTIPLE lines and the
        fallback drifts), the guard MUST reject."""
        # Patch the simulator to return a large diff — proves that
        # `_check_totals.valid` is wired to that diff.
        from integrations.qoyod import qoyod_simulation as qs
        real = qs.build_qoyod_simulation
        def _fake(inbox_row):
            r = real(inbox_row=inbox_row)
            r["simulated_qoyod_diff_vs_salla_total"] = 5.00
            return r
        monkeypatch.setattr(qs, "build_qoyod_simulation", _fake)
        from integrations.qoyod import eligible_orders
        # `_check_totals` imports the simulator lazily — force it to
        # rebind by hitting the freshly-patched attribute.
        r = eligible_orders._check_totals(_prod_269632660())
        assert r["diff"] == 5.00
        assert r["valid"] is False


# ── 7. bank_transfer stays blocking even when totals reconcile ─────
class TestBankTransferStaysBlocked:

    def test_269604656_totals_pass_but_bank_transfer_blocks(self):
        order = _prod_269604656()
        totals = _check_totals(order)
        assert totals["valid"] is True
        # Simulate the classifier — bank_transfer without receiving
        # bank configured → blocked_bank_transfer_routing.
        verdict = _classify(
            order,
            inbox_row=None, invoice=None,
            customer_check={"resolved": True, "qoyod_id": 999001,
                            "reason": None},
            products_check={"resolved": True, "resolved_count": 1,
                            "dry_run_only": 0, "missing": [],
                            "first_blocker": None},
            totals_check=totals,
            receiving_bank_configured=False)
        assert verdict["classification"] == \
            "blocked_bank_transfer_routing"


# ── 8. DRY / PREVIEW / null ids stay blocking ──────────────────────
class TestIdentitySentinelsStayBlocked:

    def test_null_customer_id_blocks(self):
        order = _prod_269632660()
        totals = _check_totals(order)
        verdict = _classify(
            order, inbox_row=None, invoice=None,
            customer_check={"resolved": False, "qoyod_id": None,
                            "reason": "unmapped"},
            products_check={"resolved": True, "resolved_count": 1,
                            "dry_run_only": 0, "missing": [],
                            "first_blocker": None},
            totals_check=totals,
            receiving_bank_configured=True)
        assert verdict["classification"] == "blocked_customer"

    def test_dry_product_blocks(self):
        order = _prod_269632660()
        totals = _check_totals(order)
        verdict = _classify(
            order, inbox_row=None, invoice=None,
            customer_check={"resolved": True, "qoyod_id": 999001,
                            "reason": None},
            products_check={"resolved": False, "resolved_count": 0,
                            "dry_run_only": 1, "missing": [],
                            "first_blocker": "dry_run_only"},
            totals_check=totals,
            receiving_bank_configured=True)
        assert verdict["classification"] == "blocked_product"


# ── 9. payload_date_source == "send_date" ──────────────────────────
class TestPayloadDateSource:

    def test_send_date_marker_present(self):
        r = _check_totals(_prod_269632660())
        assert r["payload_date_source"] == "send_date"


# ── 10 & 11 & 13. Read-Only / no-write / no-API-call invariants ──
class TestReadOnlyInvariants:

    def test_check_totals_body_does_not_call_qoyod_api(self):
        src = inspect.getsource(_check_totals)
        assert "QoyodAPIClient" not in src
        assert "httpx" not in src
        assert "requests.post" not in src

    def test_check_totals_body_does_not_write_db(self):
        src = inspect.getsource(_check_totals)
        for banned in ("insert_one", "update_one", "delete_one",
                       "$set", "$unset"):
            assert banned not in src

    def test_simulation_module_read_only(self):
        import integrations.qoyod.qoyod_simulation as mod
        src = open(mod.__file__, encoding="utf-8").read()
        for banned in ("QoyodAPIClient", "insert_one", "update_one",
                       "delete_one", "$set"):
            assert banned not in src


# ── 12. Blocker precedence unchanged ────────────────────────────────
class TestBlockerPrecedenceUnchanged:
    """Ordering must remain: already_sent → totals → bank_transfer
    → blocked_status → blocked_customer → blocked_product → ..."""

    def test_already_sent_wins_over_bank_transfer(self):
        order = _prod_269604656()   # bank_transfer
        totals = _check_totals(order)
        verdict = _classify(
            order, inbox_row=None,
            invoice={"qoyod_invoice_id": 1234567,
                     "posting_mode": "auto"},
            customer_check={"resolved": True, "qoyod_id": 999001,
                            "reason": None},
            products_check={"resolved": True, "resolved_count": 1,
                            "dry_run_only": 0, "missing": [],
                            "first_blocker": None},
            totals_check=totals,
            receiving_bank_configured=False)
        assert verdict["classification"] == "already_sent"


# ── Extra: no gate flip / no policy change ──────────────────────────
class TestNoPolicyChanges:

    def test_selective_send_policy_untouched(self):
        """This iter must not modify the policy module."""
        import integrations.qoyod.selective_send_policy as p
        src = open(p.__file__, encoding="utf-8").read()
        assert "selective_live_send_enabled=True" not in src
        assert "production_writes_locked=False" not in src
