"""Actual standing budget-increase helpers with external transport collaborator."""
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_automation_policy_mutation_adapter import mutate_google_automation_policy
from tests.test_google_ads_automation_gateway import GatewayDB,NativeProvider,preview,execute
from tests.test_google_ads_automation_authority import case_documents,freeze_guard_clock,NOW

class BudgetProvider(NativeProvider):
    async def mutate(self,operations,*,validate_only=False):
        if validate_only:return await super().mutate(operations,validate_only=True)
        self.writes+=1
        assert len(operations)==1 and 'campaignBudgetOperation' in operations[0]
        update=operations[0]['campaignBudgetOperation']['update']
        self.native['campaign_snapshot']['campaign_budget']['amountMicros']=update['amountMicros']
        return {'mutateOperationResponses':[{'campaignBudgetResult':{'resourceName':update['resourceName']}}]}

def advance(monkeypatch,current):
    freeze_guard_clock(monkeypatch,current)
    monkeypatch.setattr(management,'now',lambda:current)
    monkeypatch.setattr(authority,'_now',lambda supplied=None:current)

async def setup(monkeypatch,db=None):
    docs,native=case_documents('CHANGE_BUDGET');docs[management.PROPOSALS]=[];docs[management.FENCES]=[]
    if db is None:db=GatewayDB(docs)
    else:
        from tests.test_google_ads_automation_authority_mongo import seed
        await seed(db,docs)
    provider=BudgetProvider(native);advance(monkeypatch,NOW)
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:True)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','true')
    async def client(database,user_id,account_id,**kwargs):
        assert user_id=='owner' and account_id=='123'
        return provider,'test-epoch'
    monkeypatch.setattr(management,'account_client',client)
    return db,provider

@pytest.mark.asyncio
async def test_budget_increase_one_guard_floor_and_original_resource(monkeypatch):
    db,provider=await setup(monkeypatch);row=await preview(db);result=await execute(db,row)
    assert result['status']=='completed' and provider.writes==1
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    allocation=next(iter(state['allocations'].values()))
    assert state['epoch']==3 and allocation['budget_native']=='22'
    assert allocation['dispatch_marker']['budget_resource']=='customers/123/campaignBudgets/999'
    assert (await db[guard.AUTOMATION_POLICIES].find_one({'account_id':'123'}))['_dispatch_days'][0]['proposal_ids']==[row['proposal_id']]
    assert (await execute(db,row))['status']=='completed' and provider.writes==1

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['before_floor','after_floor_ack'])
async def test_revoke_orders_actual_budget_increase(monkeypatch,timing):
    db,provider=await setup(monkeypatch);row=await preview(db);original=authority.acknowledge_standing_dispatch_floor
    async def changed(database,proposal,**kwargs):
        if timing=='after_floor_ack':result=await original(database,proposal,**kwargs)
        result_revoke=await mutate_google_automation_policy(database,policy_kind='standing',scope={'user_id':'owner','account_id':'123'},
            expected_revision=3,values={'enabled':False,'updated_by':'owner'})
        assert result_revoke['committed'] is True
        return await original(database,proposal,**kwargs) if timing=='before_floor' else result
    monkeypatch.setattr(authority,'acknowledge_standing_dispatch_floor',changed)
    result=await execute(db,row);state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert provider.writes==(0 if timing=='before_floor' else 1)
    assert result['submission_started'] is (timing=='after_floor_ack')
    assert state['epoch']==(2 if timing=='before_floor' else 4)
    if timing=='before_floor':assert state['active_claim'] and (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['waiting','before_commit','during_ack'])
async def test_policy_expiry_crossing_all_floor_phases_retains_hold(monkeypatch,timing):
    db,provider=await setup(monkeypatch)
    await db[guard.AUTOMATION_POLICIES].update_one({'account_id':'123'},{'$set':{'expires_at':(NOW+timedelta(seconds=20)).isoformat()}})
    row=await preview(db)
    if timing=='waiting':
        original=authority.acknowledge_standing_dispatch_floor
        async def expired(database,proposal,**kwargs):
            advance(monkeypatch,NOW+timedelta(seconds=21));return await original(database,proposal,**kwargs)
        monkeypatch.setattr(authority,'acknowledge_standing_dispatch_floor',expired)
    elif timing=='before_commit':
        original=authority._guard_standing_dispatch_floor
        async def expired(*args,**kwargs):
            result=await original(*args,**kwargs);advance(monkeypatch,NOW+timedelta(seconds=21));return result
        monkeypatch.setattr(authority,'_guard_standing_dispatch_floor',expired)
    else:
        original=guard._commit_once;calls=0
        async def expired(*args):
            nonlocal calls
            calls+=1;result=await original(*args)
            if calls==2:advance(monkeypatch,NOW+timedelta(seconds=21))
            return result
        monkeypatch.setattr(guard,'_commit_once',expired)
    result=await execute(db,row)
    assert result['status']=='uncertain' and provider.writes==0 and result['submission_started'] is (timing=='during_ack')
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['epoch']==(2 if timing=='during_ack' else 1) and state['active_claim']
    assert next(iter(state['allocations'].values()))['budget_native']=='22'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('tamper',['resource','amount','analysis','native','fence','operations','claim'])
async def test_floor_reloads_original_budget_proofs(monkeypatch,tamper):
    db,provider=await setup(monkeypatch);row=await preview(db);original=authority.acknowledge_standing_dispatch_floor
    async def changed(database,proposal,**kwargs):
        if tamper in {'resource','amount'}:
            state=await database[guard.BUDGET_GUARDS].find_one({'account_id':'123'});allocations=deepcopy(state['allocations']);entry=next(iter(allocations.values()))
            entry['budget_resource' if tamper=='resource' else 'budget_native']='customers/123/campaignBudgets/998' if tamper=='resource' else '1'
            await database[guard.BUDGET_GUARDS].update_one({'account_id':'123'},{'$set':{'allocations':allocations}})
        elif tamper=='analysis':await database[management.ANALYSES].update_one({'analysis_id':'analysis1'},{'$set':{'created_at':'changed'}})
        elif tamper=='native':kwargs['native_evidence']=deepcopy(kwargs['native_evidence']);kwargs['native_evidence']['inventory']['complete']=False
        elif tamper=='fence':await database[management.FENCES].update_one({'account_id':'123'},{'$set':{'status':'released'}})
        elif tamper=='operations':await database[management.PROPOSALS].update_one({'proposal_id':proposal['proposal_id']},{'$set':{'operations':[]}})
        else:
            policy=await database[guard.AUTOMATION_POLICIES].find_one({'account_id':'123'});claim=deepcopy(policy['_dispatch_claim']);claim['operations_digest']='0'*64
            await database[guard.AUTOMATION_POLICIES].update_one({'account_id':'123'},{'$set':{'_dispatch_claim':claim}})
        return await original(database,proposal,**kwargs)
    monkeypatch.setattr(authority,'acknowledge_standing_dispatch_floor',changed)
    result=await execute(db,row)
    assert provider.writes==0 and result['submission_started'] is False
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['epoch']==1 and state['active_claim'] and next(iter(state['allocations'].values())).get('dispatch_marker') is None
