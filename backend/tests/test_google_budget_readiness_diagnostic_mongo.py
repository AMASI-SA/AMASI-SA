"""Only against an explicitly disposable loopback replica test database."""
import uuid
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center.google_budget_readiness_diagnostic import COLLECTIONS, diagnose
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri

@pytest_asyncio.fixture
async def disposable(automation_mongo_uri):
    client = AsyncIOMotorClient(automation_mongo_uri, serverSelectionTimeoutMS=1000)
    name = 'g_diagnostic_' + uuid.uuid4().hex
    try:
        assert (await client.admin.command('hello')).get('setName')
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()

@pytest.mark.asyncio
async def test_snapshot_sees_private_claim_fence_and_leaves_documents_unchanged(disposable):
    db = disposable
    scope = {'user_id': 'fixture-owner', 'account_id': '123'}
    standing = db[COLLECTIONS['standing'][0]]
    fences = db[COLLECTIONS['fences'][0]]
    await standing.insert_one({**scope, '_dispatch_claim': {'execution_claim_id': 'claim'}})
    await fences.insert_one({**scope, 'status': 'claimed', 'execution_claim_id': 'claim'})
    before = [await standing.count_documents({}), await fences.count_documents({})]
    result = await diagnose(db)
    assert result['inventory_exhausted'] and not result['complete'] and result['counts']['private_claims_without_reserved_record'] == 1
    assert result['counts']['claimed_fences'] == 1 and not result['database_identity_verified']
    assert [await standing.count_documents({}), await fences.count_documents({})] == before
    assert not await db[COLLECTIONS['guards'][0]].count_documents({})

@pytest.mark.asyncio
async def test_snapshot_reports_incomplete_when_real_cursor_has_extra_row(disposable):
    col = disposable[COLLECTIONS['standing'][0]]
    await col.insert_many([{'user_id': 'a', 'account_id': '1'}, {'user_id': 'b', 'account_id': '2'}])
    result = await diagnose(disposable, max_rows=1)
    assert result['reason'] == 'row_limit' and not result['complete'] and 'counts' not in result
