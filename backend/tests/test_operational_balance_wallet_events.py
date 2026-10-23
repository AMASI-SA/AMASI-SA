"""Owner rule, chronology, identity and money conservation at the pure seam."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_ad_wallet_events import project_wallet_events


def event(identity, kind, amount, hour=10, **changes):
    return {"owner_id": "owner", "account_id": "ad", "currency": "SAR",
            "id": identity, "evidence_id": "evidence:" + identity,
            "kind": kind, "amount": amount,
            "occurred_at": f"2026-10-20T{hour:02d}:00:00+03:00", **changes}


def project(events, wallet="50", debt="0", **changes):
    return project_wallet_events(owner_id="owner", account_id="ad", currency="SAR",
                                 opening_at="2026-10-20T00:00:00+03:00",
                                 opening_wallet=wallet, opening_debt=debt,
                                 events=events, **changes)


@pytest.mark.parametrize("wallet,spend,left,debt", [
    ("50", "80", "0.00", "30.00"), ("0", "80", "0.00", "80.00"),
    ("50", "50", "0.00", "0.00"), ("100", "80", "20.00", "0.00"),
    ("0.01", "0.03", "0.00", "0.02"), ("50", "0", "50.00", "0.00"),
])
def test_spend_consumes_wallet_then_creates_debt(wallet, spend, left, debt):
    result = project([event("s", "spend", spend)], wallet=wallet)
    assert (result["wallet_balance"], result["payable"]) == (left, debt)
    assert result["payments"] == "0.00"


@pytest.mark.parametrize("payment,wallet,debt", [
    ("200", "100.00", "0.00"), ("100", "0.00", "0.00"),
    ("50", "0.00", "50.00"),
])
def test_payment_settles_debt_before_funding_wallet(payment, wallet, debt):
    result = project([event("p", "payment", payment)], wallet="0", debt="100")
    assert (result["wallet_balance"], result["payable"]) == (wallet, debt)
    assert result["payments"] == f"{Decimal(payment):.2f}"


def test_funding_after_debt_does_not_reclassify_earlier_spend():
    result = project([event("s", "spend", "80"), event("p", "payment", "100", 11)])
    assert result["allocations"][0]["debt_added"] == "30.00"
    assert result["allocations"][1]["debt_settled"] == "30.00"
    assert result["wallet_balance"] == "70.00" and result["payable"] == "0.00"
    reversed_time = project([event("s", "spend", "80", 11), event("p", "payment", "100")])
    assert reversed_time["allocations"][1]["debt_added"] == "0.00"


def test_repeated_refresh_and_reordered_input_are_identical_and_do_not_mutate_input():
    events = [event("s", "spend", "80"), event("p", "payment", "100", 11), event("s2", "spend", "90", 12)]
    before = deepcopy(events)
    result = project(events)
    assert project(list(reversed(events)) + events) == result
    assert events == before
    assert result["wallet_balance"] == "0.00" and result["payable"] == "20.00"
    assert result["payments"] == "100.00" and result["spend"] == "170.00"


@pytest.mark.parametrize("change,code", [
    ({"owner_id": "other"}, "scope_mismatch"),
    ({"account_id": "other"}, "scope_mismatch"),
    ({"currency": "USD"}, "scope_mismatch"),
    ({"evidence_id": ""}, "evidence_required"),
    ({"kind": "daily_total"}, "kind_unsupported"),
    ({"kind": "correction"}, "kind_unsupported"),
    ({"occurred_at": "2026-10-20T10:00:00"}, "time_invalid"),
    ({"occurred_at": "2026-10-19T10:00:00Z"}, "before_opening"),
    ({"sequence": True}, "sequence_invalid"),
    ({"amount": "NaN"}, "amount_invalid"),
    ({"amount": "-1"}, "amount_invalid"),
    ({"amount": "0.001"}, "amount_invalid"),
    ({"amount": 0.1}, "amount_invalid"),
])
def test_unsupported_or_ambiguous_evidence_is_not_guessed(change, code):
    with pytest.raises(ValueError, match="wallet_event_" + code):
        project([{**event("s", "spend", "80"), **change}])


def test_conflicting_replay_identity_is_rejected():
    with pytest.raises(ValueError, match="wallet_event_identity_conflict"):
        project([event("s", "spend", "80"), event("s", "spend", "81")])


def test_same_evidence_cannot_be_counted_again_under_a_different_event_id():
    with pytest.raises(ValueError, match="wallet_event_evidence_reused"):
        project([event("s", "spend", "80"),
                 event("new-id", "spend", "80", 11, evidence_id="evidence:s")])


def test_aggregate_bound_is_enforced_without_changing_input():
    events = [event("p", "payment", "999999999999.99")]
    before = deepcopy(events)
    with pytest.raises(ValueError, match="wallet_event_total_exceeds_limit"):
        project(events)
    assert events == before


def test_equal_timestamps_require_evidenced_sequence_not_list_order():
    with pytest.raises(ValueError, match="wallet_event_chronology_required"):
        project([event("s", "spend", "80"), event("p", "payment", "100")])
    result = project([event("p", "payment", "100", sequence=2), event("s", "spend", "80", sequence=1)])
    assert result["allocations"][0]["debt_added"] == "30.00"
    assert result["allocations"][1]["debt_settled"] == "30.00"
    with pytest.raises(ValueError, match="wallet_event_chronology_required"):
        project([event("p", "payment", "100", sequence=1), event("s", "spend", "80", sequence=1)])


def test_timezone_offsets_order_the_same_actual_instants():
    result = project([event("s", "spend", "80", occurred_at="2026-10-20T08:00:00Z"),
                      event("p", "payment", "100", occurred_at="2026-10-20T10:00:00+03:00")])
    assert result["allocations"][0]["id"] == "p"
    assert result["allocations"][1]["debt_added"] == "0.00"


def test_every_prefix_conserves_wallet_minus_debt():
    events = [event("s", "spend", "80"), event("p", "payment", "100", 11),
              event("s2", "spend", "90", 12), event("p2", "payment", "10", 13)]
    for size in range(len(events) + 1):
        result = project(events[:size], debt="15")
        assert Decimal(result["wallet_balance"]) - Decimal(result["payable"]) == (
            Decimal("50") - Decimal("15") + Decimal(result["payments"]) - Decimal(result["spend"]))
