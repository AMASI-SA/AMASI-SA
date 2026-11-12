"""Completion authority stays separate from delivery, cash, COD and history."""
import asyncio
from copy import deepcopy
from decimal import Decimal

import pytest
from mongomock_motor import AsyncMongoMockClient

from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources
from operational_completed_shipping_admission import APPROVALS, MARKER, approval_record, review
from operational_completed_shipping_sources import review_candidate
from operational_balance_store import digest
from test_operational_balance_delivery_cutover import START, NOW, DELIVERED
from test_operational_balance_prepaid_shipping import fixture, calculate, COMPLETED
from test_operational_balance_courier_order_transition import history, COLLECTION
from test_operational_completed_shipping_expansion import admitted, without_label


def deferred(*, old=True, activated=True):
    data, state = fixture(old=old)
    state['courier_tariffs'][0]['payment_mode'] = 'deferred'
    state['courier_tariffs'][0].pop('cash_account')
    state['courier_tariffs'][0]['terms']['cod_fee_fixed_per_order'] = '2'
    if activated:
        admitted(state)
    return data, state


def delivered(data):
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    raw.update(status='delivered', status_changed_at=DELIVERED)
    raw['shipping'].update(status='delivered', delivered_at=DELIVERED)
    raw['shipments'][0].update(status='delivered', delivered_at=DELIVERED)
    source['salla_shipping_current']['status'] = 'delivered'
    source['order_status_slug'] = 'delivered'
    data[COLLECTION] = [history(COMPLETED, to_status='completed')]


async def database(data):
    db = AsyncMongoMockClient()['synthetic_completed_shipping']
    for name, docs in data.items():
        if docs:
            await db[name].insert_many(deepcopy(docs))
    return db


@pytest.mark.parametrize('old', [False, True])
def test_deferred_completed_work_books_only_base_with_no_delivery_cash_or_cod(old):
    data, state = deferred(old=old)
    without_label(data)
    result = calculate(data, state)
    charge = result['engine']['obligations']['shipping-completed:100']
    assert charge['confirmed'] == '34.50' and charge['expected'] == '0.00'
    assert charge['payment_mode'] == 'deferred' and charge['completed_at'] == COMPLETED
    assert result['movements'] == [] and result['openings'] == state['openings']
    assert not any(key.startswith(('shipping:', 'delivery_event:', 'cod_collection:')) for key in result['engine']['facts'])
    assert not any(Decimal(row['confirmed']) for row in result['engine']['obligations'].values() if row['kind'] == 'cod')
    assert calculate(data, result) == result


@pytest.mark.parametrize('same_run', [False, True])
def test_later_delivery_adds_only_cod_commission_and_custody_without_duplicate_base(same_run):
    data, original = deferred()
    state = original if same_run else calculate(data, original)
    delivered(data)
    result = calculate(data, state)
    obligations = result['engine']['obligations']
    assert obligations['shipping-completed:100']['confirmed'] == '34.50'
    assert obligations['shipping:100:cod_commission']['confirmed'] == '2.30'
    assert sum(Decimal(row['confirmed']) for row in obligations.values() if row['kind'] == 'shipping') == Decimal('36.80')
    assert 'shipping:100:base_shipping' not in obligations
    assert 'shipping_fee:100' not in result['engine']['facts']
    assert obligations['cod:100']['confirmed'] == '200.00'
    assert result['movements'] == [] and result['openings'] == original['openings']
    replay = calculate(data, result)
    assert replay['engine']['facts'] == result['engine']['facts']
    assert replay['engine']['obligations'] == obligations


def test_pending_historical_base_does_not_bypass_review_through_delivery():
    data, state = deferred(activated=False)
    delivered(data)
    result = calculate(data, state)
    assert result['engine'][MARKER]['activated_at'] == NOW
    assert 'shipping-completed:100' not in result['engine']['obligations']
    assert not result['movements']
    assert result['engine']['obligations']['shipping:100:cod_commission']['confirmed'] == '2.30'
    assert result['engine']['obligations']['cod:100']['confirmed'] == '200.00'
    assert 'shipping:100:base_shipping' not in result['engine']['obligations']
    assert any(row['code'] == 'operational_shipping_completion_history_review_required' for row in result['engine']['issues'])
    async def scenario():
        db = await database(data)
        event, blocks, _ = await review_candidate(db, 'owner', result, '10', NOW)
        assert not blocks and not review(result, event)['issues']
        result[APPROVALS] = {digest('10'): approval_record(result, event, actor='owner', approved_at=NOW,
            evidence_ref='reviewed opening and payouts', expected_preview_hash=review(result, event)['preview_hash'])}
        sources = await collect_sources(db, 'owner', START, NOW, operational_state=result)
        final = reconcile(result, sources, NOW)
        assert final['engine']['obligations']['shipping-completed:100']['confirmed'] == '34.50'
        assert final['engine']['obligations']['shipping:100:cod_commission']['confirmed'] == '2.30'
        assert final['movements'] == []
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['prepaid', 'deferred'])
@pytest.mark.parametrize('activated', [False, True])
def test_native_overlap_blocks_both_accepted_events_and_preview_candidates(mode, activated):
    data, state = fixture() if mode == 'prepaid' else deferred(activated=False)
    without_label(data)
    if activated:
        admitted(state)
    async def scenario():
        db = await database(data)
        await db.mz2_shipping_native_events_v2.insert_one({'user_id': 'owner', 'kind': 'fee',
            'order_number': '10', 'party_type': 'courier', 'party_id': 'aramex'})
        before = deepcopy(state)
        sources = await collect_sources(db, 'owner', START, NOW, operational_state=state)
        assert state == before and ('completed_shipping_admission' in state['engine']) == activated
        assert sources['completed_shipping_events'] == sources['prepaid_shipping_events'] == []
        assert sources['completed_shipping_native_conflicts']['10']
        diagnostic = next(row for row in sources['issues'] if row['code'].endswith('native_overlap'))
        public = diagnostic['completed_shipping_review']
        assert (public['party_type'], public['party_id'], public['order_id'], public['currency']) == ('courier', 'aramex', '100', 'SAR')
        assert 'contract_snapshot' not in str(sources['issues']) and 'cash_account' not in str(sources['issues'])
        event, blocks, applied = await review_candidate(db, 'owner', state, '10', NOW)
        assert event and blocks and applied is None
        result = reconcile(state, sources, NOW)
        assert result['movements'] == [] and not result['engine']['obligations']
    asyncio.run(scenario())


def test_single_order_preview_never_reads_financial_product_or_advertising_sources():
    data, state = deferred(activated=False)
    without_label(data)
    async def scenario():
        db = await database(data)
        reads = []
        allowed = {'unified_orders', 'mz2_shipping_setup_v2', 'store_delivery_assignments',
                   'mz2_financial_accounts', COLLECTION, 'mz2_shipping_accounting_events', 'mz2_shipping_native_events_v2'}
        class ReadOnly:
            def __getitem__(self, name):
                assert name in allowed
                real = db[name]
                class Collection:
                    def find(self, query, projection):
                        assert query['user_id'] == 'owner'
                        if name == 'unified_orders':
                            assert len(query) > 1  # No all-history order scan.
                        reads.append((name, deepcopy(query)))
                        return real.find(query, projection)
                return Collection()
        before = deepcopy(state)
        event, issues, applied = await review_candidate(ReadOnly(), 'owner', state, '10', NOW)
        assert event['amount'] == '34.50' and not issues and applied is None
        assert state == before and MARKER not in state['engine']
        assert len(reads) < 12
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['same_id', 'same_number', 'foreign_number', 'native_fee', 'shared_waybill'])
def test_one_order_preview_rejects_conflicting_evidence(change):
    data, state = deferred(activated=False)
    async def scenario():
        db = await database(data)
        duplicate = deepcopy(data['unified_orders'][0])
        raw = duplicate['raw_by_source']['salla_direct']
        if change == 'same_id':
            duplicate['order_number'] = raw['reference_id'] = 'different'
        elif change == 'same_number':
            raw['id'] = 'another-id'
        elif change == 'foreign_number':
            await db.unified_orders.delete_many({'user_id': 'owner'})
            duplicate['user_id'] = 'other'
        elif change == 'shared_waybill':
            duplicate['order_number'] = raw['reference_id'] = 'different'
            raw['id'] = 'another-id'
        else:
            await db.mz2_shipping_native_events_v2.insert_one({'user_id': 'owner', 'kind': 'fee', 'order_number': '10'})
        if change != 'native_fee':
            await db.unified_orders.insert_one(duplicate)
        if change == 'shared_waybill':
            with pytest.raises(ValueError):
                await review_candidate(db, 'owner', state, '10', NOW)
        else:
            event, issues, _ = await review_candidate(db, 'owner', state, '10', NOW)
            assert issues and (event is None or change == 'native_fee')
    asyncio.run(scenario())


def test_visible_calculation_cancellation_escapes_without_a_partial_result(monkeypatch):
    import operational_demand_refresh as demand
    _, state = deferred()
    original = deepcopy(state)
    calls = []
    def checkpoint():
        calls.append(True)
        if len(calls) == 6:
            raise asyncio.CancelledError
    monkeypatch.setattr(demand, 'calculation_checkpoint', checkpoint)
    sources = {'recurring': [{'id': str(index), 'due_at': NOW, 'party_id': 'vendor',
                             'currency': 'SAR', 'amount': '10.00'} for index in range(100)]}
    with pytest.raises(asyncio.CancelledError):
        reconcile(state, sources, NOW)
    assert len(calls) == 6 and state == original
