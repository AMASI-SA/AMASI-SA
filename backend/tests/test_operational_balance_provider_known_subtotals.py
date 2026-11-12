"""Unresolved orders never erase other proved platform money or become fee-free net."""
from copy import deepcopy

import pytest

from operational_balance_service import report
from operational_configured_provider_fees import refresh_provider_fee_inputs
from operational_provider_report_coverage import provider_evidence_coverage
from operational_provider_summary import _records
from test_operational_balance_configured_provider_fees import paid_state, platform, settings
from test_operational_balance_financial_position import NOW, obligation, opening


def add_order(state, oid, *, paid="100.00", method="mada", claim=True):
    payment = {"provider": "salla", "currency": "SAR", "gross": paid, "payment_method": method,
               "captures": [{"id": "receipt-" + oid, "amount": paid, "captured_at": NOW}]}
    order = {"id": oid, "order_number": "order-" + oid, "currency": "SAR", "payment": payment,
             "created_at": NOW, "updated_at": NOW}
    refresh_provider_fee_inputs(state, {"provider_fee_settings": settings("مدى", "10", "0", "0"), "orders": [order]})
    if claim:
        obligation(state, "provider:" + oid, "provider", "provider", paid, party="salla", direction="receivable")
        state["engine"]["facts"]["capture:receipt-" + oid] = {"order_id": oid, "provider": "salla", "amount": paid}


def priced_state():
    state, _ = paid_state("salla", method="mada", terms=settings("مدى", "10", "0", "0"))
    add_order(state, "one")
    # The added input and the original capture describe the same receipt.
    state["engine"]["facts"].pop("capture:receipt")
    return state


def test_unknown_fee_preserves_other_net_but_never_labels_gross_as_known_net():
    state = priced_state()
    add_order(state, "two", method="unknown")
    state["engine"]["issues"] = [{"code": "provider_fee_estimate_unavailable", "source_id": "provider:two"}] * 2
    before = deepcopy(state)
    row = platform(state, "salla")
    assert row["gross"] == row["known_gross"] == "200.00"
    assert row["fees"] is None and row["known_fees"] == "10.00"
    assert row["net"] is None and row["known_net"] == "90.00"
    assert row["confirmed_orders"] == 2 and row["net_confirmed_orders"] == 1
    assert row["pending_orders"] == 1 and row["pending_order_ids"] == ["two"]
    assert row["pending_order_numbers"] == ["order-two"]
    reason, = row["pending_reasons"]
    assert reason["code"] == "provider_fee_estimate_unavailable" and reason["orders"] == 1
    assert state == before and row == platform(state, "salla")


def test_only_unknown_fee_has_no_known_net_zero_and_opening_is_independent():
    state, order = paid_state()
    state["engine"]["provider_fee_inputs"]["settings"] = {}
    row = platform(state)
    assert row["known_gross"] == "100.00" and row["known_fees"] is None
    assert row["known_net"] is None and row["pending_orders"] == 1
    opening(state, "opening", "provider", "120.00", party="tamara")
    assert platform(state)["known_net"] == "120.00"


def test_held_observation_does_not_offset_known_balance_or_opening_twice():
    state = priced_state()
    add_order(state, "held", paid="50.00", claim=False)
    opening(state, "opening", "provider", "20.00", party="salla")
    row = platform(state, "salla")
    assert row["known_gross"] == "150.00" and row["known_net"] == "110.00"
    assert row["known_fees"] == "10.00"
    assert row["confirmed_orders"] == 1 and row["pending_orders"] == row["held_orders"] == 1
    assert row["paid_orders"] == 2 and row["paid_order_ids"] == ["held", "one"]
    assert row["pending_order_ids"] == ["held"]
    assert not state["movements"] and state["openings"]["opening"]["amount"] == "20.00"


def test_seven_unknown_orders_show_unique_count_and_keep_proved_platform_amount():
    state = priced_state()
    issues = [{"scope": "provider_payment", "provider": "salla", "currency": "SAR",
               "code": "provider_payment_amount_conflict", "source_id": f"pending-{i}", "order_number": f"ref-{i}"}
              for i in range(7)]
    state["engine"]["issues"] = issues + issues + [{**issues[0], "code": "provider_payment_reference_conflict"}]
    row = platform(state, "salla")
    assert row["known_gross"] == "100.00" and row["gross"] is None
    assert row["known_net"] == "90.00" and row["net"] is None
    assert row["confirmed_orders"] == 1 and row["pending_orders"] == 7 and row["orders"] == 8
    assert row["paid_orders"] == 1
    assert len(row["pending_order_ids"]) == len(row["pending_order_numbers"]) == 7
    assert {r["code"]: r["orders"] for r in row["pending_reasons"]} == {
        "provider_payment_amount_conflict": 7, "provider_payment_reference_conflict": 1}


def test_unknown_source_without_paid_proof_stays_unknown_not_zero():
    state = priced_state()
    state["engine"]["obligations"].clear()
    state["engine"]["facts"].clear()
    state["engine"]["provider_fee_inputs"]["orders"].clear()
    state["engine"]["issues"] = [{"scope": "provider_payment", "provider": "salla", "currency": "SAR",
        "source_id": "pending", "code": "provider_capture_evidence_missing"}]
    row = platform(state, "salla")
    assert row["known_gross"] is None and row["gross"] is None
    assert row["known_net"] is None and row["known_fees"] is None
    assert row["pending_orders"] == 1 and row["confirmed_orders"] == 0


def test_expected_claim_with_no_paid_evidence_does_not_create_a_known_zero():
    state = priced_state()
    state["engine"]["provider_fee_inputs"]["orders"].clear()
    state["engine"]["facts"].clear()
    state["engine"]["obligations"]["provider:one"].update(confirmed="0.00", expected="90.00")
    state["engine"]["issues"] = [{"source_id": "provider:one", "code": "provider_capture_evidence_missing"}]
    row = platform(state, "salla")
    assert row["known_gross"] is None and row["known_fees"] is None and row["known_net"] is None
    assert row["pending_orders"] == 1 and row["confirmed_orders"] == 0


def test_all_pending_order_details_are_available_for_pagination():
    state = priced_state()
    state["engine"]["issues"] = [{"scope": "provider_payment", "provider": "salla", "currency": "SAR",
        "source_id": f"pending-{i}", "order_number": f"ref-{i}", "code": "provider_payment_amount_conflict"}
        for i in range(35)]
    row = platform(state, "salla")
    assert row["pending_orders"] == len(row["pending_order_details"]) == 35
    assert not row["pending_details_truncated"]
    assert all(set(detail) == {"order_id", "order_number", "reasons"} for detail in row["pending_order_details"])
    assert all(set(reason) == {"code", "message"} for detail in row["pending_order_details"] for reason in detail["reasons"])


def test_partial_settlement_and_refund_keeps_known_fees_but_not_ambiguous_remaining_net():
    state, _ = paid_state(refunded="40.00", terms=settings(rate="10", fixed="0", vat="0"))
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "tamara",
        "currency": "SAR", "amount": "45.00", "actual_fee_amount": "5.00", "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": "50.00"}]})
    row = platform(state)
    assert row["known_fees"] == "5.00" and row["known_net"] is None
    assert row["pending_orders"] == 1


def test_complete_page_evidence_does_not_require_provider_statement_for_customer_paid_coverage():
    state = priced_state()
    projection = report(state)
    row, = provider_evidence_coverage(projection)
    assert row["status"] == "complete"
    assert row["confirmed_orders"] == 1 and row["pending_orders"] == 0
    assert "يتطلب كشف" not in row["message"]


@pytest.mark.parametrize("evidence", ["unchanged" * 10000, ["retained"], 17, True, None],
                         ids=["packed-text", "list", "number", "boolean", "absent"])
def test_unrelated_retained_fact_evidence_cannot_break_provider_amounts(evidence):
    state = priced_state()
    expected = platform(state, "salla")
    state["engine"]["facts"]["retained-proof"] = {"amount": "17.25", "evidence": evidence}
    before = deepcopy(state)
    assert platform(state, "salla") == expected
    assert state == before


def test_provider_fact_identity_in_dict_evidence_is_still_read():
    state = priced_state()
    state["engine"]["facts"]["capture:receipt-one"] = {
        "provider": "salla", "amount": "120.00", "evidence": {"order_id": "one"}}
    state["engine"]["facts"]["refund:salla:return-one"] = {
        "amount": "20.00", "evidence": {"order_id": "one"}}
    records, _ = _records(state, [], {})
    assert records["provider:one"]["gross"] == 120
    assert records["provider:one"]["refunded"] == 20
    assert records["provider:one"]["refund_events"] == [{"id": "return-one", "amount": "20.00", "status": "executed"}]


def test_paid_page_and_capture_are_one_paid_order_even_when_recognition_is_held():
    from test_operational_balance_order_page_report import page
    from test_operational_balance_financial_position import state_for
    state = state_for()
    obligation(state, "provider:a", "provider", "provider", "40.00", party="tamara", direction="receivable")
    obligation(state, "provider:b", "provider", "provider", "20.00", party="tamara", direction="receivable")
    state["engine"]["facts"] = {"provider_order_balance:a": page("a", "40.00", "60.00"),
        "capture:a": {"order_id": "a", "provider": "tamara", "amount": "40.00"},
        "capture:b": {"order_id": "b", "provider": "tamara", "amount": "20.00"}}
    state["engine"]["issues"] = [{"code": "provider_settled_capture_review_required", "source_id": "provider:a",
                                 "order_page_balance_review": page("a", "85.00", "15.00")}]
    row = platform(state)
    assert row["known_gross"] == "105.00"
    assert row["paid_orders"] == 2 and row["paid_order_ids"] == ["a", "b"]
    assert row["confirmed_orders"] == 1 and row["net_confirmed_orders"] == 0


def test_legacy_sar_paid_total_uses_matching_individual_foreign_proof_for_count():
    from test_operational_balance_order_page_report import page
    from test_operational_balance_financial_position import state_for
    state = state_for()
    state["engine"]["facts"] = {"provider_order_balance:foreign": page("foreign", "9.00", "91.00", currency="KWD")}
    state["engine"]["customer_tender_coverage"] = {"sar": {"by_method": [
        {"method": "tamara", "orders": 1, "paid": "110.19", "remaining": None}]}}
    rows = report(state, provider_balances_sar=True, provider_balances_net=True)["details"]["provider_balances"]["SAR"]
    row, = rows
    assert row["known_gross"] == "110.19" and row["paid_orders"] == 1
    assert row["confirmed_orders"] == row["net_confirmed_orders"] == 0
    assert row["paid_order_ids"] == ["foreign"]
    assert row["pending_order_details"][0]["order_id"] == "foreign"


@pytest.mark.parametrize("identities,expected", [(None, None), (["one", "one", "two"], 2)])
def test_sar_aggregate_needs_individual_proof_index_to_count_paid_orders(identities, expected):
    from test_operational_balance_financial_position import state_for
    state = state_for()
    source = {"method": "salla", "orders": 2, "paid": "100.00", "remaining": None}
    if identities is not None:
        source["paid_order_ids"] = identities
    state["engine"]["customer_tender_coverage"] = {"sar": {"by_method": [source]}}
    row, = report(state, provider_balances_sar=True, provider_balances_net=True)["details"]["provider_balances"]["SAR"]
    assert row["known_gross"] == "100.00" and row["paid_orders"] == expected
    assert row["paid_orders_complete"] is (identities is not None)
    assert row["confirmed_orders"] == 0


def test_sar_source_count_index_tracks_positive_proved_payments_only():
    from operational_customer_tender_coverage import new_coverage, _record_sar, finish_coverage
    coverage = new_coverage(NOW, NOW)
    coverage["source_order_count"] = 3
    for oid, paid in (("paid", "50.00"), ("unpaid", "0.00")):
        _record_sar(coverage, {"id": oid}, {"salla"},
            {"paid": paid, "basis": "native_sar", "shares": {"salla": paid}}, {})
    _record_sar(coverage, {"id": "unknown"}, {"salla"}, None, {})
    finish_coverage(coverage)
    row, = coverage["sar"]["by_method"]
    assert row["known_paid"] == "50.00" and row["orders"] == 3
    assert row["paid_order_ids"] == ["paid"] and row["paid_orders"] == 1


def test_verified_zero_paid_source_order_is_not_an_unidentified_pending_payment():
    from decimal import Decimal
    from operational_customer_tender_coverage import new_coverage, _record_sar, finish_coverage
    from test_operational_balance_financial_position import state_for

    state = state_for()
    coverage = new_coverage(state["started_at"], NOW)
    coverage["source_order_count"] = 1
    _record_sar(coverage, {"id": "unpaid"}, {"salla"},
        {"paid": "0.00", "basis": "native_sar", "shares": {"salla": "0.00"}}, {"salla": Decimal("100.00")})
    finish_coverage(coverage)
    state["engine"]["customer_tender_coverage"] = coverage
    before = deepcopy(state)
    row, = report(state, provider_balances_sar=True, provider_balances_net=True)["details"]["provider_balances"]["SAR"]
    assert row["orders"] == 1 and row["paid_orders"] == 0
    assert row["known_gross"] == row["known_net"] == "0.00" and row["complete"]
    assert row["pending_orders"] == row["unidentified_pending_orders"] == 0
    assert row["pending_order_details"] == [] and row["pending_details_complete"]
    assert state == before


def test_zero_paid_order_does_not_hide_or_duplicate_the_real_pending_fee_among_paid_orders():
    from decimal import Decimal
    from operational_customer_tender_coverage import new_coverage, _record_sar, finish_coverage

    state = priced_state()
    add_order(state, "two", method="unknown")
    coverage = new_coverage(state["started_at"], NOW)
    coverage["source_order_count"] = 3
    for oid, paid in (("one", "100.00"), ("two", "100.00"), ("unpaid", "0.00")):
        _record_sar(coverage, {"id": oid}, {"salla"},
            {"paid": paid, "basis": "native_sar", "shares": {"salla": paid}}, {"salla": Decimal(0)})
    finish_coverage(coverage)
    state["engine"]["customer_tender_coverage"] = coverage
    before = deepcopy(state)
    row, = report(state, provider_balances_sar=True, provider_balances_net=True)["details"]["provider_balances"]["SAR"]
    assert row["orders"] == 3 and row["paid_orders"] == 2
    assert row["known_gross"] == "200.00" and row["known_fees"] == "10.00" and row["known_net"] == "90.00"
    assert row["pending_orders"] == 1 and row["unidentified_pending_orders"] == 0
    assert [item["order_id"] for item in row["pending_order_details"]] == ["two"]
    assert row["pending_details_complete"] and not row["complete"]
    assert state == before
