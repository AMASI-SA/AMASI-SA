"""Standing creation uses native PAUSED proposals and explicit private claims."""
from copy import deepcopy
import pytest
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


class DB:
    def __init__(self,docs):self.collections={k:Collection(v) for k,v in docs.items()}
    def __getitem__(self,key):return self.collections.setdefault(key,Collection())
    def __getattr__(self,key):return self[key]


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
