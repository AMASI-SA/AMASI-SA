from datetime import datetime, timezone
from copy import deepcopy
from decimal import Decimal
import pytest

from operational_profit_period_expenses import recurring_evidence, advertising_evidence
from operational_balance_sources import AD_SOURCES


def at(day):
    return datetime.fromisoformat(day+'T21:00:00+00:00')


def obligation(**changes):
    return {'id': 'rent', 'status': 'active', 'expense_type': 'rent',
        'start_date': '2026-11-01', 'cycle': 'monthly', 'period_amount': '3000', **changes}


def test_recurring_consumption_is_calendar_accrual_not_payment_or_whole_cycle():
    result = recurring_evidence([obligation()], [], at('2026-11-01'), at('2026-11-03'))
    assert result['known_subtotal'] == '300.00'
    assert result['source_count'] == 3
    assert not result['complete'] and result['amount'] is None
    assert result['issues'][0]['code'] == 'profit_expense_input_tax_basis_required'


def test_utilities_require_actual_invoice_not_historical_estimate():
    row = obligation(expense_type='electricity', estimation_basis='manual')
    missing = recurring_evidence([row], [], at('2026-11-01'), at('2026-11-03'))
    assert missing['known_subtotal'] is None
    assert missing['issues'][0]['code'] == 'profit_utility_actual_invoice_missing_or_ambiguous'
    invoice = {'obligation_id': 'rent', 'period_start': '2026-11-01', 'period_end': '2026-11-30', 'amount': '6000'}
    actual = recurring_evidence([row], [invoice], at('2026-11-01'), at('2026-11-03'))
    assert actual['known_subtotal'] == '600.00'
    duplicate = recurring_evidence([row], [invoice, deepcopy(invoice)], at('2026-11-01'), at('2026-11-03'))
    assert duplicate['known_subtotal'] is None


@pytest.mark.parametrize('change', [dict(currency='USD'), dict(cycle='unknown'),
    dict(updated_at='2026-11-02T00:00:00Z', created_at='2026-10-01T00:00:00Z')])
def test_no_currency_relabel_or_unversioned_history(change):
    assert recurring_evidence([obligation(**change)], [], at('2026-11-01'), at('2026-11-03'))['known_subtotal'] is None


def test_missing_expenses_are_unknown_not_zero_and_duplicate_ids_not_counted():
    assert recurring_evidence([], [], at('2026-11-01'), at('2026-11-03'))['amount'] == '0.00'
    assert recurring_evidence([obligation(), obligation()], [], at('2026-11-01'), at('2026-11-03'))['known_subtotal'] is None


def test_stopped_recurring_and_full_cycle_rounding():
    result = recurring_evidence([obligation(stopped_at='2026-11-03', status='stopped')], [], at('2026-10-31'), at('2026-11-29'))
    assert result['known_subtotal'] == '200.00'
    full = recurring_evidence([obligation(period_amount='100')], [], at('2026-10-31'), at('2026-11-29'))
    assert full['known_subtotal'] == '100.00'


def data():
    return {key: [] for key in ['mz2_ad_account_bindings_v2', 'mezan_integration_accounts_v2',
        'mezan_ad_account_cost_settings_v2', 'mz2_ad_automation_policies_v2', 'mz2_ad_fx_snapshots_v2',
        *(item[1] for item in AD_SOURCES.values())]}


def test_missing_ad_applicability_does_not_mean_zero():
    result = advertising_evidence(data(), {'owner_id': 'owner'}, 'owner', at('2026-11-01'), at('2026-11-03'))
    assert not result['complete'] and result['amount'] is None and result['known_subtotal'] is None


def test_ad_missing_day_and_fx_do_not_certify_other_days(monkeypatch):
    import operational_profit_period_expenses as module
    source = data()
    source['mz2_ad_account_bindings_v2'] = [{'_id': 'ad', 'status': 'active', 'confirmed_by': 'owner',
        'confirmed_at': '2026-11-01T00:00:00Z', 'platform': 'meta', 'integration_account_id': 'acct', 'platform_account_id': 'external'}]
    source['mezan_integration_accounts_v2'] = [{'provider': 'meta_ads', 'mezan_integration_account_id': 'acct',
        'external_account_id': 'external', 'timezone': 'Asia/Riyadh'}]
    def project(data, out, *args):
        out['ad_snapshots'] = [{'account_id': 'ad', 'date': '2026-11-02', 'closed': True, 'complete': True, 'sar_amount': '12.50'}]
    monkeypatch.setattr(module, '_advertising', project)
    partial = advertising_evidence(source, {'owner_id': 'owner'}, 'owner', at('2026-11-01'), at('2026-11-03'))
    assert partial['known_subtotal'] == '12.50' and not partial['complete']
    complete = advertising_evidence(source, {'owner_id': 'owner'}, 'owner', at('2026-11-01'), at('2026-11-02'))
    assert complete['complete'] and complete['amount'] == '12.50'


def test_collect_reads_only_bounded_owner_scoped_sources_without_writes():
    import asyncio
    from operational_profit_period_expenses import collect
    seen = []
    class Cursor:
        async def to_list(self, limit):
            assert 0 < limit <= 20001
            return []
    class Collection:
        def find(self, query, projection=None):
            assert query == {'user_id': 'owner'}
            return Cursor()
    class DB:
        def __getitem__(self, name):
            seen.append(name)
            return Collection()
    result = asyncio.run(collect(DB(), 'owner', {'owner_id': 'owner'}, at('2026-11-01'), at('2026-11-03')))
    assert len(seen) == len(set(seen)) == 15
    assert result['by_currency']['SAR']['operating_expenses']['amount'] == '0.00'
    assert result['by_currency']['SAR']['advertising']['amount'] is None


def expenses(legacy=None, recurring=None, state=None):
    from operational_profit_period_expenses import operating_expense_evidence, EXPENSE_SOURCES
    source = {name: [] for name in EXPENSE_SOURCES}
    source.update(legacy or {})
    return operating_expense_evidence({'operating_recurring_obligations_v2': recurring or [],
        'operating_recurring_invoices_v2': []}, source, state or {}, at('2026-10-31'), at('2026-11-29'))


def test_exhaustive_expense_empty_scope_is_known_zero():
    result = expenses()
    assert result['complete'] and result['amount'] == '0.00'
    assert len(result['coverage_sources']) == 7


def test_daily_consumption_counted_once_without_bank_settlement_and_tax_issue_names_row():
    row = {'id': 'fuel', 'date': '2026-11-04', 'amount': '115', 'linked_transaction_id': 'tx'}
    result = expenses({'operating_daily_expenses': [row]})
    assert result['known_subtotal'] == '115.00' and result['source_count'] == 1
    assert result['amount'] is None
    assert result['issues'] == [{'code': 'profit_expense_input_tax_basis_required',
        'source_id': 'fuel', 'source': 'operating_daily_expenses', 'currency': 'SAR'}]


def test_legacy_prepaid_and_rent_are_consumption_not_cash_or_full_balance():
    prepaid = {'id': 'insurance', 'start_date': '2026-11-01', 'end_date': '2026-12-30', 'amount': '600'}
    rental = {'id': 'warehouse', 'start_date': '2026-01-01', 'end_date': '2026-12-31', 'annual_amount': '3650'}
    result = expenses({'operating_prepaid_expenses': [prepaid], 'operating_rentals': [rental]})
    assert result['known_subtotal'] == '600.00'
    assert {r['source_id'] for r in result['issues']} == {'insurance', 'warehouse'}


def test_duplicate_daily_ids_and_recurring_legacy_overlap_never_double_count():
    row = {'id': 'fuel', 'date': '2026-11-04', 'amount': '115'}
    assert expenses({'operating_daily_expenses': [row, row]})['known_subtotal'] is None
    rental = {'id': 'legacy', 'start_date': '2026-01-01', 'end_date': '2026-12-31', 'annual_amount': '3650'}
    result = expenses({'operating_rentals': [rental]}, recurring=[obligation()])
    assert result['known_subtotal'] == '3000.00'
    assert any(r['code'] == 'profit_legacy_recurring_overlap_review_required' and r['source_id'] == 'legacy' for r in result['issues'])


def test_direct_expense_payment_needs_link_and_allocated_settlement_is_not_second_expense():
    movement = {'id': 'payment', 'party_type': 'operating_expense', 'kind': 'payment',
        'occurred_at': '2026-11-03T10:00:00Z', 'amount': '100', 'allocations': []}
    result = expenses(state={'movements': [movement]})
    assert result['amount'] is None and result['known_subtotal'] is None
    assert result['issues'][0]['source_id'] == 'payment'
    movement['allocations'] = [{'amount': '100'}]
    assert expenses(state={'movements': [movement]})['amount'] is None
    movement['allocations'][0]['obligation_id'] = 'recurring:rent'
    state = {'movements': [movement], 'engine': {'obligations': {'recurring:rent': {
        'kind': 'recurring', 'source_context': {'obligation_id': 'rent'}}}}}
    result = expenses(recurring=[obligation()], state=state)
    assert result['known_subtotal'] == '3000.00'
    assert not any(r['code'] == 'profit_direct_expense_accrual_link_required' for r in result['issues'])


def test_outside_period_and_personal_household_never_enter_business_expense():
    result = expenses({'operating_daily_expenses': [{'id': 'old', 'date': '2026-10-10', 'amount': '999'}],
        'operating_salaries': [{'id': 'home', 'category': 'household', 'monthly_amount': '999'}]})
    assert result['complete'] and result['amount'] == '0.00'


def test_ad_funding_is_not_expense_but_explicit_bank_fee_is_separate_known_cost():
    movement = {'id': 'fx', 'party_type': 'ad_account', 'kind': 'wallet_funding',
        'occurred_at': '2026-11-03T10:00:00Z', 'amount': '100', 'currency': 'USD',
        'bank_currency': 'SAR', 'bank_amount': '385', 'bank_fee_amount': '10'}
    result = expenses(state={'movements': [movement]})
    assert result['known_subtotal'] == '10.00' and result['amount'] is None
    assert result['issues'][0]['source'] == 'operational_bank_fee'
