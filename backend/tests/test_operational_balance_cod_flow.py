"""COD source -> reducer -> real local Mongo movement/report parity regressions."""
import pytest
from test_operational_balance_integration import run, started, movement, START, NOW
from test_operational_balance_source_fixes import shipping_data
from operational_balance_service import refresh, report, create_movement
from operational_balance_store import read


async def native_order(db, method=None):
    data = shipping_data(delivered_shipment=method is None, payment={'method': 'cod'}, paid_amount='100', status='delivered')
    if method:
        rate = data['mz2_shipping_setup_v2'][0]['contracts'][0]
        rate.update(party_type='store_driver', party_id='driver')
        data['store_drivers'] = [{'user_id':'owner','id':'driver','name':'Driver','status':'active'}]
        data['store_delivery_assignments'] = [{'user_id':'owner','id':'assignment','order_number':'10','driver_id':'driver','status':'delivered'}]
        data['store_delivery_collections'] = [{'user_id':'owner','id':'collection','assignment_id':'assignment','order_number':'10',
            'driver_id':'driver','amount':'200','amount_source':'unified_orders.remaining_amount','payment_method':method,'cod_custody_amount':'200' if method=='cash' else '0','collected_at':NOW}]
    for collection, documents in data.items():
        await db[collection].insert_many(documents)


@pytest.mark.parametrize('method', [None, 'cash'])
def test_cod_native_remaining_or_cash_custody_settles_once_without_double_bank(method):
    async def scenario(db):
        await started(db)
        await native_order(db, method)
        before = report(await refresh(db,'owner',clock=NOW))
        if method:
            # The legacy collection contract does not invent the canonical
            # order identity required by the newer delivery-status proof.
            assert 'delivery_event:100' not in before['details']['facts']
        key, cod = next(iter(before['details']['cod_reports'].items()))
        assert (cod['gross'],cod['collected'],cod['customer_outstanding'],cod['confirmed_custody'],cod['settled'],cod['outstanding']) == ('300','300' if method else '100','0' if method else '200','200.00','0.00','200.00')
        assert before['summary']['actual_liquidity']=='1000.00'
        payment=movement()
        payment.update(party_type='store_driver' if method else 'courier',party_id='driver' if method else 'carrier',
            direction='incoming',kind='collection',amount='150',reference='native-cod-transfer',
            allocations=[{'obligation_id':key,'amount':'150'}])
        first=await create_movement(db,'owner','owner',payment,clock=NOW)
        assert await create_movement(db,'owner','owner',payment,clock=NOW)==first
        final=report(await refresh(db,'owner',clock=NOW))
        assert final['summary']['actual_liquidity']=='1150.00'
        assert (final['details']['cod_reports'][key]['settled'], final['details']['cod_reports'][key]['outstanding'])==('150.00','50.00')
        assert len((await read(db,'owner'))['movements'])==1
    run(scenario)


@pytest.mark.parametrize('method',['bank_transfer','card_terminal'])
def test_driver_non_cash_proof_creates_no_driver_custody_or_implicit_bank_cash(method):
    async def scenario(db):
        await started(db)
        await native_order(db,method)
        result=report(await refresh(db,'owner',clock=NOW))
        assert 'delivery_event:100' not in result['details']['facts']
        cod=next(iter(result['details']['cod_reports'].values()))
        assert cod['payment_method']==method
        assert (cod['custody_amount'],cod['confirmed_custody'],cod['settled'],cod['outstanding'])==('0','0.00','0.00','0.00')
        assert result['summary']['actual_liquidity']=='1000.00'
        assert (await read(db,'owner'))['movements']==[]
    run(scenario)

def test_native_missing_commission_keeps_cod_and_verified_shipping_then_completes_once():
    from test_operational_balance_source_fixes import rich_rate
    from decimal import Decimal
    async def scenario(db):
        await started(db)
        data=shipping_data(rich_rate(), delivered_shipment=True, payment={'method':'cod'},paid_amount='100',status='delivered')
        setup=data['mz2_shipping_setup_v2'][0]
        next(e for e in setup['contract_evidence'] if e['purpose']=='commission_tax')['state']='revoked'
        for collection,documents in data.items():
            await db[collection].insert_many(documents)
        first=report(await refresh(db,'owner',clock=NOW))
        assert next(iter(first['details']['cod_reports'].values()))['outstanding']=='200.00'
        assert sum(Decimal(r['confirmed']) for r in first['obligations'] if r['kind']=='shipping')==Decimal('10')
        assert any(i.get('component')=='cod_commission' for i in first['issues'])
        next(e for e in setup['contract_evidence'] if e['purpose']=='commission_tax')['state']='approved'
        await db.mz2_shipping_setup_v2.replace_one({'user_id':'owner'},setup)
        final=report(await refresh(db,'owner',clock=NOW))
        assert sum(Decimal(r['confirmed']) for r in final['obligations'] if r['kind']=='shipping')==Decimal('12')
        assert final['summary']['actual_liquidity']=='1000.00'
        assert report(await refresh(db,'owner',clock=NOW))==final
    run(scenario)

@pytest.mark.parametrize('method',['cash','bank_transfer','card_terminal'])
def test_late_native_collection_after_delivery_confirms_only_cash_once(method):
    async def scenario(db):
        await started(db)
        await native_order(db,method)
        collection=await db.store_delivery_collections.find_one({'user_id':'owner'})
        await db.store_delivery_collections.delete_one({'_id':collection['_id']})
        incomplete=report(await refresh(db,'owner',clock=NOW))
        assert next(iter(incomplete['details']['cod_reports'].values()))['confirmed_custody']=='0.00'
        assert any(i['code']=='driver_collection_custody_evidence_incomplete' for i in incomplete['issues'])
        await db.store_delivery_collections.insert_one(collection)
        complete=report(await refresh(db,'owner',clock=NOW))
        cod=next(iter(complete['details']['cod_reports'].values()))
        assert cod['confirmed_custody']==('200.00' if method=='cash' else '0.00')
        assert cod['evidence_ids']==['collection']
        assert complete['summary']['actual_liquidity']=='1000.00'
        assert report(await refresh(db,'owner',clock=NOW))==complete
        assert len([k for k in (await read(db,'owner'))['engine']['facts'] if k.startswith('cod_collection:')])==1
    run(scenario)

def test_initial_invalid_cod_source_can_be_corrected_after_delivery():
    async def scenario(db):
        await started(db)
        await native_order(db)
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.remaining_amount':'250'}})
        bad=report(await refresh(db,'owner',clock=NOW))
        assert any(i['code']=='cod_collection_contract_incomplete' for i in bad['issues'])
        assert sum(float(r['confirmed']) for r in bad['obligations'] if r['kind']=='shipping') == 10
        assert any(i.get('component')=='cod_commission' for i in bad['issues'])
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.remaining_amount':'200'}})
        fixed=report(await refresh(db,'owner',clock=NOW))
        assert next(iter(fixed['details']['cod_reports'].values()))['confirmed_custody']=='200.00'
        assert fixed['summary']['actual_liquidity']=='1000.00'
        assert report(await refresh(db,'owner',clock=NOW))==fixed
    run(scenario)


@pytest.mark.parametrize('method',['bank_transfer','card_terminal'])
def test_customer_non_cash_confirmation_updates_proof_without_driver_or_bank_effect(method):
    async def scenario(db):
        await started(db)
        await native_order(db,method)
        pending=report(await refresh(db,'owner',clock=NOW))
        assert next(iter(pending['details']['cod_reports'].values()))['collected']=='100'
        await db.store_delivery_collections.update_one({'user_id':'owner'},{'$set':{'payment_confirmed':True}})
        confirmed=report(await refresh(db,'owner',clock=NOW))
        cod=next(iter(confirmed['details']['cod_reports'].values()))
        assert (cod['collected'],cod['customer_outstanding'],cod['confirmed_custody'])==('300','0','0.00')
        assert confirmed['summary']['actual_liquidity']=='1000.00'
        assert report(await refresh(db,'owner',clock=NOW))==confirmed
    run(scenario)


@pytest.mark.parametrize('fault', ['ambiguous', 'foreign', 'wrong_order_id', 'reused_assignment_id'])
def test_legacy_driver_compatibility_never_admits_an_ambiguous_or_foreign_assignment(fault):
    async def scenario(db):
        await started(db)
        await native_order(db, 'cash')
        if fault in {'ambiguous', 'reused_assignment_id'}:
            await db.store_delivery_assignments.insert_one({
                'user_id': 'owner', 'id': 'other-assignment' if fault == 'ambiguous' else 'assignment',
                'order_number': '10' if fault == 'ambiguous' else 'another-order',
                'driver_id': 'other-driver', 'status': 'delivered'})
        elif fault == 'foreign':
            await db.store_delivery_assignments.update_one({'user_id': 'owner'}, {'$set': {'user_id': 'other'}})
        else:
            await db.store_delivery_assignments.update_one({'user_id': 'owner'}, {'$set': {'order_id': 'another-order'}})
        state = await refresh(db, 'owner', clock=NOW)
        assert 'delivery_event:100' not in state['engine']['facts']
        assert 'cod_collection:100' not in state['engine']['facts']
        assert state['engine']['cod_reports'] == {}
        assert not any(r['kind'] in {'shipping', 'cod'} and r['confirmed'] != '0.00'
                       for r in state['engine']['obligations'].values())
        assert any(i['code'].startswith('operational_delivery_') for i in state['engine']['issues'])
        assert state['movements'] == []
        assert (await db.store_delivery_collections.find_one({'user_id': 'owner'}))['amount'] == '200'
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('method', ['cash', 'bank_transfer', 'card_terminal'])
def test_held_delivery_evidence_preserves_the_established_independent_collection(method):
    async def scenario(db):
        await started(db)
        await native_order(db, method)
        first = await refresh(db, 'owner', clock=NOW)
        assert first['engine']['facts']['cod_collection:100']['amount'] == ('200.00' if method == 'cash' else '0.00')
        # A later canonical assignment introduces contradictory delivery proof.
        # It cannot erase or rewrite the already verified collection contract.
        await db.store_delivery_assignments.update_one({'user_id': 'owner'}, {
            '$set': {'order_id': '100', 'active': True}})
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.status_changed_at': START,
            'raw_by_source.salla_direct.paid_amount': '200'}})
        held = await refresh(db, 'owner', clock=NOW)
        assert any(i['code'] == 'operational_delivery_timestamp_before_order' for i in held['engine']['issues'])
        assert held['engine']['facts']['cod_collection:100'] == first['engine']['facts']['cod_collection:100']
        assert held['engine']['cod_reports'] == first['engine']['cod_reports']
        assert held['engine']['obligations']['cod:100'] == first['engine']['obligations']['cod:100']
        assert held['movements'] == first['movements'] == []
        assert report(held)['summary']['actual_liquidity'] == '1000.00'
    run(scenario)


def test_recorded_status_delivery_cannot_downgrade_to_the_legacy_assignment_contract():
    from test_operational_balance_delivery_start_rule import calculate, undated
    data = undated('contract_driver')
    first = calculate(data)
    assert first['engine']['facts']['delivery_event:100']['delivered_at'] is None
    data['store_delivery_assignments'][0].pop('order_id')
    held = calculate(data, first)
    assert any(i['code'] == 'operational_delivery_assignment_order_conflict' for i in held['engine']['issues'])
    assert held['engine']['facts'] == first['engine']['facts']
    assert held['engine']['obligations'] == first['engine']['obligations']
    assert held['engine']['cod_reports'] == first['engine']['cod_reports']


def test_unreconciled_owner_delivery_proof_cannot_downgrade_to_a_legacy_assignment():
    from test_operational_balance_delivery_start_rule import calculate, undated
    from test_operational_balance_delivery_cutover import active
    data = undated('contract_driver')
    data['store_delivery_assignments'][0].pop('order_id')
    prior = active()
    # Even an incomplete saved proof must force strict revalidation. Its
    # presence cannot silently enable the older assignment-only contract.
    prior['engine'] = {'facts': {'owner_delivery_after_start:100': {
        'schema': 'operational_owner_delivery_confirmation_v1', 'order_id': '100'}}}
    held = calculate(data, prior)
    assert any(i['code'].startswith('operational_delivery_') for i in held['engine']['issues'])
    assert held['engine']['facts'] == prior['engine']['facts']
    assert not any(r['kind'] in {'shipping', 'cod'} and r['confirmed'] != '0.00'
                   for r in held['engine']['obligations'].values())
    assert held['engine']['cod_reports'] == {} and held['movements'] == []
