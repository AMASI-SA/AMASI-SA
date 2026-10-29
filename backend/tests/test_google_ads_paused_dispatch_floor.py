"""Creation-only atomic floor; no real provider calls or production flags."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_automation_creation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_automation_policy_mutation_adapter import mutate_google_automation_policy
from tests.test_google_ads_automation_creation_gateway import setup, preview, execute

@pytest.fixture(autouse=True)
def enabled_contract(monkeypatch):
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:True)

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['before_floor','after_floor_ack'])
async def test_revoke_order_is_serialized_at_common_floor(monkeypatch,timing):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    original=authority.acknowledge_paused_creation_dispatch_floor
    async def revoked(database,proposal):
        if timing=='after_floor_ack':result=await original(database,proposal)
        changed=await mutate_google_automation_policy(database,policy_kind='creation',scope={'user_id':'owner','account_id':'123'},
            expected_revision=2,values={'enabled':False,'updated_by':'owner'})
        assert changed['committed'] is True
        if timing=='before_floor':return await original(database,proposal)
        return result
    monkeypatch.setattr(authority,'acknowledge_paused_creation_dispatch_floor',revoked)
    result=await execute(db,row)
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    if timing=='before_floor':
        assert provider.writes==0 and result['submission_started'] is False
        assert state['epoch']==2 and state['active_claim']
        assert next(iter(state['allocations'].values())).get('dispatch_marker') is None
        assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    else:
        assert provider.writes==1 and result['submission_started'] is True and result['status']=='completed'
        assert state['epoch']==4
        assert next(iter(state['allocations'].values()))['dispatch_marker']['policy_revision']==2

@pytest.mark.asyncio
@pytest.mark.parametrize('tamper',['operations','confirmation','allocation','fence'])
async def test_floor_reloads_original_binding_and_retains_hold_on_drift(monkeypatch,tamper):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    original=authority.acknowledge_paused_creation_dispatch_floor
    async def changed(database,proposal):
        if tamper in {'operations','confirmation'}:
            await database[management.PROPOSALS].update_one({'proposal_id':proposal['proposal_id']},
                {'$set':{'operations':[]} if tamper=='operations' else {'confirmation_digest':'0'*64}})
        elif tamper=='fence':
            await database[management.FENCES].update_one({'account_id':'123'},{'$set':{'status':'released'}})
        else:
            record=await database[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
            allocations=deepcopy(record['allocations']);next(iter(allocations.values()))['creation_entry_digest']='0'*64
            await database[guard.BUDGET_GUARDS].update_one({'account_id':'123'},{'$set':{'allocations':allocations}})
        return await original(database,proposal)
    monkeypatch.setattr(authority,'acknowledge_paused_creation_dispatch_floor',changed)
    if tamper in {'operations','confirmation'}:
        with pytest.raises(HTTPException) as error:
            await execute(db,row)
        assert error.value.detail['code']=='google_creation_proposal_binding_invalid'
        result=await management.get_proposal(db,'owner',row['proposal_id'])
    else:
        result=await execute(db,row)
    assert result['submission_started'] is False and provider.writes==0
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['epoch']==1 and state['active_claim']
    assert next(iter(state['allocations'].values())).get('dispatch_marker') is None
    assert (await db[guard.CREATION_POLICIES].find_one({'account_id':'123'}))['_creation_claim']
