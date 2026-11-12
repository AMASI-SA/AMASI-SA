"""A partial ledger must distinguish recorded movements, opening money and missing fees."""
from test_operational_balance_financial_position import state_for, opening, obligation, table, NOW
from operational_financial_position import build_financial_position


def test_opening_receivable_is_not_new_provider_sales_and_currency_is_separate():
    state = state_for()
    opening(state, "old-provider", "provider", "125.00", party="p")
    opening(state, "foreign", "provider", "999.00", party="p", currency="USD")
    obligation(state, "provider:sale", "provider", "provider", "72.30",
               party="p", direction="receivable")
    state["engine"]["issues"] = [{"code": "provider_capture_evidence_incomplete", "source_id": "another-sale"}]
    result = build_financial_position(state, generated_at=NOW)
    audit = table(result, "balance_origin")
    row, = [r for r in audit["rows"] if r["side"] == "لنا"]
    assert (row["opening"], row["posted"], row["allocated"], row["remaining"]) == ("125.00", "72.30", "0.00", "197.30")
    assert audit["complete"] is False and audit["show_totals"] is False and audit["totals"]["remaining"] is None
    # The old obligation preserves its gross principal in the ledger. Missing
    # fee evidence cannot turn that principal into a proved net receivable.
    assert result["currencies"]["SAR"]["debt_totals"]["receivable"] == "125.00"
    provider, = [row for row in table(result, "providers")["rows"] if row["provider"] == "p"]
    assert provider["known_gross"] == "72.30" and provider["fees"] is None
    assert provider["known_net"] == "125.00"


def test_carrier_opening_and_fees_separate_from_cash_collected_and_not_counted_twice():
    state = state_for()
    opening(state, "opening-fees", "courier", "50.00", direction="for_party", party="carrier")
    obligation(state, "shipping:synthetic", "shipping", "courier", "18.00", party="carrier")
    obligation(state, "cod:synthetic", "cod", "courier", "200.00", party="carrier", direction="receivable")
    result = build_financial_position(state)
    detail = {r["side"]: r for r in table(result, "balance_origin")["rows"]}
    assert (detail["علينا"]["opening"], detail["علينا"]["posted"], detail["علينا"]["remaining"]) == ("50.00","18.00","68.00")
    assert (detail["لنا"]["opening"], detail["لنا"]["posted"], detail["لنا"]["remaining"]) == ("0.00","200.00","200.00")
    assert result["currencies"]["SAR"]["debt_totals"] == {"receivable":"200.00","payable":"68.00"}


def test_unproven_courier_fee_is_not_displayed_as_free_delivery():
    state = state_for()
    opening(state, "bank", "bank", "30.00")
    state["engine"]["cod_reports"] = {"cod:order-z": {"order_id": "order-z", "currency": "SAR", "customer_outstanding":"90.00"}}
    state["engine"]["issues"] = [{"code": "shipping_identity_or_rate_incomplete", "source_id":"order-z", "component":"shipping_fee"}]
    row, = build_financial_position(state)["currencies"]["SAR"]["deliveries"]["rows"]
    assert row["customer_outstanding"] == "90.00"
    assert all(row[k] is None for k in ("shipping_confirmed","shipping_settled","shipping_outstanding"))
