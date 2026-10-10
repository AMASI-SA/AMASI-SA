"""Corrected behavior on the dedicated isolated Mongo replica set."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_operational_physical_stock_prerequisites import run
from fulfillment_v2_routes import (
    _inventory_rows, _load_inventory_evidence, _persist_order_inventory_reservations,
    _consume_order_inventory_reservations,
)
from stock_component_consumption_service import _available, _deduct
from operational_atomic import operational_owner


async def seed(db, kind='product', condition=None, source='purchase_invoice'):
    item = dict(receipt_id='lot', lot_id='lot', quantity=10, source_type=source,
                source_id='document', source_line_id='line', configuration_key='configuration')
    item.update(dict(item_type='stock_component', resource_id='component') if kind == 'component'
                else dict(item_type='product', product_id='product'))
    if condition:
        item['condition'] = condition
    location = dict(id='loc', user_id='owner', warehouse_id='wh', state='occupied',
                    occupancy=dict(items=[item], total_quantity=10))
    receipt = dict(**item, id='lot', user_id='owner', warehouse_id='wh', location_id='loc', status='posted')
    await db.warehouse_locations.insert_one(deepcopy(location))
    await db.mezan_inventory_receipts_v2.insert_one(deepcopy(receipt))
    return location


async def read(db, kind):
    if kind == 'component':
        return (await _available(db, 'owner', ['wh']))[0]
    locations = await db.warehouse_locations.find({'user_id': 'owner'}).to_list(20)
    return _inventory_rows(locations, await _load_inventory_evidence(db, 'owner', locations))[0]


def available(row):
    return row.get('remaining', row.get('available'))


@pytest.mark.parametrize('kind', ['product', 'component'])
@pytest.mark.parametrize('condition', [None, 'damaged', 'quarantine', 'pending_inspection'])
def test_condition_and_pending_preserve_physical_stock(kind, condition):
    async def scenario(db):
        await seed(db, kind, condition)
        row = await read(db, kind)
        assert row['on_hand'] == 10
        assert available(row) == (0 if condition else 10)
        await db.mezan_inventory_receipts_v2.update_one({'user_id': 'owner', 'id': 'lot'}, {'$set': {'status': 'pending'}})
        row = await read(db, kind)
        assert row['on_hand'] == 10 and available(row) == 0
        assert (await db.warehouse_locations.find_one({'id': 'loc'}))['occupancy']['total_quantity'] == 10
    run(scenario)


@pytest.mark.parametrize('kind', ['product', 'component'])
@pytest.mark.parametrize('legacy', ['receipt', 'opening', 'unknown', 'wrong_owner'])
def test_legacy_proof_is_required(kind, legacy):
    async def scenario(db):
        await seed(db, kind)
        await db.warehouse_locations.update_one({'id': 'loc'}, {'$unset': {
            'occupancy.items.0.source_type': '', 'occupancy.items.0.source_id': '', 'occupancy.items.0.source_line_id': ''}})
        if legacy == 'opening':
            await db.mezan_inventory_receipts_v2.update_one({'id': 'lot'}, {'$set': {
                'id': 'opening', 'source_type': 'opening_inventory', 'schema_version': 'g47-opening-inventory-v1',
                'adopted_receipt_ids': ['lot'], 'opening_txn_group_id': 'approved-opening',
                'evidence_sha256': 'fixture-evidence', 'cutover_at': '2026-01-01T00:00:00Z'}})
        elif legacy == 'unknown':
            await db.mezan_inventory_receipts_v2.delete_many({})
        elif legacy == 'wrong_owner':
            await db.mezan_inventory_receipts_v2.update_one({'id': 'lot'}, {'$set': {'user_id': 'other'}})
        assert available(await read(db, kind)) == (10 if legacy in {'receipt', 'opening'} else 0)
    run(scenario)


async def reserve_product(db, order='order', quantity=3):
    allocation = dict(location_id='loc', receipt_id='lot', item_index=0,
                      inventory_row_key='receipt:lot', quantity=quantity)
    return await _persist_order_inventory_reservations(db, user_id='owner', order_number=order,
        lines=[dict(requires_branch_inventory=True, inventory_available=True, order_item_id='line',
                    quantity=quantity, inventory_allocations=[allocation])])


async def consume(db, kind):
    if kind == 'component':
        await _deduct(db, 'owner', [dict(location_id='loc', lot_id='lot', resource_id='component', quantity='3')])
    else:
        await _consume_order_inventory_reservations(db, user_id='owner', order_numbers=['order'], actor_id='actor', batch_id='batch')


@pytest.mark.parametrize('kind', ['product', 'component'])
@pytest.mark.parametrize('mutation', ['pending', 'damaged', 'quarantine'])
def test_revalidate_before_final_deduction(kind, mutation):
    async def scenario(db):
        await seed(db, kind)
        if kind == 'product':
            await reserve_product(db)
        else:
            await db.mezan_component_consumption_units_v1.insert_one(dict(user_id='owner', state='reserved',
                allocations=[dict(location_id='loc', lot_id='lot', resource_id='component', quantity='3')]))
        if mutation == 'pending':
            await db.mezan_inventory_receipts_v2.update_one({'id': 'lot'}, {'$set': {'status': 'pending'}})
        else:
            await db.warehouse_locations.update_one({'id': 'loc'}, {'$set': {'occupancy.items.0.condition': mutation}})
        before = await db.warehouse_locations.find_one({'id': 'loc'})
        with pytest.raises(HTTPException) as error:
            await consume(db, kind)
        assert error.value.status_code == 409
        assert await db.warehouse_locations.find_one({'id': 'loc'}) == before
        assert await db.mezan_inventory_reservations_v2.count_documents({'status': 'consumed'}) == 0
    run(scenario)


def test_product_reservation_concurrency_and_duplicate_consumption():
    async def scenario(db):
        await seed(db)
        results = await asyncio.gather(reserve_product(db, 'order', 7), reserve_product(db, 'other', 7), return_exceptions=True)
        assert sum(isinstance(r, HTTPException) for r in results) == 1
        winner = await db.mezan_inventory_reservations_v2.find_one({'status': 'active'})
        args = dict(user_id='owner', order_numbers=[winner['order_number']], actor_id='actor', batch_id='batch')
        results = await asyncio.gather(*[_consume_order_inventory_reservations(db, **args) for _ in range(2)])
        assert sorted(results) == [0, 1]
        assert (await db.warehouse_locations.find_one({'id': 'loc'}))['occupancy']['total_quantity'] == 3
    run(scenario)


@pytest.mark.parametrize('kind', ['product', 'component'])
def test_owner_serialization_receipt_change_wins_before_consumer(kind):
    async def scenario(db):
        await seed(db, kind, source='stock_preparation_order')
        if kind == 'product':
            await reserve_product(db)
        entered, release = asyncio.Event(), asyncio.Event()
        async def change(scoped):
            await scoped.mezan_inventory_receipts_v2.update_one({'user_id': 'owner', 'id': 'lot'}, {'$set': {'status': 'pending'}})
            entered.set()
            await release.wait()
        writer = asyncio.create_task(operational_owner(db, 'owner', change))
        await asyncio.wait_for(entered.wait(), 5)
        consumer = asyncio.create_task(consume(db, kind))
        await asyncio.sleep(.05)
        release.set()
        await writer
        with pytest.raises(HTTPException):
            await consumer
        assert (await db.warehouse_locations.find_one({'id': 'loc'}))['occupancy']['total_quantity'] == 10
    run(scenario)


def test_salla_reader_only_no_writes():
    from salla_inventory_sync_routes import _inventory_facts
    async def scenario(db):
        await seed(db)
        before = await db.warehouse_locations.find_one({'id': 'loc'})
        _, _, rows = await _inventory_facts(db, merchant_id='owner', warehouse_ids=['wh'])
        assert rows[0]['remaining'] == 10
        await db.mezan_inventory_receipts_v2.update_one({'id': 'lot'}, {'$set': {'status': 'pending'}})
        _, _, rows = await _inventory_facts(db, merchant_id='owner', warehouse_ids=['wh'])
        assert rows[0]['remaining'] == 0 and rows[0]['on_hand'] == 10
        assert await db.warehouse_locations.find_one({'id': 'loc'}) == before
    run(scenario)


async def component_plan(db, condition=None):
    from stock_component_consumption_service import reserve_component_stock
    await seed(db, 'component', condition, source='stock_preparation_order')
    await db.settings.insert_one({'user_id': 'owner', 'g47_inventory': {'component_lifecycle_starts_at': '2026-01-01T00:00:00Z'}})
    await db.mezan_products_v2.insert_one({'user_id': 'owner', 'id': 'product', 'salla_product_id': 'product'})
    await db.mezan_cost_resources_v2.insert_one({'user_id': 'owner', 'id': 'component', 'kind': 'stock_component', 'track_inventory': True})
    await db.mezan_product_resource_bindings_v2.insert_one({'user_id': 'owner', 'id': 'binding',
        'salla_product_id': 'product', 'resource_id': 'component', 'quantity': 3})
    return await reserve_component_stock(db, merchant_id='owner', order_id='order', source_version=1,
        source_created_at='2026-01-02T00:00:00Z', lines=[{'order_line_id': 'line', 'product_id': 'product', 'quantity': 1}])


@pytest.mark.parametrize('condition', ['damaged', 'quarantine', 'pending_inspection'])
def test_public_component_reservation_denies_ineligible_stock(condition):
    async def scenario(db):
        with pytest.raises(HTTPException):
            await component_plan(db, condition)
        assert await db.mezan_component_consumption_plans_v1.count_documents({}) == 0
        assert await db.mezan_component_consumption_units_v1.count_documents({}) == 0
    run(scenario)


def test_public_component_consumer_denies_receipt_changed_after_reservation():
    from stock_component_consumption_service import consume_component_stock
    async def scenario(db):
        await component_plan(db)
        async def quarantine(scoped):
            await scoped.mezan_inventory_receipts_v2.update_one({'user_id': 'owner', 'id': 'lot'}, {'$set': {'status': 'pending'}})
        await operational_owner(db, 'owner', quarantine)
        with pytest.raises(HTTPException):
            await consume_component_stock(db, merchant_id='owner', order_id='order')
        assert await db.mezan_component_consumption_units_v1.count_documents({'state': 'reserved'}) == 1
        assert (await db.warehouse_locations.find_one({'id': 'loc'}))['occupancy']['total_quantity'] == 10
    run(scenario)


def test_public_component_concurrent_consumption_is_once_only():
    from stock_component_consumption_service import consume_component_stock
    async def scenario(db):
        await component_plan(db)
        result = await asyncio.gather(*[consume_component_stock(db, merchant_id='owner', order_id='order') for _ in range(2)])
        assert sum(bool(r['duplicate']) for r in result) == 1
        assert (await db.warehouse_locations.find_one({'id': 'loc'}))['occupancy']['total_quantity'] == 7
    run(scenario)


@pytest.mark.parametrize('kind', ['product', 'component'])
def test_consumer_wins_then_receipt_writer_serializes_after_commit(kind):
    async def scenario(db):
        await seed(db, kind, source='stock_preparation_order')
        if kind == 'product':
            await reserve_product(db)
        entered, release = asyncio.Event(), asyncio.Event()
        async def deduction(scoped):
            await consume(scoped, kind)
            entered.set()
            await release.wait()
        consumer = asyncio.create_task(operational_owner(db, 'owner', deduction))
        await asyncio.wait_for(entered.wait(), 5)
        async def change(scoped):
            await scoped.mezan_inventory_receipts_v2.update_one({'user_id': 'owner', 'id': 'lot'}, {'$set': {'status': 'pending'}})
        writer = asyncio.create_task(operational_owner(db, 'owner', change))
        await asyncio.sleep(.05)
        release.set()
        await consumer
        await writer
        row = await read(db, kind)
        assert row['on_hand'] == 7 and available(row) == 0
    run(scenario)


def test_product_identity_change_after_reservation_rejects_atomically():
    async def scenario(db):
        await seed(db)
        await reserve_product(db)
        # Even an internally consistent replacement cannot inherit an old hold.
        await db.warehouse_locations.update_one({'id': 'loc'}, {'$set': {'occupancy.items.0.configuration_key': 'different'}})
        await db.mezan_inventory_receipts_v2.update_one({'id': 'lot'}, {'$set': {'configuration_key': 'different'}})
        with pytest.raises(HTTPException):
            await consume(db, 'product')
        assert (await db.warehouse_locations.find_one({'id': 'loc'}))['occupancy']['total_quantity'] == 10
    run(scenario)


def test_component_second_lot_failure_rolls_back_first_lot_deduction():
    async def scenario(db):
        await seed(db, 'component')
        with pytest.raises(HTTPException):
            await _deduct(db, 'owner', [dict(location_id='loc', lot_id='lot', resource_id='component', quantity='3'),
                                      dict(location_id='missing', lot_id='missing', resource_id='component', quantity='1')])
        assert (await db.warehouse_locations.find_one({'id': 'loc'}))['occupancy']['total_quantity'] == 10
    run(scenario)


def test_evidence_reads_are_batched_and_owner_scoped_on_real_mongo():
    async def scenario(db):
        locations = [{'id': f'loc-{index}'} for index in range(201)]
        await db.mezan_inventory_receipts_v2.insert_many([
            dict(id=f'lot-{index}', user_id=owner, location_id=f'loc-{index}', status='posted')
            for index in range(201) for owner in ('owner', 'other')])
        queries = []
        class ObservedCollection:
            def find(self, query):
                queries.append(deepcopy(query))
                return db.mezan_inventory_receipts_v2.find(query)
        class ObservedDatabase:
            def __getitem__(self, name):
                assert name == 'mezan_inventory_receipts_v2'
                return ObservedCollection()
        evidence = await _load_inventory_evidence(ObservedDatabase(), 'owner', locations)
        assert len(queries) == 3 and len(evidence) == 201
        assert all(q['user_id'] == 'owner' and len(q['location_id']['$in']) <= 100 for q in queries)
        assert all(r['user_id'] == 'owner' for rows in evidence.values() for r in rows)
    run(scenario)


def test_existing_accounting_and_cost_documents_unchanged():
    async def scenario(db):
        await seed(db)
        for name in ('mz2_inventory_cost_states', 'general_ledger'):
            await db[name].insert_one({'_id': 'unchanged-fixture', 'user_id': 'owner', 'amount': 150, 'authoritative': True})
        before = {name: await db[name].find_one({'_id': 'unchanged-fixture'})
                  for name in ('mz2_inventory_cost_states', 'general_ledger')}
        await reserve_product(db)
        await consume(db, 'product')
        for name, expected in before.items():
            assert await db[name].find_one({'_id': 'unchanged-fixture'}) == expected
            await db[name].delete_one({'_id': 'unchanged-fixture'})
    run(scenario)

# Original writer-contract regressions. Real Mongo, no opening/financial writer.
async def contract_stock(db, kind='product', source='purchase_invoice'):
    from product_inventory_rules import build_inventory_configuration_key
    location = await seed(db, kind, source=source)
    fields = dict(preparation_state='ready_complete', specifications={'color': 'gold', 'name': 'Abeer'}, sku='SKU')
    fields['configuration_key'] = build_inventory_configuration_key(sku='SKU', preparation_state='ready_complete', specifications=fields['specifications']) if kind == 'product' else 'component-config'
    await db.warehouse_locations.update_one({'id': 'loc'}, {'$set': {'occupancy.items.0.'+k:v for k,v in fields.items()}})
    await db.mezan_inventory_receipts_v2.update_one({'id': 'lot'}, {'$set': fields})
    if source == 'stock_preparation_order':
        await db.warehouse_locations.update_one({'id': 'loc'}, {'$set': {'occupancy.items.0.lot_id': 'stock-preparation:document:line:lot'}})
        await db.mezan_inventory_receipts_v2.update_one({'id':'lot'}, {'$unset': {'lot_id':''}})
    return await db.warehouse_locations.find_one({'id': 'loc'})


async def contract_adoption(db, kind='product'):
    await contract_stock(db, kind)
    await db.mezan_inventory_receipts_v2.update_one({'id':'lot'}, {'$set': {'source_type':'legacy_purchase'}})
    await db.warehouse_locations.update_one({'id':'loc'}, {'$set': {'occupancy.items.0.source_type':'legacy_purchase'}})
    receipt = await db.mezan_inventory_receipts_v2.find_one({'id':'lot'})
    receipt.pop('_id')
    receipt.update(id='opening', receipt_id='opening', lot_id='opening', source_type='opening_inventory', source_id='approved-import',
        schema_version='g47-opening-inventory-v1', adopted_receipt_ids=['lot'], opening_txn_group_id='approved-opening',
        evidence_sha256='fixture-evidence', cutover_at='2026-01-01T00:00:00Z')
    await db.mezan_inventory_receipts_v2.insert_one(receipt)


def test_original_stock_preparation_lot_contract_is_available():
    async def scenario(db):
        await contract_stock(db, source='stock_preparation_order')
        assert available(await read(db, 'product')) == 10
        await reserve_product(db)
        await consume(db, 'product')
        assert available(await read(db, 'product')) == 7
    run(scenario)


@pytest.mark.parametrize('kind', ['product','component'])
def test_opening_adoption_with_retained_historical_receipt_is_available(kind):
    async def scenario(db):
        await contract_adoption(db, kind)
        assert available(await read(db, kind)) == 10
        if kind == 'product': await reserve_product(db)
        await consume(db, kind)
        assert available(await read(db, kind)) == 7
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 2
    run(scenario)


@pytest.mark.parametrize('kind', ['product','component'])
@pytest.mark.parametrize('mutation', ['specs','state','variant','missing_proof'])
def test_missing_configuration_requires_exact_original_contract(kind, mutation):
    async def scenario(db):
        await contract_stock(db, kind)
        changes = {'specs': {'specifications':{'color':'silver'}}, 'state':{'preparation_state':'requires_preparation'},
                   'variant': {'salla_variant_id':'unknown'}, 'missing_proof':{}}[mutation]
        await db.warehouse_locations.update_one({'id':'loc'}, {'$unset':{'occupancy.items.0.configuration_key':''}})
        if changes: await db.warehouse_locations.update_one({'id':'loc'}, {'$set':{'occupancy.items.0.'+k:v for k,v in changes.items()}})
        if mutation == 'missing_proof': await db.mezan_inventory_receipts_v2.update_one({'id':'lot'}, {'$unset':{'configuration_key':'','specifications':'','preparation_state':''}})
        row = await read(db, kind)
        assert row['on_hand'] == 10 and available(row) == 0
    run(scenario)

@pytest.mark.parametrize('mutation', ['lot','source','line','receipt','damaged','quarantine','pending_inspection','pending','rejected'])
def test_preparation_lot_contract_rejects_unproven_or_unsafe_stock(mutation):
    async def scenario(db):
        await contract_stock(db, source='stock_preparation_order')
        if mutation in {'damaged','quarantine','pending_inspection'}:
            await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.condition':mutation}})
        elif mutation in {'pending','rejected'}:
            await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'status':mutation}})
        else:
            field={'lot':'lot_id','source':'source_id','line':'source_line_id','receipt':'receipt_id'}[mutation]
            await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.'+field:'wrong'}})
        row=await read(db,'product')
        assert row['on_hand']==10 and available(row)==0
        with pytest.raises(HTTPException): await reserve_product(db)
        assert await db.mezan_inventory_reservations_v2.count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('kind',['product','component'])
@pytest.mark.parametrize('mutation',['pending','rejected','damaged','quarantine','pending_inspection','quantity','specs','location','source','duplicate_adoption'])
def test_opening_adoption_never_overrides_conflicts_or_current_veto(kind,mutation):
    async def scenario(db):
        await contract_adoption(db,kind)
        if mutation in {'pending','rejected'}:
            await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'status':mutation}})
        elif mutation in {'damaged','quarantine','pending_inspection'}:
            await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$set':{'condition':mutation}})
        elif mutation=='duplicate_adoption':
            r=await db.mezan_inventory_receipts_v2.find_one({'id':'opening'});r.pop('_id');r['id']='opening2'
            await db.mezan_inventory_receipts_v2.insert_one(r)
        else:
            changes={'quantity':{'quantity':9},'specs':{'specifications':{'color':'silver'}},'location':{'location_id':'other'},'source':{'source_id':''}}[mutation]
            await db.mezan_inventory_receipts_v2.update_one({'id':'opening'},{'$set':changes})
        row=await read(db,kind)
        assert row['on_hand']==10 and available(row)==0
        with pytest.raises(HTTPException):
            if kind=='product': await reserve_product(db)
            else: await consume(db,kind)
        assert (await db.warehouse_locations.find_one({'id':'loc'}))['occupancy']['total_quantity']==10
    run(scenario)


@pytest.mark.parametrize('kind',['product','component'])
def test_missing_configuration_with_complete_matching_proof_is_available(kind):
    async def scenario(db):
        await contract_stock(db,kind)
        await db.warehouse_locations.update_one({'id':'loc'},{'$unset':{'occupancy.items.0.configuration_key':''}})
        assert available(await read(db,kind))==10
    run(scenario)


def test_original_purchase_key_proves_specs_without_receipt_copy():
    async def scenario(db):
        await contract_stock(db)
        await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$unset':{'specifications':'','preparation_state':''}})
        await db.warehouse_locations.update_one({'id':'loc'},{'$unset':{'occupancy.items.0.configuration_key':''}})
        assert available(await read(db,'product'))==10
        await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.specifications':{'color':'silver'}}})
        assert available(await read(db,'product'))==0
    run(scenario)


@pytest.mark.parametrize('kind',['product','component'])
def test_adoption_veto_after_reservation_rolls_back_consumption(kind):
    async def scenario(db):
        await contract_adoption(db,kind)
        if kind=='product': await reserve_product(db)
        async def invalidate(scoped):
            # Existing financial transaction shared owner lock, fixture only.
            await scoped.mezan_inventory_receipts_v2.update_one({'user_id':'owner','id':'lot'},{'$set':{'status':'pending'}})
        from accounting_atomic import atomic_owner
        await db.mz2_atomic_owners.update_one({'_id':'owner'}, {'$set':{'writes_paused':False}})
        await atomic_owner(db,'owner',invalidate)
        with pytest.raises(HTTPException): await consume(db,kind)
        assert (await db.warehouse_locations.find_one({'id':'loc'}))['occupancy']['total_quantity']==10
        if kind=='product': assert await db.mezan_inventory_reservations_v2.count_documents({'status':'active'})==1
    run(scenario)

@pytest.mark.parametrize('kind',['product','component'])
@pytest.mark.parametrize('conflict',[None,'specs','identity','quantity','pending'])
def test_opening_witness_supplies_missing_legacy_fields_but_never_conflicts(kind,conflict):
    async def scenario(db):
        await contract_adoption(db,kind)
        await db.mezan_inventory_receipts_v2.delete_one({'id':'lot'})
        old=dict(user_id='owner',id='lot',location_id='loc',status='posted',source_type='legacy_purchase',source_id='document')
        if conflict=='specs': old['specifications']={'color':'silver'}
        if conflict=='identity': old['resource_id' if kind=='component' else 'product_id']='wrong'
        if conflict=='quantity': old['quantity']=2
        if conflict=='pending': old['status']='pending'
        await db.mezan_inventory_receipts_v2.insert_one(old)
        row=await read(db,kind)
        assert row['on_hand']==10 and available(row)==(10 if conflict is None else 0)
    run(scenario)


@pytest.mark.parametrize('sku',['SKU',None])
def test_exact_preparation_receipt_aliases_and_skuless_contract(sku):
    async def scenario(db):
        from product_inventory_rules import build_inventory_configuration_key
        await contract_stock(db,source='stock_preparation_order')
        fields=dict(mezan_product_id='mezan',sku=sku)
        fields['configuration_key']=build_inventory_configuration_key(sku=sku or 'mezan',preparation_state='ready_complete',specifications={'color':'gold','name':'Abeer'})
        await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.'+k:v for k,v in dict(fields,product_id='salla').items()}})
        await db.mezan_inventory_receipts_v2.update_one({'id':'lot'},{'$unset':{'product_id':''},'$set':dict(fields,salla_product_id='salla')})
        assert available(await read(db,'product'))==10
    run(scenario)


@pytest.mark.parametrize('kind',['product','component'])
def test_opening_adoption_aggregate_quantity_cannot_be_duplicated(kind):
    async def scenario(db):
        await contract_adoption(db,kind)
        loc=await db.warehouse_locations.find_one({'id':'loc'})
        original=loc['occupancy']['items'][0]
        extra=dict(original,receipt_id='second',lot_id='second',quantity=1)
        await db.warehouse_locations.update_one({'id':'loc'},{'$push':{'occupancy.items':extra},'$inc':{'occupancy.total_quantity':1}})
        await db.mezan_inventory_receipts_v2.update_one({'id':'opening'},{'$set':{'adopted_receipt_ids':['lot','second']}})
        assert available(await read(db,kind))==0
        assert (await db.warehouse_locations.find_one({'id':'loc'}))['occupancy']['total_quantity']==11
    run(scenario)

@pytest.mark.parametrize('kind',['product','component'])
def test_conflicting_variant_alias_is_not_hidden_by_matching_configuration(kind):
    async def scenario(db):
        await contract_stock(db,kind)
        await db.warehouse_locations.update_one({'id':'loc'},{'$set':{'occupancy.items.0.variant_id':'unproven'}})
        assert available(await read(db,kind))==0
    run(scenario)
