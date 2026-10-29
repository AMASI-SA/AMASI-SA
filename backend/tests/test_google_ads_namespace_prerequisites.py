"""Read-only namespace contract, fail closed and privacy boundaries."""
import asyncio
from copy import deepcopy
import json

from bson.int64 import Int64
from fastapi import FastAPI, APIRouter
import httpx
import pytest

from integrations_control_center import google_ads_namespace_prerequisites as audit
from tests.test_google_ads_automation_policy import DB as OwnerDB


class Session:
    def __init__(self):self.ended=False
    async def end_session(self):self.ended=True


class DB:
    name='private_database_marker'
    def __init__(self):
        self.client=self;self.users=OwnerDB().users
        self.users.rows.append({'id':'staff','role':'staff','owner_id':'owner'})
        self.calls=[];self.sessions=[];self.rows={name:[] for name in audit.NAMES.values()}
        self.metadata={name:{'name':name,'type':'collection','options':{}} for name in audit.NAMES.values()}
        self.indexes={name:[{'name':'_id_','key':{'_id':1}}] for name in audit.NAMES.values()}
        for label in ('standing_policy','creation_policy'):
            self.indexes[audit.NAMES[label]].append({'name':'scope','key':{'user_id':1,'account_id':1},'unique':True})
        self.privileges={'ok':1,'authInfo':{'authenticatedUsers':[{'user':'private_username_marker'}],
            'authenticatedUserRoles':[{'role':'private_role_marker'}],'authenticatedUserPrivileges':[
                {'resource':{'db':self.name,'collection':''},'actions':['find','insert','update','createIndex']}]}}
        self.open_cursor=False;self.killed=[]
    async def start_session(self):
        session=Session();self.sessions.append(session);return session
    async def command(self,command,**kw):
        assert kw.get('session') is not None
        key=next(iter(command));self.calls.append(key)
        assert key in ('connectionStatus','listCollections','listIndexes','aggregate','killCursors'), 'no business writes/noop/index creation'
        if key=='connectionStatus':return deepcopy(self.privileges)
        if key=='killCursors':
            self.killed.extend(command['cursors']);return {'ok':1,'cursorsKilled':command['cursors']}
        if key=='listCollections':rows=list(self.metadata.values());ns='$cmd.listCollections'
        elif key=='listIndexes':rows=self.indexes[command[key]];ns=command[key]
        else:
            assert command['pipeline'][0]=={'$match':{'user_id':'owner'}}
            assert command['pipeline'][1]=={'$limit':1001}
            assert command['readConcern']=={'level':'majority'}
            rows=self.rows[command[key]];ns=command[key]
        return {'ok':1,'cursor':{'id':Int64(42 if self.open_cursor and key=='aggregate' else 0),
            'ns':self.name+'.'+ns,'firstBatch':deepcopy(rows)}}


def mounted(db,user):
    app,router=FastAPI(),APIRouter()
    async def current():return user
    audit.attach_google_ads_namespace_prerequisite_routes(router,db,current);app.include_router(router);return app


async def request(db,user={'id':'owner'}):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(mounted(db,user)),base_url='http://test') as client:
        return await client.get('/google_ads/automation/namespace-prerequisites')


@pytest.mark.asyncio
async def test_full_read_metadata_scope_success_is_not_migration_or_engine_authority():
    db=DB();response=await request(db);data=response.json()
    assert response.status_code==200 and data['namespace_prerequisites_verified'] is True
    assert data['engine_commit_verified'] is data['migration_ready'] is data['execution_authorized'] is data['legacy_bindings_verified'] is False
    assert data['business_writes']==data['provider_writes']==0
    assert all(session.ended for session in db.sessions)
    text=json.dumps(data)
    for private in ('private_database_marker','private_username_marker','private_role_marker',*audit.NAMES.values()):assert private not in text


@pytest.mark.asyncio
@pytest.mark.parametrize('user,status',[(None,401),({'id':'missing'},401),({'id':'staff'},403)])
async def test_owner_required_before_any_privilege_metadata(user,status):
    db=DB();response=await request(db,user)
    assert response.status_code==status and not db.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('auth',[{}, {'authenticatedUsers':[],'authenticatedUserPrivileges':[]},
    {'authenticatedUsers':[{}],'authenticatedUserPrivileges':[]}])
async def test_unavailable_or_noauth_metadata_never_invents_privileges(auth):
    db=DB();db.privileges={'ok':1,'authInfo':auth}
    data=(await request(db)).json()
    assert not data['namespace_prerequisites_verified'] and 'effective_privileges_unproved' in data['blocked_codes']


@pytest.mark.asyncio
async def test_update_only_cannot_prove_insert_or_put_index_action():
    db=DB();db.privileges['authInfo']['authenticatedUserPrivileges'][0]['actions']=['find','update']
    data=(await request(db)).json()
    assert not data['namespace_prerequisites_verified']
    assert not data['namespaces']['standing_policy']['actions_verified']['insert']
    assert not data['namespaces']['standing_policy']['create_index_for_put_verified']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['absent','validator','view','missing_index','ttl_index','reversed_index'])
async def test_unknown_collection_contract_or_index_is_unproved(mode):
    db=DB();name=audit.NAMES['standing_policy']
    if mode=='absent':del db.metadata[name]
    elif mode=='validator':db.metadata[name]['options']={'validator':{'private_validator_marker':True}}
    elif mode=='view':db.metadata[name]['type']='view'
    elif mode=='missing_index':db.indexes[name]=db.indexes[name][:1]
    elif mode=='ttl_index':db.indexes[name].append({'name':'ttl','key':{'expires':1},'expireAfterSeconds':0})
    else:db.indexes[name][1]['key']={'account_id':1,'user_id':1}
    data=(await request(db)).json()
    assert not data['namespace_prerequisites_verified'] and data['namespaces']['standing_policy']['blocked_codes']
    assert 'private_validator_marker' not in json.dumps(data)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['malformed','duplicate','pending','incomplete'])
async def test_bounded_legacy_inventory_never_claims_binding_or_returns_values(mode):
    db=DB();row={'account':'123','schema':1,'revision':1,'enabled':False,'claim_type':'missing','private_type':'missing','private_count':0}
    if mode=='malformed':row['account']='private_account_marker'
    elif mode=='pending':row['claim_type']='object'
    db.rows[audit.NAMES['standing_policy']]=[row,row] if mode=='duplicate' else [row]
    db.open_cursor=mode=='incomplete'
    data=(await request(db)).json()
    assert not data['namespace_prerequisites_verified'] and not data['legacy_bindings_verified']
    assert 'private_account_marker' not in json.dumps(data) and '"123"' not in json.dumps(data)
    if mode=='incomplete':assert db.killed==[42,42,42]


@pytest.mark.asyncio
async def test_driver_error_sanitized_deadline_keeps_future_and_resource_slot(monkeypatch):
    db=DB();future=asyncio.get_running_loop().create_future();original=db.command
    async def command(value,**kw):
        if 'connectionStatus' in value:return await future
        return await original(value,**kw)
    db.command=command;monkeypatch.setattr(audit,'TIMEOUT_SECONDS',.02)
    response=await request(db);state=audit._active
    assert response.status_code==503 and not future.cancelled() and state and not state['task'].done()
    try:
        second=await request(db);assert second.json()['detail']['code']=='google_namespace_audit_busy'
    finally:
        future.set_exception(ConnectionError('private_host_marker'))
        await asyncio.gather(state['task'],return_exceptions=True);await asyncio.sleep(0)
    assert audit._active is None and 'private_host_marker' not in response.text


@pytest.mark.asyncio
async def test_uncertain_own_cursor_cleanup_blocks_further_probe_resources(monkeypatch):
    db=DB();db.open_cursor=True;original=db.command
    async def command(value,**kw):
        if 'killCursors' in value:raise ConnectionError('private_cleanup_marker')
        return await original(value,**kw)
    db.command=command
    try:
        response=await request(db)
        assert response.status_code==503
        assert response.json()['detail']['code']=='google_namespace_audit_cursor_cleanup_unconfirmed'
        assert db.calls.count('aggregate')==1 and db.sessions[0].ended
        assert audit._active['resource_unconfirmed'] and audit._active['task'].done()
        before=len(db.calls)
        second=await request(db)
        assert second.json()['detail']['code']=='google_namespace_audit_busy' and len(db.calls)==before
        assert 'private_cleanup_marker' not in response.text
    finally:
        monkeypatch.setattr(audit,'_active',None)
