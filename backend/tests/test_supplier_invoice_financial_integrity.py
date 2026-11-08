"""Transaction and persisted presentation-source contracts on disposable Mongo.
No Production connection, merchant data, provider API, migration or repair.
The PDF renderer is observed at its data boundary; its output is not analysed.
"""
from __future__ import annotations
import asyncio
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

import supplier_receiving_routes as r
import supplier_invoice_integrity as integrity

pytestmark = pytest.mark.asyncio

@pytest_asyncio.fixture
async def env(monkeypatch):
    url = os.environ.get('BUILD20_INVOICE_TEST_MONGO_URL', '')
    assert urlparse(url).hostname in {'127.0.0.1', 'localhost'}, 'Disposable loopback Mongo required'
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    await client.admin.command('ping')
    name = 'build20_invoice_test_' + uuid.uuid4().hex
    db = client[name]
    async def base_context(_db, _user):
        return {'merchant_id': 'merchant', 'actor_id': 'merchant', 'is_owner': True,
                'permissions': [], 'warehouse_ids': None, 'responsibilities': set()}
    monkeypatch.setattr(r, '_base_actor_context', base_context)
    user = {'id':'merchant', 'name':'Synthetic owner', '_session_client':r.MOBILE_APP_CLIENT,
            '_mobile_owner_id':'merchant', '_mobile_actor_id':'employee', '_mobile_actor_name':'Synthetic receiver',
            '_mobile_app_permissions':[r.MOBILE_MY_PRODUCTS_PAGE_PERMISSION, r.MOBILE_EDIT_PRODUCT_PRICE_PERMISSION]}
    async def current(): return user
    app = FastAPI(); app.include_router(r.make_supplier_receiving_router(db, current))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic.test') as http:
        yield db, http, user
    await client.drop_database(name); client.close()

async def seed(db, amounts=(2100,), *, services=False, experiment=False):
    now = datetime.now(timezone.utc)
    session_id = 'session-' + uuid.uuid4().hex
    supplier = {'id':'supplier', 'company_name':'Synthetic supplier', 'phone':'', 'service_links':[]}
    await db[r.SUPPLIERS].update_one({'user_id':'merchant','id':'supplier'}, {'$set':supplier}, upsert=True)
    session = {'id':session_id,'user_id':'merchant','client_request_id':uuid.uuid4().hex,
               'reference':'SR-TEST-'+uuid.uuid4().hex[:8], 'status':'open','supplier_id':'supplier',
               'supplier_snapshot':supplier, 'opened_by':'employee','opened_by_name':'Synthetic receiver',
               'opened_at':now,'scan_count':len(amounts),'order_numbers':['123456789'],'file_numbers':['file']}
    await db[r.SESSIONS].insert_one(dict(session))
    payload_lines=[]
    if services:
        await db[r.RESOURCES].insert_one({'user_id':'merchant','id':'svc','name':'Synthetic service','code':'SVC',
            'kind':'service','unit_cost':3.25,'unit':'job','status':'active','track_inventory':False})
    for i, amount in enumerate(amounts):
        pid=f'{session_id}-p{i}'; product=f'{session_id}-product{i}'; event_id=f'{session_id}-e{i}'
        svc=[{'service_id':'svc','service_name':'Synthetic service','required_quantity':1,'status':'pending',
              'customer_selected':True,'supplier_invoice_required':True,'reference_unit_price_halalas':325}] if services else []
        piece={'user_id':'merchant','piece_id':pid,'product_id':product,'product_name':'Synthetic item','sku':product,
               'order_number':'123456789','order_item_id':str(i),'unit_index':1,'status':r.PIECE_STATUS_IN_PROGRESS,
               'supplier_receiving_session_id':session_id,'receipt_event_id':event_id,'services':svc}
        if experiment: piece['experiment_run_id']='synthetic-experiment'
        await db[r.PIECES].insert_one(dict(piece))
        await db[r.PRODUCTS].insert_one({'user_id':'merchant','id':product,'mezan_product_id':product,
                                       'salla_product_id':product,'name':'Synthetic item','sku':product})
        await db[r.COST_PROFILES].insert_one({'user_id':'merchant','salla_product_id':product,
                                            'base_cost':amount/100,'mezan_product_id':product})
        event={**piece, 'id':event_id, 'session_id':session_id, 'event_type':'supplier_piece_scanned',
               'occurred_at':now, 'product_charge_eligible':True, 'invoice_services':svc,
               'reference_product_unit_price_halalas':amount, 'reference_product_price_source':'mezan_v2_base',
               'reference_product_price_complete':True}
        await db[r.RECEIVING_EVENTS].insert_one(dict(event));await db[r.PIECE_EVENTS].insert_one(dict(event))
        payload_lines.append({'piece_ids':[pid], 'product_unit_price_halalas':amount,
                              'services':[{'service_id':'svc','unit_price_halalas':325}] if services else []})
    return session, {'expected_supplier_id':'supplier', 'confirmed_total_halalas':sum(amounts)+(325*len(amounts) if services else 0),
                     'invoice_lines':payload_lines}

async def close(http, session, payload):
    return await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)

async def unchanged(db, session):
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
    assert await db.general_ledger.count_documents({}) == 0
    assert await db.accounting_audit_log.count_documents({}) == 0
    saved=await db[r.SESSIONS].find_one({'id':session['id']})
    assert saved['status']=='open' and not saved.get('supplier_invoice')
    pieces=await db[r.PIECES].find({}).to_list(100)
    assert all(p['status']==r.PIECE_STATUS_IN_PROGRESS and p.get('receipt_event_id') for p in pieces)
    assert await db[r.RECEIVING_EVENTS].count_documents({'event_type':'supplier_piece_scanned'}) == len(pieces)
    assert await db[r.RECEIVING_EVENTS].count_documents({'event_type':'supplier_receiving_session_closed'}) == 0

@pytest.mark.parametrize('amounts,services', [((2100,),False),((32750,),False),((1100,4200,6300),True)])
async def test_exact_amount_actor_tenant_readback_and_presentation_source(env, monkeypatch, amounts, services):
    db,http,_=env;s,p=await seed(db,amounts,services=services);res=await close(http,s,p)
    assert res.status_code==200, res.text
    body=res.json();inv=body['supplier_invoice'];total=p['confirmed_total_halalas']
    assert body['financial_integrity_verified'] is True and body['financial_invoice_created'] is True and body['liability_created'] is True
    assert inv['total_halalas']==total and inv['supplier_id']=='supplier' and inv['session_id']==s['id']
    assert inv['approved_by']==inv['supplier_approved_by']=='employee'
    stored=await db[r.SUPPLIER_INVOICES].find_one({'id':inv['id']}); assert stored['user_id']=='merchant'
    ledger=await db.general_ledger.find({'txn_group_id':inv['ledger_txn_group_id']}).to_list(10)
    assert len(ledger)==2 and all(integrity.ledger_halalas(x['amount'])==total for x in ledger)
    credit=next(x for x in ledger if x['side']=='credit')
    assert credit['entity_id']=='supplier' and credit['entity_type']=='supplier' and credit['sub_account']=='payable'
    assert all(x['posted_by']=='employee' and x['user_id']=='merchant' for x in ledger)
    assert await db.accounting_audit_log.count_documents({'actor_id':'employee'})==2
    saved=await db[r.SESSIONS].find_one({'id':s['id']})
    for k in ['id','invoice_number','supplier_id','session_id','total_halalas','currency','approved_at','ledger_txn_group_id','ledger_entry_ids']:
        assert stored[k]==saved['supplier_invoice'][k]
    reread=await http.get('/supplier-receiving-v1/invoices/'+inv['id']);assert reread.status_code==200
    assert reread.json()['supplier_invoice']['total_halalas']==total
    captured=[]
    def render(document, **kwargs):
        captured.append(deepcopy(document))
        return b'synthetic-presentation-output'
    monkeypatch.setattr(r,'generate_supplier_invoice_pdf',render)
    response=await http.get('/supplier-receiving-v1/invoices/'+inv['id']+'/pdf')
    assert response.status_code==200 and len(captured)==1
    assert response.content==b'synthetic-presentation-output'
    for key in ['id','invoice_number','supplier_id','session_id','lines','total_halalas']:
        assert captured[0][key]==stored[key]
    assert captured[0]['total_halalas']==total

async def test_open_draft_omits_piece_received_by_another_employee(env):
    db, http, user = env
    other_session, other_payload = await seed(db, (1500,))
    transferred_line = other_payload["invoice_lines"][0]
    transferred_piece = transferred_line["piece_ids"][0]
    scan_snapshot = await db[r.RECEIVING_EVENTS].find_one({"piece_id": transferred_piece}, {"_id": 0})
    await db[r.SESSIONS].update_one({"id": other_session["id"]}, {"$set": {
        "opened_by": "other-employee", "opened_by_name": "Synthetic second receiver",
    }})
    user["_mobile_actor_id"] = "other-employee"
    other_receipt = await close(http, other_session, other_payload)
    assert other_receipt.status_code == 200, other_receipt.text
    other_invoice_id = other_receipt.json()["supplier_invoice"]["id"]
    original_invoice = await db[r.SUPPLIER_INVOICES].find_one({"id": other_invoice_id})
    assert original_invoice["approved_by"] == "other-employee"

    user["_mobile_actor_id"] = "employee"
    session, payload = await seed(db, (1800,))
    retained_line = payload["invoice_lines"][0]
    retained_piece = retained_line["piece_ids"][0]
    for collection in (r.PIECES, r.RECEIVING_EVENTS, r.PIECE_EVENTS):
        await db[collection].update_one(
            {"piece_id": retained_piece}, {"$set": {"order_item_id": "retained-item"}},
        )
    # A legacy draft scan can outlive its live reservation. The other
    # employee's receipt and its posted invoice are already authoritative.
    old_scan = {
        **scan_snapshot, "id": "stale-scan", "receipt_event_id": "stale-scan",
        "session_id": session["id"], "supplier_receiving_session_id": session["id"],
    }
    for collection in (r.RECEIVING_EVENTS, r.PIECE_EVENTS):
        await db[collection].insert_one(dict(old_scan))
    await db[r.SESSIONS].update_one({"id": session["id"]}, {"$set": {"scan_count": 2}})
    endpoint = f"/supplier-receiving-v1/sessions/{session['id']}"
    for response in (await http.get(endpoint), await http.post(endpoint + "/refresh")):
        assert response.status_code == 200, response.text
        assert [scan["piece_id"] for scan in response.json()["scans"]] == [retained_piece]
        assert response.json()["session"]["scan_count"] == 1
    catalog = await http.get("/supplier-receiving-v1/catalog")
    assert catalog.status_code == 200, catalog.text
    assert [scan["piece_id"] for scan in catalog.json()["active_session_scans"]] == [retained_piece]
    assert catalog.json()["active_session"]["scan_count"] == 1

    stale_approval = await close(http, session, {
        **payload, "confirmed_total_halalas": 3300,
        "invoice_lines": [transferred_line, retained_line],
    })
    assert stale_approval.status_code == 409, stale_approval.text
    assert stale_approval.json()["detail"]["code"] == "supplier_receiving_invoice_piece_mismatch"
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
    assert await db.general_ledger.count_documents({}) == 2
    corrected = await close(http, session, payload)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["supplier_invoice"]["total_halalas"] == 1800
    assert await db[r.SUPPLIER_INVOICES].find_one({"id": other_invoice_id}) == original_invoice
    assert await db[r.RECEIVING_EVENTS].find_one({"id": old_scan["id"]}, {"_id": 0}) == old_scan


async def test_presentation_rereads_saved_invoice_and_ignores_independent_draft_values(env,monkeypatch):
    db,http,_=env;s,p=await seed(db,(32750,));res=await close(http,s,p);assert res.status_code==200,res.text
    iid=res.json()['supplier_invoice']['id']
    # Synthetic non-financial label change proves the GET is a fresh document read.
    await db[r.SUPPLIER_INVOICES].update_one({'id':iid},{'$set':{'lines.0.product_name':'Persisted after commit'}})
    persisted=await db[r.SUPPLIER_INVOICES].find_one({'id':iid},{'_id':0})
    captured=[]
    def render(document, **kwargs): captured.append(deepcopy(document));return b'presentation-only'
    monkeypatch.setattr(r,'generate_supplier_invoice_pdf',render)
    response=await http.get('/supplier-receiving-v1/invoices/'+iid+'/pdf',params={
        'total_halalas':1,'supplier_id':'wrong-draft-supplier','invoice_number':'DRAFT','lines':'[]'})
    assert response.status_code==200 and len(captured)==1
    for key in ['id','invoice_number','supplier_id','session_id','lines','total_halalas']:
        assert captured[0][key]==persisted[key]
    assert captured[0]['total_halalas']==32750
    assert captured[0]['lines'][0]['product_name']=='Persisted after commit'

async def test_missing_or_foreign_invoice_never_reaches_presentation_generator(env,monkeypatch):
    db,http,_=env;captured=[]
    def render(document, **kwargs):captured.append(document);return b'presentation-only'
    monkeypatch.setattr(r,'generate_supplier_invoice_pdf',render)
    await db[r.SUPPLIER_INVOICES].insert_one({'id':'foreign','user_id':'other-merchant','supplier_approved_by':'employee'})
    for iid in ['missing','foreign']:
        response=await http.get('/supplier-receiving-v1/invoices/'+iid+'/pdf')
        assert response.status_code==404
    assert captured==[] and await db.general_ledger.count_documents({})==0

async def test_presentation_failure_does_not_change_posted_invoice_or_ledger(env,monkeypatch):
    db,http,_=env;s,p=await seed(db);res=await close(http,s,p);assert res.status_code==200,res.text
    iid=res.json()['supplier_invoice']['id']
    before_invoice=await db[r.SUPPLIER_INVOICES].find_one({'id':iid})
    before_ledger=await db.general_ledger.find({}).sort('id',1).to_list(10)
    before_session=await db[r.SESSIONS].find_one({'id':s['id']})
    def fail(_document, **kwargs):raise RuntimeError('synthetic-presentation-failure')
    monkeypatch.setattr(r,'generate_supplier_invoice_pdf',fail)
    with pytest.raises(RuntimeError,match='synthetic-presentation-failure'):
        await http.get('/supplier-receiving-v1/invoices/'+iid+'/pdf')
    assert before_invoice==await db[r.SUPPLIER_INVOICES].find_one({'id':iid})
    assert before_ledger==await db.general_ledger.find({}).sort('id',1).to_list(10)
    assert before_session==await db[r.SESSIONS].find_one({'id':s['id']})
    assert before_session['status']=='closed' and before_invoice['financial_integrity_verified'] is True

async def test_zero_total_has_no_business_writes(env):
    db,http,_=env;s,p=await seed(db,(0,));p.pop('confirmed_total_halalas')
    await db[r.RECEIVING_EVENTS].update_many({}, {'$set':{'product_charge_eligible':False}})
    response=await close(http,s,p)
    assert response.status_code==422 and response.json()['detail']['code']=='supplier_receiving_invoice_total_required'
    await unchanged(db,s)

@pytest.mark.parametrize('field,value', [('confirmed_total_halalas',2099),('expected_supplier_id','other')])
async def test_confirmed_amount_or_selected_supplier_mismatch(env,field,value):
    db,http,_=env;s,p=await seed(db);p[field]=value;response=await close(http,s,p)
    assert response.status_code==409,response.text
    await unchanged(db,s)

async def test_invoice_insert_failure_rolls_back_ledger(env, monkeypatch):
    db,http,_=env;s,p=await seed(db)
    original=AsyncIOMotorCollection.insert_one
    async def fail(self,*args,**kwargs):
        if self.name==r.SUPPLIER_INVOICES: raise RuntimeError('synthetic invoice insert failure')
        return await original(self,*args,**kwargs)
    monkeypatch.setattr(AsyncIOMotorCollection,'insert_one',fail)
    response=await close(http,s,p); assert response.status_code==503,response.text
    await unchanged(db,s)

async def test_ledger_failure_rolls_back_everything(env,monkeypatch):
    db,http,_=env;s,p=await seed(db)
    original=r._post_supplier_invoice_ledger
    async def fail(*args,**kwargs):
        await original(*args,**kwargs)
        raise RuntimeError('synthetic failure after ledger/audit inserts')
    monkeypatch.setattr(r,'_post_supplier_invoice_ledger',fail)
    response=await close(http,s,p);assert response.status_code==503,response.text
    await unchanged(db,s)

@pytest.mark.parametrize('corruption', ['amount','supplier','session_total','invoice_total','invoice_supplier',
    'missing_flags','missing_group','missing_invoice','missing_summary','second_group','debit','currency','actor'])
async def test_persisted_corruption_before_commit_rolls_back(env,monkeypatch,corruption):
    db,http,_=env;s,p=await seed(db);original=r.verify_persisted_supplier_invoice
    async def corrupt(db, **kw):
        tx=kw['mongo_session']; q={'user_id':'merchant'}
        if corruption=='amount': await db.general_ledger.update_one({**q,'side':'credit'},{'$set':{'amount':20.99}},session=tx)
        elif corruption=='supplier': await db.general_ledger.update_one({**q,'side':'credit'},{'$set':{'entity_id':'other'}},session=tx)
        elif corruption=='debit': await db.general_ledger.update_one({**q,'side':'debit'},{'$set':{'amount':20}},session=tx)
        elif corruption=='currency': await db.general_ledger.update_one(q,{'$set':{'currency':'USD'}},session=tx)
        elif corruption=='actor': await db.general_ledger.update_one(q,{'$set':{'posted_by':'merchant'}},session=tx)
        elif corruption=='session_total': await db[r.SESSIONS].update_one(q,{'$set':{'supplier_invoice.total_halalas':1}},session=tx)
        elif corruption=='missing_summary': await db[r.SESSIONS].update_one(q,{'$unset':{'supplier_invoice':''}},session=tx)
        elif corruption=='invoice_total': await db[r.SUPPLIER_INVOICES].update_one(q,{'$set':{'total_halalas':1}},session=tx)
        elif corruption=='invoice_supplier': await db[r.SUPPLIER_INVOICES].update_one(q,{'$set':{'supplier_id':'other'}},session=tx)
        elif corruption=='missing_flags': await db[r.SUPPLIER_INVOICES].update_one(q,{'$unset':{'liability_created':''}},session=tx)
        elif corruption=='missing_group': await db[r.SUPPLIER_INVOICES].update_one(q,{'$unset':{'ledger_txn_group_id':''}},session=tx)
        elif corruption=='missing_invoice': await db[r.SUPPLIER_INVOICES].delete_many(q,session=tx)
        elif corruption=='second_group':
            row=await db.general_ledger.find_one(q,{'_id':0},session=tx);row.update(id='duplicate',txn_group_id='second')
            await db.general_ledger.insert_one(row,session=tx)
        return await original(db,**kw)
    monkeypatch.setattr(r,'verify_persisted_supplier_invoice',corrupt)
    response=await close(http,s,p);assert response.status_code==409,response.text
    await unchanged(db,s)

async def test_lost_response_read_and_duplicate_close_one_group(env):
    db,http,_=env;s,p=await seed(db);first=await close(http,s,p);assert first.status_code==200,first.text
    state=(await http.get('/supplier-receiving-v1/sessions/'+s['id'])).json()
    inv=state['session']['supplier_invoice'];read=await http.get('/supplier-receiving-v1/invoices/'+inv['id'])
    assert read.status_code==200
    again=await close(http,s,p);assert again.status_code==200,again.text
    assert again.json()['idempotent'] is True and again.json()['supplier_invoice']['id']==inv['id']
    assert await db[r.SUPPLIER_INVOICES].count_documents({})==1
    assert await db.general_ledger.count_documents({})==2
    assert await db.accounting_audit_log.count_documents({})==2

async def test_concurrent_close_one_invoice(env):
    db,http,_=env;s,p=await seed(db)
    await r.ensure_supplier_receiving_indexes(db)
    replies=await asyncio.gather(close(http,s,p),close(http,s,p))
    assert any(x.status_code==200 for x in replies),[x.text for x in replies]
    assert all(x.status_code in (200,409) for x in replies),[x.text for x in replies]
    assert await db[r.SUPPLIER_INVOICES].count_documents({})==1
    assert await db.general_ledger.count_documents({})==2

async def test_closed_without_invoice_is_not_success_and_not_repaired(env):
    db,http,_=env;s,p=await seed(db);await db[r.SESSIONS].update_one({'id':s['id']},{'$set':{'status':'closed'}})
    response=await close(http,s,p);assert response.status_code==409,response.text
    assert await db[r.SUPPLIER_INVOICES].count_documents({})==0
    assert await db.general_ledger.count_documents({})==0

async def test_read_and_presentation_reject_corrupt_persisted_new_invoice_without_repair(env):
    db,http,_=env;s,p=await seed(db);body=(await close(http,s,p)).json();iid=body['supplier_invoice']['id']
    await db.general_ledger.update_one({'side':'credit'},{'$set':{'amount':20}})
    for suffix in ['', '/pdf']:
        response=await http.get('/supplier-receiving-v1/invoices/'+iid+suffix);assert response.status_code==409,response.text
    assert (await db.general_ledger.find_one({'side':'credit'}))['amount']==20


@pytest.mark.parametrize("first_service,second_component,conflict", [
    (False, "product", True),
    (True, "svc", True),
    (False, "svc", False),
])
async def test_two_receipts_of_one_unit_check_saved_invoice_components(
    env, first_service, second_component, conflict
):
    db, http, _ = env
    first, payload = await seed(db, (1500,), services=True)
    line = payload["invoice_lines"][0]
    pid = line["piece_ids"][0]
    piece = await db[r.PIECES].find_one({"piece_id": pid}, {"_id": 0})
    scan = await db[r.RECEIVING_EVENTS].find_one({"piece_id": pid}, {"_id": 0})
    if not first_service:
        line["services"] = []
        payload["confirmed_total_halalas"] = 1500
    approved = await close(http, first, payload)
    assert approved.status_code == 200, approved.text
    first_invoice = await db[r.SUPPLIER_INVOICES].find_one({})
    first_journal = await db.general_ledger.find({}).sort("id", 1).to_list(10)

    # Reproduce a stale piece/scan snapshot in another session. The actual
    # approved invoice, its scan event and its journal are kept intact.
    second = {**first, "id": "second-session", "reference": "SR-TEST-second",
              "client_request_id": "second-request"}
    await db[r.SESSIONS].insert_one(dict(second))
    piece.update(supplier_receiving_session_id=second["id"], receipt_event_id="second-scan")
    await db[r.PIECES].replace_one({"piece_id": pid}, piece)
    is_product = second_component == "product"
    scan.update(id="second-scan", session_id=second["id"], receipt_event_id="second-scan",
                supplier_receiving_session_id=second["id"], product_charge_eligible=is_product)
    for collection in (r.RECEIVING_EVENTS, r.PIECE_EVENTS):
        await db[collection].insert_one(dict(scan))
    line.update(product_unit_price_halalas=1500 if is_product else 0,
                services=[] if is_product else [{"service_id": "svc", "unit_price_halalas": 325}])
    payload["confirmed_total_halalas"] = 1500 if is_product else 325
    response = await close(http, second, payload)
    if conflict:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "supplier_receiving_component_already_invoiced"
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
        assert await db.general_ledger.find({}).sort("id", 1).to_list(10) == first_journal
        assert (await db[r.SESSIONS].find_one({"id": second["id"]}))["status"] == "open"
        assert (await db[r.PIECES].find_one({"piece_id": pid}))["receipt_event_id"] == "second-scan"
    else:
        assert response.status_code == 200, response.text
        assert response.json()["supplier_invoice"]["total_halalas"] == 325
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 2
        assert await db.general_ledger.count_documents({}) == 4
    assert (await db[r.SUPPLIER_INVOICES].find_one({"id": first_invoice["id"]})) == first_invoice


async def test_prior_component_lookup_is_owner_scoped_and_matches_each_invoice_line(env, monkeypatch):
    db, http, _ = env
    session, payload = await seed(db)
    pid = payload["invoice_lines"][0]["piece_ids"][0]
    previous = {"user_id": "merchant", "approved_at": datetime.now(timezone.utc),
                "lines": [{"piece_ids": [pid], "product_unit_price_halalas": 2100, "services": []}]}
    for key, fields in (
        ("foreign", {"user_id": "another-owner"}),
        ("experiment", {"experiment_mode": True}),
        ("run", {"experiment_run_id": "archived-run"}),
        ("draft", {"approved_at": None, "status": "draft"}),
        ("grouped", {"lines": [{"piece_ids": [pid], "product_unit_price_halalas": 0,
                                "services": [{"service_id": "embroidery"}]},
                               {"piece_ids": ["another-unit"], "product_unit_price_halalas": 2100, "services": []}]}),
    ):
        await db[r.SUPPLIER_INVOICES].insert_one({**previous, **fields, "id": key,
                                                  "invoice_number": key, "session_id": key})
    queries, unit_queries = [], []
    original = AsyncIOMotorCollection.find

    def capture(self, query, *args, **kwargs):
        if self.name == r.SUPPLIER_INVOICES and "lines.piece_ids" in query:
            assert kwargs["session"].in_transaction
            queries.append(query)
        if self.name == r.RECEIVING_EVENTS and "$or" in query and "order_item_id" in query["$or"][0]:
            assert kwargs["session"].in_transaction
            unit_queries.append(query)
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(AsyncIOMotorCollection, "find", capture)
    response = await close(http, session, payload)
    assert response.status_code == 200, response.text
    assert len(queries) == 1
    plan = await original(db[r.SUPPLIER_INVOICES], queries[0]).explain()
    assert "ix_supplier_invoice_piece_v2" in str(plan["queryPlanner"]["winningPlan"])
    assert len(unit_queries) == 1
    plan = await original(db[r.RECEIVING_EVENTS], unit_queries[0]).explain()
    assert "ix_supplier_receiving_order_unit_v1" in str(plan["queryPlanner"]["winningPlan"])


@pytest.mark.parametrize("fields", [
    {"status": "archived"},
    {"status": "reversed"},
    {"lines": [{"piece_ids": ["target"], "product_unit_price_halalas": 1500}]},
])
async def test_ambiguous_historical_receipt_requires_review(env, fields):
    db, http, _ = env
    session, payload = await seed(db)
    pid = payload["invoice_lines"][0]["piece_ids"][0]
    previous = {"user_id": "merchant", "id": "previous", "session_id": "previous-session",
                "invoice_number": "previous", "supplier_id": "other-supplier",
                "approved_at": datetime.now(timezone.utc), "status": "payable_posted",
                "lines": [{"piece_ids": [pid], "product_unit_price_halalas": 1500, "services": []}],
                **deepcopy(fields)}
    previous["lines"][0]["piece_ids"] = [pid]
    await db[r.SUPPLIER_INVOICES].insert_one(previous)
    response = await close(http, session, payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_receiving_prior_invoice_incomplete"
    assert await db.general_ledger.count_documents({}) == 0
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1


@pytest.mark.parametrize("prior_charge,next_charge,expected", [
    ("product", "product", 409), ("svc", "svc", 409), ("product", "svc", 200)
])
async def test_another_batch_id_for_same_order_unit(env, prior_charge, next_charge, expected):
    db, http, _ = env
    first, first_payload = await seed(db, (1500,), services=True)
    pid = first_payload["invoice_lines"][0]["piece_ids"][0]
    pending = (await db[r.PIECES].find_one({"piece_id": pid}))["services"]
    if prior_charge == "product":
        first_payload["invoice_lines"][0]["services"] = []
        first_payload["confirmed_total_halalas"] = 1500
    approved = await close(http, first, first_payload)
    assert approved.status_code == 200, approved.text
    # Legacy immutable scans may encode the same unit with leading zeroes.
    await db[r.RECEIVING_EVENTS].update_one({"piece_id": pid}, {"$set": {"unit_index": "001"}})
    second, payload = await seed(db, (1500,))
    new_id = payload["invoice_lines"][0]["piece_ids"][0]
    assert pid != new_id
    if next_charge == "svc":
        await db[r.PIECES].update_one({"piece_id": new_id}, {"$set": {"services": pending}})
        await db[r.RECEIVING_EVENTS].update_one({"piece_id": new_id}, {"$set": {
            "product_charge_eligible": False, "services": pending}})
        payload["invoice_lines"][0].update(product_unit_price_halalas=0,
            services=[{"service_id": "svc", "unit_price_halalas": 325}])
        payload["confirmed_total_halalas"] = 325
    response = await close(http, second, payload)
    assert response.status_code == expected, response.text
    if expected == 409:
        assert response.json()["detail"]["code"] == "supplier_receiving_component_already_invoiced"
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
        assert await db.general_ledger.count_documents({}) == 2


async def test_two_aliases_in_one_session(env):
    db, http, _ = env
    session, payload = await seed(db, (1500, 1500))
    alias = payload["invoice_lines"][1]["piece_ids"][0]
    for name in (r.PIECES, r.RECEIVING_EVENTS, r.PIECE_EVENTS):
        await db[name].update_one({"piece_id": alias}, {"$set": {"order_item_id": "0", "unit_index": "001"}})
    response = await close(http, session, payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_receiving_duplicate_unit_component"
    await unchanged(db, session)


async def test_concurrently_reserved_unit_aliases_cannot_both_close(env):
    db, http, _ = env
    first, p = await seed(db, (1500,))
    await db[r.SESSIONS].update_one({"id": first["id"]}, {"$set": {"opened_by": "another-receiver"}})
    second, payload = await seed(db, (1500,))
    response = await close(http, second, payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_receiving_unit_reserved_elsewhere"
    assert await db.general_ledger.count_documents({}) == 0
    # The scan cancellation path already releases the active reservation.
    await db[r.RECEIVING_EVENTS].update_one({"session_id": first["id"]}, {"$set": {"event_type": "removed"}})
    response = await close(http, second, payload)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("ready", [True, False])
async def test_mobile_build_rollback_retains_ready_unit_reservations(env, ready):
    import mobile_reviewed_preparation_routes as mobile
    from pymongo.errors import DuplicateKeyError

    db, _, _ = env
    await mobile.batch_module.ensure_preparation_batch_indexes(db)
    state = "ready" if ready else "building"
    await db[mobile.batch_module.BATCHES].insert_one({"user_id": "merchant", "id": "batch",
        "client_request_id": "request", "status": state})
    await db[mobile.REGISTRY].insert_one({"user_id": "merchant", "client_request_id": "request", "status": state})
    unit = {"user_id": "merchant", "order_number": "order", "order_item_id": "item", "unit_index": 1}
    allocations = db[mobile.PREPARATION_UNIT_ALLOCATIONS]
    await allocations.insert_one({**unit, "batch_id": "batch", "status": "committed"})
    await mobile._rollback_build(db, user_id="merchant", batch_id="batch", request_id="request")
    if ready:
        with pytest.raises(DuplicateKeyError):
            await allocations.insert_one({**unit, "batch_id": "another-batch"})
    else:
        assert await allocations.count_documents(unit) == 0


async def test_finalized_alias_with_missing_invoice_aborts_and_different_units_are_independent(env):
    db, http, _ = env
    current, payload = await seed(db, (1500,))
    event = {"user_id": "merchant", "piece_id": "former-alias", "session_id": "previous-session",
             "order_number": "123456789", "order_item_id": "0", "unit_index": 1,
             "event_type": "supplier_piece_service_recorded", "supplier_invoice_id": "missing"}
    await db[r.RECEIVING_EVENTS].insert_one(dict(event))
    response = await close(http, current, payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_receiving_prior_invoice_incomplete"
    assert await db.general_ledger.count_documents({}) == 0
    await db[r.RECEIVING_EVENTS].update_one({"piece_id": "former-alias"}, {"$set": {"unit_index": 2}})
    assert (await close(http, current, payload)).status_code == 200


@pytest.mark.parametrize("missing", [None, "allocation", "registry"])
async def test_mobile_ready_transition_is_atomic(env, missing):
    import mobile_reviewed_preparation_routes as mobile

    db, _, _ = env
    now = datetime.now(timezone.utc)
    draft = {"user_id": "merchant", "status": "building", "expires_at": now}
    await db[mobile.batch_module.BATCHES].insert_one({**draft, "id": "batch"})
    if missing != "registry":
        await db[mobile.REGISTRY].insert_one({**draft, "client_request_id": "request"})
    if missing != "allocation":
        await db[mobile.PREPARATION_UNIT_ALLOCATIONS].insert_one({**draft, "batch_id": "batch", "status": "reserved"})
    args = dict(user_id="merchant", batch_id="batch", request_id="request", expected_quantity=1,
                ready_patch={"status": "ready", "ready_at": now}, registry_patch={"status": "ready"})
    if missing:
        from fastapi import HTTPException
        with pytest.raises(HTTPException, match="preparation_file_reservation_mismatch"):
            await mobile._publish_ready_file(db, **args)
        assert (await db[mobile.batch_module.BATCHES].find_one({}))["status"] == "building"
        for name in (mobile.PREPARATION_UNIT_ALLOCATIONS, mobile.REGISTRY):
            assert await db[name].count_documents({"status": {"$in": ["ready", "committed"]}}) == 0
    else:
        await mobile._publish_ready_file(db, **args)
        for name, status in ((mobile.batch_module.BATCHES, "ready"), (mobile.REGISTRY, "ready"),
                             (mobile.PREPARATION_UNIT_ALLOCATIONS, "committed")):
            row = await db[name].find_one({})
            assert row["status"] == status and "expires_at" not in row
