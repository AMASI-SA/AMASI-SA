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
@pytest.mark.parametrize('tamper',['operations','confirmation','allocation','fence','allocation_amount','allocation_currency','allocation_native'])
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
            allocations=deepcopy(record['allocations'])
            target=next(iter(allocations.values()))
            key,value={'allocation_amount':('budget_native','1'),'allocation_currency':('currency_code','USD'),
                'allocation_native':('native_inventory_digest','0'*64)}.get(tamper,('creation_entry_digest','0'*64))
            target[key]=value
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

@pytest.mark.asyncio
@pytest.mark.parametrize('timing',['waiting_for_floor','before_commit','during_commit_ack'])
async def test_expiry_crossing_floor_never_sends_or_releases_unknown(monkeypatch,timing):
    from datetime import timedelta
    from tests.test_google_ads_automation_creation_gateway import advance,NOW
    db,provider,values=await setup(monkeypatch)
    await db[guard.CREATION_POLICIES].update_one({'account_id':'123'},
        {'$set':{'expires_at':(NOW+timedelta(seconds=20)).isoformat()}})
    row=await preview(db,values)
    if timing=='waiting_for_floor':
        original=authority.acknowledge_paused_creation_dispatch_floor
        async def elapsed(database,proposal):
            advance(monkeypatch,NOW+timedelta(seconds=21))
            return await original(database,proposal)
        monkeypatch.setattr(authority,'acknowledge_paused_creation_dispatch_floor',elapsed)
    elif timing=='before_commit':
        original=authority._guard_paused_creation_command
        async def elapsed(*args,**kwargs):
            result=await original(*args,**kwargs)
            if isinstance(kwargs['command'],guard.PausedCreationDispatchFloor):advance(monkeypatch,NOW+timedelta(seconds=21))
            return result
        monkeypatch.setattr(authority,'_guard_paused_creation_command',elapsed)
    else:
        original=guard._commit_once;calls=0
        async def elapsed(*args):
            nonlocal calls
            calls+=1;result=await original(*args)
            if calls==2:advance(monkeypatch,NOW+timedelta(seconds=21))
            return result
        monkeypatch.setattr(guard,'_commit_once',elapsed)
    result=await execute(db,row)
    assert provider.writes==0 and result['status']=='uncertain'
    assert result['submission_started'] is (timing=='during_commit_ack')
    state=await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'})
    assert state['active_claim'] and state['epoch']==(2 if timing=='during_commit_ack' else 1)
    assert next(iter(state['allocations'].values()))['state']=='reserved'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0

@pytest.mark.asyncio
async def test_persisted_unknown_floor_cannot_be_replayed_after_process_state_loss(monkeypatch):
    from integrations_control_center import google_ads_automation_creation_gateway as gateway
    from tests.test_google_ads_automation_creation_gateway import advance,NOW
    from datetime import timedelta
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    original=guard._commit_once;calls=0;stale=[]
    original_floor=authority.acknowledge_paused_creation_dispatch_floor
    async def saved(database,proposal):
        stale.append(deepcopy(proposal));return await original_floor(database,proposal)
    monkeypatch.setattr(authority,'acknowledge_paused_creation_dispatch_floor',saved)
    async def lost(*args):
        nonlocal calls
        calls+=1;result=await original(*args)
        if calls==2:raise TimeoutError('floor ACK lost after durable commit')
        return result
    monkeypatch.setattr(guard,'_commit_once',lost)
    result=await execute(db,row)
    assert result['submission_started'] is True and provider.writes==0
    # New facade identity excludes in-memory operation receipts, as a restarted
    # process would. The durable proposal/guard forbid a stale caller's retry.
    restarted=type(db)({name:deepcopy(collection.rows) for name,collection in db._tx_collections.items()})
    with pytest.raises(HTTPException):await original_floor(restarted,stale[0])
    with pytest.raises(HTTPException):await execute(restarted,row)
    advance(monkeypatch,NOW+timedelta(seconds=31))
    with pytest.raises(HTTPException) as error:
        await gateway.reconcile_google_automation_creation(restarted,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert error.value.detail['code']=='google_ads_recovery_read_failed'
    assert (await management.get_proposal(restarted,'owner',row['proposal_id']))['status']=='uncertain' and provider.writes==0
    assert (await restarted[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
