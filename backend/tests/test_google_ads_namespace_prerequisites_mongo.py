"""Disposable auth-enabled replica; all role setup is TEST ONLY."""
import asyncio
import os
from urllib.parse import urlsplit
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
import pytest

from integrations_control_center import google_ads_namespace_prerequisites as audit


class ReadCommands(CommandListener):
    def __init__(self):self.names=[]
    def started(self,event):self.names.append(event.command_name)
    def succeeded(self,event):pass
    def failed(self,event):pass


async def setup_case(mode='full',change=None):
    uri=os.environ.get('GOOGLE_NAMESPACE_TEST_ADMIN_URI');assert uri and urlsplit(uri).hostname=='127.0.0.1'
    root=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=2000)
    db=root['g_namespace_'+uuid.uuid4().hex]
    for label,name in audit.NAMES.items():
        if change=='absent' and label=='standing_policy':continue
        options={'validator':{'$jsonSchema':{'bsonType':'object'}},'validationLevel':'strict'} if change=='validator' and label=='standing_policy' else {}
        await db.create_collection(name,**options)
        if label in ('standing_policy','creation_policy') and change not in ('no_index','duplicates'):
            await db[name].create_index([('user_id',1),('account_id',1)],unique=True)
    await db.users.insert_one({'id':'owner','role':'owner'})
    if change in ('duplicates','pending'):
        document={'user_id':'owner','account_id':'123','schema_version':1,'revision':1,'enabled':False}
        if change=='pending':document['_dispatch_claim']={}
        await db[audit.NAMES['standing_policy']].insert_one(dict(document))
        if change=='duplicates':await db[audit.NAMES['standing_policy']].insert_one(dict(document))
    if change=='incomplete':
        await db[audit.NAMES['standing_policy']].insert_many([{'user_id':'owner','account_id':str(i+1),
            'schema_version':1,'revision':1,'enabled':False} for i in range(1001)])
    privileges=[{'resource':{'db':db.name,'collection':''},'actions':['find','listCollections','listIndexes']}]
    if mode!='read':
        for label,name in audit.NAMES.items():
            actions=['update'] if mode=='update' else ['insert'] if label=='engine_probe' else ['find','insert','update','createIndex']
            privileges.append({'resource':{'db':db.name,'collection':name},'actions':actions})
    await db.command({'createRole':'case_access','privileges':privileges,'roles':[]})
    await db.command({'createUser':'case_principal','pwd':'ephemeral_fixture_only','roles':[{'role':'case_access','db':db.name}]})
    capture=ReadCommands()
    client=AsyncIOMotorClient('mongodb://127.0.0.1:'+str(urlsplit(uri).port)+'/',username='case_principal',
        password='ephemeral_fixture_only',authSource=db.name,serverSelectionTimeoutMS=2000,event_listeners=[capture])
    return root,db,client,client[db.name],capture


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['read','update','full'])
async def test_actual_restricted_privileges_cannot_invent_insert_or_index_grants(mode):
    root,raw,client,db,capture=await setup_case(mode)
    try:
        data=await audit.audit_namespace_prerequisites(db,{'id':'owner'})
        assert data['namespace_prerequisites_verified'] is (mode=='full')
        assert data['migration_ready'] is data['engine_commit_verified'] is data['execution_authorized'] is False
        assert data['business_writes']==data['provider_writes']==0
        assert data['namespaces']['standing_policy']['actions_verified']['insert'] is (mode=='full')
        assert data['namespaces']['standing_policy']['create_index_for_put_verified'] is (mode=='full')
        assert not {'insert','update','delete','createIndexes','commitTransaction'}.intersection(capture.names)
        import json
        text=json.dumps(data)
        assert db.name not in text and 'case_principal' not in text and 'case_access' not in text and '127.0.0.1' not in text
    finally:client.close();await root.drop_database(raw.name);root.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['validator','absent','no_index','duplicates','pending','incomplete'])
async def test_real_metadata_and_legacy_counter_gaps_stay_unproved_without_writes(change):
    root,raw,client,db,capture=await setup_case(change=change)
    try:
        before=await raw[audit.NAMES['standing_policy']].count_documents({})
        data=await audit.audit_namespace_prerequisites(db,{'id':'owner'})
        assert data['namespace_prerequisites_verified'] is False
        row=data['namespaces']['standing_policy']
        assert row['blocked_codes']
        if change=='duplicates':assert row['duplicate_scope_count']==1
        if change=='pending':assert row['legacy_evidence_count']==1
        if change=='incomplete':assert row['inventory_complete'] is False and row['records_checked']==1001
        assert await raw[audit.NAMES['standing_policy']].count_documents({})==before
        assert not {'insert','update','delete','createIndexes'}.intersection(capture.names)
    finally:client.close();await root.drop_database(raw.name);root.close()
