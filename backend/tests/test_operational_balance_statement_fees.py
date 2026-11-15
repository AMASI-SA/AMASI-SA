"""Accepted statement fees replace estimates without posting payment or bank money."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_store import digest
from operational_configured_provider_fees import estimate_fee_details, refresh_provider_fee_inputs
from operational_financial_position import build_financial_position
from operational_provider_balance_sar import refresh_basis
from operational_provider_statement_fees import (
    freeze_statement_fee_events, prepare_statement_fee_context, statement_event_issue,
    statement_event_issues, statement_evidence_fingerprint,
    derive_statement_average_profile, store_statement_average_profile, statement_average_projection,
    select_statement_average_profile, statement_average_summary,
)
from test_operational_balance_configured_provider_fees import paid_state, platform, settings
from test_operational_balance_financial_position import NOW, table


def source_statement(*, provider="tabby", currency="SAR", account=None, rows=None, source="a", day="2026-11-01"):
    def sale(index, principal, variable, fixed, vat):
        net = Decimal(variable) + Decimal(fixed)
        total = net + Decimal(vat)
        return {"provider": provider, "native_currency": currency, "merchant_account": account,
            "event_type": "sale", "order_number": "synthetic-" + str(index),
            "principal_amount": principal, "fee_variable_net": variable, "fee_fixed_net": fixed,
            "fee_net": format(net, ".2f"), "fee_vat": vat, "fee_total": format(total, ".2f"),
            "transferred_amount": format(Decimal(principal) - total, ".2f"), "fee_vat_rate": "0.15",
            "fee_component_issues": [], "event_date": day, "row_fingerprint": digest([source, index]), "blockers": []}
    return {"schema_version": "operational_statement_v1", "provider": provider, "merchant_account": account,
        "file_hash": source * 64, "fingerprint": digest([source, provider, currency, account, day]),
        "statement_reference": "synthetic-week", "statement_date": day,
        "rows": rows if rows is not None else [sale(1, "100.00", "10.00", "1.00", "1.65"),
                                               sale(2, "300.00", "15.00", "2.00", "2.55")]}


def source_profile(state, **kwargs):
    return derive_statement_average_profile(state, source_statement(**kwargs),
        statement_id="synthetic-profile", accepted_at="2026-11-02T12:00:00+03:00")


def test_average_keeps_weighted_variable_fixed_mean_and_vat_separate():
    state, _ = paid_state("tabby")
    profile = source_profile(state)
    assert profile["rate_percent"] == "6.25000000"
    assert (profile["fee_variable_net_total"], profile["fee_fixed_net_total"], profile["principal_total"]) == ("25.00", "3.00", "400.00")
    assert profile["fixed_amount_per_sale"] == "1.50000000"
    components = statement_average_projection(profile, "150.00", operations=1)
    assert tuple(components[key] for key in ("variable", "fixed", "vat", "fee")) == (
        Decimal("9.38"), Decimal("1.50"), Decimal("1.63"), Decimal("12.51"))


@pytest.mark.parametrize("invalid_rate", [False, True])
def test_contradictory_source_vat_cannot_price_future_tax(invalid_rate):
    state, _ = paid_state("tabby")
    data = source_statement()
    for row in data["rows"]:
        row["fee_vat_rate"] = None if invalid_rate else "0.05"
        row["fee_component_issues"] = ["statement_tax_rate_invalid"] if invalid_rate else []
    profile = derive_statement_average_profile(state, data, statement_id="synthetic-profile",
        accepted_at="2026-11-02T12:00:00+03:00")
    assert profile["rate_percent"] == "6.25000000"
    assert profile["vat_rate_status"] == "source_conflict" and profile["vat_rate"] is None
    assert statement_average_projection(profile, "100.00", operations=1)["fee"] is None


@pytest.mark.parametrize("second_tax", [{"vat_rate": "5"}, {"vat_treatment": "inclusive"}])
def test_mixed_verified_policy_tax_is_not_hidden_by_source_vat(second_tax):
    state, _ = tabby_state()
    inputs = state["engine"]["provider_fee_inputs"]
    inputs["policies"].append({**inputs["policies"][0], "id": "other-plan", "plan_id": "other-plan", **second_tax})
    order = inputs["orders"]["one"]
    result = estimate_fee_details({}, "tabby", "100.00", policies=inputs["policies"], payment=order["payment"],
        created_at=order["created_at"], observed_at=order["updated_at"], as_of=state["engine"]["as_of"],
        source_mode="mz2_policies", native_currency="SAR", native_gross="100.00", paid_proven=True,
        started_at=state["started_at"], average_profile=source_profile(state), operations=1)
    assert result["basis"] == "average_estimate" and result["components"]["variable"] == Decimal("6.25")
    assert result["fee"] is None and result["components"]["vat_basis"] == "conflicting_policy"


def test_uniquely_configured_fixed_term_beats_source_fixed_mean():
    state, order = paid_state("tabby")
    partial_terms = settings("تابي", None, "1", "15")
    result = estimate_fee_details(partial_terms, "tabby", "100.00", payment=order["payment"],
        created_at=order["created_at"], observed_at=order["updated_at"], as_of=state["engine"]["as_of"],
        native_currency="SAR", native_gross="100.00", paid_proven=True, started_at=state["started_at"],
        average_profile=source_profile(state), operations=1)
    assert result["fee"] == Decimal("8.34")
    assert result["components"]["fixed"] == Decimal("1.00")
    assert result["components"]["fixed_basis"] == "configured"


@pytest.mark.parametrize("extra_tax,status", [("0.02", "explicit_uniform"), ("0.03", "source_conflict")])
def test_source_vat_tolerance_is_bounded_to_component_rounding(extra_tax, status):
    state, _ = paid_state("tabby")
    data = source_statement()
    row = data["rows"][0]
    row["fee_vat"] = str(Decimal(row["fee_vat"]) + Decimal(extra_tax))
    row["fee_total"] = str(Decimal(row["fee_total"]) + Decimal(extra_tax))
    row["transferred_amount"] = str(Decimal(row["transferred_amount"]) - Decimal(extra_tax))
    profile = derive_statement_average_profile(state, data, statement_id="rounding",
        accepted_at="2026-11-02T12:00:00+03:00")
    assert profile["vat_rate_status"] == status


def test_only_complete_sale_sample_enters_average_and_other_events_do_not():
    state, _ = paid_state("tabby")
    data = source_statement()
    expected = source_profile(state)
    data["rows"] += [{"event_type": kind, "principal_amount": "999.00", "blockers": ["excluded"]}
                     for kind in ("refund", "settlement_fee", "salla_purchase", "unsupported")]
    actual = derive_statement_average_profile(state, data, statement_id="synthetic-profile",
        accepted_at="2026-11-02T12:00:00+03:00")
    assert actual == expected
    data["rows"][1]["blockers"] = ["statement_transfer_mismatch"]
    assert derive_statement_average_profile(state, data, statement_id="invalid",
        accepted_at="2026-11-02T12:00:00+03:00") is None


def test_profiles_are_owner_provider_native_currency_and_account_bound():
    state, _ = paid_state("tabby")
    first = source_profile(state, account="synthetic-account-a")
    second = source_profile(state, account="synthetic-account-b", source="b")
    foreign = source_profile(state, currency="KWD", source="c")
    other_provider = source_profile(state, provider="tamara", source="d")
    for profile in (first, second, foreign, other_provider):
        assert store_statement_average_profile(state, profile)
    freeze_statement_fee_events(state)
    assert select_statement_average_profile(state, "tabby", "SAR") is None
    assert select_statement_average_profile(state, "tabby", "SAR", payment={"merchant_account": "synthetic-account-a"}) == first
    assert select_statement_average_profile(state, "tabby", "KWD") == foreign
    assert select_statement_average_profile(state, "tamara", "SAR") == other_provider
    assert select_statement_average_profile(state, "salla", "SAR") is None
    wrong_owner = deepcopy(first)
    wrong_owner["owner_id"] = "someone-else"
    with pytest.raises(ValueError, match="provider_statement_average_invalid"):
        store_statement_average_profile(state, wrong_owner)


def test_profile_same_source_reupload_is_noop_and_exact_source_date_tie_is_held():
    state, _ = paid_state("tabby")
    profile = source_profile(state)
    assert store_statement_average_profile(state, profile)
    repeated = derive_statement_average_profile(state, source_statement(), statement_id="reexport",
        accepted_at="2026-11-03T12:00:00+03:00")
    assert not store_statement_average_profile(state, repeated)
    assert state["provider_statement_reconciliation"]["average_profiles"][profile["scope_key"]] == profile
    conflicting = source_profile(state, source="b")
    assert store_statement_average_profile(state, conflicting)
    freeze_statement_fee_events(state)
    assert select_statement_average_profile(state, "tabby", "SAR") is None
    ambiguous = state["engine"]["provider_fee_inputs"]["average_profiles"][0]
    assert statement_average_summary(ambiguous) == {"available": False, "reason_code": "source_profile_ambiguous"}


def test_profile_scope_budget_is_bounded_without_dropping_an_existing_account():
    state, _ = paid_state("tabby")
    for index in range(32):
        store_statement_average_profile(state, source_profile(state, account="account-" + str(index)))
    before = deepcopy(state["provider_statement_reconciliation"])
    with pytest.raises(ValueError, match="provider_statement_average_scope_limit"):
        store_statement_average_profile(state, source_profile(state, account="account-over-limit"))
    assert state["provider_statement_reconciliation"] == before


def test_missing_vat_keeps_variable_and_fixed_visible_without_inventing_total():
    state, order = paid_state("tamara")
    state["engine"]["provider_fee_inputs"]["settings"] = {}
    data = source_statement(provider="tamara")
    for sale in data["rows"]:
        sale["fee_vat_rate"] = None
    profile = derive_statement_average_profile(state, data, statement_id="tamara-profile",
        accepted_at="2026-11-02T12:00:00+03:00")
    store_statement_average_profile(state, profile)
    freeze_statement_fee_events(state)
    row = platform(state, "tamara")
    assert (row["average_variable_fees"], row["average_fixed_fees"], row["average_vat_fees"]) == ("6.25", "1.50", None)
    assert row["known_fees"] is None and row["estimated_fees"] is None and row["known_net"] is None
    assert row["average_estimated_orders"] == 1 and row["fee_status"] == "review"
    assert statement_average_summary(profile)["full_estimate_requires_vat"] is True
    refresh_provider_fee_inputs(state, {"orders": [order], "provider_fee_settings": settings("تمارا", None, None, "15")})
    row = platform(state, "tamara")
    assert row["known_fees"] == "8.91" and row["fee_status"] == "estimated"


def test_missing_source_variable_fixed_split_never_becomes_an_inclusive_average():
    state, _ = paid_state("salla")
    data = source_statement(provider="salla")
    for sale in data["rows"]:
        sale.update(fee_variable_net=None, fee_fixed_net=None, fee_vat_rate=None)
    assert derive_statement_average_profile(state, data, statement_id="salla-profile",
        accepted_at="2026-11-02T12:00:00+03:00") is None


def tabby_state(*, plan=None, currency="SAR"):
    state, order = paid_state("tabby", currency=currency, terms=settings("تابي", "10", "0", "0"))
    order["payment"]["plan_id"] = plan
    policy = {"id": "synthetic-plan", "provider": "tabby", "currency": currency,
              "status": "active", "effective_from": "2026-11-01", "plan_id": "synthetic-plan",
              "percentage": "7", "fixed_amount": "1", "vat_treatment": "exclusive", "vat_rate": "15"}
    refresh_provider_fee_inputs(state, {"provider_fee_settings": settings("تابي", "10", "0", "0"),
        "orders": [order], "fee_policies": [policy], "provider_fee_source_mode": "mz2_policies"})
    store_statement_average_profile(state, source_profile(state))
    freeze_statement_fee_events(state)
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


def test_missing_tabby_plan_has_source_components_but_remains_explicitly_estimated():
    state, _ = tabby_state()
    before = deepcopy(state)
    row = platform(state, "tabby")
    assert row["known_fees"] == "8.34"
    assert row["average_estimated_fees"] == "8.34" and row["average_estimated_orders"] == 1
    assert (row["average_variable_fees"], row["average_fixed_fees"], row["average_vat_fees"]) == ("6.25", "1.00", "1.09")
    assert row["average_fixed_bases"] == ["configured"]
    assert row["average_operations_bases"] == ["capture_facts"]
    assert row["fee_basis"] == "average_estimate" and row["fee_status"] == "estimated"
    assert row["actual_statement_fees"] == "0.00" and row["net_confirmed_orders"] == 0
    assert row["net"] is None and not row["complete"]
    assert state == before


def test_absent_source_profile_never_invents_an_average():
    state, _ = tabby_state()
    state["engine"]["provider_fee_inputs"].pop("average_profiles", None)
    row = platform(state, "tabby")
    assert row["known_fees"] is None and row["average_estimated_orders"] == 0


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
    assert inputs["average_profiles"][0]["rate_percent"] == "6.25000000"


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
    state["engine"]["provider_fee_inputs"]["policies"][0]["fixed_amount"] = "0"
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


def test_new_native_refund_alias_holds_only_its_order_without_changing_saved_principal():
    state, _ = tabby_state(plan="synthetic-plan")
    attach(state, accepted_event(state))
    source = deepcopy(state["engine"]["provider_fee_inputs"]["orders"]["one"])
    source["payment"]["captures"][0]["id"] = "second-receipt"
    state["engine"]["provider_fee_inputs"]["orders"]["two"] = source
    state["engine"]["facts"]["capture:second-receipt"] = {"order_id": "two", "provider": "tabby", "amount": "100.00"}
    state["engine"]["obligations"]["provider:two"] = {**state["engine"]["obligations"]["provider:one"], "id": "provider:two"}
    state["engine"]["issues"] = [{"source_id": "provider:one", "order_id": "one", "provider": "tabby",
                                  "code": "provider_statement_refund_alias_review"}]
    before = deepcopy(state)
    row = platform(state, "tabby")
    assert row["known_gross"] == "200.00" and row["paid_orders"] == 2
    assert row["held_orders"] == 1 and row["known_net"] == "90.80" and row["known_fees"] == "9.20"
    assert row["net_confirmed_orders"] == 1 and row["pending_order_ids"] == ["one"]
    assert row["fee_status"] == "review" and state == before
    assert state["engine"]["obligations"]["provider:one"]["confirmed"] == "100.00"


def split_captures(state):
    facts = state["engine"]["facts"]
    facts.pop("capture:receipt")
    captures = []
    for identity, amount in (("first", "60.00"), ("second", "40.00")):
        facts["capture:" + identity] = {"order_id": "one", "provider": "tabby", "amount": amount}
        captures.append({"id": identity, "amount": amount, "captured_at": NOW})
    state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]["captures"] = captures


def test_individual_capture_facts_count_fixed_cost_once_per_capture():
    state, _ = tabby_state()
    split_captures(state)
    row = platform(state, "tabby")
    assert (row["average_variable_fees"], row["average_fixed_fees"], row["average_vat_fees"]) == ("6.25", "2.00", "1.24")
    assert row["known_fees"] == "9.49" and row["net_confirmed_orders"] == 0


def test_unique_paid_page_cost_uses_labelled_one_order_estimate_until_actual_replaces_it():
    state, _ = tabby_state()
    facts = state["engine"]["facts"]
    facts.pop("capture:receipt")
    facts["provider_order_balance:one"] = {"order_id": "one", "provider": "tabby", "currency": "SAR",
        "gross": "100.00", "paid": "100.00", "remaining": "0.00"}
    payment = state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]
    payment["captures"] = []
    payment["order_page_balance"] = {"paid": "100.00"}
    before = deepcopy(state)
    row = platform(state, "tabby")
    assert row["known_fees"] == "8.34" and row["net_confirmed_orders"] == 0
    assert row["average_operations_bases"] == ["one_provider_order_estimate"]
    assert state == before
    displayed, = table(build_financial_position(state), "providers")["rows"]
    assert "افتراض رسم ثابت واحد لكل طلب" in displayed["fee_description"]
    event = accepted_event(state, cover="provider_order_balance:one")
    event.update(event_date=None, date_precision="snapshot_interval")
    attach(state, event)
    row = platform(state, "tabby")
    assert row["known_fees"] == "8.05" and row["average_estimated_orders"] == 0
    assert row["average_operations_bases"] == []


def test_page_estimated_operation_count_cannot_override_known_partial_capture_detail():
    state, _ = tabby_state()
    facts = state["engine"]["facts"]
    facts["capture:receipt"]["amount"] = "40.00"
    facts["provider_order_balance:one"] = {"order_id": "one", "provider": "tabby", "currency": "SAR", "paid": "100.00"}
    payment = state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]
    payment["captures"][0]["amount"] = "40.00"
    payment["order_page_balance"] = {"paid": "100.00"}
    row = platform(state, "tabby")
    assert row["known_fees"] is None and row["average_variable_fees"] == "6.25"
    assert row["average_fixed_fees"] is None and row["average_operations_bases"] == ["unavailable"]


def test_partial_settlement_cannot_reuse_an_average_fixed_cost_without_event_coverage():
    state, _ = tabby_state()
    state["movements"].append({"id": "partial", "kind": "settlement", "party_type": "provider", "party_id": "tabby",
        "currency": "SAR", "amount": "47.00", "actual_fee_amount": "3.00", "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": "50.00"}]})
    row = platform(state, "tabby")
    assert row["known_fees"] == "3.00" and row["actual_settlement_fees"] == "3.00"
    assert row["fees"] is None and row["average_estimated_orders"] == 0
    assert "provider_statement_fee_settlement_conflict" in {reason["code"] for reason in row["pending_reasons"]}


def test_disjoint_partial_captures_keep_only_uncovered_average_then_replace_it():
    state, _ = tabby_state()
    split_captures(state)
    first = accepted_event(state, cover="capture:first", principal="60.00", fee="4.00", vat=".60", total="4.60")
    attach(state, first)
    row = platform(state, "tabby")
    assert (row["known_fees"], row["actual_statement_fees"], row["average_estimated_fees"]) == ("8.63", "4.60", "4.03")
    assert (row["average_variable_fees"], row["average_fixed_fees"], row["average_vat_fees"]) == ("2.50", "1.00", "0.53")
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
    state["provider_statement_reconciliation"]["events"] = {event["event_key"]: event}
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
    assert row["fee_basis_label"] == "متوسط العمولة المتغيرة من الكشف قبل الضريبة"
    assert "6.25% قبل الضريبة" in row["fee_description"]
    assert "الثابتة التقديرية: 1.00" in row["fee_description"] and "ضريبتها التقديرية: 1.09" in row["fee_description"]
    attach(state, accepted_event(state))
    displayed = table(build_financial_position(state), "providers")
    row, = displayed["rows"]
    assert row["fee_status_label"] == "فعلية مثبتة"
    assert row["actual_fees"] == "8.05" and row["estimated_fees"] == "0.00"
    assert row["statement_evidence_as_of"] == "2026-11-02T12:00:00+03:00"
    assert "statement_evidence_date_label" in {column["key"] for column in displayed["columns"]}
