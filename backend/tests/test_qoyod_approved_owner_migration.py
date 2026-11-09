import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod.approved_owner_migration import apply_approved_recovery

OWNER = '5aee091a-cc47-42cd-b14c-a14e32f169cc'
SETTINGS = ObjectId('6a3e951b5f40fa3bcd76f6d7')


async def fixture(name='salla-analytics-test_database'):
    db = AsyncMongoMockClient()[name]
    await db.users.insert_one({'id': OWNER, 'role': 'owner'})
    await db.qoyod_settings.insert_one({
        '_id': SETTINGS, 'user_id': 'main',
        'plan_b_auto_send_orders_user_id': OWNER,
        'enabled': True, 'api_key': 'unchanged-test-value',
    })
    return db


@pytest.mark.asyncio
async def test_repairs_only_approved_record_and_is_idempotent():
    from fastapi import HTTPException
    from security_sensitive_routes import require_qoyod_security_owner
    db = await fixture()
    before = await db.qoyod_settings.find_one({})
    with pytest.raises(HTTPException):
        await require_qoyod_security_owner(db, {'id': OWNER})
    assert await apply_approved_recovery(db) == 'bound'
    assert (await require_qoyod_security_owner(db, {'id': OWNER}))['id'] == OWNER
    await db.users.insert_one({'id': 'other-owner', 'role': 'owner'})
    with pytest.raises(HTTPException):
        await require_qoyod_security_owner(db, {'id': 'other-owner'})
    await db.users.insert_one({'id': 'employee', 'role': 'employee'})
    with pytest.raises(HTTPException):
        await require_qoyod_security_owner(db, {'id': OWNER, '_mobile_actor_id': 'employee'})
    after = await db.qoyod_settings.find_one({})
    assert after.pop('security_owner_id') == OWNER
    audit = after.pop('security_owner_binding_audit')
    assert len(audit) == 1
    assert audit[0]['previous_owner_id'] is None
    assert after == before
    assert await apply_approved_recovery(db) == 'already_bound'
    assert len((await db.qoyod_settings.find_one({}))['security_owner_binding_audit']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('patch', [
    {'security_owner_id': 'other-owner'}, {'security_owner_id': None},
    {'security_owner_id': ''}, {'plan_b_auto_send_orders_user_id': 'other-owner'},
    {'security_owner_binding_audit': [{'migration_id': 'QOYOD-OWNER-RECOVERY-20261109'}]},
])
async def test_drift_or_revocation_is_not_overwritten(patch):
    db = await fixture()
    await db.qoyod_settings.update_one({}, {'$set': patch})
    before = await db.qoyod_settings.find_one({})
    assert await apply_approved_recovery(db) != 'bound'
    assert await db.qoyod_settings.find_one({}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('patch', [
    {'role': 'admin'}, {'role': 'employee'}, {'is_active': False}, {'disabled': True},
])
async def test_invalid_owner_is_not_bound(patch):
    db = await fixture()
    await db.users.update_one({}, {'$set': patch})
    assert await apply_approved_recovery(db) == 'owner_unavailable'
    assert 'security_owner_id' not in await db.qoyod_settings.find_one({})


@pytest.mark.asyncio
async def test_wrong_database_or_document_is_not_changed():
    db = await fixture('preview')
    assert await apply_approved_recovery(db) == 'not_target_database'
    assert 'security_owner_id' not in await db.qoyod_settings.find_one({})
    db = await fixture()
    await db.qoyod_settings.delete_one({})
    await db.qoyod_settings.insert_one({'user_id': 'main'})
    assert await apply_approved_recovery(db) == 'target_unavailable'
    assert await db.qoyod_settings.count_documents({}) == 1


@pytest.mark.asyncio
async def test_concurrent_replica_or_binding_change_cannot_overwrite():
    db = await fixture()
    results = await asyncio.gather(*[apply_approved_recovery(db) for _ in range(5)])
    assert results.count('bound') == 1
    assert len((await db.qoyod_settings.find_one({}))['security_owner_binding_audit']) == 1
    db = await fixture()
    collection = db.qoyod_settings
    async def race(query, update):
        await collection.update_one({}, {'$set': {'security_owner_id': 'concurrent-owner'}})
        return await collection.update_one(query, update)
    wrapped = SimpleNamespace(name=db.name, users=db.users, qoyod_settings=SimpleNamespace(
        find_one=collection.find_one, update_one=race))
    assert await apply_approved_recovery(wrapped) == 'precondition_changed'
    assert (await collection.find_one({}))['security_owner_id'] == 'concurrent-owner'
