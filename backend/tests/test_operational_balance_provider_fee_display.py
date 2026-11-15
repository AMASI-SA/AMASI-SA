"""Provider fee presentation keeps calculable estimates visible and qualified."""
from copy import deepcopy

import pytest

from operational_financial_position_core import _provider_table


def provider_row(provider="tabby", *, known="10.00", full=None, complete=False):
    return {
        "provider": provider, "name": provider, "orders": 2, "paid_orders": 2,
        "gross": "100.00", "known_gross": "100.00", "fees": None,
        "known_fees": "25.00", "net": None, "known_net": "75.00",
        "refunded": "0.00", "actual_statement_fees": "15.00",
        "actual_settlement_fees": "0.00", "estimated_fees": full,
        "known_estimated_fees": known, "estimated_fees_complete": complete,
        "estimated_fee_incomplete_orders": 0 if complete else 1,
        "fee_status": "mixed" if complete else "review",
        "fee_basis_sources": ["actual_statement", "average_estimate"],
        "complete": complete, "pending_orders": 1,
    }


def test_provider_table_displays_direct_known_estimate_and_partial_total():
    partial = provider_row()
    other = provider_row("tamara", known="20.00", full="20.00", complete=True)
    other.update(actual_statement_fees="0.00", known_fees="20.00")
    rows = [partial, other]
    before = deepcopy(rows)

    result = _provider_table(rows, [])

    assert result["rows"][0]["estimated_fees"] == "10.00"
    assert result["rows"][0]["estimated_fees_complete"] is False
    assert result["rows"][0]["estimated_fee_incomplete_orders"] == 1
    assert result["rows"][1]["estimated_fees"] == "20.00"
    assert result["totals"]["estimated_fees"] == "30.00"
    assert result["totals"]["estimated_fees_complete"] is False
    assert result["totals"]["fees"] == "45.00"
    assert result["totals"]["actual_fees"] == "15.00"
    assert result["complete"] is False
    assert rows == before


@pytest.mark.parametrize("known,full,complete,expected", [
    (None, None, False, None),
    ("0.00", None, False, "0.00"),
    ("0.00", "0.00", True, "0.00"),
    ("6.25", "6.25", True, "6.25"),
])
def test_provider_table_estimate_preserves_unknown_zero_and_complete(known, full, complete, expected):
    result = _provider_table([provider_row(known=known, full=full, complete=complete)], [])
    assert result["rows"][0]["estimated_fees"] == expected
    assert result["totals"]["estimated_fees"] == expected
    assert result["totals"]["estimated_fees_complete"] is complete


def test_provider_table_does_not_derive_missing_estimate_from_total_minus_actual():
    row = provider_row(known=None)
    row.update(known_fees="25.00", actual_statement_fees="15.00")
    result = _provider_table([row], [])
    assert result["rows"][0]["estimated_fees"] is None
    assert result["totals"]["estimated_fees"] is None
    assert result["totals"]["fees"] == "25.00"


def test_provider_table_known_estimate_sum_keeps_unknown_provider_partial():
    known = provider_row(known="6.25", full="6.25", complete=True)
    unknown = provider_row("other", known=None)
    result = _provider_table([known, unknown], [])
    assert result["totals"]["estimated_fees"] == "6.25"
    assert result["totals"]["estimated_fees_complete"] is False


def test_provider_table_empty_incomplete_coverage_has_no_estimate_or_complete_claim():
    result = _provider_table([], [{"code": "provider_source_unavailable"}])
    assert result["totals"]["estimated_fees"] is None
    assert result["totals"]["estimated_fees_complete"] is False
