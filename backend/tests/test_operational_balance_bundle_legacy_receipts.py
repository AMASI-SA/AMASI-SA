"""R03: actual frozen capture + legacy receiving + operational reducer/report.

All records are synthetic. Populated integration cases use isolated Mongo.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal
import pytest

import test_bundle_reporting_financial as financial
from test_operational_balance_remaining_products import database, state, START, NOW, CREATED
from operational_remaining_bundle_sources import normalized_orders, load_remaining_bundles
from operational_balance_sources import collect_sources
from operational_balance_engine import reconcile
from operational_open_cost_exposure import project
from operational_bundle_legacy_receipts import map_financial_receipts
from order_cost_authority import line_fingerprint

OWNER=financial.OWNER
LINE='salla:ORDER:LINE'


async def fixture(db, monkeypatch, *, rose=True, name=True, schema=2, created=CREATED):
    source = await financial.db.__wrapped__(monkeypatch)
    if schema == 2:
        await financial.schema2_service_fixture(source)
    raw={'id':'order-id','reference_id':'ORDER','created_at':created,'updated_at':created,
         'status':'in_progress','currency':'KWD','amounts':{'total':{'amount':'40','currency':'KWD'}},
         'items':[{'id':'LINE','product_id':financial.ROOT,'quantity':4,'options':[
             {'id':'rose-choice','name':financial.ROSE_NAME,'value':{'id':'yes' if rose else 'no','name':'نعم' if rose else 'لا'}},
             {'id':'name-choice','name':financial.NAME_NAME,'value':{'id':'yes' if name else 'no','name':'نعم' if name else 'لا'}}]}]}
    item=normalized_orders([raw])[0]['items'][0]
    item['order_line_id']=LINE
    capture_line={**item,'options_raw':financial.sale_line(rose=rose,name=name)['options_raw']}
    assert line_fingerprint(capture_line)==line_fingerprint(item)
    reporting,blocked={},{}
    snapshots=await financial.fulfillment.capture_intake(source,OWNER,[capture_line],{},
        reporting_costs=reporting,blocked_intakes=blocked,order_created_at=created)
    for collection in await source.list_collection_names():
        records=await source[collection].find({}).to_list(None)
        if records:await db[collection].insert_many(deepcopy(records))
    await db.unified_orders.insert_one({'user_id':OWNER,'order_number':'ORDER','raw_by_source':{'salla_direct':raw}})
    await db.mezan_component_order_lifecycle_v1.insert_one({'user_id':OWNER,'order_number':'ORDER',
        'bundle_intake_snapshots':snapshots,'reporting_cost_snapshots':reporting})
    return raw,item,reporting[LINE]['bundle_snapshot']


async def pieces(db, *, received=0, completed=0, name=True, partial=False):
    for i in range(1,5):
        p={'user_id':OWNER,'id':f'piece{i}','order_number':'ORDER','order_item_id':LINE,
            'unit_index':i,'product_id':financial.ROOT,'created_at':CREATED,'services':[]}
        if i<=received:
            p.update(supplier_dispatch_status='received',execution_status='received_from_supplier',
                received_at='2026-11-02T09:00:00Z',supplier_id='external-supplier')
        if name:
            service={'service_id':financial.SERVICE,'required_quantity':1,'completed_quantity':0,'status':'pending'}
            if i<=completed:
                service.update(completed_quantity=.5 if partial else 1,status='in_progress' if partial else 'completed',
                    completed_by_supplier_id='external-supplier',completed_at='2026-11-02T09:00:00Z')
            p['services']=[service]
        await db.mezan_preparation_pieces_v1.insert_one(p)


async def totals(db):
    before={n:await db[n].find({}).to_list(None) for n in await db.list_collection_names()}
    sources=await collect_sources(db,OWNER,START,NOW)
    output=reconcile(state(),sources,NOW)
    assert output==reconcile(output,sources,NOW)
    exposure=await project(db,OWNER,output,as_of=NOW)
    after={n:await db[n].find({}).to_list(None) for n in await db.list_collection_names()}
    assert before==after
    rows=[r for r in output['engine']['obligations'].values() if r['kind']=='supplier']
    assert all(r['confirmed']=='0.00' for r in rows)
    return (sum(Decimal(r['expected']) for r in rows if not r['party_id']),
            sum(Decimal(r['expected']) for r in rows if r['party_id']),sources,exposure)


@pytest.mark.parametrize('schema',[1,2])
@pytest.mark.parametrize('rose,name,amount',[(True,True,'260'),(True,False,'220'),(False,True,'100'),(False,False,'60')])
def test_financial_envelope_preserves_main_price_choices_once(monkeypatch,schema,rose,name,amount):
    async def test():
        async with database() as db:
            await fixture(db,monkeypatch,rose=rose,name=name,schema=schema)
            a,b,s,e=await totals(db)
            assert a==Decimal(amount) and b==0,s['issues']
            assert not any('bundle_' in p['code'] for p in s['issues']),s['issues']
            assert e['currencies']['SAR']['poststart_supplier_unreceived']==format(a,'.2f')
    asyncio.run(test())


@pytest.mark.parametrize('received,completed,expected,at_supplier',[(4,0,200,60),(0,4,220,40),(4,2,180,80),(4,4,160,100)])
def test_parent_and_existing_external_service_receipts_are_independent(monkeypatch,received,completed,expected,at_supplier):
    async def test():
        async with database() as db:
            await fixture(db,monkeypatch)
            await pieces(db,received=received,completed=completed)
            a,b,s,e=await totals(db)
            assert (a,b)==(Decimal(expected),Decimal(at_supplier)),s['issues']
            assert e['currencies']['SAR']['poststart_supplier_unreceived']==format(a,'.2f')
            assert e['currencies']['SAR']['poststart_supplier_received_uninvoiced']==format(b,'.2f')
    asyncio.run(test())


def test_partial_external_service_receipt_does_not_remove_the_whole_service(monkeypatch):
    async def test():
        async with database() as db:
            await fixture(db,monkeypatch)
            await pieces(db,completed=1,partial=True)
            a,b,_,_=await totals(db)
            assert a==255 and b==5
    asyncio.run(test())


@pytest.mark.parametrize('damage',['duplicate-index','owner','product','future','service-missing','quantity','completed-without-proof'])
def test_ambiguous_legacy_receipts_do_not_show_complete_zero(monkeypatch,damage):
    async def test():
        async with database() as db:
            raw,item,snapshot=await fixture(db,monkeypatch)
            await pieces(db,received=1,completed=1)
            ps=await db.mezan_preparation_pieces_v1.find({}).to_list(None)
            if damage=='duplicate-index':ps[1]['unit_index']=1
            if damage=='owner':ps[0]['user_id']='other'
            if damage=='product':ps[0]['product_id']='other'
            if damage=='future':ps[0]['received_at']='2027-01-01T00:00:00Z'
            if damage=='service-missing':ps[0]['services']=[]
            if damage=='quantity':ps[0]['services'][0]['required_quantity']=2
            if damage=='completed-without-proof':ps[0]['services'][0].pop('completed_by_supplier_id')
            before=deepcopy((snapshot,ps))
            with pytest.raises(ValueError):map_financial_receipts(owner=OWNER,number='ORDER',item=item,
                snapshot=snapshot,pieces=ps,native_units=None,as_of=NOW)
            assert before==(snapshot,ps)
    asyncio.run(test())


def test_old_order_receipt_before_invoice_is_not_unreceived(monkeypatch):
    async def test():
        async with database() as db:
            await fixture(db,monkeypatch,created='2026-10-25T10:00:00Z')
            await pieces(db,received=4,completed=2)
            a,b,s,e=await totals(db)
            assert a==b==0 # Never replay the old order into financial state.
            assert e['currencies']['SAR']['prestart_supplier_unreceived']=='180.00'
            assert e['currencies']['SAR']['prestart_supplier_received_uninvoiced']=='80.00'
    asyncio.run(test())


def test_no_catalog_repricing_or_source_mutation(monkeypatch):
    async def test():
        async with database() as db:
            await fixture(db,monkeypatch)
            first=(await totals(db))[:2]
            await db.mezan_product_cost_profiles_v2.update_many({}, {'$set':{'base_cost':999,'cost_currency':'KWD'}})
            await db.mezan_cost_resources_v2.update_many({}, {'$set':{'unit_cost':999}})
            assert (await totals(db))[:2]==first==(Decimal(260),Decimal(0))
    asyncio.run(test())


def test_first_product_supplier_remains_correct_after_final_service_supplier_changes(monkeypatch):
    async def test():
        async with database() as db:
            raw,item,snap=await fixture(db,monkeypatch)
            await pieces(db,received=4,completed=4)
            await db.mezan_preparation_pieces_v1.update_many({}, {'$set':{
                'supplier_id':'last-service-vendor', 'supplier_receiving_history':[
                    {'invoice_id':'product-invoice','received_at':'2026-11-02T08:00:00Z','supplier_id':'first-product-vendor'},
                    {'invoice_id':'service-invoice','received_at':'2026-11-02T09:00:00Z','supplier_id':'last-service-vendor'}]}})
            ps=await db.mezan_preparation_pieces_v1.find({}).to_list(None)
            _,_,items=map_financial_receipts(owner=OWNER,number='ORDER',item=item,snapshot=snap,pieces=ps,native_units=None,as_of=NOW)
            bases=[i for i in items if ':service:' not in i['id']]
            assert len(bases)==4 and all(i['supplier_id']=='first-product-vendor' for i in bases)
    asyncio.run(test())


@pytest.mark.parametrize('bad',['duplicate-index','owner','missing-unit','missing-leaf','identity','future'])
def test_native_coverage_cannot_disappear_when_all_leaves_remap_to_legacy(monkeypatch,bad):
    async def test():
        async with database() as db:
            raw,item,snap=await fixture(db,monkeypatch,rose=False,name=True,schema=1)
            units=[{'user_id':OWNER,'order_id':'ORDER','order_line_id':LINE,'unit_index':i,'state':'reserved',
                'created_at':CREATED,'bundle_leaves':deepcopy(snap['leaves'])} for i in range(1,5)]
            if bad=='duplicate-index':units[1]['unit_index']=1
            if bad=='owner':units[0]['user_id']='other'
            if bad=='missing-unit':units.pop()
            if bad=='missing-leaf':units[0]['bundle_leaves']=[]
            if bad=='identity':units[0]['bundle_leaves'][0]['resource_id']='other'
            if bad=='future':units[0]['created_at']='2027-01-01T00:00:00Z'
            with pytest.raises(ValueError):map_financial_receipts(owner=OWNER,number='ORDER',item=item,
                snapshot=snap,pieces=[],native_units=units,as_of=NOW)
    asyncio.run(test())


def test_final_legacy_invoice_replaces_parent_and_service_without_readding_estimate(monkeypatch):
    async def test():
        async with database() as db:
            await fixture(db,monkeypatch)
            await pieces(db,received=4,completed=4)
            await db.mezan_suppliers_v2.update_one({'user_id':OWNER,'id':'external-supplier'},
                {'$set':{'name':'Synthetic supplier','status':'active'}},upsert=True)
            await db.mezan_supplier_invoices_v2.insert_one({'user_id':OWNER,'id':'final','invoice_number':'FINAL',
                'supplier_id':'external-supplier','status':'payable_posted','currency':'SAR','approved_at':'2026-11-02T10:00:00Z',
                'subtotal_halalas':8000,'total_halalas':8000,'lines':[{
                    'piece_ids':['piece1','piece2','piece3','piece4'],'quantity':4,
                    'product_price_authority':'mezan_v2','product_unit_price_halalas':1200,'product_total_halalas':4800,
                    'services':[{'service_id':financial.SERVICE,'quantity_per_piece':1,'unit_price_halalas':800,'total_halalas':3200}],
                    'total_halalas':8000}]})
            s=await collect_sources(db,OWNER,START,NOW)
            output=reconcile(state(),s,NOW)
            obligations=[r for r in output['engine']['obligations'].values() if r['kind']=='supplier']
            assert sum(Decimal(o['confirmed']) for o in obligations)==80,s['issues']
            assert sum(Decimal(o['expected']) for o in obligations)==160,s['issues'] # only unreceived roses
            assert output==reconcile(output,s,NOW)
            assert not [o for o in obligations if o['party_id'] and Decimal(o['expected'])]
    asyncio.run(test())
