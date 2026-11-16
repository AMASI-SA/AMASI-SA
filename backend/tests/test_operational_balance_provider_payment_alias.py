"""A newer unclaimed payment shape cannot duplicate an existing paid order.

All identities, prices, fees and dates are synthetic. Assertions use the public
read-only report and financial-position projections; no database is required.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

import pytest

from operational_balance_service import report
from operational_configured_provider_fees import refresh_provider_fee_inputs
from operational_financial_position import build_financial_position
from operational_provider_balance_sar import refresh_basis
from test_operational_balance_configured_provider_fees import settings
from test_operational_balance_financial_position import NOW, obligation, state_for, table


ORDER = "synthetic-alias-order"
PARENT = "provider:" + ORDER
ALIAS = PARENT + ":salla"
CENT = Decimal(".01")


def money(value):
    return format(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def terms():
    result = settings("مدى", "10", "1", "15")
    result["payment_methods"] += settings("تابي", "10", "1", "15")["payment_methods"]
    return result


def old_payment(gross="100.00", refunded="0.00", *, currency="SAR"):
    state = state_for()
    confirmed = money(Decimal(gross) - Decimal(refunded))
    obligation(state, PARENT, "provider", "provider", confirmed,
               party="salla", direction="receivable", currency=currency)
    page = {"order_id": ORDER, "provider": "salla", "currency": currency,
            "order_number": "synthetic-reference", "gross": gross,
            "paid": gross, "remaining": "0.00", "observed_at": NOW}
    state["engine"]["facts"]["provider_order_balance:" + ORDER] = page
    if Decimal(refunded):
        state["engine"]["facts"]["refund:salla:synthetic-refund"] = {
            "order_id": ORDER, "amount": refunded}
    # This is retained old pricing authority. Replacing the source input below
    # does not give the old parent a new rail or a new fee calculation.
    fee = money((Decimal(gross) * Decimal(".10") + Decimal("1")) * Decimal("1.15"))
    state["engine"]["provider_reports"] = {PARENT: {
        "provider": "salla", "currency": currency, "gross": gross,
        "net": confirmed, "refunded": refunded,
        "estimated_fees": fee, "estimated_fees_original": fee}}
    order = {"id": ORDER, "order_number": "synthetic-reference", "currency": currency,
             "created_at": NOW, "updated_at": NOW,
             "payment": {"provider": "salla", "currency": currency, "gross": gross,
                         "payment_method": "mada", "order_page_balance": deepcopy(page)}}
    refresh_provider_fee_inputs(state, {"provider_fee_settings": terms(), "orders": [order]})
    return state, order


def add_new_split(state, gross="150.00", *, currency="SAR", provider="salla",
                  extra_tender=None, order_id=ORDER, fx=None):
    first = Decimal(gross).quantize(CENT) / 2
    first = first.quantize(CENT, rounding=ROUND_HALF_UP)
    method = "mada" if provider == "salla" else "tabby_installment"
    tenders = [{"provider": provider, "method": method, "amount": money(first)},
               {"provider": provider, "method": method, "amount": money(Decimal(gross) - first)}]
    if extra_tender is not None:
        tenders.append(deepcopy(extra_tender))
    split = {"order_id": order_id, "currency": currency,
             "gross": money(sum((Decimal(t["amount"]) for t in tenders), Decimal(0))),
             "tenders": tenders}
    order = {"id": order_id, "order_number": "synthetic-reference-" + order_id,
             "currency": currency, "created_at": NOW, "updated_at": NOW,
             "payment": {"split": split}}
    if fx is not None:
        order["customer_payment_sar"] = deepcopy(fx)
    refresh_provider_fee_inputs(state, {"provider_fee_settings": terms(), "orders": [order]})
    return split


def provider_row(state, provider="salla", currency="SAR", *, sar_view=True):
    projection = report(state, provider_balances_sar=sar_view, provider_balances_net=True)
    return next(row for row in projection["details"]["provider_balances"][currency]
                if row["provider"] == provider)


@pytest.mark.parametrize("old,new,expected", [
    ("100.00", "150.00", "150.00"),
    ("150.00", "100.00", "150.00"),
    ("100.00", "100.00", "100.00"),
    ("12.34", "56.78", "56.78"),
], ids=["newer-larger", "older-larger", "equal", "fractional-money"])
def test_same_provider_unclaimed_alias_displays_principal_once(old, new, expected):
    state, _ = old_payment(old)
    add_new_split(state, new)
    before = deepcopy(state)

    row = provider_row(state)
    assert row["gross"] == row["known_gross"] == expected
    assert row["paid_orders"] == 1 and row["paid_order_ids"] == [ORDER]
    displayed, = table(build_financial_position(state, generated_at=NOW), "providers")["rows"]
    assert displayed["gross"] == expected and displayed["orders"] == 1
    assert state == before


def test_alias_refund_is_displayed_once_without_rewriting_the_saved_refund():
    state, _ = old_payment(refunded="20.00")
    old_row = provider_row(state)
    add_new_split(state)
    before = deepcopy(state)

    row = provider_row(state)
    assert row["known_gross"] == "150.00" and row["refunded"] == "20.00"
    assert row["known_fees"] == old_row["known_fees"] == "12.65"
    assert row["known_net"] == old_row["known_net"] == "67.35"
    displayed, = table(build_financial_position(state, generated_at=NOW), "providers")["rows"]
    assert displayed["refunded"] == "20.00"
    assert state == before


def test_alias_keeps_old_fee_and_net_authority_and_marks_new_fee_scope_incomplete():
    state, _ = old_payment()
    old_row = provider_row(state)
    assert old_row["known_fees"] == "12.65" and old_row["known_net"] == "87.35"
    add_new_split(state)
    before = deepcopy(state)

    row = provider_row(state)
    assert row["known_gross"] == "150.00"
    assert row["known_fees"] == row["known_estimated_fees"] == "12.65"
    assert row["known_net"] == "87.35"
    # 12.65 + a second 18.40 estimate must never become raw fees of 31.05.
    assert row["fees"] is None and not row["fees_complete"]
    assert not row["estimated_fees_complete"] and row["estimated_fee_incomplete_orders"] == 1
    assert not row["complete"] and row["pending_order_ids"] == [ORDER]
    assert row["net"] is None
    assert set(state["engine"]["obligations"]) == {PARENT}
    assert ALIAS not in state["engine"]["provider_reports"]
    assert state == before


@pytest.mark.parametrize("allocated,cash,actual_fee,known_fee,known_net", [
    ("50.00", "47.00", "3.00", "9.32", "43.68"),
    ("100.00", "94.00", "6.00", "6.00", "0.00"),
], ids=["partial-settlement", "fully-settled-old-claim"])
def test_old_allocations_actual_fees_and_balances_survive_the_alias(
        allocated, cash, actual_fee, known_fee, known_net):
    state, _ = old_payment()
    state["movements"].append({
        "id": "synthetic-old-payout", "kind": "settlement", "direction": "incoming",
        "party_type": "provider", "party_id": "salla", "currency": "SAR",
        "amount": cash, "actual_fee_amount": actual_fee,
        "allocations": [{"obligation_id": PARENT, "amount": allocated}]})
    old_report = report(state, provider_balances_sar=True, provider_balances_net=True)
    old_row = provider_row(state)
    add_new_split(state)
    before = deepcopy(state)

    row = provider_row(state)
    current_report = report(state, provider_balances_sar=True, provider_balances_net=True)
    assert row["known_gross"] == "150.00"
    assert row["actual_settlement_fees"] == old_row["actual_settlement_fees"] == actual_fee
    assert row["known_fees"] == old_row["known_fees"] == known_fee
    assert row["known_net"] == old_row["known_net"] == known_net
    assert row["settled"] == old_row["settled"] == cash
    assert current_report["obligations"] == old_report["obligations"]
    assert current_report["summary"]["actual_liquidity"] == old_report["summary"]["actual_liquidity"]
    assert row["fees"] is None and not row["estimated_fees_complete"]
    assert state == before


@pytest.mark.parametrize("authority,expected", [
    ("claim", "250.00"), ("booked-split", "300.00"), ("both", "300.00"),
])
def test_an_accepted_child_or_booked_split_is_not_reclassified_as_an_unclaimed_alias(authority, expected):
    state, _ = old_payment()
    split = add_new_split(state)
    if authority in {"claim", "both"}:
        obligation(state, ALIAS, "provider", "provider", "150.00",
                   party="salla", direction="receivable")
    if authority in {"booked-split", "both"}:
        state["engine"]["facts"]["provider_split:" + ORDER] = {
            "evidence": deepcopy(split), "booked_providers": ["salla"]}
    before = deepcopy(state)

    assert provider_row(state)["known_gross"] == expected
    assert state == before


@pytest.mark.parametrize("extra", [
    {"provider": "tabby", "method": "tabby_installment", "amount": "25.00"},
    {"provider": None, "method": "customer_wallet", "amount": "25.00"},
], ids=["another-provider", "wallet"])
def test_every_new_tender_must_belong_to_the_same_provider_for_aliasing(extra):
    state, _ = old_payment()
    add_new_split(state, extra_tender=extra)
    before = deepcopy(state)

    # A provider leg inside a genuinely mixed tender is not an alias of the
    # old whole-order claim; recognition remains governed by the existing path.
    assert provider_row(state)["known_gross"] == "250.00"
    if extra["provider"]:
        assert provider_row(state, "tabby")["known_gross"] == "25.00"
    assert state == before


@pytest.mark.parametrize("second_provider,second_method", [
    ("tabby", "tabby_installment"), (None, "customer_wallet"),
], ids=["accepted-two-providers", "accepted-provider-and-wallet"])
def test_accepted_cross_provider_and_wallet_shares_are_unchanged(second_provider, second_method):
    state = state_for()
    split = {"order_id": ORDER, "currency": "SAR", "gross": "100.00", "tenders": [
        {"provider": "salla", "method": "mada", "amount": "65.00"},
        {"provider": second_provider, "method": second_method, "amount": "35.00"}]}
    booked = ["salla"] + ([second_provider] if second_provider else [])
    for provider, paid in [("salla", "65.00")] + ([(second_provider, "35.00")] if second_provider else []):
        obligation(state, PARENT + ":" + provider, "provider", "provider", paid,
                   party=provider, direction="receivable")
    state["engine"]["facts"]["provider_split:" + ORDER] = {
        "evidence": deepcopy(split), "booked_providers": booked}
    order = {"id": ORDER, "currency": "SAR", "created_at": NOW, "updated_at": NOW,
             "payment": {"split": split}}
    refresh_provider_fee_inputs(state, {"provider_fee_settings": terms(), "orders": [order]})
    before = deepcopy(state)

    rows = report(state, provider_balances_sar=True, provider_balances_net=True)["details"]["provider_balances"]["SAR"]
    assert {r["provider"]: r["known_gross"] for r in rows} == (
        {"salla": "65.00", "tabby": "35.00"} if second_provider else {"salla": "65.00"})
    assert all(r["paid_orders"] == 1 and r["paid_order_ids"] == [ORDER] for r in rows)
    assert not state["movements"] and state == before


def test_a_different_order_is_never_folded_into_the_old_parent():
    state, _ = old_payment()
    add_new_split(state, order_id="synthetic-other-order")
    before = deepcopy(state)

    row = provider_row(state)
    assert row["known_gross"] == "250.00" and row["paid_orders"] == 2
    assert row["paid_order_ids"] == sorted([ORDER, "synthetic-other-order"])
    assert state == before


def test_a_changed_provider_is_never_folded_into_the_old_parent():
    state, _ = old_payment()
    add_new_split(state, provider="tabby")
    before = deepcopy(state)

    assert provider_row(state)["known_gross"] == "100.00"
    assert provider_row(state, "tabby")["known_gross"] == "150.00"
    assert state == before


def foreign_old_payment():
    state, order = old_payment("10.00", currency="KWD")
    order["customer_payment_sar"] = {"currency": "KWD", "native": {"salla": "10.00"},
        "shares": {"salla": "100.00"}, "paid": "100.00", "basis": "salla_list_paid"}
    refresh_basis(state, [order], state["engine"]["issues"])
    return state


def test_matching_display_currency_does_not_alias_incompatible_native_currencies():
    state = foreign_old_payment()
    add_new_split(state, "150.00", currency="SAR")
    before = deepcopy(state)

    # Both rows display SAR, but one claim is native KWD at a retained basis.
    assert provider_row(state)["known_gross"] == "250.00"
    assert state["engine"]["provider_sar_basis"][PARENT]["native_paid"] == "10.00"
    assert state == before


def test_two_foreign_observations_with_different_fx_ratios_are_not_silently_aliased():
    state = foreign_old_payment()
    add_new_split(state, "15.00", currency="KWD", fx={
        "currency": "KWD", "native": {"salla": "15.00"},
        "shares": {"salla": "165.00"}, "paid": "165.00", "basis": "salla_list_paid"})
    before = deepcopy(state)

    assert provider_row(state)["known_gross"] == "265.00"
    assert state["engine"]["provider_sar_basis"][PARENT]["sar_paid"] == "100.00"
    assert state == before


def test_alias_report_is_repeatable_and_keeps_every_saved_financial_identity():
    state, _ = old_payment(refunded="20.00")
    add_new_split(state)
    before = deepcopy(state)

    first = report(state, provider_balances_sar=True, provider_balances_net=True)
    position = build_financial_position(state, generated_at=NOW)
    assert first == report(state, provider_balances_sar=True, provider_balances_net=True)
    assert position == build_financial_position(state, generated_at=NOW)
    assert state == before
    assert set(state["engine"]["obligations"]) == {PARENT}
    assert set(state["engine"]["facts"]) == {
        "provider_order_balance:" + ORDER, "refund:salla:synthetic-refund"}
    assert not state["movements"] and not state["openings"]


@pytest.mark.parametrize("old,current,provider", [
    ("176.12", "209.60", "salla"),
    ("209.60", "1096.20", "salla"),
    ("208.52", "549.72", "salla"),
    ("180.55", "444.21", "tabby"),
], ids=["salla-small-increase", "salla-large-increase", "salla-mid-increase", "tabby-increase"])
def test_four_payment_amount_patterns_keep_one_current_principal(old, current, provider):
    # The amount patterns exercise the regression; identities and the 10% + 1
    # + 15% tariff remain synthetic and do not assert any merchant's real rates.
    state, _ = old_payment(old)
    if provider == "tabby":
        state["engine"]["obligations"][PARENT].update(party_id=provider, name=provider)
        state["engine"]["facts"]["provider_order_balance:" + ORDER]["provider"] = provider
        state["engine"]["provider_reports"][PARENT]["provider"] = provider
        payment = state["engine"]["provider_fee_inputs"]["orders"][ORDER]["payment"]
        payment.update(provider=provider, payment_method="tabby_installment")
        payment["order_page_balance"]["provider"] = provider
    add_new_split(state, current, provider=provider)
    before = deepcopy(state)

    row = provider_row(state, provider)
    assert row["gross"] == row["known_gross"] == current
    assert row["paid_orders"] == 1 and row["paid_order_ids"] == [ORDER]
    assert row["pending_order_ids"] == [ORDER] and not row["complete"]
    assert row["fees"] is None and not row["fees_complete"]
    assert not row["estimated_fees_complete"] and row["estimated_fee_incomplete_orders"] == 1
    assert state == before
