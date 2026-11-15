"""Accepted statement fees replace estimates without posting payment or bank money."""
from copy import deepcopy

import pytest

from operational_balance_store import digest
from operational_configured_provider_fees import refresh_provider_fee_inputs
from operational_financial_position import build_financial_position
from operational_provider_balance_sar import refresh_basis
from operational_provider_statement_fees import (
    freeze_statement_fee_events, prepare_statement_fee_context, statement_event_issue,
    statement_event_issues, statement_evidence_fingerprint,
)
from test_operational_balance_configured_provider_fees import paid_state, platform, settings
from test_operational_balance_financial_position import NOW, table


def tabby_state(*, plan=None, currency="SAR"):
    state, order = paid_state("tabby", currency=currency, terms=settings("تابي", "10", "0", "0"))
    order["payment"]["plan_id"] = plan
    policy = {"id": "synthetic-plan", "provider": "tabby", "currency": currency,
              "status": "active", "effective_from": "2026-11-01", "plan_id": "synthetic-plan",
              "percentage": "7", "fixed_amount": "1", "vat_treatment": "exclusive", "vat_rate": "15"}
    refresh_provider_fee_inputs(state, {"provider_fee_settings": settings("تابي", "10", "0", "0"),
        "orders": [order], "fee_policies": [policy], "provider_fee_source_mode": "mz2_policies"})
    return state, order


def accepted_event(state, *, key="sale", provider="tabby", kind="capture", cover="capture:receipt",
                   principal="100.00", fee="7.00", vat="1.05", total="8.05", currency="SAR"):
    fact = state["engine"]["facts"][cover]
    return {"event_key": key, "provider": provider, "order_id": "one", "native_currency": currency,
            "kind": kind, "principal_amount": principal, "fee_net": fee, "fee_vat": vat, "fee_total": total,
            "event_date": "2026-11-01", "date_precision": "day",
            "covered_event": {"kind": "fact", "id": cover, "fingerprint": digest(fact),
                              "amount": principal, "financial_effect": "fee_only"},
            "source": {"file_hash": "a" * 64, "row_fingerprint": "b" * 64, "statement_ref": "synthetic-week"},
            "confirmation": "exact_match", "accepted_at": "2026-11-02T12:00:00+03:00"}


def attach(state, *events):
    state["engine"]["provider_fee_inputs"]["statement_events"] = list(events)
    state["engine"]["provider_fee_inputs"]["statement_evidence_as_of"] = "2026-11-02T12:00:00+03:00"


def test_missing_tabby_plan_has_inclusive_average_but_remains_explicitly_estimated():
    state, _ = tabby_state()
    before = deepcopy(state)
    row = platform(state, "tabby")
    assert row["known_fees"] == "9.10"
    assert row["average_estimated_fees"] == "9.10" and row["average_estimated_orders"] == 1
    assert row["fee_basis"] == "average_estimate" and row["fee_status"] == "estimated"
    assert row["actual_statement_fees"] == "0.00" and row["net_confirmed_orders"] == 0
    assert row["net"] is None and not row["complete"]
    assert state == before


def test_actual_statement_replaces_average_read_only_and_uses_separate_evidence_date():
    state, _ = tabby_state()
    attach(state, accepted_event(state))
    # A later acceptance for another provider must not relabel this event.
    state["engine"]["provider_fee_inputs"]["statement_evidence_as_of"] = "2026-11-04T12:00:00+03:00"
    before = deepcopy(state)
    row = platform(state, "tabby")
    assert (row["known_fees"], row["known_net"]) == ("8.05", "91.95")
    assert row["fee_basis"] == "actual_statement" and row["fee_status"] == "confirmed"
    assert row["actual_statement_fees"] == "8.05" and row["estimated_fees"] == "0.00"
    assert row["statement_evidence_as_of"] == "2026-11-02T12:00:00+03:00"
    assert state["engine"]["as_of"] == NOW and state == before
    assert platform(state, "tabby") == row


def test_source_refresh_preserves_accepted_statement_events_and_evidence_date():
    state, order = tabby_state()
    event = accepted_event(state)
    attach(state, event)
    refresh_provider_fee_inputs(state, {"provider_fee_settings": {}, "orders": [order]})
    inputs = state["engine"]["provider_fee_inputs"]
    assert inputs["statement_events"] == [event]
    assert inputs["statement_evidence_as_of"] == "2026-11-02T12:00:00+03:00"
    assert inputs["statement_events"][0] is not event


def test_known_plan_wins_over_average_and_actual_replaces_the_known_plan():
    state, _ = tabby_state(plan="synthetic-plan")
    row = platform(state, "tabby")
    assert row["known_fees"] == "9.20" and row["average_estimated_orders"] == 0
    assert row["fee_basis"] == "plan_estimate" and row["fee_status"] == "estimated"
    attach(state, accepted_event(state))
    assert platform(state, "tabby")["known_fees"] == "8.05"


@pytest.mark.parametrize("change", ["plan_conflict", "unknown_plan", "missing_date", "date_gap", "tax_invalid", "prestart", "refund", "payment_conflict"])
def test_average_cannot_repair_other_missing_or_conflicting_evidence(change):
    state, _ = tabby_state()
    inputs = state["engine"]["provider_fee_inputs"]
    source, policy = inputs["orders"]["one"], inputs["policies"][0]
    if change == "plan_conflict":
        source["payment"]["fee_plan_issue"] = "conflict"
    elif change == "unknown_plan":
        source["payment"]["plan_id"] = "not-a-configured-plan"
    elif change == "missing_date":
        source["payment"]["captures"][0].pop("captured_at")
    elif change == "date_gap":
        policy["effective_from"] = "2026-11-02"
    elif change == "tax_invalid":
        policy["vat_treatment"] = "unknown"
    elif change == "prestart":
        source["created_at"] = state["started_at"]
    elif change == "refund":
        state["engine"]["facts"]["refund:tabby:r"] = {"order_id": "one", "amount": "10.00"}
        state["engine"]["obligations"]["provider:one"]["confirmed"] = "90.00"
    else:
        state["engine"]["issues"] = [{"source_id": "provider:one", "code": "provider_payment_amount_conflict"}]
    row = platform(state, "tabby")
    assert row["known_fees"] is None and row["average_estimated_orders"] == 0


def test_missing_plan_average_does_not_hide_a_changed_tariff_inside_undated_payment_window():
    state, _ = tabby_state()
    inputs = state["engine"]["provider_fee_inputs"]
    source = inputs["orders"]["one"]
    source["payment"]["captures"] = []
    source["payment"]["order_page_balance"] = {"paid": "100.00"}
    source["updated_at"] = state["engine"]["as_of"] = "2026-11-03T12:00:00+03:00"
    inputs["policies"][0]["effective_to"] = "2026-11-01"
    inputs["policies"].append({**inputs["policies"][0], "id": "changed", "percentage": "8",
                              "effective_from": "2026-11-02", "effective_to": None})
    row = platform(state, "tabby")
    assert row["known_fees"] is None and row["average_estimated_orders"] == 0


def test_an_average_rounding_to_zero_is_still_estimated_not_confirmed():
    state, _ = tabby_state()
    state["engine"]["facts"]["capture:receipt"]["amount"] = "0.01"
    state["engine"]["obligations"]["provider:one"]["confirmed"] = "0.01"
    payment = state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]
    payment["gross"] = payment["captures"][0]["amount"] = "0.01"
    row = platform(state, "tabby")
    assert row["known_fees"] == "0.00" and row["fee_status"] == "estimated"
    assert row["average_estimated_orders"] == 1 and row["net_confirmed_orders"] == 0


@pytest.mark.parametrize("provider", ["tamara", "salla"])
def test_no_average_is_invented_for_other_providers(provider):
    state, _ = paid_state(provider)
    state["engine"]["provider_fee_inputs"]["settings"] = {}
    row = platform(state, provider)
    assert row["known_fees"] is None and row["average_estimated_orders"] == 0


@pytest.mark.parametrize("provider", ["tamara", "salla"])
def test_other_providers_use_exact_actual_fees_without_an_average(provider):
    state, _ = paid_state(provider)
    state["engine"]["provider_fee_inputs"]["settings"] = {}
    attach(state, accepted_event(state, provider=provider))
    row = platform(state, provider)
    assert row["known_fees"] == "8.05" and row["fee_status"] == "confirmed"
    assert row["average_estimated_orders"] == 0 and row["estimated_fees"] == "0.00"


def test_foreign_payment_converted_to_sar_does_not_acquire_sar_average():
    state, order = tabby_state(currency="KWD")
    order["customer_payment_sar"] = {"currency": "KWD", "native": {"tabby": "100.00"},
        "shares": {"tabby": "1200.00"}, "paid": "1200.00", "basis": "salla_list_paid"}
    state["engine"]["provider_fee_inputs"]["orders"]["one"]["customer_payment_sar"] = order["customer_payment_sar"]
    refresh_basis(state, [order], state["engine"]["issues"])
    row = platform(state, "tabby", provider_balances_sar=True)
    assert row["known_gross"] == "1200.00" and row["known_fees"] is None
    assert row["average_estimated_orders"] == 0
    event = accepted_event(state, currency="SAR")
    attach(state, event)
    assert platform(state, "tabby", provider_balances_sar=True)["known_fees"] is None
    assert statement_event_issue(state, event) == "provider_statement_fee_currency_conflict"


def test_reexport_same_economic_cover_replaces_once_and_conflicting_duplicate_is_reviewed():
    state, _ = tabby_state()
    first = accepted_event(state)
    second = deepcopy(first)
    second.update(event_key="other-export", accepted_at="2026-11-03T12:00:00+03:00")
    second["source"] = {"file_hash": "c" * 64, "row_fingerprint": "d" * 64, "statement_ref": "second-week"}
    attach(state, first, second)
    assert platform(state, "tabby")["known_fees"] == "8.05"
    second.update(fee_net="8.00", fee_vat="1.20", fee_total="9.20")
    attach(state, first, second)
    row = platform(state, "tabby")
    assert row["known_fees"] is None and row["fee_status"] == "review"
    assert row["average_estimated_orders"] == 0


def test_changed_covered_fact_invalidates_actual_and_does_not_hide_behind_average():
    state, _ = tabby_state()
    event = accepted_event(state)
    attach(state, event)
    state["engine"]["facts"]["capture:receipt"]["evidence_revision"] = "changed"
    row = platform(state, "tabby")
    assert row["known_fees"] is None and row["fee_status"] == "review"
    assert statement_event_issues(state, [event]) == ["provider_statement_fee_evidence_changed"]


def split_captures(state):
    facts = state["engine"]["facts"]
    facts.pop("capture:receipt")
    captures = []
    for identity, amount in (("first", "60.00"), ("second", "40.00")):
        facts["capture:" + identity] = {"order_id": "one", "provider": "tabby", "amount": amount}
        captures.append({"id": identity, "amount": amount, "captured_at": NOW})
    state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]["captures"] = captures


def test_disjoint_partial_captures_keep_only_uncovered_average_then_replace_it():
    state, _ = tabby_state()
    split_captures(state)
    first = accepted_event(state, cover="capture:first", principal="60.00", fee="4.00", vat=".60", total="4.60")
    attach(state, first)
    row = platform(state, "tabby")
    assert (row["known_fees"], row["actual_statement_fees"], row["average_estimated_fees"]) == ("8.24", "4.60", "3.64")
    assert row["fee_status"] == "mixed" and row["average_estimated_orders"] == 1
    second = accepted_event(state, key="second", cover="capture:second", principal="40.00", fee="3.00", vat=".45", total="3.45")
    attach(state, first, second)
    row = platform(state, "tabby")
    assert row["known_fees"] == "8.05" and row["estimated_fees"] == "0.00"
    assert row["fee_status"] == "confirmed" and row["average_estimated_orders"] == 0


def test_partial_actual_does_not_apportion_a_per_order_fixed_tariff():
    state, _ = tabby_state(plan="synthetic-plan")
    split_captures(state)
    attach(state, accepted_event(state, cover="capture:first", principal="60.00", fee="4.00", vat=".60", total="4.60"))
    row = platform(state, "tabby")
    assert row["fees"] is None and row["known_fees"] == "4.60" and row["known_net"] is None
    assert row["fee_status"] == "review" and row["estimated_fees"] == "0.00"


def test_signed_refund_fee_reduces_capture_fee_without_posting_refund_again():
    state, _ = paid_state("tabby", refunded="40.00", terms=settings("تابي", "10", "0", "0"))
    sale = accepted_event(state)
    refund = accepted_event(state, key="refund", kind="refund", cover="refund:tabby:refund", principal="40.00",
                            fee="-2.00", vat="-.30", total="-2.30")
    attach(state, sale, refund)
    before = deepcopy(state)
    row = platform(state, "tabby")
    assert (row["fees"], row["refunded"], row["known_net"]) == ("5.75", "40.00", "54.25")
    assert row["actual_statement_fees"] == "5.75" and row["fee_status"] == "confirmed"
    assert state == before
    attach(state, sale)
    row = platform(state, "tabby")
    assert row["fees"] is None and row["known_fees"] == "8.05" and row["known_net"] is None


@pytest.mark.parametrize("field,value", [("fee_total", "9.00"), ("fee_net", "NaN"), ("principal_amount", "90.00"), ("native_currency", "USD")])
def test_invalid_accepted_amount_or_currency_stays_reviewable(field, value):
    state, _ = tabby_state()
    event = {**accepted_event(state), field: value}
    attach(state, event)
    row = platform(state, "tabby")
    assert row["known_fees"] is None and row["fee_status"] == "review"
    assert statement_event_issue(state, event, context=prepare_statement_fee_context(state)) is not None


@pytest.mark.parametrize("principal,fee,cash,expected,complete", [
    ("100.00", "8.05", "91.95", "8.05", True),
    ("100.00", "6.00", "94.00", "6.00", False),
    ("50.00", "3.00", "47.00", "3.00", False),
])
def test_existing_settlement_fee_is_not_added_again_or_blindly_subtracted(principal, fee, cash, expected, complete):
    state, _ = tabby_state()
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "tabby",
        "currency": "SAR", "amount": cash, "actual_fee_amount": fee, "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": principal}]})
    attach(state, accepted_event(state))
    before = deepcopy(state)
    row = platform(state, "tabby")
    assert row["known_fees"] == expected and row["actual_statement_fees"] == "0.00"
    assert row["fees_complete"] is complete
    assert row["fees"] == (expected if complete else None) and state == before
    assert (statement_event_issue(state, accepted_event(state)) is None) is complete


def test_freeze_copies_only_fee_evidence_and_preserves_source_snapshot():
    state, _ = tabby_state()
    event = accepted_event(state)
    state["provider_statement_reconciliation"] = {"events": {event["event_key"]: event}}
    before = deepcopy(state)
    freeze_statement_fee_events(state)
    expected = deepcopy(before)
    attach(expected, deepcopy(event))
    assert state == expected
    assert statement_evidence_fingerprint(event) == digest(event)
    state["provider_statement_reconciliation"]["events"]["sale"]["fee_total"] = "invalid"
    assert state["engine"]["provider_fee_inputs"]["statement_events"][0]["fee_total"] == "8.05"


def test_undated_page_evidence_is_explicit_interval_only_for_full_unique_paid_snapshot():
    state, _ = tabby_state()
    facts = state["engine"]["facts"]
    facts.pop("capture:receipt")
    facts["provider_order_balance:one"] = {"order_id": "one", "provider": "tabby", "currency": "SAR", "paid": "100.00"}
    event = accepted_event(state, cover="provider_order_balance:one")
    event.update(event_date=None, date_precision="snapshot_interval")
    attach(state, event)
    assert statement_event_issue(state, event) is None
    assert platform(state, "tabby")["known_fees"] == "8.05"
    facts["capture:receipt"] = {"order_id": "one", "provider": "tabby", "amount": "100.00"}
    assert statement_event_issue(state, event) is not None


def test_financial_table_displays_fee_basis_and_separate_evidence_date():
    state, _ = tabby_state()
    row, = table(build_financial_position(state), "providers")["rows"]
    assert row["fee_status_label"] == "تقديرية"
    assert row["fee_basis_label"] == "متوسط تابي 9.1% شامل الضريبة"
    attach(state, accepted_event(state))
    displayed = table(build_financial_position(state), "providers")
    row, = displayed["rows"]
    assert row["fee_status_label"] == "فعلية مثبتة"
    assert row["actual_fees"] == "8.05" and row["estimated_fees"] == "0.00"
    assert row["statement_evidence_as_of"] == "2026-11-02T12:00:00+03:00"
    assert "statement_evidence_date_label" in {column["key"] for column in displayed["columns"]}
