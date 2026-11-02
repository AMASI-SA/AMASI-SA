"""Read-only diagnosis of pinned production 3d872086; synthetic data only.

Run explicitly against that source with backend and backend/tests on PYTHONPATH.
Passing checks reproduce defects, NOT release acceptance. Do not run these as
future corrected-source invariants. No merchant identity, credential or data.
"""
from copy import deepcopy
from decimal import Decimal
import pytest
import operational_balance_sources as src
from operational_balance_engine import reconcile
from test_operational_balance_sources import DB, row, order, run, START, NOW


def fixture_order(method, nested=False, **overrides):
    payment = {'method': method, 'status': 'paid', 'paid_amount': '300',
               'paid_at': '2026-10-06T02:00:00+00:00',
               'reference': 'synthetic-capture', 'plan_id': 'synthetic-plan'}
    raw = dict(status={'slug': 'under_review', 'name': 'بإنتظار المراجعة'},
               payment_method=method,
               payment_methods=[{'amount': '300', 'payment_method': method,
                                 'transaction_reference': 'synthetic-capture', 'provider': None}],
               is_pending_payment=False,
               payment_actions={
                   'refund_action': {'paid_amount': {'amount': '300', 'currency': 'SAR'},
                                     'refunded_transactions': [],
                                     'refund_amount': {'amount': '0', 'currency': 'SAR'}},
                   'remaining_action': {'paid_amount': {'amount': '300', 'currency': 'SAR'},
                                        'has_remaining_amount': False, 'remaining_amount': None}})
    if nested:
        raw['payment'] = payment
    raw.update(overrides)
    return order(**raw)


def collect(method, nested=False, **overrides):
    db = DB({'unified_orders': [fixture_order(method, nested, **overrides)],
             'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')]})
    before = deepcopy(db.data)
    result = run(src.collect_sources(db, 'owner', START, NOW))
    assert db.data == before
    assert len(result['orders']) == 1
    assert result['orders'][0]['status'] == 'under_review'
    return result


def policy(provider):
    return {'id': 'synthetic-policy', 'provider': provider, 'currency': 'SAR',
            'status': 'active', 'effective_from': '2026-01-01', 'percentage': '2',
            'fixed_amount': '1', 'vat_treatment': 'exempt'}


@pytest.mark.parametrize('method', ['tamara_installment', 'tabby_installment', 'credit_card'])
@pytest.mark.parametrize('nested', [False, True])
def test_current_alias_gap_silently_omits_payment_even_with_complete_nested_capture(method, nested):
    result = collect(method, nested)
    assert 'payment' not in result['orders'][0]
    assert not any(i['code'].startswith('provider_') for i in result['issues'])


@pytest.mark.parametrize('method', ['tamara', 'tabby', 'mada'])
def test_native_paid_actions_and_transaction_reference_do_not_become_confirmed_capture(method):
    result = collect(method)
    item = result['orders'][0]
    assert 'captures' not in item['payment']
    result['fee_policies'] = [policy(item['payment']['provider'])]
    state = {'status': 'active', 'started_at': START}
    before = deepcopy(state)
    projection = reconcile(state, result, NOW)
    obligation = projection['engine']['obligations']['provider:100']
    assert obligation['confirmed'] == '0.00'
    assert any(i['code'] == 'provider_capture_evidence_missing'
               for i in projection['engine']['issues'])
    assert state == before


@pytest.mark.parametrize('method', ['tamara', 'tabby', 'mada'])
def test_awaiting_review_does_not_block_confirmed_provider_with_complete_evidence(method):
    result = collect(method, nested=True)
    result['fee_policies'] = [policy(result['orders'][0]['payment']['provider'])]
    state = {'status': 'active', 'started_at': START}
    first = reconcile(state, result, NOW)
    assert first['engine']['obligations']['provider:100']['confirmed'] == '300.00'
    assert reconcile(first, result, NOW) == first
    assert not first.get('movements')


@pytest.mark.parametrize('method', ['tamara', 'tabby'])
def test_current_source_drops_explicit_plan_id(method):
    result = collect(method, nested=True)
    assert 'plan_id' not in result['orders'][0]['payment']


def test_current_payroll_skips_cutover_day_then_counts_second_day_once():
    started = '2026-11-01T00:30:01+03:00'
    state = {'status': 'active', 'started_at': started,
             'openings': {'synthetic-opening': {'amount': '600.00'}},
             'movements': []}
    before = deepcopy(state)
    sources = {'employees': [{'id': 'synthetic-employee', 'currency': 'SAR',
                'hire_date': '2026-01-01', 'salary_revisions': [{
                  'effective_from': '2026-01-01', 'status': 'active', 'salary': '3000'}]}]}
    day_one = reconcile(state, sources, '2026-11-01T23:59:59+03:00')
    assert day_one['engine']['salary_days'] == {}
    day_two = reconcile(day_one, sources, '2026-11-02T00:00:00+03:00')
    assert set(day_two['engine']['salary_days']) == {'salary:synthetic-employee:2026-11-02'}
    assert Decimal(day_two['engine']['salary_days']['salary:synthetic-employee:2026-11-02']['amount']) == Decimal('100.00')
    assert day_two['openings'] == before['openings']
    assert day_two['movements'] == before['movements']
    assert state == before
    assert reconcile(day_two, sources, '2026-11-02T00:00:00+03:00') == day_two
