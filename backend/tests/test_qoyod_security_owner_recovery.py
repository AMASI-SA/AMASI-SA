"""Operator recovery preserves the HTTP boundary and all financial settings."""
import importlib.util
from pathlib import Path
import pytest
from mongomock_motor import AsyncMongoMockClient
from security_sensitive_routes import require_qoyod_security_owner
from fastapi import HTTPException

spec = importlib.util.spec_from_file_location('binding', Path(__file__).resolve().parents[2] / 'scripts/qoyod_security_owner_binding.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
recover = module.recover_binding

async def fixture(binding=None):
    db = AsyncMongoMockClient().offline
    await db.users.insert_one({'id': 'approved', 'role': 'owner', 'is_active': True})
    await db.qoyod_settings.insert_one({'user_id': 'main', 'enabled': True,
        'api_key': 'fixture-secret', **({'security_owner_id': binding} if binding else {})})
    return db

@pytest.mark.asyncio
async def test_missing_dry_run_then_explicit_atomic_repair_unlocks_existing_guard():
    db = await fixture()
    before = await db.qoyod_settings.find_one({})
    with pytest.raises(HTTPException):
        await require_qoyod_security_owner(db, {'id': 'approved'})
    assert (await recover(db, owner_id='approved', expected_owner=None))['state'] == 'missing'
    assert await db.qoyod_settings.find_one({}) == before
    assert (await recover(db, owner_id='approved', expected_owner=None, apply=True,
                          approval='owner-approved-test'))['changed']
    assert (await require_qoyod_security_owner(db, {'id': 'approved'}))['id'] == 'approved'
    after = await db.qoyod_settings.find_one({})
    assert after['api_key'] == before['api_key'] and after['enabled'] is True
    assert len(after['security_owner_binding_audit']) == 1
    assert not (await recover(db, owner_id='approved', expected_owner=None, apply=True))['changed']
    assert len((await db.qoyod_settings.find_one({}))['security_owner_binding_audit']) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize('role,active,disabled', [('employee',True,False), ('admin',True,False), ('owner',False,False), ('owner',True,True)])
async def test_invalid_actors_never_bound(role, active, disabled):
    db = await fixture()
    await db.users.update_one({'id':'approved'}, {'$set': {'role':role,'is_active':active,'disabled':disabled}})
    with pytest.raises(ValueError, match='active_owner_required'):
        await recover(db, owner_id='approved', expected_owner=None, apply=True, approval='test')
    assert 'security_owner_id' not in await db.qoyod_settings.find_one({})

@pytest.mark.asyncio
async def test_existing_different_owner_requires_exact_expected_binding_and_approval():
    db = await fixture('other')
    with pytest.raises(ValueError, match='mismatch'):
        await recover(db, owner_id='approved', expected_owner=None, apply=True, approval='test')
    with pytest.raises(ValueError, match='approval_reference'):
        await recover(db, owner_id='approved', expected_owner='other', apply=True)
    assert (await db.qoyod_settings.find_one({}))['security_owner_id'] == 'other'
    assert (await recover(db, owner_id='approved', expected_owner='other', apply=True, approval='explicit-transfer'))['changed']

@pytest.mark.asyncio
async def test_settings_missing_never_created():
    db = await fixture()
    await db.qoyod_settings.delete_many({})
    with pytest.raises(ValueError, match='existing_qoyod_settings'):
        await recover(db, owner_id='approved', expected_owner=None, apply=True, approval='test')
    assert await db.qoyod_settings.count_documents({}) == 0

@pytest.mark.asyncio
@pytest.mark.parametrize('has_snapshot', [False, True])
async def test_reconciliation_distinguishes_missing_snapshot_from_verified_empty(has_snapshot):
    from test_inherited_security_qoyod import request, OWNER
    db = AsyncMongoMockClient().offline
    await db.users.insert_one(OWNER)
    await db.qoyod_settings.insert_one({'user_id': 'main', 'security_owner_id': OWNER['id']})
    if has_snapshot:
        await db.qoyod_full_scans.insert_one({'_id': 'main', 'last_success': {
            'orders_user_id': OWNER['id'], 'updated_at': '2026-11-08T00:00:00Z',
            'report': {'ok': True, 'rows': [], 'counts': {}}}})
    response = await request(db, OWNER, 'GET', '/reconciliation-report')
    assert response.status_code == 200
    assert response.json()['snapshot_available'] is has_snapshot

@pytest.mark.asyncio
async def test_concurrent_binding_change_is_not_overwritten():
    from types import SimpleNamespace
    db = await fixture()
    settings = db.qoyod_settings
    async def race(query, update):
        await settings.update_one({'user_id': 'main'}, {'$set': {'security_owner_id': 'concurrent-owner'}})
        return await settings.update_one(query, update)
    wrapper = SimpleNamespace(users=db.users, qoyod_settings=SimpleNamespace(
        find_one=settings.find_one, update_one=race))
    with pytest.raises(ValueError, match='binding_changed_during_repair'):
        await recover(wrapper, owner_id='approved', expected_owner=None, apply=True, approval='test')
    saved = await settings.find_one({})
    assert saved['security_owner_id'] == 'concurrent-owner'
    assert 'security_owner_binding_audit' not in saved
