"""R05 source/preview/approval/HTTP checks in UUID-named isolated Mongo only."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import initial, STATES, read, digest
from operational_balance_service import report, refresh
from operational_native_purchase_link import LINKS
import operational_native_purchase_link_service as service
from operational_supplier_invoice_preview import supplier_invoice_preview
from test_operational_balance_native_purchase_review import seed_native, START, NOW
from test_operational_balance_integration import run
from test_operational_balance_supplier_invoice_preview import contents


def choice():
    return dict(invoice_id='native',existing_obligation_ids=[],evidence_ref='Synthetic supplier invoice and bank statement',
                payment_links=[{'payment_operation_id':'payment-1','movement_id':None}])


async def seed(db):
    s=initial('owner');s.update(status='active',started_at=START,revision=7)
    s['openings']={'bank':{'id':'opening-bank','party_type':'bank','party_id':'bank','currency':'SAR',
        'direction':'for_us','amount':'1000.00','name':'Synthetic bank'}}
    await db[STATES].insert_one(s)
    await seed_native(db)
    return s


def approval(p,ch=None,**extra):
    result=dict(request_id='native-link-approval',selection=ch or choice(),expected_preview_hash=p['preview_hash'],
        expected_session_scope=p['session_scope'],confirm_reviewed_identities=True,confirm_unrecorded_money=True)
    result.update(extra);return result


async def native_contents(db):
    all_rows=await contents(db)
    return {k:v for k,v in all_rows.items() if k!=STATES}


async def client_for(db):
    app=FastAPI()
    async def user(x_test_user:str=Header('owner'),x_test_mobile:str=Header('no')):
        return {'id':x_test_user,'_session_client':'amasi_mobile' if x_test_mobile=='yes' else 'web'}
    app.include_router(make_operational_balance_router(db,user),prefix='/api')
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated')


def test_actual_sealed_preview_approval_and_refresh_preserve_native_sources_and_bank_once():
    async def test(db):
        original=await seed(db);before=await contents(db)
        candidates=await service.candidates(db,'owner','owner','native',clock=NOW)
        assert candidates['source']['source_verified'] and candidates['existing_movements']==[]
        p=await service.preview(db,'owner','owner',choice(),clock=NOW)
        assert p['can_approve'],p
        assert p['before']['banks']['bank']=='1000.00' and p['after']['banks']['bank']=='975.00'
        assert p['supplier_difference_sar']=='55.00' and before==await contents(db)
        native=await native_contents(db)
        payload=approval(p)
        applied=await service.approve(db,'owner','owner',payload,clock=NOW)
        assert applied['ok'] and not applied['actual_bank_transfer_executed'] and not applied['native_ledger_changed']
        saved=await read(db,'owner')
        assert saved['openings']==original['openings'] and len(saved['movements'])==1 and len(saved['audit'])==1
        assert native==await native_contents(db)
        assert applied==await service.approve(db,'owner','owner',payload,clock=NOW)
        assert saved==await read(db,'owner')
        refreshed=await refresh(db,'owner',clock=NOW)
        r=report(refreshed)
        supplier=next(p for p in r['parties'] if p['party_type']=='supplier')
        bank=next(p for p in r['parties'] if p['party_type']=='bank')
        assert supplier['outstanding_payable']=='55.00' and bank['actual']=='975.00'
        assert native==await native_contents(db)
        review=await supplier_invoice_preview(db,'owner',as_of=NOW)
        item=review['native_purchase_review'][0]
        assert review['native_financial_matching_complete'],item
        assert item['verified_native_payable_sar']=='55.00' and item['difference_sar']=='0.00'
        assert not item['posting_allowed']
    run(test)


@pytest.mark.parametrize('duplicate_request',[True,False])
def test_concurrent_approval_is_atomic_and_never_mirrors_bank_twice(duplicate_request):
    async def test(db):
        await seed(db);p=await service.preview(db,'owner','owner',choice(),clock=NOW)
        a=approval(p);b={**a,'request_id':a['request_id'] if duplicate_request else 'another-request'}
        r=await asyncio.gather(service.approve(db,'owner','owner',a,clock=NOW),
            service.approve(db,'owner','owner',b,clock=NOW),return_exceptions=True)
        assert sum(isinstance(x,HTTPException) for x in r)==(0 if duplicate_request else 1),r
        saved=await read(db,'owner')
        assert len(saved['movements'])==1 and len(saved['audit'])==1 and len(saved[LINKS])==1
    run(test)


@pytest.mark.parametrize('damage',['state','source','no-identity-confirm','no-money-confirm','scope','owner-disabled','inactive-accounting','replay-conflict'])
def test_failed_or_stale_approval_leaves_finance_unchanged(damage):
    async def test(db):
        await seed(db);p=await service.preview(db,'owner','owner',choice(),clock=NOW);payload=approval(p)
        if damage=='state':await db[STATES].update_one({'_id':'owner'},{'$inc':{'revision':1}})
        if damage=='source':await db.liabilities.update_one({'id':'debt'},{'$set':{'paid_amount':'0'}})
        if damage=='no-identity-confirm':payload['confirm_reviewed_identities']=False
        if damage=='no-money-confirm':payload['confirm_unrecorded_money']=False
        if damage=='scope':payload['expected_session_scope']=digest(['other','owner'])
        if damage=='owner-disabled':await db.users.update_one({'id':'owner'},{'$set':{'disabled':True}})
        if damage=='inactive-accounting':await db.mz2_atomic_owners.insert_one({'_id':'owner','ledger_backend_state':'v2_active'})
        if damage=='replay-conflict':
            await service.approve(db,'owner','owner',payload,clock=NOW)
            payload['selection']={**payload['selection'],'evidence_ref':'Changed document'}
        before=await contents(db)
        with pytest.raises(HTTPException):await service.approve(db,'owner','owner',payload,clock=NOW)
        assert before==await contents(db)
    run(test)


def test_mid_request_owner_revocation_does_not_apply_the_proposal(monkeypatch):
    async def test(db):
        await seed(db);p=await service.preview(db,'owner','owner',choice(),clock=NOW)
        before=await read(db,'owner');original=service._read_context
        async def revoke(*a,**k):
            result=await original(*a,**k)
            await db.users.update_one({'id':'owner'},{'$set':{'disabled':True}})
            return result
        monkeypatch.setattr(service,'_read_context',revoke)
        with pytest.raises(HTTPException):await service.approve(db,'owner','owner',approval(p),clock=NOW)
        assert before==await read(db,'owner')
    run(test)


def test_source_reversal_is_visible_after_link_without_rewriting_saved_money():
    async def test(db):
        await seed(db);p=await service.preview(db,'owner','owner',choice(),clock=NOW)
        await service.approve(db,'owner','owner',approval(p),clock=NOW)
        before=await read(db,'owner')
        await db.accounting_audit_log_v2.delete_one({'user_id':'owner'})
        matched=await service.verified_saved_links(db,'owner',before,as_of=NOW)
        assert not matched['native']['matched'] and before==await read(db,'owner')
    run(test)


@pytest.mark.parametrize('who,mobile',[('staff',False),('viewer',False),('other',False),('owner',True)])
def test_new_link_routes_are_not_employee_mobile_or_cross_owner_authority(monkeypatch,who,mobile):
    monkeypatch.setattr(service,'now',lambda:NOW)
    async def test(db):
        await seed(db);before=await read(db,'owner')
        async with await client_for(db) as client:
            h={'x-test-user':who,'x-test-mobile':'yes' if mobile else 'no'}
            result=await client.post('/api/operational-balances/native-purchase-links/preview',json=choice(),headers=h)
            assert result.status_code in (403,409),result.text
        assert before==await read(db,'owner')
    run(test)


def test_http_preview_and_approval_dont_accept_client_amounts_or_unconfirmed_changes(monkeypatch):
    monkeypatch.setattr(service,'now',lambda:NOW)
    async def test(db):
        await seed(db)
        async with await client_for(db) as client:
            base='/api/operational-balances/native-purchase-links'
            p=await client.post(base+'/preview',json=choice());assert p.status_code==200,p.text
            p=p.json();assert p['can_approve'],p
            before=await read(db,'owner')
            assert (await client.post(base+'/preview',json={**choice(),'amount':'1'})).status_code==422
            no=await client.post(base+'/approve',json=approval(p,confirm_unrecorded_money=False))
            assert no.status_code==422 and before==await read(db,'owner')
            a=await client.post(base+'/approve',json=approval(p));assert a.status_code==200,a.text
            b=await client.post(base+'/approve',json=approval(p));assert b.json()==a.json()
            assert len((await read(db,'owner'))['movements'])==1
    run(test)
