from copy import deepcopy
import hashlib
import json
import pytest

from integrations_control_center.google_store_product_observations import observe_product_lines, observe_prior_season_lines
from tests.test_google_store_product_observations import NOW, order


def prior_order():
    value = order(order_date="2025-11-30")
    value["raw_by_source"]["salla_direct"]["date"] = "2025-11-30T21:30:00Z"
    return value


def prior(rows, **changes):
    return observe_prior_season_lines(rows, owner_id="owner", product_ids=["123"], as_of=NOW,
        **({"date_from": "2025-11-01", "date_to": "2025-12-31"} | changes))


def test_prior_raw_lines_use_same_partial_contract_without_last90_fallback():
    current = order(order_id="11", order_number="21")
    current["raw_by_source"]["salla_direct"].update(id=11, reference_id=21)
    result = prior([prior_order(), current])
    window = result["products"][0]["currency_groups"][0]["windows"]["owner_reviewed_prior_season"]
    assert window == {"observed_orders": 1, "observed_lines": 1, "observed_units": "2"}
    assert result["windows"] == {"owner_reviewed_prior_season": {"from": "2025-11-01", "to": "2025-12-31", "timezone": "Asia/Riyadh"}}
    assert result["coverage_complete"] is False and result["refunds_reconciled"] is False
    assert result["products"][0]["profitability"] is None
    assert result["products"][0]["growth_14_days_percent"] is None


def test_prior_source_offset_binding_and_duplicate_refund_rules_not_weakened():
    value = prior_order()
    assert prior([value, deepcopy(value)])["products"][0]["currency_groups"] == []
    value["raw_by_source"]["salla_direct"]["payment_actions"] = {"refund_action": {"has_refund_amount": True}}
    assert prior([value])["products"][0]["currency_groups"] == []
    value = prior_order()
    value["raw_by_source"]["salla_direct"]["date"] = "2025-11-30T21:30:00"
    assert prior([value])["products"][0]["currency_groups"] == []


@pytest.mark.parametrize("dates", [{"date_from": "2024-01-01", "date_to": "2025-01-01"},
    {"date_from": "2026-10-29", "date_to": "2026-10-29"}, {"date_from": "2025-02-30"}])
def test_unbounded_unclosed_or_invalid_prior_dates_rejected(dates):
    with pytest.raises(ValueError):
        prior([], **dates)


def test_366_inclusive_days_supported_without_calendar_shift_or_zero_fill():
    result = prior([], date_from="2024-01-01", date_to="2024-12-31")
    assert result["products"][0]["currency_groups"] == []
    assert result["windows"]["owner_reviewed_prior_season"]["from"] == "2024-01-01"


def test_prior_currency_groups_stay_native_and_separate():
    first, second = prior_order(), prior_order()
    second.update(order_id="11", order_number="21", currency="USD")
    second["raw_by_source"]["salla_direct"].update(id=11, reference_id=21, currency="USD")
    groups = prior([first, second])["products"][0]["currency_groups"]
    assert [g["currency_code"] for g in groups] == ["SAR", "USD"]
    assert all(g["platform_account_id"] is None for g in groups)


def test_current_four_window_contract_matches_pre_factor_baseline():
    value = order()
    cases = [observe_product_lines(rows, owner_id="owner", product_ids=["123", "456"], as_of=NOW, truncated=truncated)
             for rows, truncated in [([], False), ([value], False), ([value, deepcopy(value)], False), ([value], True)]]
    # Captured from immutable8dc before factoring: empty, valid, duplicate and
    # truncated current-window responses, including all fields/limitations.
    digest = hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert digest == "af8875a23d4868f15cf835e6863239b251c0fec8d59b1429bc4c6ed39ce583eb"
