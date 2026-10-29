"""Disposable CI replica proof. Never reads application environment or DBs."""
import os
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from integrations_control_center import google_ads_budget_storage_readiness as readiness


@pytest.mark.asyncio
async def test_actual_replica_probe_aborts_only_own_write_preserving_existing_data():
    uri = os.environ.get('GOOGLE_ADS_TEST_MONGO_URI')
    assert uri and uri.startswith('mongodb://127.0.0.1:'), 'explicit disposable loopback test Mongo required'
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000, retryWrites=False)
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
        assert await db[readiness.PROBE_COLLECTION].find({}).to_list(None) == [{'_id':'foreign-existing','keep':True}]
        assert await db['google_ads_automation_policies'].find_one({'_id':'owner-policy'}) == {'_id':'owner-policy','enabled':True,'revision':7}
    finally:
        await client.drop_database(name)
        client.close()
