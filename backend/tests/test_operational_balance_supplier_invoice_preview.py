"""Read-only snapshot/HTTP supplier review, synthetic isolated Mongo only."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import os
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, initial
from operational_balance_sources import collect_sources
from operational_supplier_invoice_preview import (SnapshotReads, _preview, supplier_invoice_preview,
                                                 supplier_proposal, native_purchase_inventory)
from mobile_app_permissions import OPERATIONAL_APP_READ, OPERATIONAL_APP_WRITE
from test_operational_supplier_invoice_cutover import native_data, native_invoice, START, NOW, OLD

PATH='/api/operational-balances/supplier-invoice-review'


@asynccontextmanager
async def database():
    uri=os.environ.get('OPERATIONAL_TEST_MONGO_URI')
    if not uri: pytest.skip('Explicit isolated Mongo required')
    p=urlparse(uri)
    assert p.scheme=='mongodb' and p.hostname=='127.0.0.1' and p.port and not p.username and not p.password
    client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000)
    name='test_supplier_preview_'+uuid4().hex
    try:
        assert (await client.admin.command('hello')).get('setName')
        yield client[name]
    finally:
        assert name.startswith('test_supplier_preview_')
        await client.drop_database(name);client.close()


def run(f): return asyncio.run(f)


async def seed(db,*,partial=False):
    data=native_data()
    for name,rows in data.items():
        if rows: await db[name].insert_many(deepcopy(rows))
    s=initial('owner');s.update(status='active',started_at=START,revision=7)
    s['engine']={'as_of':START,'facts':{},'obligations':{}}
    s['openings']={'supplier':{'id':'opening','party_type':'supplier','party_id':'supplier','currency':'SAR',
        'name':'Synthetic','direction':'for_party','amount':'100.00','approved_at':START}}
    s['engine']['facts']['provider:capture']={'amount':'777.00'}
    s['engine']['obligations']['salary']={'id':'salary','kind':'salary','party_type':'employee','party_id':'staff',
        'currency':'SAR','direction':'payable','confirmed':'30.00','expected':'0.00'}
    if partial:
        sources=await collect_sources(db,'owner',START,NOW)
        s,_=supplier_proposal(s,sources['supplier_receipts'][:1],NOW)
        key=next(k for k in s['engine']['obligations'] if k.startswith('supplier:'))
        s['movements']=[{'id':'paid','amount':'25.00','allocations':[{'obligation_id':key,'amount':'25.00'}]}]
    await db[STATES].insert_one(s)
    await db.users.insert_many([{'id':'owner','role':'owner','is_active':True},
        {'id':'other','role':'owner','is_active':True}, {'id':'staff','role':'employee','created_by':'owner','is_active':True}])
    await db.mezan_role_assignments_v2.insert_one({'owner_user_id':'owner','user_id':'staff','extra_permissions':['suppliers.read']})
    await db.mezan_mobile_app_access_v1.insert_one({'owner_user_id':'owner','user_id':'staff','enabled':True,'permissions':[OPERATIONAL_APP_READ]})
    return s


async def contents(db):
    return {n:await db[n].find({}).to_list(None) for n in sorted(await db.list_collection_names())}


async def request(db, principal=None,method='GET',suffix=''):
    app=FastAPI()
    async def current():
        if principal=={}:raise HTTPException(401)
        return principal or {'id':'owner'}
    app.include_router(make_operational_balance_router(db,current),prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated') as c:
        return await c.request(method,PATH+suffix)


@pytest.mark.parametrize('partial,delta',[(False,'80.00'),(True,'40.00')])
def test_snapshot_reads_actual_native_invoice_but_never_saves_difference(partial,delta):
    async def test():
        async with database() as db:
            await seed(db,partial=partial)
            before=await contents(db)
            a=await supplier_invoice_preview(db,'owner',as_of=NOW)
            b=await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert a==b
            assert a['arithmetic_complete'],a
            assert a['totals']['difference']==delta
            assert a['snapshot_consistent'] and a['read_only'] and a['state_revision']==7
            assert not a['posting_allowed'] and not a['financial_correction_approved'] and not a['opening_coverage_verified']
            assert not a['proposed_financial_write_allowed'] and a['approval_required']
            r=a['rows'][0]
            assert r['proposed']['confirmed']=='80.00'
            assert r['opening_coverage']=='requires_owner_review'
            if partial:
                assert r['current']['outstanding']=='15.00' and r['proposed']['outstanding']=='55.00'
                assert r['current']['settled']==r['proposed']['settled']=='25.00'
            assert before==await contents(db)
    run(test())


def test_preview_preserves_unrelated_provider_payroll_opening_and_supplier_returns():
    async def test():
        async with database() as db:
            s=await seed(db,partial=True)
            rid=next(k for k in s['engine']['facts'] if k.startswith('receipt:'))
            key=next(k for k in s['engine']['obligations'] if k.startswith('supplier:'))
            s['engine']['facts']['return:test']={'receipt_id':rid,'amount':'30.00'}
            s['engine']['obligations'][key]['confirmed']='10.00'
            await db[STATES].replace_one({'_id':'owner'},s)
            before=await contents(db)
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert r['arithmetic_complete'],r
            assert r['rows'][0]['current']['overpayment']==r['rows'][0]['proposed']['overpayment']=='15.00'
            assert r['rows'][0]['difference']=='40.00'
            assert before==await contents(db)
    run(test())


@pytest.mark.parametrize('status',['draft','frozen','accounting_active'])
def test_nonactive_never_produces_current_balance_proposal(status):
    async def test():
        async with database() as db:
            await seed(db);await db[STATES].update_one({'_id':'owner'},{'$set':{'status':status}})
            before=await contents(db)
            with pytest.raises(HTTPException) as e:await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert e.value.detail['code']=='supplier_review_not_active'
            assert before==await contents(db)
    run(test())


@pytest.mark.parametrize('native_gate',['marker','settings'])
def test_native_accounting_gate_is_not_bypassed_by_read_preview(native_gate):
    async def test():
        async with database() as db:
            await seed(db)
            if native_gate=='marker':await db.mz2_atomic_owners.insert_one({'_id':'owner','ledger_backend_state':'v2_active'})
            else:await db.settings.insert_one({'user_id':'owner','mezan2_financial_cutover':{'status':'active'}})
            with pytest.raises(HTTPException) as e:await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert e.value.detail['code']=='operational_accounting_active'
    run(test())


@pytest.mark.parametrize('damage',['amount','supplier','duplicate','missing_piece'])
def test_incomplete_supplier_proof_blocks_total_instead_of_zero(damage):
    async def test():
        async with database() as db:
            await seed(db)
            if damage=='amount':await db.mezan_supplier_invoices_v2.update_one({'id':'invoice'},{'$set':{'total_halalas':9900}})
            if damage=='supplier':await db.mezan_supplier_invoices_v2.update_one({'id':'invoice'},{'$set':{'supplier_id':'missing'}})
            if damage=='duplicate':await db.mezan_supplier_invoices_v2.insert_one(native_invoice())
            if damage=='missing_piece':await db.mezan_preparation_pieces_v1.delete_one({'id':'p1'})
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert not r['arithmetic_complete'] and r['totals'] is None and r['issues']
    run(test())


def native(**changes):
    return dict(user_id='owner',id='native',supplier_counterparty_id='supplier',supplier_entity_id='supplier',
        supplier_account_id='supplier',invoice_number='SI-SYNTHETIC-invoice',total='80',currency='SAR',
        state='approved',approved_at=NOW,liability_id='native-debt',txn_group_id='native-journal',**changes)


def test_native_purchase_is_separate_and_duplicate_reference_never_becomes_a_new_debt():
    async def test():
        async with database() as db:
            await seed(db);await db.purchase_invoices.insert_one(native())
            before=await contents(db)
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            n=r['native_purchase_review'][0]
            assert n['source_declared_gross_sar']=='80.00'
            assert n['verified_native_payable_sar'] is None and n['difference_sar'] is None
            assert n['possible_existing_documents']==[{'source':'preparation_invoice','id':'invoice'}]
            assert 'native_purchase_possible_duplicate_document' in n['issues']
            assert not r['native_financial_matching_complete']
            assert r['totals']['invoice_gross']=='80.00','native invoice must not double report totals'
            assert before==await contents(db)
    run(test())


def test_same_number_for_other_supplier_or_generated_sequence_is_not_a_match():
    s=initial('owner')
    s['inventory_purchases']=[{'id':'x','supplier_id':'supplier','supplier_invoice_number':'vendor-ref','invoice_number':'SI-SYNTHETIC-invoice'}]
    prep=[{'id':'x','supplier_id':'other','invoice_number':'SI-SYNTHETIC-invoice'}]
    a=native_purchase_inventory(s,prep,[native()],owner='owner',as_of=NOW)[0]
    assert a['possible_existing_documents']==[]


@pytest.mark.parametrize('value',[None,True,'NaN','-1','0.001'])
def test_native_bad_declared_amount_is_unknown(value):
    n=native();n['total']=value
    r=native_purchase_inventory(initial('owner'),[],[n],owner='owner',as_of=NOW)[0]
    assert r['source_declared_gross_sar'] is None and r['difference_sar'] is None


def test_native_cross_owner_rejected_even_when_passed_by_bad_caller():
    n=native();n['user_id']='other'
    with pytest.raises(ValueError,match='owner_mismatch'):
        native_purchase_inventory(initial('owner'),[],[n],owner='owner',as_of=NOW)


def test_http_cannot_select_other_owner_and_has_no_post_action():
    async def test():
        async with database() as db:
            await seed(db)
            a=await request(db,suffix='?owner_id=other&approve=true')
            assert a.status_code==200,a.text
            assert a.json()['owner_id']=='owner' and not a.json()['posting_allowed']
            assert (await request(db,method='POST')).status_code==405
            assert (await request(db,{})).status_code==401
            assert (await request(db,{'id':'other'})).status_code==409
    run(test())


@pytest.mark.parametrize('mode',['read','write_only','no_supplier_permission','disabled'])
def test_http_mobile_permissions_remain_actor_scoped(mode):
    async def test():
        async with database() as db:
            await seed(db)
            if mode=='write_only':await db.mezan_mobile_app_access_v1.update_one({'user_id':'staff'},{'$set':{'permissions':[OPERATIONAL_APP_WRITE]}})
            if mode=='no_supplier_permission':await db.mezan_role_assignments_v2.update_one({'user_id':'staff'},{'$set':{'extra_permissions':[]}})
            if mode=='disabled':await db.users.update_one({'id':'staff'},{'$set':{'disabled':True}})
            p={'id':'owner','role':'owner','_session_client':'amasi_mobile','_mobile_actor_id':'staff'}
            r=await request(db,p)
            assert r.status_code==(200 if mode=='read' else 403),r.text
    run(test())


def test_snapshot_remains_consistent_when_invoice_changes_mid_read():
    async def test():
        async with database() as db:
            await seed(db)
            async with await db.client.start_session() as session:
                from pymongo.read_concern import ReadConcern
                async with session.start_transaction(read_concern=ReadConcern('snapshot')):
                    scoped=SnapshotReads(db,'owner',session)
                    await scoped[STATES].find_one({'_id':'owner','owner_id':'owner'})
                    await db.mezan_supplier_invoices_v2.update_one({'id':'invoice'},{'$set':{'total_halalas':9900}})
                    r=await _preview(scoped,'owner',NOW)
                    assert r['arithmetic_complete'] and r['rows'][0]['invoice_gross']=='80.00'
            later=await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert not later['arithmetic_complete']
    run(test())


@pytest.mark.parametrize('attack',['collection','owner','merge','out','lookup','writer'])
def test_read_capability_rejects_mutations_and_unscoped_reads(attack):
    class DB:
        def __getitem__(self,name):return object()
    p=SnapshotReads(DB(),'owner',None)
    with pytest.raises((ValueError,AttributeError)):
        if attack=='collection':p['accounting_general_ledger_v2']
        if attack=='owner':p['purchase_invoices'].find({'user_id':'other'})
        if attack=='writer':p['purchase_invoices'].update_one({}, {})
        if attack in {'merge','out','lookup'}:
            p['purchase_invoices'].aggregate([{'$match':{'user_id':'owner'}},{'$'+attack:'target'}])


def test_no_standalone_or_non_snapshot_fallback():
    from pymongo.errors import OperationFailure
    class Client:
        async def start_session(self):raise OperationFailure('snapshot unavailable')
    class DB:client=Client()
    with pytest.raises(HTTPException) as e:run(supplier_invoice_preview(DB(),'owner',as_of=NOW))
    assert e.value.status_code==503 and e.value.detail['code']=='supplier_preview_snapshot_unavailable'


def test_source_change_cannot_reprice_saved_receipt():
    async def test():
        async with database() as db:
            await seed(db,partial=True)
            invoice=native_invoice(unit=5000)
            await db.mezan_supplier_invoices_v2.replace_one({'id':'invoice'},invoice)
            before=await contents(db)
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert not r['arithmetic_complete'] and r['totals'] is None
            assert any(x['code']=='confirmed_evidence_changed' for x in r['issues'])
            assert before==await contents(db)
    run(test())


def test_future_saved_snapshot_rejected():
    async def test():
        async with database() as db:
            await seed(db)
            await db[STATES].update_one({'_id':'owner'},{'$set':{'engine.as_of':'2027-01-01T00:00:00+00:00'}})
            with pytest.raises(HTTPException) as e:await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert e.value.detail['code']=='supplier_review_stale_snapshot'
    run(test())


def test_role_revoked_during_snapshot_is_rechecked_before_disclosure(monkeypatch):
    import operational_supplier_invoice_preview as mod
    original=mod.supplier_invoice_preview
    async def revoking(db,owner,**kw):
        result=await original(db,owner,**kw)
        await db.mezan_role_assignments_v2.update_one({'user_id':'staff'},{'$set':{'extra_permissions':[]}})
        return result
    monkeypatch.setattr(mod,'supplier_invoice_preview',revoking)
    async def test():
        async with database() as db:
            await seed(db)
            r=await request(db,{'id':'staff'})
            assert r.status_code==403
            assert '80.00' not in r.text
    run(test())


def test_full_accounting_activation_during_snapshot_blocks_current_preview(monkeypatch):
    import operational_supplier_invoice_preview as mod
    original=mod.supplier_invoice_preview
    async def activating(db,owner,**kw):
        result=await original(db,owner,**kw)
        await db.mz2_atomic_owners.insert_one({'_id':owner,'ledger_backend_state':'v2_active'})
        return result
    monkeypatch.setattr(mod,'supplier_invoice_preview',activating)
    async def test():
        async with database() as db:
            await seed(db)
            r=await request(db)
            assert r.status_code==409 and r.json()['detail']['code']=='operational_accounting_active'
    run(test())


def test_receiving_only_compares_source_pieces_for_saved_window_and_cannot_post():
    async def test():
        async with database() as db:
            await seed(db, partial=True)
            await db.mezan_supplier_invoices_v2.insert_one({**native_invoice(), "user_id": "another", "id": "foreign"})
            before=await contents(db)
            response=await request(db,suffix="?receiving_only=true&owner_id=another")
            assert response.status_code==200,response.text
            result=response.json()
            assert result["read_only"] and result["snapshot_consistent"] and not result["posting_allowed"]
            assert result["invoice_count"]==1 and result["review_count"]==1
            row=result["rows"][0]
            assert row["invoice_id"]=="invoice" and row["invoice_gross_sar"]=="80.00"
            assert row["matched_components"]==1 and row["component_count"]==2
            assert [(d["status"],d["source_amount_sar"]) for d in row["differences"]]==[("missing","40.00")]
            assert "native_purchase_review" not in result and "review_manifest_sha256" not in result
            assert before==await contents(db)
            assert (await request(db,{},suffix="?receiving_only=true")).status_code==401
    run(test())
