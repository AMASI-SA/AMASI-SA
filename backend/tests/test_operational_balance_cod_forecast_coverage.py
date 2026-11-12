"""A successful COD scan can prove coverage; rejected candidates stay visible as gaps."""
from copy import deepcopy
import pytest

from operational_financial_position_core import build_financial_position
from operational_forecast_projection import cod_forecast_coverage
from test_operational_balance_open_customer_cod import order
from test_operational_open_cost_exposure import seeded_db, operating_state, preview, START, NOW


def projection(documents, *, obligations=None):
    db = seeded_db(order_row=documents[0])
    db.data["unified_orders"] = documents
    state = {**operating_state(obligations), "openings": {}, "movements": []}
    exposure = preview(db, state)
    result = build_financial_position(state, cost_exposure=exposure)
    return exposure, result


def test_complete_current_customer_source_proves_its_total_without_a_permanent_missing_metadata_flag():
    exposure, result = projection([order()])
    assert exposure["open_customer_cod"]["withheld_candidates"] == 0
    assert cod_forecast_coverage(exposure, START, NOW)["complete"]
    table = next(row for row in result["currencies"]["SAR"]["tables"] if row["id"] == "cod_expected")
    assert table["complete"] and table["totals"]["amount"] == "77.00"


def test_unproven_customer_order_keeps_the_other_known_amount_and_withholds_only_the_full_total():
    missing = order("MISSING")
    missing["raw_by_source"]["salla_direct"].pop("payment_actions")
    exposure, result = projection([order(), missing])
    assert exposure["open_customer_cod"]["withheld_candidates"] == 1
    assert not cod_forecast_coverage(exposure, START, NOW)["complete"]
    table = next(row for row in result["currencies"]["SAR"]["tables"] if row["id"] == "cod_expected")
    assert table["known_totals"]["amount"] == "77.00"
    assert not table["complete"] and table["totals"]["amount"] is None


def test_closed_orders_and_established_other_payment_methods_do_not_invalidate_open_customer_coverage():
    closed = order("CLOSED", status="delivered")
    platform = order("PLATFORM")
    platform["raw_by_source"]["salla_direct"]["payment_method"] = "tamara"
    exposure, _ = projection([order(), closed, platform])
    assert exposure["open_customer_cod"]["withheld_candidates"] == 0


def test_duplicate_candidate_is_one_unresolved_order_and_a_proved_zero_is_not_unresolved():
    duplicate = order("DUPLICATE")
    exposure, _ = projection([order("PAID", paid="100"), duplicate, deepcopy(duplicate)])
    assert exposure["open_customer_cod"]["withheld_candidates"] == 1
    assert exposure["open_customer_cod"]["by_currency"] == {}


def cod_table(result):
    return next(row for row in result["currencies"]["SAR"]["tables"] if row["id"] == "cod_expected")


def test_missing_canonical_order_source_preserves_known_due_without_claiming_complete_coverage():
    missing = order("MISSING")
    missing.pop("raw_by_source")
    exposure, result = projection([order(), missing])
    coverage = cod_forecast_coverage(exposure, START, NOW)
    assert not coverage["complete"]
    assert "canonical_salla_source_missing_for_open_cost_preview" in coverage["reason_codes"]
    table = cod_table(result)
    assert table["known_totals"]["amount"] == "77.00"
    assert not table["complete"] and table["totals"]["amount"] is None


@pytest.mark.parametrize("claim", [None, "zero", "different_amount", "different_currency", "status_conflict", "exact"])
def test_current_waybill_needs_a_matching_saved_cod_forecast_to_close_its_source_gap(claim):
    shipped = order("WAYBILL")
    raw = shipped["raw_by_source"]["salla_direct"]
    raw["shipping"] = {"tracking_number": "W"}
    if claim == "status_conflict":
        shipped["order_status_slug"] = "cancelled"
    key = "cod:" + raw["id"]
    obligations = {}
    if claim is not None:
        obligations[key] = {"id": key, "kind": "cod", "party_type": "courier", "party_id": "carrier",
            "name": "Carrier", "currency": "QAR" if claim == "different_currency" else "SAR",
            "direction": "receivable", "confirmed": "0.00",
            "expected": "0.00" if claim == "zero" else "76.00" if claim == "different_amount" else "77.00"}
    exposure, result = projection([order(), shipped], obligations=obligations)
    table = cod_table(result)
    assert exposure["prestart_open_cod"]["by_currency"] == {}
    assert exposure["open_customer_cod"]["withheld_candidates"] == (0 if claim == "exact" else 1)
    assert table["complete"] is (claim == "exact")
    if claim == "exact":
        assert table["totals"]["amount"] == "154.00" and table["totals"]["orders"] == 2
        assert sum(row["id"] == key for row in table["rows"]) == 1
    else:
        assert table["totals"]["amount"] is None
        # Saved estimates stay visible; a missing claim is never invented.
        assert table["known_totals"]["amount"] == ({"different_amount": "153.00", "status_conflict": "154.00"}.get(claim, "77.00"))


def test_proved_prestart_customer_order_closes_only_its_own_rejection_in_the_shipping_cohort():
    exposure, result = projection([order(), order("OLD", old=True)])
    assert exposure["open_customer_cod"]["withheld_candidates"] == 0
    assert exposure["prestart_open_cod"] == {"by_currency": {}, "withheld_candidates": 0}
    table = cod_table(result)
    assert table["complete"] and table["totals"]["amount"] == "154.00"
    assert table["totals"]["orders"] == 2
    assert all(not key.startswith("_") for key in exposure["open_customer_cod"])


def test_other_cohort_resolution_does_not_clear_an_unknown_old_order_or_count_it_twice():
    missing = order("MISSING", old=True)
    missing["raw_by_source"]["salla_direct"].pop("payment_actions")
    exposure, result = projection([order("OLD", old=True), missing])
    coverage = cod_forecast_coverage(exposure, START, NOW)
    assert not coverage["complete"] and coverage["withheld_candidates"] == 1
    table = cod_table(result)
    assert not table["complete"] and table["known_totals"]["amount"] == "77.00"


def test_proved_external_prestart_cohort_closes_customer_rejection_without_duplicate_money():
    from test_operational_balance_prestart_cod_forecast import proved

    exposure, result = projection([order(), proved("SHIPPED")])
    assert exposure["open_customer_cod"]["withheld_candidates"] == 0
    assert exposure["prestart_open_cod"]["withheld_candidates"] == 0
    table = cod_table(result)
    assert table["complete"] and table["totals"]["amount"] == "154.00"
    assert table["totals"]["orders"] == 2


@pytest.mark.parametrize("status", ["delivered", "cancelled", "returned"])
@pytest.mark.parametrize("damage", [None, "conflict", "future", "refresh_pending", "identity_mismatch", "alias_mismatch"])
def test_only_positive_current_closed_status_excludes_old_cod_from_the_open_forecast(status, damage):
    closed = order("CLOSED", old=True, status=status)
    raw = closed["raw_by_source"]["salla_direct"]
    if damage == "conflict":
        closed["order_status_slug"] = "in_progress"
    elif damage == "future":
        raw["updated_at"] = closed["g47_salla_snapshot"]["source_updated_at"] = "2026-11-03T00:00:00+00:00"
    elif damage == "refresh_pending":
        closed["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    elif damage == "identity_mismatch":
        closed["salla_order_id"] = "foreign-order"
    elif damage == "alias_mismatch":
        closed["order_id"] = "foreign-order"
    exposure, result = projection([order(), closed])
    coverage = cod_forecast_coverage(exposure, START, NOW)
    assert coverage["complete"] is (damage is None)
    assert coverage["withheld_candidates"] == (0 if damage is None else 1)
    table = cod_table(result)
    assert table["known_totals"]["amount"] == "77.00"
    assert table["totals"]["amount"] == ("77.00" if damage is None else None)
