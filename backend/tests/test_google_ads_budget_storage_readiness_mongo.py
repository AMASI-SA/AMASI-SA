"""Disposable CI replica proof. Never reads application environment or DBs."""
import os
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
import pytest
from fastapi import HTTPException
from pymongo.monitoring import CommandListener

from integrations_control_center import google_ads_budget_storage_readiness as readiness


class Capture(CommandListener):
    def __init__(self): self.commands = []
    def started(self, event):
        if event.command_name in ('insert', 'abortTransaction'):
            self.commands.append(dict(event.command))
    def succeeded(self, event): pass
    def failed(self, event): pass


@pytest.mark.asyncio
async def test_actual_replica_probe_aborts_only_own_write_preserving_existing_data():
    uri = os.environ.get('GOOGLE_ADS_TEST_MONGO_URI')
    assert uri and uri.startswith('mongodb://127.0.0.1:'), 'explicit disposable loopback test Mongo required'
    capture = Capture()
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000, retryWrites=False, event_listeners=[capture])
    name = 'google_storage_readiness_test_' + uuid.uuid4().hex
    db = client[name]
    try:
        hello = await db.command('hello')
        assert hello.get('setName') and hello.get('isWritablePrimary')
        await db[readiness.PROBE_COLLECTION].insert_one({'_id':'foreign-existing', 'keep':True})
        await db['google_ads_automation_policies'].insert_one({'_id':'owner-policy', 'enabled':True, 'revision':7})
        result = await readiness.verify_budget_storage(db)
        assert result['storage_verified'] is True and result['snapshot_majority_write_aborted'] is True
        assert result['execution_authorized'] is False and result['funding_ready'] is False
        abort = next(command for command in capture.commands if 'abortTransaction' in command)
        insert = next(command for command in capture.commands if command.get('startTransaction') is True)
        assert abort['lsid'] == insert['lsid'] and abort['txnNumber'] == insert['txnNumber']
        assert abort['autocommit'] is False and abort['$db'] == 'admin'
        assert abort['writeConcern']['w'] == 'majority'
        assert insert['readConcern']['level'] == 'snapshot'

        assert await db[readiness.PROBE_COLLECTION].find({}).to_list(None) == [{'_id':'foreign-existing','keep':True}]
        assert await db['google_ads_automation_policies'].find_one({'_id':'owner-policy'}) == {'_id':'owner-policy','enabled':True,'revision':7}
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [{'errorCode':91}, {'closeConnection':True}])
async def test_actual_abort_command_error_or_unknown_response_never_returns_success(fault):
    uri = os.environ.get('GOOGLE_ADS_TEST_MONGO_URI')
    assert uri and uri.startswith('mongodb://127.0.0.1:')
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000, retryWrites=False)
    name = 'google_storage_readiness_test_' + uuid.uuid4().hex
    db = client[name]
    try:
        await db[readiness.PROBE_COLLECTION].insert_one({'_id':'foreign-existing'})
        await client.admin.command({'configureFailPoint':'failCommand', 'mode':{'times':1},
            'data':{'failCommands':['abortTransaction'], **fault}})
        with pytest.raises(HTTPException) as error:
            await readiness.verify_budget_storage(db)
        assert error.value.status_code == 503
        assert error.value.detail['code'] == 'google_budget_storage_unverified'
        # Later successful driver cleanup cannot upgrade the failed diagnostic.
        assert await db[readiness.PROBE_COLLECTION].find({}).to_list(None) == [{'_id':'foreign-existing'}]
    finally:
        await client.admin.command({'configureFailPoint':'failCommand', 'mode':'off'})
        await client.drop_database(name)
        client.close()
