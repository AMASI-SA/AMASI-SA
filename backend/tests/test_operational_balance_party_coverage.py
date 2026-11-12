from test_operational_balance_financial_position import state_for, opening, obligation, table
from operational_financial_position import build_financial_position
from operational_balance_completeness import annotate_report_coverage
from operational_balance_service import report


def test_seven_distinct_exceptions_preserve_courier_balance_and_delivery_count():
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    for order in ("known1", "known2"):
        obligation(state, "shipping:" + order, "shipping", "courier", "15.00", party="carrier")
    for i in range(7):
        for code in ("operational_delivery_timestamp_missing", "shipping_identity_or_rate_incomplete"):
            state["engine"]["issues"].append({"code": code, "source_id": f"pending{i}", "currency": "SAR",
                "courier_fee_review": {"party_type": "courier", "party_id": "carrier", "order_id": f"pending{i}",
                                       "order_number": f"100{i}", "party_name": "carrier", "currency": "SAR"}})
    result = build_financial_position(state)
    courier = table(result, "couriers")
    row = courier["rows"][0]
    assert row["payable"] == "30.00" and row["confirmed_orders"] == 2
    assert row["pending_orders"] == 7 and len(row["pending_order_details"]) == 7
    assert all(len(detail["reasons"]) == 2 for detail in row["pending_order_details"])
    assert all(detail["order_number"] for detail in row["pending_order_details"])
    assert not courier["complete"]
    assert table(result, "banks_network")["complete"] is True


def test_supplier_issue_does_not_mark_bank_or_driver_incomplete():
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    obligation(state, "shipping:o", "shipping", "store_driver", "20.00", party="d")
    state["engine"]["issues"] = [{"code": "supplier_invoice_identity_ambiguous", "source_id": "invoice"}]
    result = build_financial_position(state)
    assert table(result, "suppliers")["complete"] is False
    assert table(result, "drivers")["complete"] is True
    assert table(result, "banks_network")["complete"] is True


def test_two_fee_components_count_one_delivery_and_keep_known_fee_when_another_component_missing():
    state = state_for()
    obligation(state, "shipping:o:base_shipping", "shipping", "courier", "10.00", party="c", order_id="o", fee_component="base_shipping")
    obligation(state, "shipping:o:cod_commission", "shipping", "courier", "3.00", party="c", order_id="o", fee_component="cod_commission")
    state["engine"]["issues"] = [{"code": "shipping_fee_incomplete", "source_id": "shipping:o", "component": "shipping_fee"}]
    result = build_financial_position(state)
    row = table(result, "couriers")["rows"][0]
    assert row["confirmed_orders"] == 1 and row["pending_orders"] == 1 and row["payable"] == "13.00"
    assert result["currencies"]["SAR"]["deliveries"]["rows"][0]["shipping_confirmed"] == "13.00"


def test_unassigned_exception_never_gets_assigned_by_matching_display_name():
    state = state_for()
    obligation(state, "shipping:known", "shipping", "courier", "12.00", party="c")
    state["engine"]["issues"] = [{"code": "operational_delivery_timestamp_missing", "source_id": "unknown",
        "courier_fee_review": {"party_name": "c", "order_id": "unknown", "order_number": "123"}}]
    result = build_financial_position(state)
    courier = table(result, "couriers")
    assert courier["rows"][0]["pending_orders"] == 0
    assert len(courier["unassigned_order_details"]) == 1
    assert courier["rows"][0]["payable"] == "12.00"


def test_failed_refresh_keeps_known_amount_but_cannot_claim_current_complete():
    for code in ('operational_capacity_requires_archive', 'operational_source_read_timeout'):
        state = state_for()
        obligation(state, 'shipping:o', 'shipping', 'courier', '20.00', party='c')
        state['engine']['issues'] = [{'code': code}]
        result = build_financial_position(state)
        courier = table(result, 'couriers')
        assert courier['complete'] is False and courier['rows'][0]['complete'] is False
        assert courier['rows'][0]['payable'] == '20.00'


def test_legacy_bare_order_issues_remain_clickable_without_guessing_party():
    state = state_for()
    obligation(state, 'shipping:known', 'shipping', 'courier', '20.00', party='c')
    state['engine']['issues'] = [{'code': 'operational_delivery_timestamp_missing', 'source_id': str(i)} for i in range(7)]
    courier = table(build_financial_position(state), 'couriers')
    assert len(courier['unassigned_order_details']) == 7
    assert all(row['reasons'][0]['message'] for row in courier['unassigned_order_details'])


def test_deferred_completion_without_cod_proof_does_not_assert_no_collection():
    state = state_for()
    obligation(state, 'shipping-completed:o', 'shipping', 'courier', '20.00', party='c', order_id='o',
               payment_mode='deferred', recognition_event='order_completed', completed_at=state['engine']['as_of'])
    delivery = build_financial_position(state)['currencies']['SAR']['deliveries']['rows'][0]
    assert delivery['shipping_confirmed'] == '20.00'
    assert delivery['payment_mode'] == 'deferred'
    assert delivery['collection_status'] == 'unavailable'


def test_native_fixed_cod_component_without_order_id_counts_one_delivery():
    state = state_for()
    obligation(state, 'shipping:100:base_shipping', 'shipping', 'courier', '10.00', party='c', fee_component='base_shipping')
    obligation(state, 'shipping:100:cod_fixed_commission', 'shipping', 'courier', '3.00', party='c', fee_component='cod_fixed_commission')
    courier = table(build_financial_position(state), 'couriers')['rows'][0]
    assert courier['payable'] == '13.00' and courier['confirmed_orders'] == 1


def test_delivery_proof_without_a_priced_fee_is_not_counted_as_a_calculated_delivery():
    state = state_for()
    obligation(state, 'shipping:100', 'shipping', 'courier', '0.00', expected='15.00', party='c')
    state['engine']['facts']['shipping:100'] = {'id': 'c', 'party_type': 'courier', 'cost': None}
    state['engine']['issues'] = [{'code': 'shipping_fee_incomplete', 'source_id': 'shipping:100'}]
    courier = table(build_financial_position(state), 'couriers')['rows'][0]
    assert courier['confirmed_orders'] == 0 and courier['pending_orders'] == 1
    state['engine']['facts']['shipping:100'].update(cost='0.00', fee_currency='SAR')
    assert table(build_financial_position(state), 'couriers')['rows'][0]['confirmed_orders'] == 1


def test_provider_snapshot_failure_is_partial_without_erasing_known_net():
    from test_operational_balance_provider_known_subtotals import priced_state
    state = priced_state()
    state['engine']['issues'] = [{'code': 'operational_capacity_requires_archive'}]
    providers = table(build_financial_position(state), 'providers')
    assert providers['complete'] is False and providers['rows'][0]['complete'] is False
    assert providers['rows'][0]['net'] == '90.00'


def test_unknown_provider_net_never_becomes_a_known_zero_summary():
    from test_operational_balance_configured_provider_fees import paid_state
    state, _ = paid_state()
    state['engine']['provider_fee_inputs']['settings'] = {}
    projected = annotate_report_coverage(report(state, provider_balances_net=True))
    assert projected['known_summaries']['SAR']['receivable'] is None
    assert projected['known_summaries']['SAR']['outstanding'] is None
    assert projected['parties'][0]['pending_orders'] == 1


def test_provider_with_only_pending_source_keeps_a_clickable_party_row():
    state = state_for()
    state['engine']['issues'] = [{'scope': 'provider_payment', 'provider': 'salla', 'currency': 'SAR',
        'code': 'provider_payment_amount_conflict', 'source_id': '100', 'order_number': '9001'}]
    projected = annotate_report_coverage(report(state, provider_balances_net=True))
    provider, = projected['parties']
    assert provider['party_type'] == 'provider' and provider['party_id'] == 'salla'
    assert provider['pending_orders'] == 1 and provider['known_outstanding_receivable'] is None
    assert provider['pending_order_details'][0]['order_number'] == '9001'


def test_main_report_and_financial_position_preserve_known_provider_net_beside_pending_fee():
    from test_operational_balance_provider_known_subtotals import priced_state, add_order
    state = priced_state(); add_order(state, 'two', method='unknown')
    projected = annotate_report_coverage(report(state, provider_balances_net=True))
    assert projected['known_summaries']['SAR']['receivable'] == '90.00'
    result = build_financial_position(state)
    assert table(result, 'providers')['totals']['net'] == '90.00'
    assert next(row for row in result['currencies']['SAR']['debt_rows'] if row['id'] == 'providers')['receivable'] == '90.00'
