"""Real ledger and replica-set tests. Dedicated disposable synthetic databases only."""
import asyncio
from copy import deepcopy
import io
import os
from uuid import uuid4
import pytest
from PIL import Image
from mezan_special_orders.binding import Enablement, SpecialOrdersDatabase
from mezan_special_orders.domain import DomainError
from mezan_special_orders.evidence import EvidenceStore
from mezan_special_orders.finance_service import FinancialService
from mezan_special_orders.ledger_adapter import POLICIES, EVENTS, OPERATION_ID, minor
from mezan_special_orders.repository import MongoStore
from mezan_special_orders.tests.test_core import Harness, OWNER, request_data

URI=os.environ.get('MEZAN_SPECIAL_TEST_REPLICA_URI')
pytestmark=pytest.mark.skipif(not URI, reason='Dedicated disposable replica-set URI not supplied')


def run(scenario):
    async def wrapped():
        from motor.motor_asyncio import AsyncIOMotorClient
        client=AsyncIOMotorClient(URI,serverSelectionTimeoutMS=5000)
        name='test_mezan_special_fin_'+uuid4().hex
        try:
            assert (await client.admin.command('hello')).get('setName'), 'Real replica set required'
            raw=client[name]; db=SpecialOrdersDatabase(raw,Enablement(True,True,True,True))
            h=Harness(); h.db,h.raw=db,raw; h.store=h.service.store=MongoStore(db)
            await h.store.ensure_indexes()
            await raw.settings.insert_one({'user_id':OWNER.tenant_id,'mezan2_financial_cutover':{'operation_id':OPERATION_ID,'status':'active','cutover_at':'2026-01-01T00:00:00+00:00'}})
            await raw.accounts.insert_one({'id':'bank-demo','user_id':OWNER.tenant_id,'account_type':'bank','status':'active','currency':'SAR','current_balance':1000})
            output=io.BytesIO();Image.new('RGB',(5,5),(255,255,255)).save(output,format='PNG');h.png=output.getvalue()
            h.evidence=EvidenceStore(db);await h.evidence.ensure_indexes()
            h.policy_ev=await h.evidence.upload(OWNER.tenant_id,OWNER.actor_id,kind='cost_document',content_type='image/png',data=h.png)
            await raw[POLICIES].insert_one({'tenant_id':OWNER.tenant_id,'effective_at':'2026-01-01T00:00:00+00:00','approved_by':OWNER.actor_id,'policy':{'effective_at':'2026-01-01T00:00:00+00:00','classification':'expense_recovery','tax_basis_points':1500,'tax_treatment':'tax_inclusive','evidence':h.policy_ev.model_dump(mode='json'),'reason':'Synthetic explicit accounting policy only'}})
            h.financial=FinancialService(db)
            async def new_order(partial=True,key='financial-order-001'):
                data=request_data(partial=partial);data['fx']['evidence_id']='sar-fixed-1'
                return await h.create(data=data,key=key)
            h.new_order=new_order
            async def claim(order,amount=4000,key='bank-receipt-test'):
                ev=await h.evidence.upload(OWNER.tenant_id,OWNER.actor_id,kind='bank_receipt',content_type='image/png',data=h.png)
                order=await h.command(order,'attach_receipt',{'evidence':ev.model_dump(mode='json'),'bank_account_id':'bank-demo','amount_minor':amount,'transferred_at':'2026-09-27T12:00:00+00:00'},key=key)
                return order,ev
            h.claim=claim
            async def command(order,operation,payload,key):
                return await h.financial.execute(OWNER,order['order_id'],order['revision'],key,operation,payload)
            h.fin_command=command
            await scenario(h)
        finally:
            await client.drop_database(name);client.close()
    return asyncio.run(wrapped())


def movement(ev,reference='BANK-TEST-001'):
    return {'bank_account_id':'bank-demo','bank_reference':reference,'occurred_at':'2026-09-27T12:00:00+00:00','evidence':ev.model_dump(mode='json'),'confirmed_new_movement':True,'cash_fx':{'currency':'SAR','rate_to_sar':'1','captured_at':'2026-09-27T12:00:00+00:00','evidence_id':'sar-fixed-1'}}


async def gl_balance(h,entity_type,entity_id,sub=None):
    q={'user_id':OWNER.tenant_id,'entity_type':entity_type,'entity_id':entity_id,'status':'posted'}
    if sub:q['sub_account']=sub
    rows=await h.raw.general_ledger.find(q).to_list(100)
    return sum(minor(r['amount'])*(1 if r['side']=='debit' else -1) for r in rows)


def test_real_bank_posting_replay_no_second_bank_or_sale():
    async def scenario(h):
        o,ev=await h.claim(await h.new_order())
        assert await h.raw.general_ledger.count_documents({})==0
        payload={'receipt_claim_id':o['receipt_claims'][0]['claim_id'],'movement':movement(ev)}
        r=await h.fin_command(o,'bank_collection',payload,'finance-bank-001')
        assert r['balances']['remaining_minor']==2000
        assert await gl_balance(h,'bank','bank-demo')==4000
        assert await gl_balance(h,'special_order_receivable',o['order_id'])==2000
        assert await gl_balance(h,'tax','output_vat')==-783
        count=await h.raw.general_ledger.count_documents({})
        again=await h.fin_command(o,'bank_collection',payload,'finance-bank-001')
        assert again['revision']==r['revision']
        assert await h.raw.general_ledger.count_documents({})==count
        assert await h.raw.account_transactions.count_documents({})==1
        assert not await h.raw.general_ledger.find_one({'entity_type':'revenue'})
    run(scenario)


def test_real_bank_failure_rolls_back_agreement_claim_and_outbox():
    async def scenario(h):
        o,ev=await h.claim(await h.new_order());old=await h.doc(o)
        bad=movement(ev);bad['bank_account_id']='missing-bank'
        with pytest.raises(DomainError):
            await h.fin_command(o,'bank_collection',{'receipt_claim_id':o['receipt_claims'][0]['claim_id'],'movement':bad},'finance-bad-bank')
        assert await h.doc(o)==old
        assert await h.raw.general_ledger.count_documents({})==0
        assert await h.raw.account_transactions.count_documents({})==0
        assert await h.raw[EVENTS].count_documents({})==0
    run(scenario)


def test_real_concurrent_financial_commands_one_revision_winner():
    async def scenario(h):
        o,ev=await h.claim(await h.new_order());p={'receipt_claim_id':o['receipt_claims'][0]['claim_id'],'movement':movement(ev)}
        result=await asyncio.gather(h.fin_command(o,'bank_collection',p,'race-finance-a'),h.fin_command(o,'bank_collection',p,'race-finance-b'),return_exceptions=True)
        assert sum(isinstance(r,DomainError) for r in result)==1,result
        assert await gl_balance(h,'bank','bank-demo')==4000
        assert await h.raw.account_transactions.count_documents({})==1
    run(scenario)


def test_real_ledger_tampering_blocks_further_financial_actions():
    async def scenario(h):
        o,ev=await h.claim(await h.new_order())
        o=await h.fin_command(o,'bank_collection',{'receipt_claim_id':o['receipt_claims'][0]['claim_id'],'movement':movement(ev)},'finance-good-bank')
        await h.raw.general_ledger.update_one({'entity_type':'bank'},{'$set':{'status':'reversed'}})
        with pytest.raises(DomainError,match='ledger_reconciliation_required'):
            await h.fin_command(o,'recognize_agreement',{},'finance-after-tamper')
    run(scenario)
