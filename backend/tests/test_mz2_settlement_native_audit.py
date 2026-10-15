"""Actual settlement lifecycle routes: native audit and atomic failure recovery."""
import os
from uuid import uuid4
from urllib.parse import urlsplit, parse_qs
import pytest
import pytest_asyncio
from fastapi import FastAPI, APIRouter, HTTPException
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
from pymongo.errors import OperationFailure
from accounting_atomic import atomic_owner
from accounting_write_control import AccountingDatabase
from accounting_settlement_audit import COLLECTION, write_audit
from accounting_receipt_service import install_accounting_receipt_routes
from accounting_settlement_routes import install_accounting_settlement_routes
from accounting_settlement_lifecycle_routes import install_accounting_settlement_lifecycle_routes
from mz2_native_fixture import provision_native_opening

BASE='/accounting-module'
class NoLegacy(CommandListener):
    def __init__(self): self.accesses=[]
    def started(self,event):
        value=event.command.get(event.command_name)
        if isinstance(value,str) and value in {'accounts','general_ledger','account_transactions','accounting_audit_log','counterparties'}:
            self.accesses.append((event.command_name,value))
    def succeeded(self,event): pass
    def failed(self,event): pass

@pytest_asyncio.fixture
async def environment():
    uri=os.environ['MZ2_TEST_MONGO_URI']
    parsed=urlsplit(uri)
    assert parsed.scheme=='mongodb' and parsed.hostname in {'127.0.0.1','localhost','::1'}
    assert not parsed.username and not parsed.password and parse_qs(parsed.query).get('replicaSet')
    monitor=NoLegacy(); mongo=AsyncIOMotorClient(uri,event_listeners=[monitor])
    assert (await mongo.admin.command('hello')).get('setName')==parse_qs(parsed.query)['replicaSet'][0]
    db=mongo['mz2_settlement_audit_'+uuid4().hex]
    await db.users.insert_many([{'id':'owner','role':'owner'},{'id':'other','role':'owner'}])
    await provision_native_opening(db,bank_balances={'bank':0},entries=[
        {'entity_type':'payment_gateway','entity_id':'tabby','sub_account':'receivable','side':'debit','amount':'115.00'},
        {'entity_type':'equity','entity_id':'opening_balance_equity','sub_account':'main','side':'credit','amount':'115.00'}])
    await db.mz2_atomic_owners.insert_one({'_id':'other','revision':0,'writes_paused':False})
    await db.accounting_provider_bank_bindings_v2.insert_one(dict(user_id='owner',provider='tabby',bank_account_id='bank',
        verification_status='verified',bank_account_source='mz2_financial_accounts',identity_contract_version=1))
    app=FastAPI(); router=APIRouter(); actor_state={'id':'owner'}; wrapped=AccountingDatabase(db)
    async def actor(): return dict(actor_state)
    install_accounting_settlement_lifecycle_routes(router,wrapped,actor)
    install_accounting_settlement_routes(router,wrapped,actor)
    install_accounting_receipt_routes(router,wrapped,actor)
    app.include_router(router)
    post_routes=[route for route in router.routes if getattr(route,'path','')==BASE+'/settlements/drafts/{draft_id}/post'
        and 'POST' in getattr(route,'methods',set())]
    assert post_routes[0].endpoint.__module__=='accounting_settlement_lifecycle_routes'
    client=AsyncClient(transport=ASGITransport(app),base_url='http://synthetic')
    try:
        yield db,client,actor_state,wrapped
        assert monitor.accesses==[],monitor.accesses
    finally:
        await client.aclose(); await mongo.drop_database(db.name); mongo.close()

async def reviewed(environment):
    db,client,_,_=environment
    response=await client.post(BASE+'/bank-receipts',json=dict(provider='tabby',amount='104.65',
        bank_message='SYN bank ref: NATIVE-AUDIT-01',received_on='2026-09-19',request_id=str(uuid4())))
    assert response.status_code==200,response.text
    receipt=response.json()
    await db.accounting_settlements_v2.insert_one(dict(id='draft',user_id='owner',provider='tabby',currency='SAR',status='draft',
        version=1,receipt_workflow_version=2,bank_account_id='bank',source_file_id='SYN-file',source_file_hash='SYN-hash',
        statement_reference='SYN-native-audit',source_review_count=0,idempotency_key='SYN-native-audit',
        source_snapshot={'matched':1,'unmatched':0},amounts=dict(gross_sales=115,reported_net=104.65,
            commission=3,commission_vat=.45,settlement_fee=6,settlement_fee_vat=.9)))
    response=await client.put(BASE+'/settlements/drafts/draft/receipt',json={'receipt_id':receipt['id']})
    assert response.status_code==200,response.text
    for action in ['submit','review']:
        response=await client.post(BASE+'/settlements/drafts/draft/'+action,json={})
        assert response.status_code==200,response.text
    return receipt

@pytest.mark.asyncio
async def test_actual_post_route_native_audit_once_and_replay_has_no_extra_effect(environment):
    db,client,_,_=environment
    receipt=await reviewed(environment)
    response=await client.post(BASE+'/settlements/drafts/draft/post',json={'notes':'Reviewed native settlement'})
    assert response.status_code==200,response.text
    posted=response.json()
    assert posted['status']=='posted'
    audits=await db[COLLECTION].find({'action':'post_accounting_settlement_lifecycle'}).to_list(2)
    assert len(audits)==1
    row=audits[0]
    assert row['user_id']==row['actor_id']=='owner' and row['entity_id']=='tabby'
    assert row['before_state']=={'draft_id':'draft','status':'reviewed'}
    assert row['after_state']['status']=='posted' and row['notes']=='Reviewed native settlement'
    saved=await db.mz2_bank_receipts.find_one({'id':receipt['id']})
    assert saved['status']=='posted' and saved['ledger_txn_group_id']==posted['ledger_txn_group_id']
    before=await db.accounting_general_ledger_v2.count_documents({})
    response=await client.post(BASE+'/settlements/drafts/draft/post',json={})
    assert response.status_code==409,response.text
    assert await db[COLLECTION].count_documents({'action':'post_accounting_settlement_lifecycle'})==1
    assert await db.accounting_general_ledger_v2.count_documents({})==before

@pytest.mark.asyncio
async def test_real_audit_insert_failure_rolls_back_journal_draft_and_receipt(environment):
    db,client,_,_=environment
    receipt=await reviewed(environment)
    before=await db.accounting_general_ledger_v2.count_documents({})
    draft=await db.accounting_settlements_v2.find_one({'id':'draft'})
    saved_receipt=await db.mz2_bank_receipts.find_one({'id':receipt['id']})
    await db.command({'collMod':COLLECTION,'validator':{'action':{'$ne':'post_accounting_settlement_lifecycle'}}})
    with pytest.raises(OperationFailure):
        await client.post(BASE+'/settlements/drafts/draft/post',json={})
    assert await db.accounting_general_ledger_v2.count_documents({})==before
    assert await db.accounting_settlements_v2.find_one({'id':'draft'})==draft
    assert await db.mz2_bank_receipts.find_one({'id':receipt['id']})==saved_receipt
    assert await db[COLLECTION].count_documents({'action':'post_accounting_settlement_lifecycle'})==0
    await db.command({'collMod':COLLECTION,'validator':{}})
    response=await client.post(BASE+'/settlements/drafts/draft/post',json={})
    assert response.status_code==200,response.text

@pytest.mark.asyncio
async def test_foreign_owner_cannot_post_or_append_cross_owner_transaction_audit(environment):
    db,client,actor,wrapped=environment
    await reviewed(environment)
    actor['id']='other'
    before=await db[COLLECTION].count_documents({})
    response=await client.post(BASE+'/settlements/drafts/draft/post',json={})
    assert response.status_code==404,response.text
    assert await db[COLLECTION].count_documents({})==before
    async def wrong(scoped):
        token=wrapped.scope.set(scoped)
        try:
            await write_audit(wrapped,user_id='other',actor_id='owner',actor_name='',entity_type='payment_gateway',entity_id='tabby',action='forbidden')
        finally: wrapped.scope.reset(token)
    with pytest.raises(HTTPException,match='settlement_audit_owner_scope_mismatch'):
        await atomic_owner(db,'owner',wrong)
    assert await db[COLLECTION].count_documents({})==before

@pytest.mark.asyncio
async def test_nonfinancial_audit_needs_no_opening_or_activation(environment):
    db,_,_,wrapped=environment
    before=await db.accounting_general_ledger_v2.count_documents({})
    identifier=await write_audit(wrapped,user_id='other',actor_id='other',actor_name='Other owner',
        entity_type='payment_gateway',entity_id='tabby',action='synthetic_draft_edit',before_state={'status':'draft'},after_state={'status':'draft'})
    row=await db[COLLECTION].find_one({'id':identifier,'user_id':'other'})
    assert row and row['notes']=='' and row['ledger_entry_id'] is None
    assert await db.accounting_general_ledger_v2.count_documents({})==before
