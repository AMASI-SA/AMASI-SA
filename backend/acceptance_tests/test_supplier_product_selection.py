"""Owner acceptance matrix: order number or canonical physical-piece barcode.
Standalone Product ID/SKU lookup is explicitly outside the clarified acceptance contract.

Run explicitly with isolated loopback Mongo. No application patches or cost mocks.
Authentication alone is replaced with a synthetic employee; real route/DB logic runs.
"""
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import supplier_receiving_routes as r

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def env(monkeypatch):
    uri = os.environ.get('BUILD37_TEST_MONGO_URI', '')
    assert urlparse(uri).hostname in {'127.0.0.1', 'localhost'}, 'isolated Mongo required'
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    await client.admin.command('ping')
    db = client['build37_selection_' + uuid.uuid4().hex]
    async def actor(_db, user):
        return {'merchant_id': 'merchant', 'actor_id': user['id'], 'is_owner': False,
                'permissions': [r.RECEIVE_PERMISSION]}
    async def user():
        return {'id': 'receiver', 'name': 'Synthetic employee'}
    monkeypatch.setattr(r, '_base_actor_context', actor)
    app = FastAPI()
    app.include_router(r.make_supplier_receiving_router(db, user))
    await r.ensure_supplier_receiving_indexes(db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated.test') as http:
        try:
            yield db, http
        finally:
            await client.drop_database(db.name)
            client.close()


async def seed(env, *, assigned=True, variant=False, product_id='product-1', sku='SKU-100', order='900001'):
    db, _ = env
    now = datetime.now(timezone.utc)
    await db[r.SESSIONS].insert_one({'user_id':'merchant', 'id':'session-1', 'status':'open',
        'opened_by':'receiver', 'opened_at':now, 'reference':'SR-TEST', 'scan_count':0,
        'supplier_id':'supplier-A', 'supplier_snapshot':{'id':'supplier-A','company_name':'Supplier A','service_links':[]}})
    await db[r.PRODUCTS].insert_one({'user_id':'merchant','id':product_id,'salla_product_id':'salla-1','sku':sku,'created_at':now if product_id=='new-product' else datetime(2020,1,1,tzinfo=timezone.utc)})
    await db[r.COST_PROFILES].insert_one({'user_id':'merchant','salla_product_id':'salla-1','base_cost':10})
    await db[r.RESOURCES].insert_one({'user_id':'merchant','id':'service-1','kind':'service','name':'Tailoring',
        'status':'active','unit_cost':3,'requires_preparation':True})
    await db[r.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'merchant','salla_product_id':'salla-1',
        'resource_id':'service-1','supplier_invoice_required':True})
    piece={'user_id':'merchant','piece_id':'a'*32,'batch_id':'batch-1','file_number':'file-1','group_key':'group-1',
        'order_number':order,'order_item_id':'item-1','unit_index':1,'product_id':product_id,'product_name':'Synthetic item',
        'sku':sku,'status':r.PIECE_STATUS_IN_PROGRESS,'execution_status':'supplier_sent',
        'supplier_id':'supplier-A' if assigned else None, 'supplier_name':'Supplier A' if assigned else None,
        'supplier_dispatch_status':'sent' if assigned else 'pending',
        'responsible_employee_id':'preparer','product_options_snapshot':{'color':'red','name':'Sample'},
        'services':[{'service_id':'service-1','service_name':'Tailoring','status':'pending',
                     'quantity':1,'source':'product','supplier_invoice_required':True}]}
    if variant:
        piece['variant_id']='variant-1'
    await db[r.PIECES].insert_one(dict(piece))
    return piece


async def search(env, query):
    return await env[1].get('/supplier-receiving-v1/sessions/session-1/search', params={'q':query})


def searched_piece(response, piece):
    assert response.status_code == 200, response.text
    return next(p for p in response.json()['pieces'] if p['piece_id']==piece['piece_id'])


@pytest.mark.parametrize('query,assigned', [('MEZAN-PIECE:'+'a'*32,True),('a'*32,True),('MEZAN-PIECE:'+'a'*32,False)])
async def test_AB_piece_barcode_search_assignment_acceptance(env, query, assigned):
    piece=await seed(env,assigned=assigned)
    row=searched_piece(await search(env,query),piece)
    assert row['previous_supplier_id']==('supplier-A' if assigned else None)
    assert row['can_add_to_current_invoice'] is assigned


async def test_C_other_supplier_requires_confirmation_and_scan_rejects_without_it(env):
    piece=await seed(env)
    await env[0][r.SESSIONS].update_one({'id':'session-1'},{'$set':{'supplier_id':'supplier-B','supplier_snapshot':{'id':'supplier-B','company_name':'Supplier B'}}})
    row=searched_piece(await search(env,piece['order_number']),piece)
    assert row['previous_supplier_id']=='supplier-A'
    assert row['current_supplier_id']=='supplier-B'
    assert row['requires_supplier_reassignment_confirmation'] is True
    response=await env[1].post('/supplier-receiving-v1/sessions/session-1/scan',json={
        'barcode':row['barcode'],'quantity':1,'client_request_id':'acceptance-cross-supplier'})
    assert response.status_code==409, response.text
    assert response.json()['detail']['code']=='supplier_piece_dispatched_to_different_supplier'
    assert await env[0][r.RECEIVING_EVENTS].count_documents({'event_type':'supplier_piece_scanned'})==0


@pytest.mark.parametrize('assigned',[True,False])
async def test_AB_order_search_live_assignment_control(env,assigned):
    piece=await seed(env,assigned=assigned)
    row=searched_piece(await search(env,piece['order_number']),piece)
    assert row['can_add_to_current_invoice'] is assigned
    assert row['previous_supplier_id']==('supplier-A' if assigned else None)
    if not assigned:
        assert row['blocker_code']=='supplier_piece_not_dispatched'


@pytest.mark.parametrize('variant',[False,True])
async def test_DE_order_selection_preserves_piece_product_sku_variant_options(env,variant):
    piece=await seed(env,variant=variant)
    row=searched_piece(await search(env,piece['order_number']),piece)
    assert (row['product_id'],row['sku'])==(piece['product_id'],piece['sku'])
    assert {'name':'color','value':'red'} in row['specifications']
    response=await env[1].post('/supplier-receiving-v1/sessions/session-1/scan',json={
        'barcode':row['barcode'],'quantity':1,'client_request_id':'acceptance-order-selected'})
    assert response.status_code==200,response.text
    scan=response.json()['scans'][0]
    for field in ('piece_id','product_id','sku','order_item_id'):
        assert scan[field]==piece[field]
    assert scan.get('variant_id')==piece.get('variant_id')
    saved=await env[0][r.PIECES].find_one({'piece_id':piece['piece_id']})
    assert saved['product_options_snapshot']==piece['product_options_snapshot']
    assert saved['supplier_id']=='supplier-A'
    assert saved['supplier_receiving_session_id']=='session-1'
    assert await env[0][r.SUPPLIER_INVOICES].count_documents({})==0


async def test_F_cost_service_and_draft_from_order_selection(env):
    piece=await seed(env)
    row=searched_piece(await search(env,piece['order_number']),piece)
    response=await env[1].post('/supplier-receiving-v1/sessions/session-1/scan',json={
        'barcode':row['barcode'],'quantity':1,'client_request_id':'acceptance-cost-services'})
    assert response.status_code==200,response.text
    scan=response.json()['scans'][0]
    assert scan['reference_product_unit_price_halalas']==1000
    assert scan['product_price_authority']=='mezan_v2'
    assert scan['salla_price_fallback_allowed'] is False
    assert scan['invoice_services'][0]['service_id']=='service-1'
    assert scan['invoice_services'][0]['reference_unit_price_halalas']==300
    session=await env[0][r.SESSIONS].find_one({'id':'session-1'})
    draft=r.build_supplier_receiving_invoice(session=session,scans=[scan],saved_at=datetime.now(timezone.utc),
        requested_lines=[r.SupplierReceivingInvoiceLineRequest(piece_ids=[piece['piece_id']],product_unit_price_halalas=1000,
            services=[r.SupplierReceivingInvoiceServiceRequest(service_id='service-1',unit_price_halalas=300)])])
    assert draft['total_halalas']==1300
    assert draft['lines'][0]['piece_ids']==[piece['piece_id']]
    assert await env[0][r.SUPPLIER_INVOICES].count_documents({})==0


async def test_G_new_product_search_acceptance(env):
    piece=await seed(env,product_id='new-product',sku='NEW-900')
    row=searched_piece(await search(env,'MEZAN-PIECE:'+piece['piece_id']),piece)
    assert row['previous_supplier_id']=='supplier-A'


async def test_H_similar_sku_piece_barcode_matches_exact_identity(env):
    piece=await seed(env)
    other={**piece,'piece_id':'b'*32,'product_id':'product-10','sku':'SKU-1000','order_item_id':'item-2'}
    await env[0][r.PIECES].insert_one(other)
    response=await search(env,'MEZAN-PIECE:'+piece['piece_id'])
    row=searched_piece(response,piece)
    assert row['sku']=='SKU-100'
    assert response.json()['matched_piece_id']==piece['piece_id']
    assert response.json()['pieces'][0]['piece_id']==piece['piece_id']
    assert len(response.json()['pieces'])==2  # Order context remains visible; matched piece first.


async def test_I_reload_reads_current_piece_assignment_not_previous_response(env):
    piece=await seed(env)
    assert searched_piece(await search(env,piece['order_number']),piece)['can_add_to_current_invoice'] is True
    await env[0][r.PIECES].update_one({'piece_id':piece['piece_id']},{'$set':{'supplier_id':None,'supplier_dispatch_status':'pending'}})
    row=searched_piece(await search(env,piece['order_number']),piece)
    assert row['can_add_to_current_invoice'] is False and row['previous_supplier_id'] is None
    await env[0][r.PIECES].update_one({'piece_id':piece['piece_id']},{'$set':{'supplier_id':'supplier-B','supplier_dispatch_status':'sent'}})
    row=searched_piece(await search(env,piece['order_number']),piece)
    assert row['previous_supplier_id']=='supplier-B' and row['requires_supplier_reassignment_confirmation'] is True


async def test_search_without_barcode_is_read_only_and_tenant_scoped(env):
    piece=await seed(env)
    await env[0][r.PIECES].insert_one({**piece,'piece_id':'b'*32,'user_id':'another-merchant'})
    before=await env[0][r.PIECES].find_one({'piece_id':piece['piece_id']})
    response=await search(env,'#'+piece['order_number'])
    assert response.status_code==200 and len(response.json()['pieces'])==1
    assert await env[0][r.PIECES].find_one({'piece_id':piece['piece_id']})==before
    assert await env[0][r.RECEIVING_EVENTS].count_documents({})==0

async def test_F_selected_customer_option_cost_is_retained_through_scan_and_refresh(env):
    from product_option_cost_routes import BINDINGS
    piece=await seed(env,variant=True)
    await env[0][r.COST_PROFILES].update_one({'salla_product_id':'salla-1'},
        {'$set':{'variant_costs':{'variant-1':12.75}}})
    await env[0][BINDINGS].insert_many([
        {'user_id':'merchant','salla_product_id':'salla-1','mode':'direct','option_name':'color',
         'option_id':'color','value_name':'red','value_id':'red','direct_amount':2.25,'quantity':1},
        {'user_id':'merchant','salla_product_id':'salla-1','mode':'direct','option_name':'color',
         'option_id':'color','value_name':'blue','value_id':'blue','direct_amount':99,'quantity':1},
    ])
    row=searched_piece(await search(env,piece['order_number']),piece)
    response=await env[1].post('/supplier-receiving-v1/sessions/session-1/scan',json={
        'barcode':row['barcode'],'quantity':1,'client_request_id':'acceptance-selected-option'})
    assert response.status_code==200,response.text
    scan=response.json()['scans'][0]
    # 12.75 variant + selected red option 2.25; blue is not selected.
    assert scan['reference_product_unit_price_halalas']==1500, scan
    response=await env[1].post('/supplier-receiving-v1/sessions/session-1/refresh',json={})
    assert response.status_code==200,response.text
    scan=response.json()['scans'][0]
    assert scan['reference_product_unit_price_halalas']==1500
    assert scan['reference_product_option_cost_halalas']==225
    assert scan['invoice_services'][0]['reference_unit_price_halalas']==300
    assert scan['variant_id']=='variant-1'
    assert scan['piece_id']==piece['piece_id']
    assert await env[0][r.SUPPLIER_INVOICES].count_documents({})==0
