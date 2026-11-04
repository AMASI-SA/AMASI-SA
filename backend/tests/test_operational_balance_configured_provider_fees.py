"""Configured commissions belong once in the platform's remaining net balance."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_service import report
from operational_configured_provider_fees import estimate_fee, refresh_provider_fee_inputs
from operational_financial_position import build_financial_position
from operational_provider_balance_sar import refresh_basis
from test_operational_balance_financial_position import NOW, obligation, opening, state_for, table


def settings(name="تمارا", rate="6.99", fixed="1.50", vat="15"):
    return {"payment_methods": [{"name": name, "commission_percent": rate, "fixed_fee": fixed, "vat_percent": vat}]}


def paid_state(provider="tamara", gross="100.00", refunded="0.00", currency="SAR", method=None, terms=None):
    state = state_for()
    obligation(state, "provider:one", "provider", "provider", str(Decimal(gross) - Decimal(refunded)),
               party=provider, direction="receivable", currency=currency)
    state["engine"]["facts"]["capture:receipt"] = {"order_id": "one", "provider": provider, "amount": gross}
    if Decimal(refunded):
        state["engine"]["facts"]["refund:" + provider + ":refund"] = {"order_id": "one", "amount": refunded}
    order = {"id": "one", "currency": currency, "created_at": NOW, "updated_at": NOW,
             "payment": {"provider": provider, "gross": gross, "currency": currency, "payment_method": method,
                         "captures": [{"id": "receipt", "amount": gross, "captured_at": NOW}]}}
    refresh_provider_fee_inputs(state, {"provider_fee_settings": terms or settings(), "orders": [order]})
    return state, order


def platform(state, provider="tamara", **kwargs):
    return next(row for row in report(state, **kwargs)["details"]["provider_balances"]["SAR"] if row["provider"] == provider)


def test_saved_rates_and_provider_rounding_are_used_without_defaulting_missing_fields():
    assert estimate_fee(settings(rate="7", fixed="2", vat="10"), "tamara", "100") == Decimal("9.90")
    assert estimate_fee(settings("تابي", "6.99", "1", "15"), "tabby", "414.88") == Decimal("34.49")
    assert estimate_fee(settings("مدى", "1", "1", "15"), "salla", "100", method="mada") == Decimal("2.30")
    assert estimate_fee(settings(rate="0", fixed="0", vat="0"), "tamara", "100") == 0
    for field in ("commission_percent", "fixed_fee", "vat_percent"):
        source = settings()
        source["payment_methods"][0].pop(field)
        assert estimate_fee(source, "tamara", "100") is None
    duplicated = settings()
    duplicated["payment_methods"] *= 2
    assert estimate_fee(duplicated, "tamara", "100") is None


def test_one_table_subtracts_fee_once_preserves_opening_and_creates_no_cash():
    state, _ = paid_state("salla", method="mada", terms=settings("مدى", "1", "1", "15"))
    opening(state, "opening", "provider", "120.00", party="salla")
    before = deepcopy(state)
    result = build_financial_position(state)
    row, = table(result, "providers")["rows"]
    assert (row["orders"], row["gross"], row["fees"], row["refunded"], row["net"]) == (1, "100.00", "2.30", "0.00", "217.70")
    assert result["currencies"]["SAR"]["debt_totals"]["receivable"] == "217.70"
    assert report(state)["summary"]["actual_liquidity"] == "0.00"
    assert not any(t["id"] in {"provider_fees_expected", "provider_order_balances", "provider_customer_paid"}
                   for t in result["currencies"]["SAR"]["tables"])
    assert not any(r["id"] == "provider_fees_expected" for r in result["currencies"]["SAR"]["forecast_rows"])
    assert state == before and result == build_financial_position(state)


def test_actual_settlement_fee_replaces_covered_estimate():
    state, _ = paid_state(terms=settings(rate="10", fixed="0", vat="0"))
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "tamara",
        "currency": "SAR", "amount": "47.00", "actual_fee_amount": "3.00", "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": "50.00"}]})
    row = platform(state)
    assert (row["fees"], row["settled"], row["net"]) == ("8.00", "47.00", "45.00")
    assert report(state, provider_balances_net=True)["summary"]["receivable"] == "45.00"
    assert report(state)["summary"]["receivable"] == "50.00"


@pytest.mark.parametrize("gross, cash_paid, fee, complete", [
    ("100.00", "90.00", "10.00", True), ("50.00", "45.00", "5.00", False)])
def test_complete_actual_settlement_needs_no_estimate_but_partial_settlement_does(gross, cash_paid, fee, complete):
    state, _ = paid_state()
    state["engine"]["provider_fee_inputs"]["settings"] = {}
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "tamara",
        "currency": "SAR", "amount": cash_paid, "actual_fee_amount": fee, "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": gross}]})
    row = platform(state)
    assert row["complete"] is complete
    assert row["fees"] == ("10.00" if complete else None)
    assert row["net"] == ("0.00" if complete else None)


@pytest.mark.parametrize("refunded, expected", [("40.00", "50.24"), ("100.00", "-9.76")])
def test_tamara_refund_is_counted_once_and_keeps_capture_fee(refunded, expected):
    state, _ = paid_state(refunded=refunded)
    row = platform(state)
    assert row["fees"] == "9.76" and row["refunded"] == refunded and row["net"] == expected
    assert row["gross"] == "100.00" and row["orders"] == 1


def test_foreign_payment_uses_verified_sar_gross_and_keeps_fixed_fee_in_sar():
    terms = settings("مدى", "1", "1", "0")
    state, order = paid_state("salla", "10.00", currency="KWD", method="mada", terms=terms)
    order["customer_payment_sar"] = {"currency": "KWD", "native": {"salla": "10.00"},
        "shares": {"salla": "100.00"}, "paid": "100.00", "basis": "salla_list_paid"}
    refresh_basis(state, [order], state["engine"]["issues"])
    refresh_provider_fee_inputs(state, {"provider_fee_settings": terms, "orders": [order]})
    row = platform(state, "salla", provider_balances_sar=True)
    assert (row["gross"], row["fees"], row["net"]) == ("100.00", "2.00", "98.00")
    assert state["engine"]["obligations"]["provider:one"]["currency"] == "KWD"


def test_foreign_partial_and_full_payouts_conserve_cumulative_cash_and_fee_rounding():
    state, order = paid_state("tamara", "12.00", currency="KWD", terms=settings(rate="10", fixed="0", vat="0"))
    order["customer_payment_sar"] = {"currency": "KWD", "native": {"tamara": "12.00"},
        "shares": {"tamara": "147.55"}, "paid": "147.55", "basis": "salla_list_paid"}
    refresh_basis(state, [order], state["engine"]["issues"])
    refresh_provider_fee_inputs(state, {"provider_fee_settings": settings(rate="10", fixed="0", vat="0"), "orders": [order]})
    first = {"id": "first", "kind": "settlement", "party_type": "provider", "party_id": "tamara", "direction": "incoming",
             "currency": "KWD", "amount": "0.83", "actual_fee_amount": "0.20",
             "allocations": [{"obligation_id": "provider:one", "amount": "1.03"}]}
    state["movements"].append(first)
    row = platform(state, provider_balances_sar=True)
    assert row["settled"] == "10.21"
    assert Decimal(row["gross"]) - Decimal(row["fees"]) - Decimal(row["settled"]) == Decimal(row["net"])
    state["movements"].append({**first, "id": "second", "amount": "10.00", "actual_fee_amount": "0.97",
                               "allocations": [{"obligation_id": "provider:one", "amount": "10.97"}]})
    row = platform(state, provider_balances_sar=True)
    assert (row["settled"], row["fees"], row["net"]) == ("133.16", "14.39", "0.00")


def test_fully_settled_refunded_capture_does_not_charge_another_estimated_fee():
    state, _ = paid_state(refunded="100.00", terms=settings(rate="10", fixed="0", vat="0"))
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "tamara",
        "currency": "SAR", "amount": "100.00", "actual_fee_amount": "0.00", "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": "100.00"}]})
    row = platform(state)
    assert (row["fees"], row["refunded"], row["net"]) == ("0.00", "100.00", "-100.00")
    credit = "credit:provider:one"
    state["movements"].append({"id": "repay", "kind": "settlement", "party_type": "provider", "party_id": "tamara",
        "currency": "SAR", "amount": "100.00", "direction": "outgoing",
        "allocations": [{"obligation_id": credit, "amount": "100.00"}]})
    row = platform(state)
    assert (row["fees"], row["settled"], row["net"]) == ("0.00", "0.00", "0.00")


def test_refund_overlapping_partial_payout_keeps_fee_credit_attribution_unresolved():
    state, _ = paid_state(refunded="40.00", terms=settings(rate="10", fixed="0", vat="0"))
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "tamara",
        "currency": "SAR", "amount": "45.00", "actual_fee_amount": "5.00", "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": "50.00"}]})
    row = platform(state)
    assert row["fees"] is None and row["net"] is None and not row["complete"]


def test_split_tenders_count_one_order_per_provider_and_fee_only_its_own_share():
    state = state_for()
    terms = settings("تابي", "10", "1", "0")
    terms["payment_methods"] += settings("مدى", "2", "1", "0")["payment_methods"]
    legs = [{"provider": "tabby", "method": "tabby_installment", "amount": "65.00"},
            {"provider": "salla", "method": "mada", "amount": "35.00"}]
    split = {"order_id": "one", "currency": "SAR", "tenders": legs}
    for leg in legs:
        obligation(state, "provider:one:" + leg["provider"], "provider", "provider", leg["amount"],
                   party=leg["provider"], direction="receivable")
    state["engine"]["facts"]["provider_split:one"] = {"evidence": split, "booked_providers": ["salla", "tabby"]}
    order = {"id": "one", "currency": "SAR", "payment": {"split": split}, "created_at": NOW, "updated_at": NOW}
    refresh_provider_fee_inputs(state, {"provider_fee_settings": terms, "orders": [order]})
    rows = {row["provider"]: row for row in report(state)["details"]["provider_balances"]["SAR"]}
    assert (rows["tabby"]["orders"], rows["tabby"]["gross"], rows["tabby"]["fees"], rows["tabby"]["net"]) == (1, "65.00", "7.50", "57.50")
    assert (rows["salla"]["orders"], rows["salla"]["gross"], rows["salla"]["fees"], rows["salla"]["net"]) == (1, "35.00", "1.70", "33.30")


def test_unresolved_paid_claim_has_fee_estimate_but_cannot_fabricate_a_net_claim():
    state, _ = paid_state()
    state["engine"]["obligations"].clear()
    state["engine"]["facts"].clear()
    opening(state, "opening", "provider", "100.00", party="tamara")
    row = platform(state)
    assert row["gross"] == "100.00" and row["fees"] == "9.76"
    assert row["net"] is None and not row["complete"] and row["held_orders"] == 1
    assert report(state)["summary"]["receivable"] == "100.00"


def test_page_and_capture_corroboration_never_count_customer_payment_twice():
    state, _ = paid_state()
    state["engine"]["facts"] = {"provider_order_balance:one": {"order_id": "one", "provider": "tamara",
        "currency": "SAR", "gross": "100.00", "paid": "100.00", "remaining": "0.00"}, **state["engine"]["facts"]}
    assert platform(state)["gross"] == "100.00"
