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
