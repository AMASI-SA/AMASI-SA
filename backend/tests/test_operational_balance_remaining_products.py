"""Remaining-work integration, using exact frozen recipes and isolated Mongo.

These tests do not post any supplier financial event or use a live tenant.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from operational_balance_sources import collect_sources
from operational_balance_engine import reconcile
from operational_open_cost_exposure import project
from operational_remaining_bundle_sources import load_remaining_bundles, normalized_orders
from order_cost_authority import line_fingerprint
from stock_component_consumption_service import PLANS, UNITS, RECEIPTS, selection_tokens

START = '2026-11-01T00:00:00+00:00'
NOW = '2026-11-02T12:00:00+00:00'
CREATED = '2026-11-01T01:00:00+00:00'
OWNER = 'remaining-products-fixture'
LINE = 'salla:ORDER:LINE'


@asynccontextmanager
async def database():
    uri = os.environ.get('OPERATIONAL_TEST_MONGO_URI')
    if not uri:
        pytest.skip('Explicit isolated localhost Mongo required')
    parsed = urlparse(uri)
    assert parsed.scheme == 'mongodb' and parsed.hostname == '127.0.0.1'
    assert parsed.port and not parsed.username and not parsed.password and parsed.path in ('', '/')
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = 'test_operational_remaining_' + uuid4().hex
    db = client[name]
    try:
        assert (await client.admin.command('hello')).get('setName')
        yield db
    finally:
        assert name.startswith('test_operational_remaining_')
        await client.drop_database(name)
        client.close()


def run(task): return asyncio.run(task)


def raw(*, created=CREATED, status='in_progress', currency='SAR', rose=True):
    return {'id': 'order-id', 'reference_id': 'ORDER', 'created_at': created, 'updated_at': created,
        'status': status, 'currency': currency, 'amounts': {'total': {'amount': '400', 'currency': currency}},
        'items': [{'id': 'LINE', 'product_id': 'bundle', 'quantity': 4,
                   'options': [{'id': 'gift', 'value': {'id': 'yes' if rose else 'no'}}]}]}


def recipe(order, *, embroidery=False, rose_qty='1'):
    line = normalized_orders([order])[0]['items'][0]
    selected = [{'option_id': 'gift', 'value_id': 'yes' if order['items'][0]['options'][0]['value']['id']=='yes' else 'no'}]
    leaves = [{'leaf_id': 'base', 'kind': 'product', 'product_id': 'base', 'quantity': '1',
        'unit_cost': '15', 'sourcing_mode': 'supplier', 'supplier_id': 'supplier', 'current_product_reference': True}]
    if selected[0]['value_id'] == 'yes':
        leaves.append({'leaf_id': 'rose', 'kind': 'product', 'product_id': 'rose', 'quantity': rose_qty,
            'unit_cost': '40', 'sourcing_mode': 'supplier', 'supplier_id': 'supplier'})
    if embroidery:
        leaves.append({'leaf_id': 'name', 'kind': 'resource', 'resource_id': 'name-service', 'quantity': '1',
            'unit_cost': '10', 'sourcing_mode': 'stock', 'cost_only': True, 'track_inventory': False})
    return {'product_id': 'bundle', 'revision': 2, 'selected_options': selected, 'cost_complete': True,
        'historical_cost_verified': True, 'sale_quantity': 4, 'line_fingerprint': line_fingerprint(line),
        'leaves': leaves, 'estimated_cost': str(sum(Decimal(l['unit_cost'])*Decimal(l['quantity']) for l in leaves))}


async def seed(db, order=None, *, embroidery=False, rose_qty='1', units=True):
    order = order or raw()
    snapshot = recipe(order, embroidery=embroidery, rose_qty=rose_qty)
    await db.unified_orders.insert_one({'user_id': OWNER, 'order_number': 'ORDER', 'raw_by_source': {'salla_direct': order}})
    await db.mezan_product_cost_profiles_v2.insert_one({'user_id': OWNER,'salla_product_id':'bundle','base_cost':'15'})
    await db.mezan_suppliers_v2.insert_one({'user_id': OWNER,'id':'supplier','name':'Synthetic supplier'})
    await db.mezan_bundle_definitions_v2.insert_one({'user_id': OWNER,'product_id':'bundle','product_type':'composite'})
    await db.mezan_component_order_lifecycle_v1.insert_one({'user_id': OWNER,'order_number':'ORDER',
        'bundle_intake_snapshots': {LINE: snapshot}})
    if units:
        line = normalized_orders([order])[0]['items'][0]
        await db[PLANS].insert_one({'_id':'plan','user_id':OWNER,'order_id':'ORDER',
            'bundle_snapshots': {LINE: snapshot}, 'lines':[line]})
        for i in range(1,5):
            leaves = [dict(l, state='ready' if l.get('cost_only') else 'awaiting_supplier',
                receipt_ids=[], reserved_quantity='0', received_quantity='0', consumed_quantity='0') for l in snapshot['leaves']]
            await db[UNITS].insert_one({'_id':f'unit{i}','user_id':OWNER,'plan_id':'plan','order_id':'ORDER',
                'order_line_id':LINE,'unit_index':i,'state':'reserved','product_id':'bundle',
                'bundle_leaves':leaves,'created_at':CREATED,'allocations':[],
                'selected_context': [list(v) for v in selection_tokens(line)]})
    return order, snapshot


async def receive(db, index, leaf_id, qty='1', *, bad_owner=False):
    unit = await db[UNITS].find_one({'_id':f'unit{index}'})
    leaf = next(l for l in unit['bundle_leaves'] if l['leaf_id']==leaf_id)
    rid, invoice_id = f'receipt-{index}-{leaf_id}', f'invoice-{index}-{leaf_id}'
    await db[RECEIPTS].insert_one({'id':rid,'user_id':'other' if bad_owner else OWNER,'status':'posted',
        'source_type':'purchase_invoice','source_id':invoice_id,'purchase_invoice_id':invoice_id,
        'posted_at':'2026-11-02T10:00:00+00:00','quantity':qty,
        'item_type':'product','product_id':leaf['product_id'],'salla_product_id':leaf['product_id'], 'variant_id':None})
    await db.purchase_invoices.insert_one({'id':invoice_id,'user_id':OWNER,'state':'approved','supplier_id':'supplier'})
    leaf.update(receipt_ids=[rid],reserved_quantity=qty,received_quantity=qty)
    unit['allocations'].append({'id':f'allocation-{index}-{leaf_id}','bundle_leaf_id':leaf_id,'quantity':qty,
        'lot_id':rid,'location_id':'location','inventory_identity':{'item_type':'product','product_id':leaf['product_id'],
                                                                       'salla_product_id':leaf['product_id'],'variant_id':None}})
    await db[UNITS].replace_one({'_id':unit['_id']},unit)


def state():
    return {'status':'active','started_at':START,'engine':{'as_of':NOW,'obligations':{},'facts':{},'issues':[]},
            'openings':{},'movements':[]}


def expected(output):
    return sum(Decimal(r['expected']) for r in output['engine']['obligations'].values() if r['kind']=='supplier')


async def verify(db, amount, *, period='poststart'):
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    sources = await collect_sources(db, OWNER, START, NOW)
    start = state()
    updated = reconcile(start, sources, NOW)
    assert start == state(), 'pure reducer mutated caller'
    assert updated == reconcile(updated, sources, NOW), 'replay changed identities or money'
    if period=='poststart':
        assert expected(updated)==Decimal(amount)
        assert all(r['currency']=='SAR' for r in updated['engine']['obligations'].values() if r['kind']=='supplier')
    else:
        assert expected(updated)==0, 'old sale must not be replayed into finance'
    assert all(r['confirmed']=='0.00' for r in updated['engine']['obligations'].values() if r['kind']=='supplier')
    out = await project(db, OWNER, updated, as_of=NOW)
    assert Decimal(out['currencies'].get('SAR',{}).get(period+'_supplier_unreceived','0'))==Decimal(amount)
    after = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    assert before==after, 'reader wrote native sources'
    return sources, updated, out


@pytest.mark.parametrize('currency',['SAR','QAR','KWD','AED'])
@pytest.mark.parametrize('created',[CREATED,'2026-10-25T10:00:00+00:00'])
def test_full_recipe_cost_is_220_in_sar_before_and_after_cutover(currency,created):
    async def test():
        async with database() as db:
            await seed(db,raw(currency=currency,created=created))
            await verify(db,'220',period='poststart' if created==CREATED else 'prestart')
    run(test())


@pytest.mark.parametrize('received_rose,amount',[(0,'160'),(2,'80'),(4,'0')])
def test_receive_shawls_does_not_receive_roses_and_partial_receiving_reduces_only_that_component(received_rose,amount):
    async def test():
        async with database() as db:
            await seed(db)
            for i in range(1,5): await receive(db,i,'base')
            for i in range(1,received_rose+1): await receive(db,i,'rose')
            sources,updated,out=await verify(db,amount)
            assert any(p['code']=='bundle_received_purchase_financial_matching_required' for p in sources['issues'])
            assert not updated['engine']['facts'], 'posted native receipt must not invent a second operational invoice'
    run(test())


@pytest.mark.parametrize('rose,embroidery,amount',[(False,False,'60'),(True,True,'260')])
def test_customer_choice_and_independent_service_apply_once(rose,embroidery,amount):
    async def test():
        async with database() as db:
            await seed(db,raw(rose=rose),embroidery=embroidery)
            await verify(db,amount)
    run(test())


def test_partial_quantity_inside_one_bundle_is_not_rounded_up_to_whole_leaf():
    async def test():
        async with database() as db:
            await seed(db,rose_qty='2')
            await receive(db,1,'rose',qty='1')
            await verify(db,'340')
    run(test())


def test_frozen_recipe_before_native_unit_materialization_and_catalog_repricing():
    async def test():
        async with database() as db:
            await seed(db,units=False)
            await db.mezan_product_cost_profiles_v2.update_one({'user_id':OWNER},{'$set':{'base_cost':'999'}})
            await verify(db,'220')
    run(test())


@pytest.mark.parametrize('status',['reviewed','completed','shipped','delivering','delivered','cancelled','pending','unknown'])
def test_current_unreceived_is_only_waiting_review_or_in_progress(status):
    async def test():
        async with database() as db:
            await seed(db,raw(status=status))
            await verify(db,'0')
    run(test())


def test_option_change_blocks_parent_fallback_and_preserves_saved_estimate():
    async def test():
        async with database() as db:
            await seed(db)
            first=await collect_sources(db,OWNER,START,NOW)
            prior=reconcile(state(),first,NOW)
            await db.unified_orders.update_one({'user_id':OWNER},{'$set':{'raw_by_source.salla_direct.items.0.options.0.value.id':'no'}})
            changed=await collect_sources(db,OWNER,START,NOW)
            assert changed['orders'][0]['items']==[] and changed['orders'][0]['items_complete'] is False
            after=reconcile(prior,changed,NOW)
            assert expected(after)==Decimal('220')
            assert any(p['code']=='cost_snapshot_line_changed' for p in after['engine']['issues'])
    run(test())


def test_wrong_tenant_receipt_is_not_treated_as_received_or_zero():
    async def test():
        async with database() as db:
            order,_=await seed(db)
            await receive(db,1,'rose',bad_owner=True)
            result=await load_remaining_bundles(db,OWNER,[order],NOW)
            line=result[('ORDER',LINE)]
            assert line['complete'] is False
            assert any(p['code']=='bundle_supplier_receipt_missing' for p in line['issues'])
    run(test())


def test_unknown_bundle_without_frozen_recipe_never_falls_back_to_parent_price():
    async def test():
        async with database() as db:
            await seed(db,units=False)
            await db.mezan_component_order_lifecycle_v1.delete_many({'user_id':OWNER})
            sources=await collect_sources(db,OWNER,START,NOW)
            assert sources['orders'][0]['items']==[]
            assert any(p['code']=='bundle_snapshot_missing' for p in sources['issues'])
    run(test())


@pytest.mark.parametrize('mutation,code',[
    ('selection','bundle_unit_selection_mismatch'),
    ('variant','bundle_unit_selection_mismatch'),
    ('plan','bundle_unit_plan_mismatch'),
    ('allocation','bundle_allocation_identity_ambiguous'),
    ('allocation_item','bundle_allocation_item_mismatch'),
    ('future','bundle_unit_newer_than_report'),
    ('receipt_total','bundle_supplier_receipt_overallocated'),
    ('invoice','bundle_supplier_invoice_unverified'),
])
def test_untrusted_native_evidence_cannot_reduce_known_remaining_cost(mutation,code):
    async def test():
        async with database() as db:
            await seed(db)
            prior=reconcile(state(),await collect_sources(db,OWNER,START,NOW),NOW)
            if mutation in {'selection','variant','plan','future'}:
                field,value={
                    'selection':('selected_context', [['id:gift','id:no']]),
                    'variant':('variant_id','wrong-variant'),
                    'plan':('plan_id','wrong-plan'),
                    'future':('updated_at','2026-11-03T00:00:00+00:00'),
                }[mutation]
                await db[UNITS].update_one({'_id':'unit1'},{'$set':{field:value}})
            else:
                await receive(db,1,'rose')
                if mutation=='allocation':
                    await db[UNITS].update_one({'_id':'unit1'}, {'$set': {'allocations.0.bundle_leaf_id':'unknown-leaf'}})
                elif mutation=='allocation_item':
                    await db[UNITS].update_one({'_id':'unit1'}, {'$set': {'allocations.0.inventory_identity.product_id':'wrong-item', 'allocations.0.inventory_identity.salla_product_id':'wrong-item'}})
                elif mutation=='receipt_total':
                    await db[RECEIPTS].update_one({'id':'receipt-1-rose'},{'$set':{'quantity':'0.5'}})
                else:
                    await db.purchase_invoices.update_one({'id':'invoice-1-rose'}, {'$set':{'state':'draft'}})
            sources=await collect_sources(db,OWNER,START,NOW)
            after=reconcile(prior,sources,NOW)
            assert expected(after)==Decimal('220')
            assert any(i['code']==code for i in sources['issues'])
            preview=await project(db,OWNER,after,as_of=NOW)
            assert not preview['complete'] and any(code in i['code'] for i in preview['issues'])
            assert not after['engine']['facts']
    run(test())


def test_duplicate_allocation_across_units_rejects_the_entire_line_without_picking_first():
    async def test():
        async with database() as db:
            order,_=await seed(db)
            await receive(db,1,'rose');await receive(db,2,'rose')
            await db[UNITS].update_one({'_id':'unit2'},{'$set':{'allocations.0.id':'allocation-1-rose'}})
            out=await load_remaining_bundles(db,OWNER,[order],NOW)
            assert not out[('ORDER',LINE)]['complete'] and not out[('ORDER',LINE)]['items']
    run(test())


def test_stock_fulfilment_removes_demand_without_recording_a_new_supplier_purchase():
    async def test():
        async with database() as db:
            await seed(db)
            unit=await db[UNITS].find_one({'_id':'unit1'})
            leaf=next(l for l in unit['bundle_leaves'] if l['leaf_id']=='rose')
            leaf.update(sourcing_mode='stock',supplier_id=None,state='reserved',reserved_quantity='1')
            unit['allocations']=[{'id':'stock-allocation','bundle_leaf_id':'rose','quantity':'1','lot_id':'existing-stock','location_id':'location',
                                  'inventory_identity':{'item_type':'product','product_id':'rose','salla_product_id':'rose','variant_id':None}}]
            await db[UNITS].replace_one({'_id':'unit1'},unit)
            sources,updated,out=await verify(db,'180')
            assert not updated['engine']['facts']
            assert not any(i['scope']=='received_products' for i in sources['issues'] if 'scope' in i)
    run(test())


def test_auto_split_sources_charge_only_the_unreceived_fraction():
    async def test():
        async with database() as db:
            await seed(db,rose_qty='2')
            unit=await db[UNITS].find_one({'_id':'unit1'})
            leaf=next(l for l in unit['bundle_leaves'] if l['leaf_id']=='rose')
            stock=dict(leaf,leaf_id='stock-part',quantity='1',sourcing_mode='stock',supplier_id=None,
                       reserved_quantity='1',state='reserved')
            supplier=dict(leaf,leaf_id='supplier-part',quantity='1',reserved_quantity='0',state='awaiting_supplier')
            leaf.update(sourcing_mode='auto',sourcing_segments=[stock,supplier],reserved_quantity='1',unresolved_quantity='0')
            unit['allocations']=[{'id':'split-allocation','bundle_leaf_id':'rose','sourcing_segment_id':'stock-part',
                                 'sourcing_mode':'stock','lot_id':'existing-stock','quantity':'1','location_id':'location',
                                 'inventory_identity':{'item_type':'product','product_id':'rose','salla_product_id':'rose','variant_id':None}}]
            await db[UNITS].replace_one({'_id':'unit1'},unit)
            await verify(db,'340')
    run(test())


def test_financial_position_reads_the_remaining_product_bucket_not_parent_or_dashboard_totals():
    from operational_financial_position_core import build_financial_position
    async def test():
        async with database() as db:
            order=raw();order['shipping']={'company_code':'missing-carrier'}
            await seed(db,order)
            sources,updated,exposure=await verify(db,'220')
            updated.update(owner_id=OWNER,parties=[],receipts=[],payments=[],currency='SAR')
            out=build_financial_position(updated,cost_exposure=exposure,generated_at=NOW)
            tables=out['currencies']['SAR']['tables']
            target=next(t for t in tables if t['id']=='products_unreceived')
            assert sum(Decimal(r['amount']) for r in target['rows'])==Decimal('220')
            assert target['complete'] and target['totals']['amount']=='220.00'
            shipping=next(t for t in tables if t['id']=='shipping_expected')
            assert not shipping['complete'] and shipping['totals']['amount'] is None
            assert any(i['code'].endswith('shipping_identity_or_rate_incomplete') for i in out['issues'])
            assert not updated['engine']['facts']
    run(test())


@pytest.mark.parametrize('created',[CREATED,'2026-10-25T10:00:00+00:00'])
@pytest.mark.parametrize('status,remaining',[('under_review','60'),('in_progress','60'),('reviewed','0'),('completed','0'),('cancelled','0')])
def test_ordinary_product_sar_status_and_piece_receipt_are_independent_of_sale_currency(created,status,remaining):
    async def test():
        async with database() as db:
            order=raw(currency='KWD',created=created,status=status)
            order['items'][0]['product_id']='ordinary'
            await db.unified_orders.insert_one({'user_id':OWNER,'order_number':'ORDER','raw_by_source':{'salla_direct':order}})
            await db.mezan_product_cost_profiles_v2.insert_one({'user_id':OWNER,'salla_product_id':'ordinary','base_cost':'20','cost_currency':'SAR'})
            await db.mezan_suppliers_v2.insert_one({'user_id':OWNER,'id':'supplier','name':'Supplier'})
            for index in range(1,5):
                piece={'id':f'ordinary-{index}','user_id':OWNER,'order_number':'ORDER','order_item_id':LINE,
                       'unit_index':index,'supplier_id':'supplier','supplier_dispatch_status':'sent','execution_status':'assigned'}
                if index==1:
                    piece.update(supplier_dispatch_status='received',execution_status='received_from_supplier',received_at=CREATED)
                await db.mezan_preparation_pieces_v1.insert_one(piece)
            sources=await collect_sources(db,OWNER,START,NOW)
            updated=reconcile(state(),sources,NOW)
            exposure=await project(db,OWNER,updated,as_of=NOW)
            period='poststart' if created==CREATED else 'prestart'
            buckets=exposure['currencies']['SAR']
            assert Decimal(buckets.get(period+'_supplier_unreceived','0'))==Decimal(remaining)
            assert Decimal(buckets.get(period+'_supplier_received_uninvoiced','0'))==Decimal('20')
            assert 'KWD' not in {g['currency'] for g in exposure['groups'] if g['kind']=='supplier'}
            assert not updated['engine']['facts']
    run(test())


def test_explicit_foreign_cost_is_not_relabelled_or_converted_by_sale_currency():
    async def test():
        async with database() as db:
            order=raw(currency='QAR');order['items'][0]['product_id']='ordinary'
            await db.unified_orders.insert_one({'user_id':OWNER,'order_number':'ORDER','raw_by_source':{'salla_direct':order}})
            await db.mezan_product_cost_profiles_v2.insert_one({'user_id':OWNER,'salla_product_id':'ordinary','base_cost':'20','cost_currency':'KWD'})
            out=await collect_sources(db,OWNER,START,NOW)
            assert not out['orders'][0]['items'] and out['orders'][0]['items_complete'] is False
            assert any(i['code']=='mz2_product_cost_currency_requires_review' for i in out['issues'])
    run(test())


class ReadAudit:
    ALLOWED=frozenset({'mezan_bundle_definitions_v2','mezan_order_item_cost_snapshots_v2',
        'mezan_component_order_lifecycle_v1','mezan_component_consumption_plans_v1',
        'mezan_component_consumption_units_v1','mezan_inventory_receipts_v2','purchase_invoices'})
    def __init__(self,db): self.db,self.reads=db,[]
    def __getitem__(self,name):
        assert name in self.ALLOWED
        audit=self
        class Collection:
            def check(self,query):
                assert query.get('user_id')==OWNER
                if name!='mezan_bundle_definitions_v2':
                    keys=[query.get(k) for k in ('order_id','order_number','plan_id','id')]
                    assert any(isinstance(v,dict) and set(v)=={'$in'} and len(v['$in'])<=250 for v in keys)
                audit.reads.append((name,deepcopy(query)))
            def find(self,query,projection=None):
                self.check(query)
                return audit.db[name].find(query,projection)
            def aggregate(self,pipeline):
                assert set(pipeline[0])=={'$match'} and set(pipeline[1])=={'$limit'} and set(pipeline[2])=={'$project'}
                self.check(pipeline[0]['$match'])
                return audit.db[name].aggregate(pipeline)
            def __getattr__(self,name): raise AssertionError('Unapproved mutation or operation: '+name)
        return Collection()


@pytest.mark.parametrize('count',[1,80])
def test_bounded_owner_scoped_reads_do_not_grow_per_order_or_component(count):
    async def test():
        async with database() as db:
            order,_=await seed(db)
            baseline={name:await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
            orders=[order]
            for n in range(1,count):
                number='ORDER-'+str(n);line_id='salla:'+number+':LINE'
                for name,documents in baseline.items():
                    if name not in {'unified_orders','mezan_component_order_lifecycle_v1',PLANS,UNITS}:continue
                    for doc in documents:
                        cloned=deepcopy(doc);cloned.pop('_id',None)
                        if name=='unified_orders':
                            cloned['order_number']=number
                            cloned['raw_by_source']['salla_direct']['id']='order-id-'+str(n)
                            cloned['raw_by_source']['salla_direct']['reference_id']=number
                            orders.append(cloned['raw_by_source']['salla_direct'])
                        elif name=='mezan_component_order_lifecycle_v1':
                            cloned['order_number']=number
                            cloned['bundle_intake_snapshots']={line_id:cloned['bundle_intake_snapshots'][LINE]}
                        elif name==PLANS:
                            cloned.update(_id='plan-'+str(n),order_id=number)
                            cloned['bundle_snapshots']={line_id:cloned['bundle_snapshots'][LINE]}
                            cloned['lines'][0]['order_item_id']=line_id
                        else:
                            cloned.update(_id='unit-'+str(n)+'-'+str(cloned['unit_index']),plan_id='plan-'+str(n),
                                          order_id=number,order_line_id=line_id)
                        await db[name].insert_one(cloned)
            audit=ReadAudit(db)
            out=await load_remaining_bundles(audit,OWNER,orders,NOW)
            assert len(out)==count and all(line['complete'] for line in out.values())
            assert sum(Decimal(i['cost']) for line in out.values() for i in line['items'])==Decimal(220*count)
            assert len(audit.reads)==7, 'A query per order/leaf or extra source was introduced'
            assert 'mezan_order_cost_corrections_v1' not in {name for name,_ in audit.reads}
    run(test())
