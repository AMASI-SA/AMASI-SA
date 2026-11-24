"""Payment overlap must depend on transaction chronology, not snapshot freshness."""
from copy import deepcopy

import pytest

from operational_provider_history import late_payment_blocks

START = '2026-10-31T21:00:00+00:00'  # November 1 in Riyadh
CREATED = '2026-11-15T15:46:00+00:00'  # 18:46 in Riyadh
NOW = '2026-11-20T12:00:00+00:00'


def fixture(provider='paypal', currency='SAR'):
    page = dict(owner_id='owner', order_id='one', order_number='300245236', provider=provider,
                currency=currency, gross='100.00', paid='100.00', remaining='0.00',
                source='canonical_salla_order_page')
    payment = dict(provider=provider, currency=currency, gross='100.00', order_page_balance=page)
    order = dict(id='one', order_number='300245236', currency=currency, created_at=CREATED,
                 updated_at=CREATED, payment=payment)
    state = dict(owner_id='owner', started_at=START, engine=dict(as_of='2026-11-19T12:00:00+00:00'),
                 openings={}, movements=[dict(party_type='provider', party_id=provider, currency=currency,
                     kind='settlement', direction='incoming', amount='50.00', allocations=[],
                     occurred_at='2026-11-10T12:00:00+00:00')])
    return state, order, payment


@pytest.mark.parametrize('provider', ['paypal','emkan','salla','tabby','tamara'])
@pytest.mark.parametrize('currency', ['SAR','KWD'])
def test_precreation_payout_does_not_hold_post_start_order_seen_after_snapshot(provider, currency):
    state, order, payment = fixture(provider,currency)
    before = deepcopy(state)
    assert not late_payment_blocks(state, order, payment, through=NOW)
    assert state == before


@pytest.mark.parametrize('link', ['order_id','order_number','allocation','credit'])
def test_explicit_order_link_still_blocks_even_with_earlier_payout_date(link):
    state, order, payment = fixture()
    movement = state['movements'][0]
    if link in ('order_id','order_number'):
        movement[link] = 'one' if link == 'order_id' else order['order_number']
    else:
        movement['allocations'] = [dict(obligation_id='credit:provider:one' if link=='credit' else 'provider:one', amount='50.00')]
    assert late_payment_blocks(state, order, payment, through=NOW)


@pytest.mark.parametrize('date', [None, CREATED, '2026-11-16T12:00:00+00:00','invalid'])
def test_payout_without_proven_precreation_date_keeps_review(date):
    state, order, payment = fixture()
    state['movements'][0]['occurred_at'] = date
    assert late_payment_blocks(state, order, payment, through=NOW)


def test_prestart_order_is_not_released_by_earlier_payout():
    state, order, payment = fixture()
    order['created_at'] = '2026-10-30T12:00:00+00:00'
    assert late_payment_blocks(state, order, payment, through=NOW)


def test_late_native_capture_is_booked_once_without_rewriting_old_payout():
    from operational_balance_engine import reconcile
    state, order, payment = fixture()
    payment['captures'] = [{'id': 'late-native-capture', 'amount': '100.00',
                            'captured_at': '2026-11-16T12:00:00+00:00'}]
    state.update(status='active', revision=1)
    before = deepcopy(state)
    result = reconcile(state, {'orders':[order]}, NOW)
    assert result['engine']['obligations']['provider:one']['confirmed'] == '100.00'
    assert result['movements'] == before['movements']
    assert result['openings'] == before['openings']
    repeated = reconcile(result, {'orders':[order]}, NOW)
    assert repeated == result
    assert state == before

