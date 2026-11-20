from copy import deepcopy

import pytest

from operational_balance_service import report
from operational_configured_provider_fees import refresh_provider_fee_inputs
from operational_provider_balance_sar import refresh_basis
from operational_workspace_order_issues import review_groups
from test_operational_balance_configured_provider_fees import paid_state, settings


@pytest.mark.parametrize("currency", ["SAR", "QAR", "KWD"])
def test_review_uses_same_fee_currency_as_provider_card(currency):
    terms = settings("PayPal", rate="4.9", fixed="1.13", vat="0")
    state, order = paid_state("paypal", gross="100.00", currency=currency, terms=terms)
    if currency != "SAR":
        order["customer_payment_sar"] = {
            "currency": currency, "native": {"paypal": "100.00"},
            "shares": {"paypal": "200.00"}, "paid": "200.00", "basis": "salla_list_paid",
        }
        refresh_basis(state, [order], state["engine"]["issues"])
        refresh_provider_fee_inputs(state, {"provider_fee_settings": terms, "orders": [order]})
    before = deepcopy(state)
    card = report(state, provider_balances_sar=True)["details"]["provider_balances"]["SAR"][0]
    review = review_groups(state, {("provider", "paypal"): "PayPal"}, False)["parties"][0]
    assert card["pending_orders"] == 0
    assert review["pending_order_details"] == []
    assert state == before


def test_review_does_not_hide_missing_exchange_evidence():
    state, _ = paid_state("paypal", currency="KWD", terms=settings("PayPal"))
    review = review_groups(state, {("provider", "paypal"): "PayPal"}, False)["parties"][0]
    assert review["pending_order_details"]


def test_review_preserves_historical_reconciliation_even_when_fee_is_known():
    state, _ = paid_state("paypal", terms=settings("PayPal"))
    state["engine"]["issues"].append({
        "scope": "provider_payment", "provider": "paypal", "source_id": "provider:one",
        "code": "provider_historical_capture_review_required", "currency": "SAR",
    })
    review = review_groups(state, {("provider", "paypal"): "PayPal"}, False)["parties"][0]
    row, = review["pending_order_details"]
    assert "provider_historical_capture_review_required" in {r["code"] for r in row["reasons"]}


def test_uncomputed_principal_is_separate_from_net_and_opening():
    from test_operational_balance_provider_known_subtotals import priced_state, add_order
    from test_operational_balance_financial_position import opening
    from operational_workspace_provider_orders import provider_position
    state = priced_state()
    add_order(state, 'held', paid='202.16', claim=False)
    add_order(state, 'unknown-fee', paid='289.66', method='unknown')
    opening(state, 'opening', 'provider', '20.00', party='salla')
    before = deepcopy(state)
    result = report(state, provider_balances_sar=True)
    position = provider_position(result, 'salla', show_orders=True)
    assert position['known_net'] == '110.00'
    assert position['uncomputed_principal'] == '491.82'
    assert position['uncomputed_orders'] == 2
    assert position['uncomputed_principal_complete'] is True
    assert 'uncomputed_orders' not in provider_position(result, 'salla', show_orders=False)
    assert state == before


def test_priced_order_is_not_counted_as_uncomputed():
    from operational_workspace_provider_orders import provider_position
    state, _ = paid_state('paypal', terms=settings('PayPal', '4.9', '1.13', '0'))
    position = provider_position(report(state, provider_balances_sar=True), 'paypal', show_orders=True)
    assert position['net'] == '93.97'
    assert position['uncomputed_principal'] == '0.00'
    assert position['uncomputed_orders'] == 0


def test_unknown_fx_cannot_be_presented_as_complete_pending_sar():
    from operational_workspace_provider_orders import provider_position
    state, _ = paid_state('paypal', currency='KWD', terms=settings('PayPal'))
    position = provider_position(report(state, provider_balances_sar=True), 'paypal', show_orders=True)
    assert position['uncomputed_principal_complete'] is False
    assert position['foreign_unconverted'] is True


def test_pending_principal_excludes_refunds_and_settled_gross():
    state, _ = paid_state(refunded='40.00', terms=settings(rate='10', fixed='0', vat='0'))
    state['movements'].append({'id': 'payout', 'kind': 'settlement', 'party_type': 'provider', 'party_id': 'tamara',
        'currency': 'SAR', 'amount': '45.00', 'actual_fee_amount': '5.00', 'direction': 'incoming',
        'allocations': [{'obligation_id': 'provider:one', 'amount': '50.00'}]})
    row = report(state, provider_balances_sar=True)['details']['provider_balances']['SAR'][0]
    assert row['uncomputed_principal'] == '10.00'
    assert row['uncomputed_orders'] == 1
