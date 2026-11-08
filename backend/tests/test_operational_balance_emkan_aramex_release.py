"""Isolated release regressions for the merchant-approved refund/shipping rules."""
import asyncio
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_engine import reconcile
from operational_balance_service import refresh, report
from operational_balance_sources import collect_sources
from operational_balance_store import read
from operational_customer_returns import order_view, save_case
from operational_financial_position import build_financial_position
from test_operational_balance_customer_returns import fixture as returns_fixture, command, compensation
from test_operational_balance_integration import run
from test_operational_balance_prepaid_shipping import fixture as prepaid_fixture
from test_operational_balance_sources import HardBoundaryDB
from test_operational_balance_courier_order_transition import history, COLLECTION

START = '2026-11-01T00:30:00+03:00'
NOW = '2026-11-07T12:00:00+00:00'


async def emkan_fixture(db):
    await returns_fixture(db)
    await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
        'raw_by_source.salla_direct.created_at': '2026-11-02T09:00:00+00:00',
        'raw_by_source.salla_direct.updated_at': NOW,
        'raw_by_source.salla_direct.shipping.delivered_at': '2026-11-02T12:00:00+00:00',
        'raw_by_source.salla_direct.payment.method': 'emkan',
        'raw_by_source.salla_direct.payment.paid_at': '2026-11-02T09:01:00+00:00',
    }})
    await db.mz2_provider_fee_policies_v2.update_one({'user_id': 'owner'}, {'$set': {'policies': [{
        'id': 'saved-emkan', 'provider': 'emkan', 'currency': 'SAR', 'status': 'active',
        'effective_from': '2026-11-01', 'percentage': '6.99', 'fixed_amount': '1.50',
        'vat_treatment': 'exclusive', 'vat_rate': '15',
        'refund_fee_treatment': 'retain', 'cancellation_fee_treatment': 'retain',
    }]}})
    await refresh(db, 'owner', clock=NOW)


@pytest.mark.parametrize('amount,items', [
    ('100', [{'id': 'a', 'quantity': 1}]),
    ('300', [{'id': 'a', 'quantity': 2}, {'id': 'b', 'quantity': 1}]),
])
def test_emkan_bank_partial_full_refund_retries_preserve_provider_capture_and_fee(amount, items):
    async def scenario(db):
        await emkan_fixture(db)
        before = await read(db, 'owner')
        original = report(before)['details']['provider_reports']['provider:order1']
        assert original['estimated_fees'] == '25.84'
        assert original['expected_receivable'] == '274.16'
        payload = command(status='refunded', amount=amount, items=items, refunded_at=NOW)
        replies = await asyncio.gather(*[save_case(db, 'owner', 'staff', payload, clock=NOW) for _ in range(4)])
        assert len({r['id'] for r in replies}) == 1
        for _ in range(2):
            await refresh(db, 'owner', clock=NOW)
        state = await read(db, 'owner')
        result = report(state)
        provider = result['details']['provider_reports']['provider:order1']
        assert provider == original
        assert result['summary']['actual_liquidity'] == f'{Decimal(1000)-Decimal(amount):.2f}'
        assert len(state['customer_returns']) == 1
        assert state['customer_returns'][0]['original_provider'] == 'emkan'
        assert state['openings'] == before['openings']
        view = await order_view(db, 'owner', '1001')
        assert view['provider'] == 'emkan' and view['refunded'] == f'{Decimal(amount):.2f}'
    run(scenario)


@pytest.mark.parametrize('amount,net', [('100', '200'), ('300', '0')])
def test_emkan_already_reduced_salla_sale_bank_execution_does_not_reduce_provider_or_sale_twice(amount, net):
    async def scenario(db):
        await emkan_fixture(db)
        original = report(await read(db, 'owner'))['details']['provider_reports']['provider:order1']
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.total': net,
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions': [
                {'payment_method': 'bank', 'amount': {'amount': amount, 'currency': 'SAR'}}],
        }})
        await refresh(db, 'owner', clock=NOW)
        before = report(await read(db, 'owner'))
        assert before['summary']['actual_liquidity'] == '1000.00'
        payload = compensation(status='salla_refund_confirmed', amount=amount, refunded_at=NOW,
                               refund_source_type='bank', refund_source_id='bank')
        first = await save_case(db, 'owner', 'staff', payload, clock=NOW)
        assert await save_case(db, 'owner', 'staff', payload, clock=NOW) == first
        await refresh(db, 'owner', clock=NOW)
        state = await read(db, 'owner')
        after = report(state)
        assert after['summary']['actual_liquidity'] == f'{Decimal(1000)-Decimal(amount):.2f}'
        projection = after['details']['provider_reports']['provider:order1']
        assert projection['expected_receivable'] == original['expected_receivable']
        assert projection['estimated_fees'] == original['estimated_fees']
        assert projection['refunded'] == '0.00'
        view = await order_view(db, 'owner', '1001')
        assert view['total'] == f'{Decimal(net):.2f}' and view['provider'] == 'emkan'
        assert view['salla_refunds_confirmed_source'] == f'{Decimal(amount):.2f}'
        assert len(state['customer_returns']) == 1
    run(scenario)


def aramex_calculate(data, state):
    sources = asyncio.run(collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state))
    return reconcile(state, sources, NOW)


@pytest.mark.parametrize('event_at,eligible', [
    ('2026-10-31T21:29:59+00:00', False),
    ('2026-10-31T21:30:00+00:00', True),
    ('2026-11-01T01:00:00+03:00', True),
])
def test_saved_aramex_rate_uses_completion_cutover_not_old_order_creation(event_at, eligible):
    data, state = prepaid_fixture(at=event_at)
    data['unified_orders'][0]['raw_by_source']['salla_direct']['updated_at'] = NOW
    data['unified_orders'][0]['g47_salla_snapshot']['source_updated_at'] = NOW
    state['started_at'] = START
    state['courier_tariffs'][0].update(effective_from=START, confirmed_at=START)
    before = deepcopy(state)
    result = aramex_calculate(data, state)
    assert [m['amount'] for m in result['movements']] == (['34.50'] if eligible else [])
    assert result['openings'] == before['openings']
    assert result['courier_tariffs'] == before['courier_tariffs']
    if eligible:
        assert result['movements'][0]['bank_id'] == 'salla-cash'
        positions = report(result)
        assert next(r for r in positions['parties'] if r['party_type'] == 'cash')['actual'] == '-34.50'
        assert next(r for r in positions['parties'] if r['party_type'] == 'courier')['expense_paid'] == '34.50'
        data['unified_orders'][0]['raw_by_source']['salla_direct'].update(status='delivered', status_changed_at='2026-11-03T12:00:00+00:00')
        data[COLLECTION] = [history(event_at, to_status='completed')]
        again = aramex_calculate(data, result)
        assert again['movements'] == result['movements']
        assert aramex_calculate(data, again)['movements'] == result['movements']
        detail, = build_financial_position(again)['currencies']['SAR']['deliveries']['rows']
        assert detail['shipping_confirmed'] == detail['shipping_settled'] == '34.50'
