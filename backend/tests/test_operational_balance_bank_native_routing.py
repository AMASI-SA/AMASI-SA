"""Native bank tenders use explicit account routing and independently proven money."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_bank_orders import native_paid_amount, resolve, transfer_bank
from operational_bank_catalog import bank_identities
from test_operational_balance_sources import DB, row, run

STAMP = '2026-11-03T03:00:07+00:00'
AS_OF = '2026-11-04T12:00:00+00:00'


def document(paid='474.99', remaining=None, total='474.99'):
    raw = {
        'id': 'native-order', 'reference_id': 'order-17',
        'created_at': '2026-11-03T01:00:00+00:00', 'updated_at': STAMP,
        'currency': 'SAR', 'total': {'amount': total, 'currency': 'SAR'},
        'status': {'slug': 'under_review'}, 'payment_method': 'bank',
        'bank': {'bank_id': 'native-bank', 'bank_name': 'مصرف الراجحي'},
        'payment_methods': [{'payment_method': 'bank', 'amount': paid, 'transaction_reference': None}],
        'payment_actions': {
            'refund_action': {'paid_amount': {'amount': paid, 'currency': 'SAR'}, 'refund_amount': {'amount': '0.00', 'currency': 'SAR'}},
            'remaining_action': {'paid_amount': {'amount': paid, 'currency': 'SAR'},
                                 'remaining_amount': None if remaining is None else {'amount': remaining, 'currency': 'SAR'},
                                 'has_remaining_amount': remaining is not None and Decimal(remaining) > 0},
        },
    }
    source = {'user_id': 'owner', 'salla_order_id': raw['id'], 'order_number': raw['reference_id'],
              'g47_salla_snapshot': {'component_pending': True, 'source_updated_at': STAMP}}
    return raw, source


def paid_amount(raw, source):
    return native_paid_amount(raw, source, owner='owner', order_number='order-17',
                              provider_order_id='native-order', currency='SAR', total=raw['total'], as_of=AS_OF)


def catalog():
    return [row(id=f'account:{code}', name=f'حساب تشغيل {index}', account_type='bank', status='active',
                currency='SAR', bank_transfer_aliases=[f'bank_{code}'])
            for index, code in enumerate(('rajhi', 'inma', 'ahli'), 1)]


@pytest.mark.parametrize(('name', 'bank'), [('مصرف الراجحي', 'rajhi'), ('مصرف الإنماء', 'inma'), ('البنك الأهلي السعودي', 'ahli')])
def test_native_bank_routes_by_exact_saved_code_instead_of_account_display_name(name, bank):
    raw, _ = document()
    raw['bank']['bank_name'] = name
    candidates = catalog()
    before = deepcopy(candidates)
    assert resolve({}, 'owner', 'bank', 'SAR', raw=raw, banks=candidates)['id'] == f'account:{bank}'
    assert candidates == before


@pytest.mark.parametrize('fault', ['label', 'missing-bank', 'duplicate', 'owner', 'inactive', 'currency', 'malformed-alias'])
def test_native_bank_does_not_guess_or_accept_ambiguous_routing(fault):
    raw, _ = document()
    candidates = catalog()
    if fault == 'label':
        raw['bank']['bank_name'] = 'حساب باسم مصرف الراجحي غير محدد'
    elif fault == 'missing-bank':
        raw.pop('bank')
    elif fault == 'duplicate':
        candidates.append({**candidates[0], 'id': 'account:second-rajhi'})
    elif fault == 'owner':
        candidates[0]['user_id'] = 'other'
    elif fault == 'inactive':
        candidates[0]['status'] = 'inactive'
    elif fault == 'currency':
        candidates[0]['currency'] = 'USD'
    else:
        candidates[0]['bank_transfer_aliases'] = 'bank_rajhi'
    with pytest.raises(ValueError, match='operational_order_bank_binding_incomplete'):
        resolve({}, 'owner', 'bank', 'SAR', raw=raw, banks=candidates)


def test_bank_routing_catalog_projects_identity_and_explicit_aliases_only():
    db = DB({'accounts': [row(id='rajhi', name='حساب التشغيل', account_type='bank', status='active', currency='SAR',
                              bank_transfer_aliases=['bank_rajhi'], opening_balance=999, current_balance=888, iban='private')],
             'mz2_financial_accounts': [row(id='native', name='حساب ميزان', account_type='bank', status='active', currency='SAR')]})
    before = deepcopy(db.data)
    ordinary = run(bank_identities(db, 'owner'))
    routed = run(bank_identities(db, 'owner', include_routing=True))
    assert all('bank_transfer_aliases' not in bank for bank in ordinary)
    bank = next(bank for bank in routed if bank['id'] == 'account:rajhi')
    assert bank['bank_transfer_aliases'] == ['bank_rajhi']
    assert not {'opening_balance', 'current_balance', 'iban'} & set(bank)
    assert db.data == before


@pytest.mark.parametrize(('paid', 'remaining', 'total'), [('474.99', None, '474.99'), ('45.50', '54.50', '100.00')])
def test_native_bank_uses_paid_amount_with_exact_pending_fulfillment_watermark(paid, remaining, total):
    raw, source = document(paid, remaining, total)
    before = deepcopy((raw, source))
    assert paid_amount(raw, source) == Decimal(paid)
    assert paid_amount(raw, source) == Decimal(paid)
    assert (raw, source) == before


@pytest.mark.parametrize('fault', ['zero', 'missing-leg', 'amount', 'currency', 'owner', 'order-id', 'reference', 'watermark',
                                  'future', 'cancelled', 'refund', 'method', 'duplicate-bank', 'stale-status'])
def test_native_bank_money_requires_current_coherent_positive_source_evidence(fault):
    raw, source = document()
    if fault == 'zero':
        raw, source = document('0.00', '474.99')
    elif fault == 'missing-leg':
        raw.pop('payment_methods')
    elif fault == 'amount':
        raw['payment_methods'][0]['amount'] = '100.00'
    elif fault == 'currency':
        raw['payment_methods'][0]['currency'] = 'USD'
    elif fault == 'owner':
        source['user_id'] = 'other'
    elif fault == 'order-id':
        source['salla_order_id'] = 'different'
    elif fault == 'reference':
        source['order_number'] = 'different'
    elif fault == 'watermark':
        source['g47_salla_snapshot']['source_updated_at'] = '2026-11-02T03:00:07+00:00'
    elif fault == 'future':
        raw['updated_at'] = source['g47_salla_snapshot']['source_updated_at'] = '2026-11-05T03:00:07+00:00'
    elif fault == 'cancelled':
        raw['status']['slug'] = 'cancelled'
    elif fault == 'refund':
        raw['payment_actions']['refund_action']['refund_amount']['amount'] = '1.00'
    elif fault == 'method':
        raw['payment'] = {'method': 'mada'}
    elif fault == 'duplicate-bank':
        raw['payment_methods'].append(deepcopy(raw['payment_methods'][0]))
    else:
        source['last_salla_direct_status_source_updated_at'] = '2026-11-03T04:00:00+00:00'
    with pytest.raises(ValueError):
        paid_amount(raw, source)


def test_explicit_wallet_split_credits_only_the_bank_leg():
    raw, source = document('100.00', None, '100.00')
    raw['payment_methods'] = [
        {'payment_method': 'bank', 'amount': '85.00', 'transaction_reference': 'bank-proof'},
        {'payment_method': 'customer_wallet', 'amount': '15.00', 'transaction_reference': 'wallet-proof'},
    ]
    assert paid_amount(raw, source) == Decimal('85.00')
    for change in ({'amount': '16.00'}, {'transaction_reference': None}, {'transaction_reference': 'bank-proof'},
                   {'payment_method': 'bank'}, {'payment_method': 'mada'}, {'currency': 'USD'}):
        broken = deepcopy(raw)
        broken['payment_methods'][1].update(change)
        with pytest.raises(ValueError):
            paid_amount(broken, source)


def test_bare_bank_is_recognized_without_matching_other_words():
    assert transfer_bank('bank') == (True, '')
    assert transfer_bank('تحويل بنكي مصرف تجريبي') == (True, 'مصرف تجريبي')
    assert transfer_bank('banking')[0] is False
    assert transfer_bank('bank_rajhi')[0] is False
