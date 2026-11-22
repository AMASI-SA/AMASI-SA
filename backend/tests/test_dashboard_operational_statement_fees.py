"""Accepted order fees replace dashboard estimates without changing balances."""
from copy import deepcopy
from decimal import Decimal

import pytest

from dashboard_operational_statement_fees import build_operational_statement_fee_index
from operational_balance_store import digest, initial
from operational_provider_statement_fees import freeze_statement_fee_events

OWNER = "synthetic-owner"
NOW = "2026-11-04T12:00:00+03:00"
SNAPSHOT = "2026-11-01T12:00:00+03:00"


def paid_state(provider="tabby", *, refunded="0.00"):
    state = initial(OWNER)
    state.update(status="active", started_at="2026-11-01T00:00:00+03:00")
    order = {"order_number": "order-one", "currency": "SAR", "created_at": SNAPSHOT, "updated_at": SNAPSHOT,
        "payment": {"provider": provider, "gross": "100.00", "currency": "SAR",
                    "captures": [{"id": "receipt", "amount": "100.00", "captured_at": SNAPSHOT}]}}
    state["engine"] = {"as_of": SNAPSHOT, "issues": [],
        "facts": {"capture:receipt": {"order_id": "one", "provider": provider, "amount": "100.00"}},
        "obligations": {"provider:one": {"id": "provider:one", "kind": "provider", "party_type": "provider",
            "party_id": provider, "direction": "receivable", "currency": "SAR",
            "confirmed": format(Decimal("100.00") - Decimal(refunded), ".2f"), "expected": "0.00"}},
        "provider_fee_inputs": {"orders": {"one": order}, "average_profiles": [], "statement_events": []}}
    if Decimal(refunded):
        state["engine"]["facts"]["refund:" + provider + ":refund"] = {"order_id": "one", "amount": refunded}
    return state, order


def tabby_state():
    return paid_state("tabby")


def accepted_event(state, *, key="sale", kind="capture", cover="capture:receipt",
                   principal="100.00", fee="7.00", vat="1.05", total="8.05"):
    return {"event_key": key, "provider": "tabby", "order_id": "one", "native_currency": "SAR",
        "kind": kind, "principal_amount": principal, "fee_net": fee, "fee_vat": vat, "fee_total": total,
        "event_date": "2026-11-01", "date_precision": "day",
        "covered_event": {"kind": "fact", "id": cover, "fingerprint": digest(state["engine"]["facts"][cover]),
                          "amount": principal, "financial_effect": "fee_only"},
        "source": {"file_hash": "a" * 64, "row_fingerprint": "b" * 64, "statement_ref": "synthetic-week"},
        "confirmation": "exact_match", "accepted_at": "2026-11-02T12:00:00+03:00"}


def split_captures(state):
    facts = state["engine"]["facts"]
    facts.pop("capture:receipt")
    captures = []
    for identity, amount in (("first", "60.00"), ("second", "40.00")):
        facts["capture:" + identity] = {"order_id": "one", "provider": "tabby", "amount": amount}
        captures.append({"id": identity, "amount": amount, "captured_at": SNAPSHOT})
    state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]["captures"] = captures


def accept(state, *events):
    state["engine"]["provider_fee_inputs"]["orders"]["one"]["order_number"] = "order-one"
    state.setdefault("provider_statement_reconciliation", {})["events"] = {
        event["event_key"]: deepcopy(event) for event in events}
    freeze_statement_fee_events(state)


def project(state, *, as_of=NOW, numbers=("order-one",), owner=OWNER):
    return build_operational_statement_fee_index(owner, state, numbers, accepted_as_of=as_of)


def test_full_actual_fee_and_plan_are_projected_without_changing_saved_state():
    state, _ = tabby_state()
    event = accepted_event(state)
    event["source"]["plan_id"] = "Installments3Months"
    accept(state, event)
    before = deepcopy(state)
    row = project(state)["order-one"]
    assert row["available"] is True
    assert (row["fee_net"], row["fee_vat"], row["fee_total"]) == ("7.00", "1.05", "8.05")
    assert (row["principal_amount"], row["refunded_amount"]) == ("100.00", "0.00")
    assert row["plan_id"] == "Installments3Months"
    assert row["source_event_count"] == 1 and row["source_event_keys"] == ["sale"]
    assert row["capture_references"] == ["receipt"] and row["refund_references"] == []
    assert state == before


def test_late_acceptance_restates_the_order_but_future_acceptance_is_excluded():
    state, _ = tabby_state()
    accept(state, accepted_event(state))
    assert project(state, as_of="2026-11-02T11:59:59+03:00") == {}
    assert project(state, as_of="2026-11-02T12:00:00+03:00")["order-one"]["fee_total"] == "8.05"
    # The fee-only upload does not move the saved snapshot date.
    assert state["engine"]["as_of"] == "2026-11-01T12:00:00+03:00"


def test_partial_actual_does_not_become_a_full_fee_but_preserves_accepted_plan():
    state, _ = tabby_state()
    split_captures(state)
    event = accepted_event(state, cover="capture:first", principal="60.00", fee="4.00", vat=".60", total="4.60")
    event["source"]["plan_id"] = "Installments3Months"
    accept(state, event)
    row = project(state)["order-one"]
    assert row["available"] is False and row["reason_code"] == "provider_statement_fee_coverage_incomplete"
    assert row["plan_id"] == "Installments3Months"
    assert row["principal_amount"] == "100.00" and row["covered_principal_amount"] == "60.00"
    assert "fee_net" not in row and "fee_total" not in row


def test_disjoint_full_capture_coverage_sums_fees_once():
    state, _ = tabby_state()
    split_captures(state)
    first = accepted_event(state, cover="capture:first", principal="60.00", fee="4.00", vat=".60", total="4.60")
    second = accepted_event(state, key="second", cover="capture:second", principal="40.00", fee="3.00", vat=".45", total="3.45")
    accept(state, first, second)
    row = project(state)["order-one"]
    assert row["available"] is True and row["fee_total"] == "8.05"
    assert row["source_event_count"] == 2
    assert row["capture_references"] == ["first", "second"]


def test_signed_refund_rebate_is_included_only_with_full_refund_coverage():
    state, _ = paid_state("tabby", refunded="40.00")
    capture = accepted_event(state)
    refund = accepted_event(state, key="refund", kind="refund", cover="refund:tabby:refund", principal="40.00",
        fee="-2.00", vat="-.30", total="-2.30")
    accept(state, capture, refund)
    row = project(state)["order-one"]
    assert row["available"] is True and row["refunded_amount"] == "40.00"
    assert (row["fee_net"], row["fee_vat"], row["fee_total"]) == ("5.00", "0.75", "5.75")
    assert row["capture_references"] == ["receipt"] and row["refund_references"] == ["refund"]
    accept(state, capture)
    row = project(state)["order-one"]
    assert row["available"] is False and "fee_total" not in row


def test_reexport_deduplicates_economic_cover_and_refuses_conflicting_actuals():
    state, _ = tabby_state()
    first = accepted_event(state)
    second = deepcopy(first)
    second.update(event_key="reexport", accepted_at="2026-11-03T12:00:00+03:00")
    second["source"]["file_hash"] = "c" * 64
    accept(state, first, second)
    row = project(state)["order-one"]
    assert row["available"] is True and row["fee_total"] == "8.05" and row["source_event_count"] == 1
    second.update(fee_net="8.00", fee_vat="1.20", fee_total="9.20")
    accept(state, first, second)
    assert project(state)["order-one"]["available"] is False


def test_conflicting_plan_proofs_do_not_invalidate_a_complete_explicit_fee():
    state, _ = tabby_state()
    first = accepted_event(state)
    first["source"]["plan_id"] = "Installments3Months"
    second = deepcopy(first)
    second["event_key"] = "reexport"
    second["source"]["plan_id"] = "Installments5Months"
    accept(state, first, second)
    row = project(state)["order-one"]
    assert row["available"] is True and row["fee_total"] == "8.05"
    assert row["plan_id"] is None and row["plan_issue"] == "provider_statement_plan_ambiguous"


@pytest.mark.parametrize("change", ["covered_fact", "canonical", "frozen_missing", "canonical_missing", "wrong_owner", "currency"])
def test_changed_or_unaccepted_evidence_never_supplies_actual_or_plan(change):
    state, _ = tabby_state()
    event = accepted_event(state)
    event["source"]["plan_id"] = "Installments3Months"
    accept(state, event)
    if change == "covered_fact":
        state["engine"]["facts"]["capture:receipt"]["revision"] = "changed"
    elif change == "canonical":
        state["provider_statement_reconciliation"]["events"]["sale"]["fee_total"] = "9.20"
    elif change == "frozen_missing":
        state["engine"]["provider_fee_inputs"]["statement_events"] = []
    elif change == "canonical_missing":
        state["provider_statement_reconciliation"]["events"] = {}
    elif change == "wrong_owner":
        state["owner_id"] = "different-owner"
    else:
        event["native_currency"] = "USD"
        accept(state, event)
    row = project(state)["order-one"]
    assert row["available"] is False and row["plan_id"] is None and "fee_total" not in row


def test_same_order_number_for_multiple_frozen_orders_is_ambiguous():
    state, _ = tabby_state()
    accept(state, accepted_event(state))
    inputs = state["engine"]["provider_fee_inputs"]
    inputs["orders"]["two"] = deepcopy(inputs["orders"]["one"])
    row = project(state)["order-one"]
    assert row["reason_code"] == "provider_statement_fee_order_ambiguous"


def test_unrelated_order_proofs_are_not_added_to_the_dashboard_cohort():
    state, _ = tabby_state()
    accept(state, accepted_event(state))
    assert project(state, numbers=("different-number",)) == {}


def test_settled_fee_is_projected_once_instead_of_added_to_the_actual_again():
    state, _ = tabby_state()
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "tabby",
        "currency": "SAR", "amount": "91.95", "actual_fee_amount": "8.05", "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": "100.00"}]})
    event = accepted_event(state)
    accept(state, event)
    assert project(state)["order-one"]["fee_total"] == "8.05"
    event["covered_event"] = {"kind": "movement", "id": "payout", "fingerprint": digest(state["movements"][0]),
        "amount": "100.00", "financial_effect": "operational_fee"}
    accept(state, event)
    assert project(state)["order-one"]["fee_total"] == "8.05"
    state["movements"][0]["actual_fee_amount"] = "9.05"
    assert project(state)["order-one"]["available"] is False


def test_unresolved_refund_alias_keeps_the_order_under_review():
    state, _ = tabby_state()
    accept(state, accepted_event(state))
    state["engine"]["issues"] = [{"source_id": "provider:one", "order_id": "one", "provider": "tabby",
                                  "code": "provider_statement_refund_alias_review"}]
    assert project(state)["order-one"]["reason_code"] == "provider_statement_refund_alias_review"


def test_future_snapshot_is_not_an_available_source_at_an_earlier_knowledge_time():
    state, _ = tabby_state()
    accept(state, accepted_event(state))
    assert project(state, as_of="2026-11-01T11:59:59+03:00")["order-one"]["reason_code"] == "provider_statement_fee_source_future"


@pytest.mark.parametrize("reference", [None, "native-page-payment"])
def test_page_only_proof_exposes_only_its_explicit_transaction_reference(reference):
    state, order = tabby_state()
    order["payment"]["captures"] = []
    state["engine"]["facts"] = {"provider_order_balance:one": {
        "order_id": "one", "provider": "tabby", "currency": "SAR", "paid": "100.00"}}
    if reference is not None:
        state["engine"]["facts"]["provider_order_balance:one"]["transaction_reference"] = reference
    event = accepted_event(state, cover="provider_order_balance:one")
    event.update(event_date=None, date_precision="snapshot_interval")
    accept(state, event)
    row = project(state)["order-one"]
    assert row["available"] is True
    assert row["capture_references"] == ([reference] if reference else [])
    assert row["refund_references"] == []


def test_statement_generated_refund_key_is_not_a_native_refund_reference():
    state, _ = paid_state("tabby", refunded="40.00")
    synthetic_key = "refund:tabby:statement:" + "f" * 64
    state["engine"]["facts"][synthetic_key] = state["engine"]["facts"].pop("refund:tabby:refund")
    refund = accepted_event(state, key="refund", kind="refund", cover=synthetic_key, principal="40.00",
        fee="-2.00", vat="-.30", total="-2.30")
    accept(state, accepted_event(state), refund)
    row = project(state)["order-one"]
    assert row["available"] is True and row["refunded_amount"] == "40.00"
    assert row["refund_references"] == []


def test_a_partial_known_refund_reference_set_is_not_presented_as_complete():
    state, _ = paid_state("tabby", refunded="40.00")
    facts = state["engine"]["facts"]
    facts.pop("refund:tabby:refund")
    native_key, generated_key = "refund:tabby:native-refund", "refund:tabby:statement:" + "f" * 64
    facts[native_key] = {"order_id": "one", "amount": "20.00"}
    facts[generated_key] = {"order_id": "one", "amount": "20.00"}
    native = accepted_event(state, key="native", kind="refund", cover=native_key, principal="20.00",
        fee="-1.00", vat="-.15", total="-1.15")
    generated = accepted_event(state, key="generated", kind="refund", cover=generated_key, principal="20.00",
        fee="-1.00", vat="-.15", total="-1.15")
    accept(state, accepted_event(state), native, generated)
    row = project(state)["order-one"]
    assert row["available"] is True and row["fee_total"] == "5.75"
    assert row["refund_references"] == []


def test_same_principal_on_a_replaced_capture_keeps_distinct_native_references():
    state, order = tabby_state()
    accept(state, accepted_event(state))
    first = project(state)["order-one"]
    state["engine"]["facts"]["capture:replacement"] = state["engine"]["facts"].pop("capture:receipt")
    order["payment"]["captures"][0]["id"] = "replacement"
    accept(state, accepted_event(state, cover="capture:replacement"))
    second = project(state)["order-one"]
    assert first["principal_amount"] == second["principal_amount"] == "100.00"
    assert first["fee_total"] == second["fee_total"] == "8.05"
    assert first["capture_references"] == ["receipt"]
    assert second["capture_references"] == ["replacement"]
