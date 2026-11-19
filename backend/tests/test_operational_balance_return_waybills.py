from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from operational_balance_sources import instant
from operational_customer_returns import shipping_obligations
from operational_delivery_register import history_version
from operational_return_waybills import MISSING, PROOF, collect, native_proof, project

START = '2026-11-01T00:00:00+03:00'
ISSUED = '2026-11-06T13:00:00+03:00'
COMPLETED = '2026-11-07T22:10:00+00:00'
NOW = '2026-11-12T09:00:00+00:00'
LEG = dict(type='return', status='returned', id='return-1', shipping_number='RET1', order_id='oid',
           order_reference_id='123', courier_name='iMile للتوصيل', created_at=ISSUED)
HISTORY = dict(type='activity', status='مسترجع', customized={'slug': 'restored'}, note='مسترجع',
               created_by={'name': 'iMile Delivery'}, created_at=COMPLETED)
SETUP = {'couriers': [dict(courier_key='carrier', salla_carrier_keys=['iMile للتوصيل'],
                           confirmed_by='owner', confirmed_at=START)],
         'contracts': [dict(id='v1', status='approved', party_type='courier', party_id='carrier', context='delivery',
                            confirmed_by='owner', confirmed_at=START, effective_from=START, delivery_fee='15',
                            vat_included=False, vat_percent='15', cod_fixed_fee='22', cod_percent='2')]}
RAW = {'id': 'oid', 'reference_id': 123, 'status': 'restored', 'updated_at': COMPLETED}


def observe(legs, history=(), *, setup=SETUP):
    out = {'issues': []}
    proof = native_proof(legs, history, 'oid', '123')
    collect(out, {PROOF: dict(version=history_version(RAW), order_id='oid', legs=proof)},
            RAW, setup, 'owner', instant(START), instant(NOW))
    return out


def publish(out, rows=None):
    rows = {} if rows is None else rows
    issues = []
    project(rows, out.get('return_waybills', []), NOW, lambda *args: issues.append(args))
    return rows, issues


def test_confirm_once_from_return_leg_and_native_courier_status():
    outbound = {**LEG, 'type': 'shipment', 'id': 'out-1'}
    out = observe([outbound, LEG], [{**HISTORY, 'note': 'inventory restored'}, HISTORY])
    rows = {'shipping:oid:base_shipping': {'confirmed': '17.25'}}
    publish(out, rows)
    assert len(rows) == 2 and rows['shipping:oid:base_shipping'] == {'confirmed': '17.25'}
    result = next(value for key, value in rows.items() if key.startswith('salla-return'))
    assert (result['confirmed'], result['expected'], result['business_date']) == ('17.25', '0.00', '2026-11-08')
    assert result['reference'] == 'RET1' and result['recorded_at'] == NOW
    frozen = deepcopy(rows)
    publish(out, rows)
    publish(observe([{**LEG, 'status': 'created'}]), rows)
    assert rows == frozen


@pytest.mark.parametrize('history', [[], [{**HISTORY, 'note': 'refund confirmed'}],
                                     [{**HISTORY, 'created_by': {'name': 'supplier'}}]])
def test_missing_physical_completion_stays_expected(history):
    out = observe([LEG], history)
    assert MISSING in [row['code'] for row in out['issues']]
    row, = publish(out)[0].values()
    assert (row['confirmed'], row['expected']) == ('0.00', '17.25')


def test_pending_then_cancelled_remains_zero_on_replay():
    rows, _ = publish(observe([{**LEG, 'status': 'created'}]))
    publish(observe([{**LEG, 'status': 'cancelled'}]), rows)
    publish(observe([{**LEG, 'status': 'in_transit'}]), rows)
    assert len(rows) == 1
    record, = rows.values()
    assert record['return_cancelled'] and record['expected'] == record['confirmed'] == '0.00'


def test_multiple_returns_require_independent_completion_clocks():
    other = {**LEG, 'id': 'return-2', 'shipping_number': 'RET2'}
    out = observe([LEG, other], [HISTORY])
    rows, _ = publish(out)
    assert all(row['confirmed'] == '0.00' for row in rows.values()) and len(rows) == 2
    publish(observe([{**LEG, 'returned_at': COMPLETED}, other], [HISTORY]), rows)
    assert sorted(row['confirmed'] for row in rows.values()) == ['0.00', '17.25']


def test_ambiguous_or_foreign_leg_cannot_post():
    with pytest.raises(ValueError):
        observe([{**LEG, 'order_id': 'foreign'}], [HISTORY])
    assert publish(observe([LEG, {**LEG, 'id': 'other-id'}]))[0] == {}
    assert publish(observe([LEG], [HISTORY], setup={**SETUP, 'contracts': SETUP['contracts'] * 2}))[0] == {}
    assert not publish(observe([{**LEG, 'type': 'shipment'}]))[0]
    assert not publish(observe([{**LEG, 'status': 'cancelled', 'created_at': '2025-01-01T00:00:00Z'}]))[0]


@pytest.mark.parametrize('manual_first', [False, True])
def test_manual_reference_and_automatic_leg_share_settlement_identity(manual_first):
    state = {'engine': {'obligations': {}}, 'customer_returns': []}
    rows = state['engine']['obligations']
    case = dict(id='case', order_id='oid', order_number='123', created_at=ISSUED,
                shipping=dict(kind='courier', id='carrier', name='company', reference='ret1', status='pending', amount='17.25'))
    if manual_first:
        state['customer_returns'].append(case)
        shipping_obligations(state)
    else:
        publish(observe([LEG], [HISTORY]), rows)
        state['customer_returns'].append(case)
    key, = rows
    for _ in range(2):
        shipping_obligations(state)
        publish(observe([LEG], [HISTORY]), rows)
    assert list(rows) == [key]
    assert rows[key]['confirmed'] == '17.25' and rows[key]['return_case_id'] == 'case'


def test_acquire_uses_bounded_native_reads_then_fences_source_revision(monkeypatch):
    import asyncio
    from salla_integration import return_waybill_sync as sync
    from types import SimpleNamespace
    collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value=dict(_id=1, order_number='123',
                raw_by_source={'salla_direct': RAW})), update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
    pages = AsyncMock(side_effect=[[LEG], [HISTORY]])
    monkeypatch.setattr(sync, '_complete_pages', pages)
    result = asyncio.run(sync.acquire(SimpleNamespace(unified_orders=collection), 'owner',
                                      [{'source_id': 'oid', 'order_number': '123'}], limit=1))
    assert result == {'checked': 1, 'saved': 1}
    query, update = collection.update_one.await_args.args
    assert query['user_id'] == 'owner' and query[sync.RAW] == RAW
    leg, = update['$set'][PROOF]['legs']
    assert instant(leg['returned_at']) == instant(COMPLETED)
    assert [call.args[2] for call in pages.await_args_list] == ['/shipments', '/orders/oid/histories']


def test_frozen_same_courier_sar_base_without_cod_for_legacy_contract():
    raw = {**RAW, 'shipments': [{**LEG, 'returned_at': COMPLETED}]}
    engine = {'facts': {'shipping:oid': {'id': 'carrier', 'party_type': 'courier', 'cost': '30'},
                        'shipping:oid:base_shipping': {'cost': '12.34'}}}
    out = {'issues': []}
    collect(out, {}, raw, {**SETUP, 'contracts': []}, 'owner', instant(START), instant(NOW), engine=engine)
    assert publish(out)[0].popitem()[1]['confirmed'] == '12.34'
    engine['facts']['shipping:oid:base_shipping']['currency'] = 'USD'
    out = {'issues': []}
    collect(out, {}, raw, {**SETUP, 'contracts': []}, 'owner', instant(START), instant(NOW), engine=engine)
    assert publish(out)[0] == {}


def test_real_operational_refresh_records_only_same_owner_courier_freight():
    from test_operational_balance_integration import run, started
    from operational_balance_service import refresh, report
    from operational_balance_store import read
    async def scenario(db):
        await started(db)
        await db.mz2_shipping_setup_v2.insert_one({'user_id': 'owner', **deepcopy(SETUP)})
        doc = {**RAW, 'created_at': ISSUED, 'updated_at': COMPLETED, 'total': '100.00', 'currency': 'SAR',
               'shipments': [{**LEG, 'returned_at': COMPLETED}, {**LEG, 'type': 'shipment', 'id': 'forward'}]}
        for owner in ('owner', 'other'):
            await db.unified_orders.insert_one({'user_id': owner, 'order_number': '123', 'raw_by_source': {'salla_direct': doc}})
        for _ in range(2):
            await refresh(db, 'owner', clock=NOW)
        state = await read(db, 'owner')
        reverse, = [r for r in report(state)['obligations'] if r.get('salla_return_shipment_id')]
        assert (reverse['confirmed'], reverse['business_date'], reverse['reference']) == ('17.25', '2026-11-08', 'RET1')
        assert not state.get('customer_returns') and not state.get('movements')
    run(scenario)
