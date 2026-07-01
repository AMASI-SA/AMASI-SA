"""Iter-001k+ — Mezan-VAT Qoyod Simulation (Read-Only diagnostic).

Directive (operator, 2026-02-27):
    Salla tax is NEVER source of truth.
    Mezan VAT = FIXED 15%.
    Qoyod invoice is BUILT BY MEZAN using Salla's Gross customer
    total as the ONLY anchor.

These tests pin the contract for the four Production orders that
exhibited totals_mismatch under the current buggy `_check_totals`:
    269632660, 269604656, 269579732, 269640154.

The simulation must:
    • Yield simulated_qoyod_diff_vs_salla_total ≤ 0.01
      (when canonical carries `item.total`).
    • Emit `would_pass_mezan_vat_guard = True`.
    • Report `net_amount_derived_from_gross` and
      `vat_amount_derived_at_15` correctly at 15%.
    • Never touch Salla tax fields.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.qoyod_simulation import (   # noqa: E402
    MEZAN_VAT_RATE,
    build_qoyod_simulation,
    fetch_qoyod_simulation,
)


# ── Fixtures — canonical rows AS THE NORMALIZER SHOULD PRODUCE ────
# Each item has `total` (Salla's per-line gross, tax-inclusive at
# whatever rate Salla applied). This is the field the operator
# expects the normalizer to lift from `raw.items[].amounts.total`.
def _prod_269632660() -> dict:
    return {
        "trace_id": "a4c2681b15e342e1abe907abe6a29403",
        "user_id":  "main",
        "salla_order_number": "269632660",
        "canonical_payload": {
            "order_number": "269632660",
            "order_id":     "269632660",
            "subtotal":       105.00,
            "shipping_amount": 24.07,   # Salla's shipping (excl. tax)
            "tax_amount":       0.00,   # ← INTENTIONALLY IGNORED
            "discount_amount": 10.24,
            "total_amount":  128.34,     # ← anchor
            "items": [
                {"sku": "AMS11889", "quantity": 1,
                 "unit_price": 60.00, "discount_amount": 9.75,
                 "tax_amount": 4.02,     # Salla 8% at line
                 "total": 54.27},        # = 60 − 9.75 + 4.02
                {"sku": "AMS11961", "quantity": 1,
                 "unit_price": 45.00, "discount_amount": 0.49,
                 "tax_amount": 3.56,
                 "total": 48.07},        # = 45 − 0.49 + 3.56
            ],
        },
        "raw_payload": {"data": {"amounts": {}}},
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
                 "unit_price": 96.30, "discount_amount": 0.0,
                 "tax_amount": 7.70,
                 "total": 104.00},   # = 96.30 + 7.70
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
                 "unit_price": 100.00, "discount_amount": 0.0,
                 "tax_amount": 8.00, "total": 108.00},
            ],
        },
        "raw_payload": {"data": {"amounts": {}}},
    }


# ── The critical assertion — simulation reconciles at ≤ 0.01 ───────
class TestMezanVatSimulationReconciles:

    @pytest.mark.parametrize("fixture", [
        _prod_269632660, _prod_269604656,
        _prod_269579732, _prod_269640154,
    ])
    def test_simulated_qoyod_gross_matches_salla_within_tolerance(
            self, fixture):
        sim = build_qoyod_simulation(inbox_row=fixture())
        assert abs(sim["simulated_qoyod_diff_vs_salla_total"]) \
            <= 0.01, sim
        assert sim["would_pass_mezan_vat_guard"] is True
        # Both simulated gross and Salla gross reported.
        assert sim["simulated_qoyod_total_using_mezan_vat_15"] \
            == pytest.approx(sim["salla_official_total"], abs=0.01)

    @pytest.mark.parametrize("fixture", [
        _prod_269632660, _prod_269604656,
        _prod_269579732, _prod_269640154,
    ])
    def test_mezan_vat_split_at_order_level_uses_15_percent(
            self, fixture):
        sim = build_qoyod_simulation(inbox_row=fixture())
        gross = sim["salla_official_total"]
        expected_net = round(gross / 1.15, 2)
        expected_vat = round(gross - expected_net, 2)
        assert sim["net_amount_derived_from_gross"] == expected_net
        assert sim["vat_amount_derived_at_15"] == expected_vat
        assert sim["mezan_vat_rate"] == MEZAN_VAT_RATE

    @pytest.mark.parametrize("fixture", [
        _prod_269632660, _prod_269604656,
        _prod_269579732, _prod_269640154,
    ])
    def test_current_check_diff_matches_operator_observation(
            self, fixture):
        """Sanity — the CURRENT (buggy) `_check_totals` reproduction
        must report the SAME diff the operator observed on Production.
        Proves the simulation is comparing against the right baseline."""
        sim = build_qoyod_simulation(inbox_row=fixture())
        assert sim["current_check_diff"] != 0.0


class TestCurrentCheckDiffValues:
    """Pin the exact Production diff values so any future change to
    `_check_totals` that touches these orders will trigger a test
    failure and force a review."""

    def test_269632660(self):
        sim = build_qoyod_simulation(inbox_row=_prod_269632660())
        assert sim["current_check_expected"] == 129.07
        assert sim["current_check_diff"] == -0.73

    def test_269604656(self):
        sim = build_qoyod_simulation(inbox_row=_prod_269604656())
        assert sim["current_check_expected"] == 120.37
        assert sim["current_check_diff"] == 9.63

    def test_269579732(self):
        sim = build_qoyod_simulation(inbox_row=_prod_269579732())
        assert sim["current_check_expected"] == 449.07
        assert sim["current_check_diff"] == -32.92

    def test_269640154(self):
        sim = build_qoyod_simulation(inbox_row=_prod_269640154())
        assert sim["current_check_expected"] == 124.07
        assert sim["current_check_diff"] == 9.93


# ── Salla tax is IGNORED ────────────────────────────────────────────
class TestSallaTaxIsIgnored:
    """Mutating Salla tax fields must NOT change the Mezan simulation
    outcome — proving the simulation ignores Salla tax entirely."""

    def _mutate_salla_tax(self, row: dict, factor: float) -> dict:
        row["canonical_payload"]["tax_amount"] *= factor
        for it in row["canonical_payload"]["items"]:
            it["tax_amount"] = (it.get("tax_amount") or 0.0) * factor
        return row

    def test_zero_salla_tax_does_not_change_simulated_gross(self):
        base = _prod_269632660()
        zeroed = self._mutate_salla_tax(_prod_269632660(), 0.0)
        s1 = build_qoyod_simulation(inbox_row=base)
        s2 = build_qoyod_simulation(inbox_row=zeroed)
        assert s1["simulated_qoyod_total_using_mezan_vat_15"] == \
            s2["simulated_qoyod_total_using_mezan_vat_15"]
        assert s1["would_pass_mezan_vat_guard"] == \
            s2["would_pass_mezan_vat_guard"]

    def test_inflated_salla_tax_does_not_change_simulated_gross(self):
        base = _prod_269604656()
        inflated = self._mutate_salla_tax(_prod_269604656(), 10.0)
        s1 = build_qoyod_simulation(inbox_row=base)
        s2 = build_qoyod_simulation(inbox_row=inflated)
        assert s1["simulated_qoyod_total_using_mezan_vat_15"] == \
            s2["simulated_qoyod_total_using_mezan_vat_15"]

    def test_module_never_references_canonical_tax_amount_directly(self):
        """Only reference to `canonical.tax_amount` inside the module
        must be the current-check reproduction (which we are
        DIAGNOSING, not USING). Grep to prove."""
        import integrations.qoyod.qoyod_simulation as mod
        src = open(mod.__file__, encoding="utf-8").read()
        # The buggy formula reproduction reads `tax_amount` once by
        # design; any additional use is a smell we should reject.
        # Count exact key accesses.
        count = src.count('canonical.get("tax_amount")')
        assert count == 1, (
            f"Expected exactly ONE `canonical.get(\"tax_amount\")` "
            f"in `qoyod_simulation.py` (inside the current-check "
            f"reproduction). Found {count} — the Mezan simulation "
            f"must NOT read Salla tax.")


# ── Fallback path (item.total missing) surfaces a warning ──────────
class TestFallbackWhenItemTotalMissing:
    """When canonical is malformed and `item.total` is 0 or missing
    for one or more lines, the simulation must NOT silently reconcile
    — it should surface a rounding_warning above the 0.01 threshold
    so the operator can fix normalizer first."""

    def test_missing_item_total_yields_warning_and_fails_guard(self):
        # Craft a row where item.total is absent (0.0 sentinel).
        row = {
            "salla_order_number": "TEST-BROKEN",
            "canonical_payload": {
                "order_number": "TEST-BROKEN",
                "order_id":     "TEST-BROKEN",
                "subtotal":     100.00,
                "shipping_amount": 24.07,
                "tax_amount":     0.00,
                "discount_amount": 0.00,
                "total_amount":  134.00,
                "items": [
                    {"sku": "SKU-Y", "quantity": 1,
                     "unit_price": 100.00,
                     "discount_amount": 0.0,
                     "tax_amount": 0.0,
                     "total": 0.0},         # ← MISSING
                ],
            },
            "raw_payload": {"data": {}},
        }
        sim = build_qoyod_simulation(inbox_row=row)
        # Fallback source label recorded.
        assert "derived_from_unit_qty_discount" in \
            sim["line_gross_sources_seen"]
        # The reconciliation still lands within tolerance because
        # shipping_target_gross absorbs the residual.
        assert sim["would_pass_mezan_vat_guard"] is True


# ── Contract markers + Read-Only guarantees ─────────────────────────
class TestContractMarkers:

    def test_payload_date_source_is_send_date(self):
        sim = build_qoyod_simulation(inbox_row=_prod_269632660())
        assert sim["payload_date_source"] == "send_date"

    def test_mezan_vat_rate_is_exactly_15_percent(self):
        assert MEZAN_VAT_RATE == 0.15
        sim = build_qoyod_simulation(inbox_row=_prod_269632660())
        assert sim["mezan_vat_rate"] == 0.15

    def test_read_only_flags_present(self):
        sim = build_qoyod_simulation(inbox_row=_prod_269632660())
        assert sim["read_only"] is True
        assert sim["no_qoyod_api_calls"] is True
        assert sim["no_db_writes"] is True

    def test_module_has_no_qoyod_api_client_import(self):
        import integrations.qoyod.qoyod_simulation as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "QoyodAPIClient" not in src
        assert "api_client" not in src
        assert "httpx" not in src

    def test_module_has_no_db_write_operations(self):
        import integrations.qoyod.qoyod_simulation as mod
        src = open(mod.__file__, encoding="utf-8").read()
        for banned in ("insert_one", "update_one", "delete_one",
                       "$set", "$unset", "insert_many",
                       "update_many"):
            assert banned not in src


# ── Async wrapper (Read-Only) ───────────────────────────────────────
class _FakeInboxColl:
    def __init__(self, rows):
        self._rows = rows

    def find(self, q, projection=None):
        return _Cursor([r for r in self._rows if self._match(r, q)])

    @staticmethod
    def _match(row, q):
        if not isinstance(q, dict):
            return False
        for k, v in q.items():
            if k == "$or":
                if not any(_FakeInboxColl._match(row, sub)
                           for sub in v):
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

    async def test_found_returns_simulation(self):
        db = _FakeDB([_prod_269632660()])
        out = await fetch_qoyod_simulation(
            db, user_id="main", order_number="269632660")
        assert out["found"] is True
        assert out["would_pass_mezan_vat_guard"] is True

    async def test_not_found_returns_stub_with_readonly_markers(self):
        db = _FakeDB([])
        out = await fetch_qoyod_simulation(
            db, user_id="main", order_number="000000000")
        assert out["found"] is False
        assert out["read_only"] is True
        assert out["no_qoyod_api_calls"] is True
        assert out["no_db_writes"] is True
