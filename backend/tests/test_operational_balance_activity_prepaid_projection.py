"""Prepaid capture must retain exact prefix evidence without full report replay."""
from copy import deepcopy
from decimal import Decimal
import random
import pytest

import operational_financial_activity as activity
import operational_balance_service as service
from test_operational_balance_activity_cash_projection import state_fixture, receipt, STAMP


def prepaid(state, index, currency='SAR', account='cash'):
    key = 'shipping-prepaid:' + str(index)
    state['engine']['obligations'][key] = {'id': key, 'kind': 'shipping',
        'party_type': 'courier', 'party_id': 'aramex', 'name': 'Aramex',
        'currency': currency, 'confirmed': '34.50', 'expected': '0.00',
        'direction': 'payable', 'business_date': '2026-11-08'}
    movement = receipt('prepaid:' + str(index), party_type='courier', party_id='aramex',
        name='Aramex', bank_kind='cash' if account == 'cash' else 'bank', bank_id=account,
        bank_name=account, currency=currency, amount='34.50', kind='payment', direction='outgoing',
        allocations=[{'obligation_id': key, 'amount': '34.50'}])
    state['movements'].append(movement)
    return {'id': 'audit:' + str(index), 'action': 'prepaid_shipping_paid', 'after': movement}


def test_hundred_prepaid_prefix_balances_without_full_report(monkeypatch):
    monkeypatch.setattr(activity, 'now', lambda: STAMP)
    state = state_fixture()
    state['movements'] = [receipt(i) for i in range(5000)]
    def forbidden(*args, **kwargs):
        raise AssertionError('prepaid capture must not rebuild the whole report')
    monkeypatch.setattr(service, 'report', forbidden)
    for i in range(100):
        event = prepaid(state, i)
        before = deepcopy(state)
        activity.capture_after_balances(state, event)
        snapshot, = event['financial_activity_snapshots']
        assert snapshot['account_balance_after'] == format(Decimal('10000') - Decimal('34.50') * (i + 1), '.2f')
        assert snapshot['counterparty_balance_after'] == '0.00'
        assert snapshot['counterparty_balance_status'] == 'available'
        assert state == before


def test_two_hundred_mixed_states_match_full_report_capture(monkeypatch):
    monkeypatch.setattr(activity, 'now', lambda: STAMP)
    rng = random.Random(20261109)
    for i in range(200):
        state = state_fixture()
        currency, account = ('USD', 'usd') if i % 4 == 0 else ('SAR', 'cash' if i % 2 else 'bank')
        state['openings']['carrier'] = {'id': 'carrier-opening', 'party_type': 'courier',
            'party_id': 'aramex', 'currency': currency, 'name': 'Opening name',
            'amount': str(rng.randrange(0, 200)), 'direction': rng.choice(['for_us', 'for_party'])}
        for n in range(8):
            key = 'debt:' + str(n)
            state['engine']['obligations'][key] = {'id': key, 'kind': 'shipping',
                'party_type': 'courier', 'party_id': 'aramex', 'currency': currency,
                'name': 'Debt name', 'direction': rng.choice(['receivable', 'payable']),
                'confirmed': str(rng.randrange(0, 50)), 'expected': '20',
                'superseded': n == 7, 'incomplete': i % 11 == 0 and n == 0}
            state['movements'].append(receipt(key, party_type='courier', party_id='aramex',
                currency=currency, bank_id=account, bank_kind='cash' if account == 'cash' else 'bank',
                amount='17.00', direction=rng.choice(['incoming', 'outgoing']),
                kind=rng.choice(['payment', 'correction', 'transfer']),
                allocations=[{'obligation_id': key, 'amount': str(rng.randrange(0, 25))}]))
        if i % 3 == 0:
            state['customer_returns'] = [{'id': 'refund', 'status': 'refunded', 'currency': currency,
                'refund_source_type': 'bank', 'refund_source_id': account,
                'refund_source_name': account, 'amount': '2.30'}]
        if i % 7 == 0:
            # A documented credit identity suppresses excess on the original.
            state['engine']['obligations']['credit:debt:0'] = {
                **state['engine']['obligations']['debt:0'], 'id': 'credit:debt:0', 'confirmed': '4.00'}
        event = prepaid(state, i, currency, account)
        if currency == 'USD' and i % 6 == 0:
            event['after'].update(bank_id='bank', bank_kind='bank', bank_name='bank',
                                  bank_currency='SAR', bank_amount='129.38')
        if i % 13 == 0:
            state['openings'].pop(event['after']['bank_id'], None)
        before = deepcopy(state)
        activity.capture_after_balances(state, event)
        expected_event = {key: deepcopy(value) for key, value in event.items() if key != 'financial_activity_snapshots'}
        # Capture's authoritative fallback, forced only for independent comparison.
        with monkeypatch.context() as patch:
            patch.setattr(activity, '_cash_only_projection', lambda *_: None)
            if hasattr(activity, '_prepaid_projection'):
                patch.setattr(activity, '_prepaid_projection', lambda *_: None)
            activity.capture_after_balances(state, expected_event)
        assert event == expected_event, i
        assert state == before, i


@pytest.mark.parametrize('special', [{'kind': 'supplier'}, {'kind': 'provider'},
                                    {'funding_type': 'prepaid'}, {'derived_credit': True}])
def test_special_carrier_credit_models_use_authoritative_fallback(monkeypatch, special):
    state = state_fixture()
    event = prepaid(state, 0)
    state['engine']['obligations']['special'] = {
        'id': 'special', 'party_type': 'courier', 'party_id': 'aramex', 'currency': 'SAR',
        'kind': 'shipping', 'direction': 'payable', 'confirmed': '5.00', 'business_date': '2026-11-01',
        **special}
    calls = []
    full = service.report
    monkeypatch.setattr(service, 'report', lambda s: (calls.append(True), full(s))[1])
    activity.capture_after_balances(state, event)
    assert calls == [True]


def test_nonfinancial_audit_actions_do_not_project_or_scan_history(monkeypatch):
    from operational_balance_store import audit
    def forbidden(*args, **kwargs):
        raise AssertionError('nonfinancial audit must not calculate balances')
    monkeypatch.setattr(activity, '_affected', forbidden)
    monkeypatch.setattr(service, 'report', forbidden)
    state = state_fixture()
    for action in ('assembly_completed', 'order_review_completed', 'reconciled', 'supplier_received'):
        audit(state, 'staff', action, {}, {'id': action}, None, 'test')
    assert len(state['audit']) == 4
    assert all('financial_activity_snapshots' not in event for event in state['audit'])
