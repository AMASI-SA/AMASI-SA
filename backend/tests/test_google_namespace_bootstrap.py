"""Typed local boundary tests; persistence guarantees are real-Mongo tests."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from bson import Binary
from bson.int64 import Int64
from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

from integrations_control_center import google_namespace_bootstrap as bootstrap


def metadata(name):
    return {'name': name, 'type': 'collection', 'options': {},
        'info': {'readOnly': False, 'uuid': Binary(b'1' * 16, 4)},
        'idIndex': {'v': 2, 'key': {'_id': 1}, 'name': '_id_'}}


def prepared(user=None):
    user = user or {'id': 'owner', 'role': 'owner'}
    stamp = bootstrap._now()
    observation = {'manifest_digest': bootstrap.MANIFEST_DIGEST,
        'catalog': {**{name: None for name in bootstrap.TARGETS.values()}, bootstrap.SLOTS: {'observed': True}},
        'topology': {'setName': 'fixture'}}
    return {'_id': bootstrap.SLOT_ID, 'schema_version': 1, 'kind': 'fixed_namespace_bootstrap',
        'state': 'prepared', 'owner_id': user['id'], 'auth_digest': bootstrap._auth_digest(user),
        'request_nonce': 'a' * 32, 'server_nonce': 'b' * 32, 'manifest_digest': bootstrap.MANIFEST_DIGEST,
        'observation': observation, 'observation_digest': bootstrap._digest(observation),
        'prepared_at': stamp, 'expires_at': stamp + timedelta(seconds=bootstrap.PREPARATION_SECONDS),
        'attempt_id': None, 'verified_at': None, 'verified_catalog_digest': None}


def test_bson_binding_preserves_missing_null_bool_and_numeric_storage_types():
    values = [{}, {'v': None}, {'v': True}, {'v': 1}, {'v': Int64(1)}, {'v': 1.0}]
    assert len({bootstrap._bytes(value) for value in values}) == len(values)
    assert bootstrap._bytes({'b': 2, 'a': 1}) == bootstrap._bytes({'a': 1, 'b': 2})


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(type='view'), lambda r: r.update(options={'validator': {}}),
    lambda r: r.update(options={'collation': {'locale': 'en'}}),
    lambda r: r.update(options={'capped': False}), lambda r: r.update(unknown=True),
    lambda r: r['info'].update(readOnly=True), lambda r: r['info'].update(uuid='not-a-native-uuid'),
    lambda r: r['info'].update(uuid=Binary(b'1' * 16, 0)), lambda r: r['info'].update(uuid=Binary(b'1', 4)),
    lambda r: r.update(idIndex={'v': 2, 'key': {'_id': True}, 'name': '_id_'}),
])
def test_custom_or_ambiguous_collection_never_becomes_default_empty_contract(mutation):
    name = bootstrap.TARGETS['account_guard']; row = metadata(name)
    mutation(row)
    with pytest.raises(HTTPException) as error: bootstrap._metadata(row, name, 'fixture_db')
    assert error.value.status_code == 503


@pytest.mark.parametrize('change', [
    {'unique': False}, {'sparse': True}, {'partialFilterExpression': {}}, {'hidden': False},
    {'expireAfterSeconds': 60}, {'buildUUID': 'in-progress'}, {'background': True},
    {'key': {'account_id': 1, 'user_id': 1}}, {'name': 'unknown_legacy_name'}, {'v': True},
])
def test_only_exact_completed_policy_index_is_eligible(change):
    name = bootstrap.TARGETS['standing_policy']
    row = {'v': 2, 'key': {'user_id': 1, 'account_id': 1}, 'unique': True,
           'name': 'google_automation_tenant_account', **change}
    with pytest.raises(HTTPException): bootstrap._index(row, name, 'fixture_db')


@pytest.mark.parametrize('user', [None, {}, {'id': 1, 'role': 'owner'}, {'id': 'staff', 'role': 'admin'},
    {'id': 'owner', 'role': 'owner', 'disabled': True}, {'id': 'owner', 'role': 'owner', 'is_active': False},
    {'id': 'owner', 'role': 'owner', 'deleted_at': 'yesterday'},
    {'id': 'owner', 'role': 'owner', 'disabled': 1}, {'id': 'owner', 'role': 'owner', '_review_session': {}},
    {'id': 'owner', 'role': 'owner', '_session_client': {}}])
def test_owner_and_signed_private_context_refused_when_ambiguous(user):
    with pytest.raises(HTTPException) as error: bootstrap._auth(user)
    assert error.value.status_code == 403


def test_prepared_token_requires_same_auth_context_and_is_not_authority():
    user = {'id': 'owner', 'role': 'owner', 'password_updated_at': datetime(2026, 10, 1, tzinfo=timezone.utc)}
    row = prepared(user); public = bootstrap._public_receipt(row, user)
    assert public['status'] == 'prepared' and len(public['execution_token']) == 64
    for key in ('execution_permitted', 'execution_authorized', 'funding_ready', 'namespace_contract_verified',
                'atomic_catalog_snapshot_verified', 'new_uuid_lineage_verified'):
        assert public[key] is False
    assert public['business_document_writes'] == public['provider_writes'] == 0
    for altered in ({**user, 'id': 'another_owner'}, {**user, '_session_client': 'other-session'},
                    {**user, 'password_updated_at': None}):
        denied = bootstrap._public_receipt(row, altered)
        assert denied['status'] == 'reservation_owned_by_another_auth_context' and 'execution_token' not in denied


@pytest.mark.parametrize('state', ['consumed', 'applied', 'verified'])
def test_consumed_or_completed_receipts_never_expose_an_executable_token(state):
    row = prepared(); row.update(state=state, attempt_id='c' * 32)
    if state == 'verified': row.update(verified_at=bootstrap._now(), verified_catalog_digest='d' * 64)
    bootstrap._valid_receipt(row)
    result = bootstrap._public_receipt(row, {'id': 'owner', 'role': 'owner'})
    assert 'execution_token' not in result and result['execution_permitted'] is False
    assert result['namespace_contract_verified'] is False


def test_expiry_retains_preparation_without_regenerating_token_or_operation():
    row = prepared(); row['prepared_at'] -= timedelta(hours=1); row['expires_at'] -= timedelta(hours=1)
    before = deepcopy(row)
    assert bootstrap._public_receipt(row, {'id': 'owner', 'role': 'owner'})['status'] == 'preparation_expired_retained'
    assert row == before


@pytest.mark.asyncio
@pytest.mark.parametrize('path,body', [
    ('prepare', {'request_nonce': 'a' * 32, 'acknowledge_preparation_only': 1}),
    ('prepare', {'request_nonce': 'a' * 32, 'acknowledge_preparation_only': True, 'collection': 'arbitrary'}),
    ('prepare', {'request_nonce': 'notcanonical', 'acknowledge_preparation_only': True}),
    ('execute', {'request_nonce': 'a' * 32, 'execution_token': 'b' * 64, 'observation_digest': 'c' * 64,
        'manifest_digest': 'd' * 64, 'acknowledge_storage_only': True, 'enabled': True}),
])
async def test_public_requests_cannot_smuggle_names_or_financial_flags(path, body):
    app, router = FastAPI(), APIRouter()
    async def current(): return {'id': 'owner', 'role': 'owner'}
    bootstrap.attach_google_namespace_bootstrap_routes(router, object(), current)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post('/google_ads/automation/namespace-bootstrap/' + path, json=body)
    assert response.status_code == 422  # object() would fail any DB access.


@pytest.mark.asyncio
async def test_deadline_retains_original_task_and_blocks_overlap_until_it_finishes(monkeypatch):
    monkeypatch.setattr(bootstrap, 'TIMEOUT_SECONDS', .02)
    bootstrap._active = None
    release = asyncio.Event()
    async def work(db, user, state):
        await release.wait()
        assert state['stop'] is True
        return bootstrap._result('late')
    try:
        with pytest.raises(HTTPException) as error: await bootstrap._run(None, {}, work)
        assert error.value.detail['code'].endswith('deadline')
        original = bootstrap._active['task']
        assert not original.done()
        with pytest.raises(HTTPException) as busy: await bootstrap._run(None, {}, work)
        assert busy.value.detail['code'].endswith('busy')
        release.set(); await original; await asyncio.sleep(0)
        assert bootstrap._active is None
    finally:
        release.set()
        if bootstrap._active: await bootstrap._active['task']
        bootstrap._active = None


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_discard_original_operation_ownership():
    bootstrap._active = None
    started, release = asyncio.Event(), asyncio.Event()
    async def work(db, user, state):
        started.set(); await release.wait()
        assert state['stop'] is True
        return bootstrap._result('late')
    outer = asyncio.create_task(bootstrap._run(None, {}, work))
    try:
        await started.wait(); original = bootstrap._active['task']; outer.cancel()
        with pytest.raises(asyncio.CancelledError): await outer
        assert not original.done() and not original.cancelled()
        with pytest.raises(HTTPException) as busy: await bootstrap._run(None, {}, work)
        assert busy.value.detail['code'].endswith('busy')
        release.set(); await original; await asyncio.sleep(0)
        assert bootstrap._active is None
    finally:
        release.set()
        if bootstrap._active: await bootstrap._active['task']
        bootstrap._active = None


@pytest.mark.asyncio
async def test_unconfirmed_session_cleanup_remains_process_blocker_even_after_work_returns(monkeypatch):
    monkeypatch.setattr(bootstrap, 'CLEANUP_SECONDS', .01)
    bootstrap._active = None
    release, closed = asyncio.Event(), asyncio.Event()
    class Session:
        async def end_session(self):
            await release.wait(); closed.set()
    async def work(db, user, state):
        await bootstrap._end(Session(), state)
    try:
        with pytest.raises(HTTPException) as error: await bootstrap._run(None, {}, work)
        assert error.value.detail['code'].endswith('session_cleanup_unconfirmed')
        assert bootstrap._active['task'].done() and bootstrap._active['resource_unconfirmed'] is True
        with pytest.raises(HTTPException) as busy: await bootstrap._run(None, {}, work)
        assert busy.value.detail['code'].endswith('busy')
        release.set(); await closed.wait()
        assert bootstrap._active['resource_unconfirmed'] is True
    finally:
        release.set(); await closed.wait(); bootstrap._active = None


@pytest.mark.asyncio
async def test_motor_future_session_cleanup_is_supported():
    class Session:
        def end_session(self):
            future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            return future
    state = {}
    await bootstrap._end(Session(), state)
    assert 'resource_unconfirmed' not in state


@pytest.mark.asyncio
async def test_cleanup_has_fresh_bounded_mongo_deadline_after_body_expired():
    from pymongo import _csot, timeout
    remaining = []
    class Session:
        async def end_session(self): remaining.append(_csot.remaining())
    with timeout(.001):
        await asyncio.sleep(.01)
        await bootstrap._end(Session(), {})
    assert len(remaining) == 1 and 0 < remaining[0] <= bootstrap.CLEANUP_SECONDS
