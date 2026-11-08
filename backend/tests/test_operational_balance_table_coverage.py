"""Table scope must follow real financial dependencies, not shared labels."""
from copy import deepcopy

import pytest

from operational_financial_position import build_financial_position
from operational_provider_report_coverage import provider_evidence_coverage
from test_operational_balance_financial_position import state_for, opening, table


def snapshot(*issues):
    state = state_for()
    for kind in ("bank", "cash", "supplier", "employee", "external_person", "provider"):
        opening(state, kind, kind, "100.00")
    state["engine"]["issues"] = list(issues)
    return state


def test_tabby_fee_gap_preserves_unrelated_complete_tables_and_all_recorded_amounts():
    issue = {"code": "provider_payment_plan_required", "source_id": "provider:one", "provider": "tabby"}
    state = snapshot(issue, deepcopy(issue))
    before = deepcopy(state)
    result = build_financial_position(state)
    assert table(result, "providers")["complete"] is False
    for identity in ("banks_network", "cash", "suppliers", "employees", "external"):
        assert table(result, identity)["complete"] is True
    assert table(result, "providers")["coverage"]["affected_source_count"] == 1
    assert table(result, "providers")["coverage"]["financial_impact"] is None
    assert "provider_payment_plan_required" not in " ".join(table(result, "providers")["notes"])
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] is None
    clean = build_financial_position(snapshot())
    for identity in ("banks_network", "cash", "suppliers", "employees", "external"):
        assert table(result, identity)["rows"] == table(clean, identity)["rows"]
        assert table(result, identity)["totals"] == table(clean, identity)["totals"]
    assert state == before


def test_prepaid_gap_blocks_cash_and_shipping_but_not_unrelated_supplier_debt():
    result = build_financial_position(snapshot({"code": "operational_prepaid_shipping_completion_timestamp_missing",
                                               "source_id": "shipment-one", "component": "prepaid_shipping"}))
    assert not table(result, "cash")["complete"]
    assert not table(result, "couriers")["complete"]
    assert table(result, "suppliers")["complete"]
    assert table(result, "banks_network")["complete"]


@pytest.mark.parametrize("issue", [
    {"code": "future_unknown_source_failure", "component": "shipping_fee"},
    {"code": "provider_settled_capture_review_required", "source_id": "provider:one"},
    {"code": "operational_order_bank_credit_conflict", "source_id": "order"},
    {"code": "supplier_receipt_evidence_incomplete", "source_id": "invoice"},
])
def test_unknown_or_bank_affecting_failure_is_never_silently_isolated(issue):
    result = build_financial_position(snapshot(issue))
    for identity in ("banks_network", "cash", "suppliers", "employees", "external"):
        assert table(result, identity)["complete"] is False


def test_salary_gap_only_blocks_employee_accrual_table_and_consolidated_position():
    result = build_financial_position(snapshot({"code": "salary_contract_missing", "source_id": "salary:one:2026-11-01"}))
    assert not table(result, "employees")["complete"]
    assert table(result, "suppliers")["complete"]
    assert table(result, "banks_network")["complete"]
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] is None


def test_missing_snapshot_time_does_not_establish_any_table_coverage():
    state = snapshot()
    state["engine"]["as_of"] = None
    result = build_financial_position(state)
    assert not table(result, "banks_network")["complete"]
    assert not table(result, "suppliers")["complete"]


def test_known_unsettled_provider_calculation_is_ready_but_statement_is_not_certified():
    projection = {"obligations": [{"id": "provider:one", "kind": "provider", "party_type": "provider",
        "party_id": "tabby", "settled": "0.00"}], "issues": [], "details": {
        "facts": {"capture:one": {"order_id": "one", "provider": "tabby"}},
        "provider_balances": {"SAR": [{"provider": "tabby", "complete": True}]}}}
    result = provider_evidence_coverage(projection)[0]
    assert result["status"] == result["calculation_status"] == "ready"
    assert result["statement_reconciliation_status"] == "unavailable"
    assert result["affected_order_count"] == 0
    projection["issues"] = [
        {"code": "provider_payment_plan_required", "source_id": "provider:one", "provider": "tabby"},
        {"code": "provider_capture_evidence_incomplete", "source_id": "provider:one", "provider": "tabby"},
        {"code": "provider_net_balance_incomplete", "source_id": "provider:tabby", "provider": "tabby"},
    ]
    result = provider_evidence_coverage(projection)[0]
    assert result["status"] == "incomplete"
    assert result["affected_order_count"] == 1


def test_provider_order_repeated_as_raw_prefixed_and_split_identity_is_one_affected_case():
    issues = [
        {"code":"provider_capture_evidence_incomplete","source_id":"one","scope":"provider_payment","provider":"tabby"},
        {"code":"provider_payment_plan_required","source_id":"provider:one","scope":"provider_payment","provider":"tabby"},
        {"code":"provider_fee_policy_incomplete_or_ambiguous","source_id":"provider:one:tabby","provider":"tabby"},
    ]
    result = build_financial_position(snapshot(*issues))
    assert table(result,"providers")["coverage"]["affected_source_count"] == 1
    diagnostic = next(r for r in result["dependencies"] if r["code"] == "provider_evidence_coverage_tabby")
    assert diagnostic["affected_order_count"] == 1
    assert diagnostic["status"] == "incomplete"
    assert len(table(result,"providers")["coverage"]["issues"]) >= 3


@pytest.mark.parametrize('code', [
    'provider_payment_method_unknown', 'provider_payment_method_conflict',
    'provider_split_payment_review_required', 'provider_payment_shape_invalid',
    'provider_payment_currency_conflict', 'provider_payment_amount_invalid',
    'provider_payment_amount_conflict', 'provider_payment_reference_missing',
    'provider_capture_original_basis_required', 'provider_refund_execution_evidence_required',
    'provider_cancellation_execution_evidence_required',
])
def test_reviewed_payment_phase_keeps_money_risk_without_blocking_suppliers_or_payroll(code):
    issue={'code':code,'source_id':'order-one','scope':'provider_payment','provider':'tabby'}
    from operational_financial_table_coverage import affected_tables
    assert 'customer_wallets' in affected_tables(issue)
    value=snapshot(issue); before=deepcopy(value)
    result=build_financial_position(value)
    for identity in ('banks_network','cash','providers','couriers','drivers','employee_custody'):
        assert not table(result,identity)['complete'], identity
    for identity in ('suppliers','employees','external','ads'):
        assert table(result,identity)['complete'], identity
    assert result['currencies']['SAR']['metrics']['monetary_position'] is None
    assert value==before


@pytest.mark.parametrize('code', [
    'provider_capture_timestamp_invalid', 'provider_capture_conflict', 'provider_capture_status_conflict',
    'provider_transaction_order_conflict', 'provider_transaction_history_conflict',
    'provider_transaction_binding_conflict', 'provider_transaction_not_captured',
    'provider_transaction_amount_conflict',
])
def test_verified_provider_capture_phase_is_independent_of_supplier_payroll_and_cash(code):
    result=build_financial_position(snapshot({'code':code,'source_id':'one','scope':'provider_payment','provider':'tabby'}))
    assert not table(result,'providers')['complete']
    for identity in ('suppliers','employees','banks_network','cash'):
        assert table(result,identity)['complete']


@pytest.mark.parametrize('code', ['provider_historical_capture_review_required','provider_settled_capture_review_required'])
def test_historical_capture_overlap_keeps_bank_cash_and_custody_risk(code):
    issue={'code':code,'source_id':'provider:one','provider':'tabby',
        'order_page_balance_review':{'order_id':'one','order_number':'1001','provider':'tabby','currency':'SAR',
                                     'gross':'100.00','paid':'100.00','remaining':'0.00'}}
    from operational_financial_table_coverage import affected_tables
    assert 'customer_wallets' in affected_tables(issue)
    result=build_financial_position(snapshot(issue))
    for identity in ('banks_network','cash','providers','couriers','drivers','employee_custody'):
        assert not table(result,identity)['complete']
    assert table(result,'suppliers')['complete'] and table(result,'employees')['complete']


@pytest.mark.parametrize('code', [
    'operational_delivery_timestamp_missing','operational_delivery_timestamp_invalid',
    'operational_delivery_timestamp_timezone_missing','operational_delivery_timestamp_ambiguous',
    'operational_delivery_timestamp_before_order','operational_delivery_timestamp_before_shipment',
    'operational_delivery_current_shipment_required','operational_delivery_shipment_identity_missing',
    'operational_delivery_shipment_identity_conflict','operational_delivery_shipment_not_delivered',
    'operational_delivery_shipment_status_conflict','operational_delivery_shipment_waybill_conflict',
    'operational_delivery_order_waybill_unproven',
])
def test_reviewed_shipping_phase_does_not_hold_independent_supplier_receipts(code):
    result=build_financial_position(snapshot({'code':code,'source_id':'one','component':'shipping_fee'}))
    for identity in ('banks_network','cash','couriers','drivers','employee_custody'):
        assert not table(result,identity)['complete']
    for identity in ('suppliers','employees','external','providers'):
        assert table(result,identity)['complete']


@pytest.mark.parametrize('issue', [
    {'code':'provider_payment_method_unknown'},
    {'code':'provider_refund_execution_evidence_required'},
    {'code':'provider_capture_timestamp_invalid','scope':'provider_payment'},
    {'code':'provider_transaction_binding_conflict','scope':'provider_payment'},
    {'code':'provider_historical_capture_review_required','source_id':'provider:one'},
    {'code':'provider_settled_capture_review_required','order_page_balance_review':{'order_id':'other','provider':'tabby'},'source_id':'provider:one','provider':'tabby'},
    {'code':'operational_delivery_timestamp_missing'},
    {'code':'operational_delivery_owner_mismatch','component':'shipping_fee'},
    {'code':'provider_future_unknown_failure','scope':'provider_payment','provider':'tabby'},
    {'code':'operational_delivery_future_unknown_failure','component':'shipping_fee'},
])
def test_new_phase_scoping_never_trusts_missing_marker_or_unknown_code(issue):
    from operational_financial_table_coverage import affected_tables, ALL_TABLES
    assert affected_tables(issue)==ALL_TABLES


def test_actual_old_driver_source_retains_delivery_phase_without_courier_display_name():
    from test_operational_balance_delivery_cutover import driver_data, collect
    from operational_financial_table_coverage import affected_tables
    data = driver_data()
    data['store_delivery_assignments'][0].pop('delivered_at')
    source = data['unified_orders'][0]
    source.pop('salla_shipping_current', None)
    source['raw_by_source']['salla_direct'].pop('shipping', None)
    result = collect(data)
    issue = next(r for r in result['issues'] if r['code'] == 'operational_delivery_timestamp_missing')
    assert {'cash', 'banks_network', 'drivers'}.issubset(affected_tables(issue))
    assert not {'suppliers', 'employees', 'external'}.intersection(affected_tables(issue))
    assert not result['orders'] and not result.get('delivery_events')


def test_missing_canonical_order_keeps_cash_supplier_and_consolidation_blocked_only():
    issue = {'code': 'canonical_salla_source_missing', 'source_id': 'order-unknown'}
    result = build_financial_position(snapshot(issue))
    for identity in ('employees', 'external', 'ads'):
        assert table(result, identity)['coverage']['complete'] is True
    for identity in ('banks_network', 'cash', 'suppliers', 'providers', 'employee_custody'):
        assert table(result, identity)['coverage']['complete'] is False
    assert result['currencies']['SAR']['completeness']['actual'] is False


@pytest.mark.parametrize('issue', [
    {'code': 'canonical_salla_source_missing'},
    {'code': 'canonical_salla_source_missing', 'source_id': ''},
    {'code': 'canonical_salla_source_missing', 'source_id': {}},
    {'code': 'canonical_salla_source_unknown', 'source_id': 'order-unknown'},
])
def test_missing_identity_or_unknown_canonical_failure_still_blocks_every_table(issue):
    from operational_financial_table_coverage import ALL_TABLES, affected_tables
    assert affected_tables(issue) == ALL_TABLES


def test_real_missing_canonical_source_does_not_change_independent_salary_or_openings():
    from operational_balance_engine import reconcile
    from operational_balance_sources import collect_sources
    from test_operational_balance_sources import DB, row, run, START, NOW
    data = {'mezan_employees_v2': [row(id='e', status='active')],
            'mezan_employee_salary_contracts_v2': [row(employee_id='e', salary_revisions=[
                {'effective_from': '2026-01-01', 'monthly_amount': '3000.01'}])]}
    baseline = run(collect_sources(DB(data), 'owner', START, NOW))
    data['unified_orders'] = [row(order_number='unknown', total=999, status='delivered')]
    missing = run(collect_sources(DB(data), 'owner', START, NOW))
    assert missing['orders'] == baseline['orders'] == []
    assert missing['employees'] == baseline['employees']
    assert missing['ad_snapshots'] == baseline['ad_snapshots']
    state = snapshot()
    state['started_at'] = START
    opening(state, 'ad', 'ad_account', '45.00')
    good = reconcile(state, baseline, NOW)
    held = reconcile(state, missing, NOW)
    assert held['engine']['obligations'] == good['engine']['obligations']
    assert held['engine']['salary_days'] == good['engine']['salary_days']
    assert held['engine']['salary_days']  # Actual accrued days, not an empty fixture.
    reports = [build_financial_position(s) for s in (good, held)]
    for identity in ('employees', 'external', 'ads'):
        assert table(reports[0], identity)['rows'] == table(reports[1], identity)['rows']
        assert table(reports[0], identity)['complete'] == table(reports[1], identity)['complete']
    for identity in ('banks_network', 'cash', 'suppliers'):
        assert table(reports[1], identity)['complete'] is False
    # Independent source gaps remain real, including an employee bank payment:
    # the order ambiguity continues to block the bank leg, never its cash effect.
    held['engine']['issues'].append({'code': 'employee_salary_contract_incomplete', 'source_id': 'e'})
    assert table(build_financial_position(held), 'employees')['complete'] is False


def test_canonical_classification_never_suppresses_independent_ad_or_bank_gaps():
    from operational_financial_table_coverage import affected_tables
    canonical = {'code': 'canonical_salla_source_missing', 'source_id': 'unknown'}
    for extra, affected in [
        ({'code': 'ad_daily_source_incomplete', 'source_id': 'ad:one'}, 'ads'),
        ({'code': 'bank_transfer_source_unresolved', 'source_id': 'employee-payment'}, 'employees'),
        ({'code': 'bank_transfer_source_unresolved', 'source_id': 'ad-funding'}, 'ads'),
    ]:
        result = build_financial_position(snapshot(canonical, extra))
        assert table(result, affected)['complete'] is False
        assert table(result, 'banks_network')['complete'] is False
        assert table(result, 'cash')['complete'] is False
    assert 'ads_currency_conversion' not in affected_tables(canonical)
    assert 'ads_currency_conversion' in affected_tables({'code': 'ad_fx_snapshot_incomplete'})
