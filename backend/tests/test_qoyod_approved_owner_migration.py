import asyncio
import ast
import logging
from pathlib import Path
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
async def test_startup_recovery_persists_outcome_without_secrets():
    from integrations.qoyod.approved_owner_migration import run_startup_recovery
    db = await fixture()
    assert await run_startup_recovery(db) == 'bound'
    result = await db.qoyod_owner_recovery_state.find_one({})
    assert result['state'] == 'bound'
    assert set(result) == {'_id', 'state', 'at'}
    assert 'unchanged-test-value' not in str(result)


@pytest.mark.asyncio
async def test_startup_timeout_is_recorded_without_retry_or_binding(monkeypatch):
    from integrations.qoyod import approved_owner_migration as recovery
    db = await fixture()
    calls = []
    async def timed_out(_db):
        calls.append(1)
        raise asyncio.TimeoutError()
    monkeypatch.setattr(recovery, 'apply_approved_recovery', timed_out)
    assert await recovery.run_startup_recovery(db) == 'timeout'
    assert calls == [1]
    assert (await db.qoyod_owner_recovery_state.find_one({}))['state'] == 'timeout'
    assert 'security_owner_id' not in await db.qoyod_settings.find_one({})


@pytest.mark.asyncio
async def test_non_target_startup_does_not_create_diagnostics():
    from integrations.qoyod.approved_owner_migration import run_startup_recovery
    db = await fixture('preview')
    assert await run_startup_recovery(db) == 'not_target_database'
    assert await db.qoyod_owner_recovery_state.count_documents({}) == 0


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


@pytest.mark.asyncio
async def test_server_startup_wiring_reaches_approved_recovery(monkeypatch):
    """Execute the full startup function, preserving Python's local scope.

    Isolate unrelated index/worker startup, but preserve the import bindings
    and exception handler that can otherwise silently skip this repair.
    """
    source = ast.parse((Path(__file__).parents[1] / 'server.py').read_text())
    imports = [node for node in source.body if isinstance(node, ast.Import)
               and any(alias.name == 'asyncio' for alias in node.names)]
    startup = next(node for node in source.body
                   if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == '_global_startup')
    # Slicing out the hook hides later local imports: Python binds locals for
    # the entire function, even before the import executes.
    module = ast.fix_missing_locations(ast.Module(body=[*imports, startup], type_ignores=[]))
    import review_acceptance_config_guard as acceptance
    class StopAfterRecovery(Exception):
        pass
    async def stop_after_recovery(_db):
        raise StopAfterRecovery
    monkeypatch.setattr(acceptance, 'ensure_acceptance_config_storage', stop_after_recovery)
    db = acceptance.AcceptanceConfigDatabase(await fixture())
    namespace = {
        'db': db, 'logger': logging.getLogger('test.startup_recovery'),
        'app': SimpleNamespace(state=SimpleNamespace()),
        'memory_snapshot': lambda: SimpleNamespace(current_bytes=0, max_bytes=1, process_rss_bytes=0),
    }
    exec(compile(module, 'server_startup_recovery', 'exec'), namespace)
    with pytest.raises(StopAfterRecovery):
        await namespace['_global_startup']()
    from security_sensitive_routes import require_qoyod_security_owner
    assert (await require_qoyod_security_owner(db, {'id': OWNER}))['id'] == OWNER
    repaired = await db.qoyod_settings.find_one({'user_id': 'main'})
    assert len(repaired['security_owner_binding_audit']) == 1
