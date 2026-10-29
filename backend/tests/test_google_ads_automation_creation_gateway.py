"""Standing creation uses native PAUSED proposals and explicit private claims."""
from copy import deepcopy
import pytest


@pytest.fixture(autouse=True)
def accepted_guard_in_unit_scope(monkeypatch):
    # Simulate an accepted code migration only for unit contract checks. This is
    # not transaction, replica, provider or production activation evidence.
    from integrations_control_center import google_ads_automation_budget_guard as guard
    monkeypatch.setattr(guard, 'budget_guard_runtime_available', lambda: True)

from fastapi import HTTPException

@pytest.mark.asyncio
async def test_default_off_creation_never_reads_storage_or_provider(monkeypatch):
    from integrations_control_center import google_ads_automation_creation_gateway as gateway
    monkeypatch.delenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED',raising=False)
    class Unavailable:
        def __getitem__(self,key):raise AssertionError('storage access before kill switch')
    with pytest.raises(HTTPException) as error:
        await gateway.preview_google_automation_creation(Unavailable(),user_id='owner',account_id='123',policy_revision=2,
            choice={'product_id':'1','template_id':'winter-search','template_version':1})
    assert error.value.detail['code']=='google_creation_execution_disabled'

from integrations_control_center import google_ads_management as management
from integrations_control_center import google_ads_automation_creation_gateway as gateway
from tests.test_google_ads_automation_creation_contract import prepared_case,NOW


def bound_case():
    from integrations_control_center.google_ads_campaign_builder import build_campaign_operations
    values=prepared_case();intent=gateway.contract.prepare_google_creation_intent(**values)
    proposal_id='creation_proposal1'
    operations=build_campaign_operations(intent['draft'],'123')
    campaign=next(op['campaignOperation']['create']['resourceName'] for op in operations if 'campaignOperation' in op)
    label='customers/123/labels/-9000000000000'
    operations += [{'labelOperation':{'create':{'resourceName':label,'name':'MEZAN-'+proposal_id}}},
        {'campaignLabelOperation':{'create':{'campaign':campaign,'label':label}}}]
    row={'user_id':'owner','account_id':'123','proposal_id':proposal_id,'actor_id':'mezan_google_automation_v1',
        'provider':'google_ads','action':'create_campaign','operations':operations,'automation_source':gateway._source(intent),
        'planned':{**intent['draft'],'initial_status':'PAUSED'},'correlation_label':'MEZAN-'+proposal_id,
        'idempotency_key':intent['creation_identity']}
    row['confirmation_digest']=management.digest(management.immutable(row))
    saved={'user_id':'owner','account_id':'123','creation_identity':intent['creation_identity'],
        'intent':intent,'choice':values['choice'],'proposal_id':proposal_id}
    return row,saved


class BindingDB:
    def __init__(self,saved):self.saved=saved
    def __getitem__(self,key):assert key==gateway.CREATION_INTENTS;return self
    async def find_one(self,scope,projection=None):
        return deepcopy(self.saved) if self.saved and all(self.saved.get(k)==v for k,v in scope.items()) else None


@pytest.mark.asyncio
async def test_exact_native_paused_binding_without_fake_human_owner():
    row,saved=bound_case()
    assert (await gateway._intent(BindingDB(saved),row))['intent']==saved['intent']


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['manual','unknown_source','extra_source','budget','enable','label','other_proposal','missing_intent'])
async def test_forged_or_drifted_creation_never_enters_execution(change):
    row,saved=bound_case()
    if change=='manual':row['actor_id']='owner'
    if change=='unknown_source':row['automation_source']['kind']='standing_policy'
    if change=='extra_source':row['automation_source']['owner']=True
    if change=='budget':row['planned']['budget_native']='999'
    if change=='enable':next(op['campaignOperation']['create'] for op in row['operations'] if 'campaignOperation' in op)['status']='ENABLED'
    if change=='label':row['correlation_label']='MEZAN-forged'
    if change=='other_proposal':saved['proposal_id']='other-proposal'
    row['confirmation_digest']=management.digest(management.immutable(row))
    with pytest.raises(HTTPException) as error:
        await gateway._intent(BindingDB(None if change=='missing_intent' else saved),row)
    assert error.value.detail['code']=='google_creation_proposal_binding_invalid'


@pytest.mark.parametrize('flag',['GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED'])
def test_dispatch_requires_all_three_feature_flags(monkeypatch,flag):
    for name in ['GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED']:
        monkeypatch.setenv(name,'true')
    assert gateway.creation_dispatch_enabled()
    monkeypatch.setenv(flag,'false')
    assert not gateway.creation_dispatch_enabled()


def test_worker_flag_uses_existing_worker_contract(monkeypatch):
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','true')
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','true')
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','enabled')
    assert gateway.creation_dispatch_enabled()
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','enabled')
    from integrations_control_center.google_ads_automation_authority import execution_enabled
    assert gateway.creation_dispatch_enabled()==execution_enabled()

from datetime import timedelta
from types import SimpleNamespace
from pymongo.errors import DuplicateKeyError
from tests.test_google_ads_automation_authority import case_documents
from tests.test_google_ads_automation_creation_plan import Native as NativeReads


def matches(row,query):
    for key,expected in query.items():
        if key=='$or':
            if not any(matches(row,q) for q in expected):return False
            continue
        if key=='$and':
            if not all(matches(row,q) for q in expected):return False
            continue
        actual=row
        for part in key.split('.'):
            actual=actual.get(part) if isinstance(actual,dict) else None
        if isinstance(expected,dict) and any(k.startswith('$') for k in expected):
            for op,value in expected.items():
                if op=='$exists' and (actual is not None)!=value:return False
                if op=='$eq' and actual!=value:return False
                if op=='$ne' and actual==value:return False
                if op=='$in' and actual not in value:return False
                if op=='$gt' and (actual is None or actual<=value):return False
                if op=='$gte' and (actual is None or actual<value):return False
                if op=='$lt' and (actual is None or actual>=value):return False
        elif actual!=expected:return False
    return True


class Cursor:
    def __init__(self,rows):self.rows=deepcopy(rows)
    def sort(self,*args):return self
    async def to_list(self,length=None):return self.rows[:length]


class Collection:
    def __init__(self,rows=()):self.rows=deepcopy(list(rows));self.indexes=[]
    async def create_index(self,keys,**kwargs):
        if kwargs.get('unique') and keys not in self.indexes:self.indexes.append(keys)
    async def find_one(self,query,projection=None):
        return next((deepcopy(row) for row in self.rows if matches(row,query)),None)
    def find(self,query,projection=None):return Cursor([r for r in self.rows if matches(r,query)])
    async def insert_one(self,row):
        for index in self.indexes:
            if any(all(other.get(k)==row.get(k) for k,_ in index) for other in self.rows):raise DuplicateKeyError('fake duplicate')
        self.rows.append(deepcopy(row));return SimpleNamespace(inserted_id='test')
    async def update_one(self,query,update,upsert=False):
        row=next((r for r in self.rows if matches(r,query)),None);inserted=False
        if row is None and upsert:
            row={k:deepcopy(v) for k,v in query.items() if not k.startswith('$') and not isinstance(v,dict)}
            row.update(deepcopy(update.get('$setOnInsert',{})));await self.insert_one(row);row=self.rows[-1];inserted=True
        if row is None:return SimpleNamespace(matched_count=0)
        for op,values in update.items():
            if op=='$setOnInsert':continue
            for key,value in values.items():
                target=row;path=key.split('.')
                for part in path[:-1]:target=target.setdefault(part,{})
                key=path[-1]
                if op=='$set':target[key]=deepcopy(value)
                elif op=='$inc':target[key]=target.get(key,0)+value
                elif op=='$unset':target.pop(key,None)
                elif op=='$push':
                    target.setdefault(key,[]).extend(deepcopy(value.get('$each',[value])) if isinstance(value,dict) else [value])
                    if isinstance(value,dict) and '$slice' in value:target[key]=target[key][value['$slice']:]
        return SimpleNamespace(matched_count=0 if inserted else 1,upserted_id='test' if inserted else None)


from tests.google_ads_transactional_fake import TransactionalDBMixin, TransactionalCollection


class DB(TransactionalDBMixin):
    def __init__(self,docs):
        self.collections={k:Collection(v) for k,v in docs.items()}
        self._tx_setup(self.collections)
    def _tx_get(self,key):
        if key not in self._tx_collections:
            self._tx_collections[key]=TransactionalCollection(self,key,Collection())
        return self._tx_collections[key]
    def __getitem__(self,key):return self._tx_get(key)
    def __getattr__(self,key):
        if key.startswith("_"):raise AttributeError(key)
        return self[key]


class NativeCreationProvider(NativeReads):
    """Fake only the external Google transport; use actual native verification."""
    def __init__(self):
        super().__init__();self.previews=self.writes=0;self.loss=False;self.verified=True
        self.operations=[];self.names=[];self.graph={};self.customer_id='123'
    async def account_metadata(self):return {**await super().account_metadata(),'name':'Test Google'}
    async def mutate(self,operations,*,validate_only=False):
        if validate_only:self.previews+=1;return {}
        self.writes+=1;self.operations=deepcopy(operations);self.names=[];response=[]
        types={'campaignOperation':'campaigns','campaignBudgetOperation':'campaignBudgets','adGroupOperation':'adGroups',
            'adGroupAdOperation':'adGroupAds','adGroupCriterionOperation':'adGroupCriteria','campaignCriterionOperation':'campaignCriteria',
            'campaignConversionGoalOperation':'campaignConversionGoals','labelOperation':'labels','campaignLabelOperation':'campaignLabels'}
        for i,op in enumerate(operations):
            key=next(iter(op));root=types[key]
            suffix='777' if root=='campaigns' else '777~888' if root=='campaignLabels' else '888' if root=='labels' else str(1000+i)
            name=f'customers/123/{root}/{suffix}'
            if key=='campaignConversionGoalOperation':name='customers/123/campaignConversionGoals/777~PURCHASE~WEBSITE'
            self.names.append(name);response.append({key.removesuffix('Operation')+'Result':{'resourceName':name}})
        from integrations_control_center.google_ads_verification import _replace,snake
        identities={spec['create']['resourceName']:name for op,name in zip(operations,self.names)
            for spec in [next(iter(op.values()))] if spec.get('create',{}).get('resourceName')}
        for op,name in zip(operations,self.names):
            key=next(iter(op));root=key.removesuffix('Operation');spec=op[key]
            payload=_replace(spec.get('create') or spec.get('update'),identities);payload['resourceName']=name
            if root=='campaign':payload['biddingStrategyType']='MAXIMIZE_CONVERSION_VALUE'
            if root=='adGroupAd':payload['ad']['type']='RESPONSIVE_SEARCH_AD'
            if root=='campaignConversionGoal':payload.update(campaign='customers/123/campaigns/777',category='PURCHASE',origin='WEBSITE')
            self.graph.setdefault(snake(root),[]).append({root:payload})
        if self.loss:raise TimeoutError('lost native response')
        return {'mutateOperationResponses':response}
    async def search(self,query,**kwargs):
        import re
        root=re.search(r'FROM ([a-z_]+)',query).group(1)
        if self.graph and root in self.graph:
            rows=deepcopy(self.graph[root])
            if root=='campaign_label':
                for row in rows:row['label']=deepcopy(self.graph['label'][0]['label'])
            return rows,True
        return await super().search(query)
    async def verify_operations(self,operations,names,**kwargs):
        if not self.verified:return False,{'readback_independent':True,'verified_resources':[],'coverage_gaps':['test-delay'],'mismatches':[]}
        from integrations_control_center.google_ads_verification import verify_google_operations
        return await verify_google_operations(self,operations,names,**kwargs)
    async def campaign_snapshot(self,campaign_id,**kwargs):
        return {'campaign':{'id':campaign_id,'resourceName':f'customers/123/campaigns/{campaign_id}','name':'Paused search',
            'status':'PAUSED','advertisingChannelType':'SEARCH'},'campaign_budget':{'period':'DAILY','amountMicros':'15000000'},'groups':[],'ads':[]}


async def setup(monkeypatch,db=None):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    values=prepared_case();docs,_=case_documents(current=NOW)
    # Explicit owner limits in this controlled fixture admit the55 native+15
    # PAUSED hold; these are never production budget recommendations.
    values['policy']['max_account_daily_budget_native']='100'
    docs['mezan_google_ads_automation_policies_v1'][0]['max_account_daily_budget_native']='100'
    docs[management.PROPOSALS]=[];docs[management.FENCES]=[]
    docs[gateway.CREATION_POLICIES]=[{**values['policy'],'template':values['template'],'updated_by':'owner'}]
    docs[gateway.PRODUCT_COLLECTION]=[values['product']]
    if db is None:db=DB(docs)
    else:
        from tests.test_google_ads_automation_authority_mongo import seed
        await seed(db,docs)
    provider=NativeCreationProvider()
    for name in ['GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED']:
        monkeypatch.setenv(name,'true')
    monkeypatch.setattr(management,'now',lambda:NOW)
    monkeypatch.setattr(authority,'now',lambda current=None:current or NOW)
    from tests.test_google_ads_automation_authority import freeze_guard_clock
    freeze_guard_clock(monkeypatch,NOW)
    async def client(database,user_id,account_id,**kwargs):return provider,'test-epoch'
    monkeypatch.setattr(management,'account_client',client)
    return db,provider,values


async def preview(db,values):
    return await gateway.preview_google_automation_creation(db,user_id='owner',account_id='123',policy_revision=2,choice=values['choice'])

async def execute(db,row):
    return await gateway.execute_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])


@pytest.mark.asyncio
async def test_native_preview_is_validate_only_and_binds_server_choice(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    assert provider.previews==1 and provider.writes==0
    assert row['actor_id']=='mezan_google_automation_v1' and row['planned']['initial_status']=='PAUSED'
    assert row['automation_source']['kind']=='standing_creation'
    assert (await db[gateway.CREATION_INTENTS].find_one({'account_id':'123'}))['proposal_id']==row['proposal_id']


@pytest.mark.asyncio
async def test_native_creation_floor_readback_owned_ledger_and_no_replay(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);result=await execute(db,row)
    assert result['status']=='completed' and result['verified'] is True and provider.writes==1
    assert result['after']['status']=='PAUSED'
    stored=await management.get_proposal(db,'owner',row['proposal_id'])
    assert stored['submission_started'] is True and stored['accepted_receipt'] is True
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    owned=policy['_creation_ledger'][row['automation_source']['creation_identity']]
    assert owned['state']=='owned' and owned['verified'] is True and owned['created_campaign_id']=='777'
    assert policy['_creation_claim'] is None
    assert (await execute(db,row))['status']=='completed' and provider.writes==1
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_active55_reserves_paused15_within_explicit100_cap_without_enable(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    intent=(await db[gateway.CREATION_INTENTS].find_one({'account_id':'123'}))['intent']
    assert intent['enable_readiness']['enabled_and_reserved_average_budget_native']=='55'
    assert intent['enable_readiness']['ready_for_enable_preview'] is False
    assert intent['executable'] is False and intent['auto_enable_new'] is False
    result=await execute(db,row)
    assert result['status']=='completed' and result['after']['status']=='PAUSED' and provider.writes==1
    entry=(await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'}))['_creation_ledger'][intent['creation_identity']]
    assert entry['reservation_native']=='15' and entry['reservation_currency_code']=='SAR'
    assert entry['reserved_enable_budget_native']=='0'


@pytest.mark.asyncio
@pytest.mark.parametrize('flag',['GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','GOOGLE_ADS_AUTOMATION_WORKER_ENABLED','GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED'])
async def test_stop_after_acked_floor_never_http_and_preserves_floor(monkeypatch,flag):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.acknowledge_paused_creation_dispatch_floor
    async def stop(database,proposal):
        result=await original(database,proposal)
        monkeypatch.setenv(flag,'false')
        return result
    monkeypatch.setattr(authority,'acknowledge_paused_creation_dispatch_floor',stop)
    result=await execute(db,row)
    assert result['status']=='failed' and result['submission_started'] is True and provider.writes==0
    stored=await management.get_proposal(db,'owner',row['proposal_id'])
    assert stored['dispatch_cancelled_before_http'] is True
    assert stored['dispatch_cancelled_before_http_claim_id']==stored['execution_claim_id']
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    assert policy['_creation_claim'] is None
    assert policy['_creation_ledger'][row['automation_source']['creation_identity']]['state']=='no_campaign'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_floor_ack_loss_has_no_http_or_no_http_marker_and_retains_reservation(monkeypatch):
    from integrations_control_center import google_ads_automation_budget_guard as guard
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=guard._commit_once
    calls=0
    async def lose(*args):
        nonlocal calls
        calls+=1
        result=await original(*args)
        if calls==2:raise TimeoutError('lost floor ACK')
        return result
    monkeypatch.setattr(guard,'_commit_once',lose)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is True and provider.writes==0
    stored=await management.get_proposal(db,'owner',row['proposal_id'])
    assert stored.get('dispatch_cancelled_before_http') is not True
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert (await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'}))['_creation_claim']['proposal_id']==row['proposal_id']
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0


@pytest.mark.asyncio
async def test_authority_commit_ack_loss_no_floor_no_http_no_unlock(monkeypatch):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.authorize_google_creation_claim
    async def lose(*args,**kwargs):
        await original(*args,**kwargs);raise TimeoutError('lost claim ACK')
    monkeypatch.setattr(authority,'authorize_google_creation_claim',lose)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert (await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'}))['_creation_claim']['proposal_id']==row['proposal_id']


@pytest.mark.asyncio
async def test_provider_accepted_response_loss_is_never_resent_or_owned_early(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);provider.loss=True
    result=await execute(db,row)
    assert result['status']=='uncertain' and provider.writes==1
    entry=(await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'}))['_creation_ledger'][row['automation_source']['creation_identity']]
    assert entry['state']=='reserved' and entry.get('verified') is not True
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==1


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['completed','rejected'])
async def test_settlement_ack_loss_keeps_native_fence_then_off_recovery_releases(monkeypatch,outcome):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.settle_google_creation_claim
    if outcome=='rejected':
        mutate=provider.mutate
        async def reject(operations,*,validate_only=False):
            if validate_only:return await mutate(operations,validate_only=True)
            provider.writes+=1
            raise HTTPException(502,detail={'code':'google_rejected','provider_status':400,'provider_codes':['INVALID_ARGUMENT']})
        monkeypatch.setattr(provider,'mutate',reject)
    async def lose(*args,**kwargs):
        await original(*args,**kwargs);raise TimeoutError('lost settlement ACK')
    monkeypatch.setattr(authority,'settle_google_creation_claim',lose)
    with pytest.raises(TimeoutError):await execute(db,row)
    assert provider.writes==1 and (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    monkeypatch.setattr(authority,'settle_google_creation_claim',original)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','false')
    result=await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']==('completed' if outcome=='completed' else 'failed') and provider.writes==1
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_revoked_policy_and_catalog_drift_never_send_or_reserve(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    await db[gateway.CREATION_POLICIES].update_one({'account_id':'123'},{'$set':{'enabled':False}})
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==0 and not (await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})).get('_creation_claim')
    await db[gateway.CREATION_POLICIES].update_one({'account_id':'123'},{'$set':{'enabled':True}})
    await db[gateway.PRODUCT_COLLECTION].update_one({'salla_product_id':'1'},{'$set':{'source_revision':'b'*64}})
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    assert not (await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})).get('_creation_claim')


@pytest.mark.asyncio
async def test_crossnamespace_catalog_alias_collision_never_previews(monkeypatch):
    db,provider,values=await setup(monkeypatch)
    db[gateway.PRODUCT_COLLECTION].rows.append({**values['product'],'salla_product_id':'2','mezan_product_id':'1'})
    with pytest.raises(HTTPException) as error:await preview(db,values)
    assert error.value.detail['code']=='google_creation_product_identity_ambiguous'
    assert provider.previews==provider.writes==0


def advance(monkeypatch,later):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    monkeypatch.setattr(management,'now',lambda:later)
    monkeypatch.setattr(authority,'now',lambda current=None:current or later)
    from tests.test_google_ads_automation_authority import freeze_guard_clock
    freeze_guard_clock(monkeypatch,later)


@pytest.mark.asyncio
async def test_lost_native_response_recovers_original_full_graph_without_receipt_or_resend(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);provider.loss=True
    result=await execute(db,row)
    assert result['status']=='uncertain' and provider.writes==1
    advance(monkeypatch,NOW+timedelta(seconds=31))
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
    result=await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']=='completed' and provider.writes==1
    stored=await management.get_proposal(db,'owner',row['proposal_id'])
    assert stored.get('accepted_receipt') is not True and stored['verification']['readback_independent'] is True
    assert stored['verification']['coverage_gaps']==[] and stored['verification']['mismatches']==[]
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    assert policy['_creation_ledger'][row['automation_source']['creation_identity']]['state']=='owned'


@pytest.mark.asyncio
async def test_ambiguous_native_correlation_cannot_establish_ownership(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);provider.loss=True
    await execute(db,row)
    duplicate=deepcopy(provider.graph['campaign_label'][0])
    duplicate['campaignLabel'].update(resourceName='customers/123/campaignLabels/999~888',campaign='customers/123/campaigns/999')
    provider.graph['campaign_label'].append(duplicate)
    advance(monkeypatch,NOW+timedelta(seconds=31))
    with pytest.raises(HTTPException) as error:
        await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert error.value.detail['code']=='google_ads_recovery_correlation_ambiguous' and provider.writes==1
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    assert policy['_creation_ledger'][row['automation_source']['creation_identity']]['state']=='reserved'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'


@pytest.mark.asyncio
@pytest.mark.parametrize('slow',['credential','metadata','inventory','validation'])
async def test_91second_earliest_native_bundle_cannot_claim_or_send(monkeypatch,slow):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);clock={'now':NOW}
    monkeypatch.setattr(management,'now',lambda:clock['now'])
    monkeypatch.setattr(authority,'now',lambda current=None:current or clock['now'])
    if slow=='credential':
        original=management.account_client
        async def delayed(*args,**kwargs):
            result=await original(*args,**kwargs);clock['now']+=timedelta(seconds=91);return result
        monkeypatch.setattr(management,'account_client',delayed)
    elif slow=='metadata':
        original=provider.account_metadata
        async def delayed():
            result=await original();clock['now']+=timedelta(seconds=91);return result
        monkeypatch.setattr(provider,'account_metadata',delayed)
    elif slow=='inventory':
        original=provider.search
        async def delayed(query,**kwargs):
            result=await original(query,**kwargs)
            if 'FROM campaign ' in query:clock['now']+=timedelta(seconds=91)
            return result
        monkeypatch.setattr(provider,'search',delayed)
    else:
        original=provider.mutate
        async def delayed(operations,**kwargs):
            result=await original(operations,**kwargs)
            if kwargs.get('validate_only'):clock['now']+=timedelta(seconds=91)
            return result
        monkeypatch.setattr(provider,'mutate',delayed)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    assert not (await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})).get('_creation_claim')
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'


@pytest.mark.asyncio
async def test_committed_claim_cancellation_stays_held_then_stale_cancel_settles_read_only(monkeypatch):
    import asyncio
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.authorize_google_creation_claim
    async def cancelled(*args,**kwargs):
        await original(*args,**kwargs);raise asyncio.CancelledError()
    monkeypatch.setattr(authority,'authorize_google_creation_claim',cancelled)
    with pytest.raises(asyncio.CancelledError):await execute(db,row)
    stored=await management.get_proposal(db,'owner',row['proposal_id'])
    assert stored['status']=='uncertain' and stored['submission_started'] is False and provider.writes==0
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    advance(monkeypatch,NOW+timedelta(minutes=6))
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
    result=await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']=='failed' and result['submission_started'] is False and provider.writes==0
    assert (await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'}))['_creation_claim'] is None
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_terminal_cancel_wins_before_old_floor_without_resurrection(monkeypatch):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.acknowledge_paused_creation_dispatch_floor
    async def cancel(database,proposal):
        await database[management.PROPOSALS].update_one({'proposal_id':proposal['proposal_id'],
            'execution_claim_id':proposal['execution_claim_id'],'status':'approved','submission_started':False},
            {'$set':{'status':'failed','submission_started':False,'provider_write_reached':False,'safe_error':'execution_cancelled_before_dispatch'}})
        await authority.settle_google_creation_claim(database,user_id='owner',account_id='123',claim_id=proposal['execution_claim_id'])
        await management._release_fence(database,proposal)
        return await original(database,proposal)
    monkeypatch.setattr(authority,'acknowledge_paused_creation_dispatch_floor',cancel)
    result=await execute(db,row)
    assert result['status']=='failed' and result['submission_started'] is False and provider.writes==0
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'

@pytest.mark.asyncio
@pytest.mark.parametrize('tamper',['identity','ownership','missing_policy'])
async def test_unproved_settlement_ack_never_unlocks_native_fence(monkeypatch,tamper):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.settle_google_creation_claim
    async def wrong(*args,**kwargs):
        if tamper=='missing_policy':
            db[gateway.CREATION_POLICIES].rows=[]
            return await original(*args,**kwargs)
        result=await original(*args,**kwargs)
        if tamper=='identity':result['creation_identity']='google_create_'+'0'*64
        else:result['verified_ownership']=False
        return result
    monkeypatch.setattr(authority,'settle_google_creation_claim',wrong)
    with pytest.raises(HTTPException):await execute(db,row)
    assert provider.writes==1
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'


@pytest.mark.asyncio
async def test_claim_ack_for_wrong_creation_identity_cannot_reach_native_floor(monkeypatch):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.authorize_google_creation_claim
    async def wrong(*args,**kwargs):
        reply=await original(*args,**kwargs)
        return {**reply,'creation_identity':'google_create_'+'0'*64}
    monkeypatch.setattr(authority,'authorize_google_creation_claim',wrong)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'


@pytest.mark.asyncio
async def test_native_proof_expiring_during_acked_floor_never_reaches_http(monkeypatch):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    db,provider,values=await setup(monkeypatch);row=await preview(db,values);original=authority.acknowledge_paused_creation_dispatch_floor
    async def slow_floor(database,proposal):
        result=await original(database,proposal)
        advance(monkeypatch,NOW+timedelta(seconds=91))
        return result
    monkeypatch.setattr(authority,'acknowledge_paused_creation_dispatch_floor',slow_floor)
    result=await execute(db,row)
    assert result['status']=='failed' and result['submission_started'] is True and provider.writes==0
    stored=await management.get_proposal(db,'owner',row['proposal_id'])
    assert stored['dispatch_cancelled_before_http'] is True
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'


@pytest.mark.asyncio
async def test_standing_policy_native_proof_expiring_after_floor_never_reaches_http(monkeypatch):
    from tests.test_google_ads_automation_gateway import integration,preview as policy_preview,execute as policy_execute
    from tests.test_google_ads_automation_authority import NOW as POLICY_NOW
    from integrations_control_center import google_ads_automation_authority as authority
    db,provider=integration.__wrapped__(monkeypatch);row=await policy_preview(db);original=authority.acknowledge_standing_dispatch_floor
    async def slow_floor(database,proposal,**kwargs):
        result=await original(database,proposal,**kwargs)
        monkeypatch.setattr(management,'now',lambda:POLICY_NOW+timedelta(seconds=91))
        monkeypatch.setattr(authority,'_now',lambda current=None:current or POLICY_NOW+timedelta(seconds=91))
        from tests.test_google_ads_automation_authority import freeze_guard_clock
        freeze_guard_clock(monkeypatch,POLICY_NOW+timedelta(seconds=91))
        return result
    monkeypatch.setattr(authority,'acknowledge_standing_dispatch_floor',slow_floor)
    result=await policy_execute(db,row)
    assert result['status']=='failed' and result['submission_started'] is True and provider.writes==0
    stored=await management.get_proposal(db,'owner',row['proposal_id'])
    assert stored['dispatch_cancelled_before_http'] is True


@pytest.mark.asyncio
async def test_unclaimed_catalog_rejection_stale_cancellation_durably_unblocks_account(monkeypatch):
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    await db[gateway.PRODUCT_COLLECTION].update_one({'salla_product_id':'1'},{'$set':{'source_revision':'b'*64}})
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    assert not (await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})).get('_creation_claim')
    advance(monkeypatch,NOW+timedelta(minutes=6))
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false')
    result=await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id'])
    assert result['status']=='failed' and result['submission_started'] is False and provider.writes==0
    policy=await db[gateway.CREATION_POLICIES].find_one({'account_id':'123'})
    cancelled=policy['_creation_ledger'][row['automation_source']['creation_identity']]
    assert cancelled['state']=='no_campaign' and cancelled['authorization_granted'] is False
    assert cancelled['reservation_native']=='0' and cancelled.get('authorized_at') is None
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='released'
    assert (await gateway.reconcile_google_automation_creation(db,user_id='owner',account_id='123',proposal_id=row['proposal_id']))['status']=='failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('target',['owner','canonical_product'])
@pytest.mark.parametrize('stage',['after_tx_reads_before_commit','after_commit'])
async def test_postcommit_live_revalidation_blocks_nonconflicting_document_drift(monkeypatch,target,stage):
    from integrations_control_center import google_ads_automation_creation_authority as authority
    from integrations_control_center import google_ads_automation_budget_guard as guard
    db,provider,values=await setup(monkeypatch);row=await preview(db,values)
    def drift():
        if target=='owner':db.users.rows[0]['disabled']=True
        else:db[gateway.PRODUCT_COLLECTION].rows[0]['quantity']=0
    if stage=='after_commit':
        async def changed():drift()
        db.after_commit=changed
    else:
        original=authority._linearize_creation_claim
        async def changed(*args):
            result=await original(*args);drift();return result
        monkeypatch.setattr(authority,'_linearize_creation_claim',changed)
    result=await execute(db,row)
    assert result['status']=='uncertain' and result['submission_started'] is False and provider.writes==0
    policy=await db[gateway.CREATION_POLICIES].find_one({'user_id':'owner','account_id':'123'})
    budget=await db[guard.BUDGET_GUARDS].find_one({'user_id':'owner','account_id':'123'})
    assert policy['_creation_claim'] and budget['active_claim']
    assert budget['epoch']==1 and next(iter(budget['allocations'].values()))['state']=='reserved'
    assert (await db[management.FENCES].find_one({'account_id':'123'}))['status']=='claimed'
    assert sum(session.commit_calls for session in db.sessions)==1
