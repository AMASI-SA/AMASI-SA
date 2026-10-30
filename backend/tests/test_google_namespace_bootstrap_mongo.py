"""Actual authenticated owner HTTP -> bootstrap -> disposable Mongo behavior."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import importlib.util
from pathlib import Path
import uuid

from fastapi import APIRouter, FastAPI, HTTPException, Request
import httpx
import pytest

import auth
from integrations_control_center import google_namespace_bootstrap as bootstrap
from integrations_control_center import google_ads_storage_commit_readiness as diagnostic
from tests.test_google_namespace_bootstrap_capabilities_mongo import fixture, NAMES, INDEX_NAMES, RECEIPTS, target_metadata


PREFIX = '/google_ads/automation/namespace-bootstrap'
NONCE = 'a' * 32
SLOT = bootstrap.SLOT_ID


class Admin:
    def __init__(self, database): self.database = database
    async def command(self, value, **kwargs):
        db = self.database
        if db.before: await db.before(value)
        result = await db.raw.client.admin.command(value, **kwargs)
        if db.after: await db.after(value)
        return result


class Client:
    def __init__(self, database): self.database, self.admin = database, Admin(database)
    async def start_session(self): return await self.database.raw.client.start_session()


class ObservedDB:
    def __init__(self, raw):
        self.raw, self.name, self.client = raw, raw.name, Client(self)
        self.before = self.after = None
    def __getattr__(self, name): return getattr(self.raw, name)
    async def command(self, value, **kwargs):
        if self.before: await self.before(value)
        result = await self.raw.command(value, **kwargs)
        if self.after: await self.after(value)
        return result


def mounted(db, module=bootstrap):
    app, router = FastAPI(), APIRouter()
    async def current(request: Request): return await auth.get_current_user_from_db(request, db)
    module.attach_google_namespace_bootstrap_routes(router, db, current)
    diagnostic.attach_google_ads_storage_commit_readiness_routes(router, db, current)
    app.include_router(router)
    return app


@asynccontextmanager
async def owner_case(monkeypatch):
    monkeypatch.setattr(auth, 'get_jwt_secret', lambda: 'ephemeral_fixture_only')
    monkeypatch.setattr(auth.jwt, 'decode', lambda token, *a, **k: {
        'sub': token, 'type': 'access', 'mfa': True, 'iat': 2000000000})
    bootstrap._active = None
    diagnostic._active = None
    async with fixture(with_owner=True) as (_, raw, _, db, capture, foreign):
        observed = ObservedDB(db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(mounted(observed)),
            base_url='http://test', headers={'Authorization': 'Bearer owner'}) as api:
            yield raw, observed, api, capture, foreign
        active = bootstrap._active
        assert active is None or active['task'].done(), 'a real retained task must finish before fixture teardown'
    bootstrap._active = None


async def prepare(api):
    response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
    assert response.status_code == 200, response.text
    data = response.json(); assert data['status'] == 'prepared', data
    return {key: data[key] for key in ('request_nonce', 'execution_token', 'observation_digest', 'manifest_digest')} | {
        'acknowledge_storage_only': True}


def ddl_count(capture): return sum(name in ('create', 'createIndexes') for name, _ in capture.commands)


def state_update(value, state):
    return value.get('update') == RECEIPTS and value['updates'][0]['u']['$set'].get('state') == state


async def create_existing(raw, index, *, complete=True):
    await raw.create_collection(NAMES[index])
    if index < 2 and complete:
        await raw[NAMES[index]].create_index([('user_id', 1), ('account_id', 1)], unique=True, name=INDEX_NAMES[index])


@pytest.mark.asyncio
async def test_real_auth_prepare_has_no_ddl_execute_is_single_use_and_keeps_diagnostic_receipts(monkeypatch):
    async with owner_case(monkeypatch) as (raw, _, api, capture, foreign):
        old_get = await api.get('/google_ads/automation/storage-commit-readiness')
        assert old_get.status_code == 200 and old_get.json()['status'] == 'historical_completed_receipt'
        body = await prepare(api)
        assert ddl_count(capture) == 0 and await target_metadata(raw) == {}
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'verified_at_observation' and result['namespace_contract_verified'] is True
        assert result['execution_authorized'] is result['funding_ready'] is result['new_uuid_lineage_verified'] is False
        assert ddl_count(capture) == 6 and len([c for n, c in capture.commands if n == 'commitTransaction']) == 1
        before = await target_metadata(raw)
        assert set(before) == set(NAMES)
        assert await raw[RECEIPTS].find_one({'_id': foreign['_id']}) == foreign
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == 'verified'
        bootstrap._active = None  # Simulated new process; durable slot remains.
        duplicate = await api.post(PREFIX + '/execute', json=body)
        assert duplicate.json()['status'] == 'historical_completed_receipt'
        assert duplicate.json()['namespace_contract_verified'] is False
        assert (await api.get(PREFIX + '/receipt')).json()['status'] == 'historical_completed_receipt'
        assert (await api.get('/google_ads/automation/storage-commit-readiness')).json() == old_get.json()
        assert ddl_count(capture) == 6 and await target_metadata(raw) == before


@pytest.mark.asyncio
async def test_real_execute_missing_reservation_never_inserts_one(monkeypatch):
    async with owner_case(monkeypatch) as (raw, _, api, capture, foreign):
        body = {'request_nonce': NONCE, 'execution_token': 'b' * 64, 'observation_digest': 'c' * 64,
            'manifest_digest': bootstrap.MANIFEST_DIGEST, 'acknowledge_storage_only': True}
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 409 and response.json()['detail']['code'].endswith('preparation_missing')
        assert await raw[RECEIPTS].find({}).to_list(length=2) == [foreign]
        assert ddl_count(capture) == 0


@pytest.mark.asyncio
async def test_real_mixed_absent_and_compatible_empty_preserves_existing_uuids(monkeypatch):
    async with owner_case(monkeypatch) as (raw, _, api, capture, _):
        await create_existing(raw, 0); await create_existing(raw, 2)
        before = await target_metadata(raw)
        body = await prepare(api); response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 200, response.text
        after = await target_metadata(raw)
        assert all(after[name] == row for name, row in before.items())
        assert set(response.json()['created_labels']) == {'creation_policy', 'engine_probe'}
        assert ddl_count(capture) == 3


@pytest.mark.asyncio
async def test_real_all_compatible_empty_targets_are_noop_without_reservation(monkeypatch):
    async with owner_case(monkeypatch) as (raw, _, api, capture, foreign):
        for index in range(4): await create_existing(raw, index)
        before = await target_metadata(raw)
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        assert response.status_code == 200 and response.json()['status'] == 'already_compatible_empty_noop'
        assert 'execution_token' not in response.json() and response.json()['funding_ready'] is False
        assert ddl_count(capture) == 0 and await target_metadata(raw) == before
        assert await raw[RECEIPTS].find({}).to_list(length=2) == [foreign]


@pytest.mark.asyncio
@pytest.mark.parametrize('index', range(4))
async def test_real_any_foreign_owner_data_blocks_before_reservation_or_target_writes(monkeypatch, index):
    async with owner_case(monkeypatch) as (raw, _, api, capture, foreign):
        await create_existing(raw, index)
        row = {'_id': 'foreign', 'user_id': 'not_this_owner', 'account_id': '99'}
        await raw[NAMES[index]].insert_one(row); before = await target_metadata(raw)
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        assert response.status_code == 409 and response.json()['detail']['code'].endswith('existing_data_blocks_bootstrap')
        assert ddl_count(capture) == 0 and await target_metadata(raw) == before
        assert await raw[NAMES[index]].find({}).to_list(length=2) == [row]
        assert await raw[RECEIPTS].find({}).to_list(length=2) == [foreign]


@pytest.mark.asyncio
@pytest.mark.parametrize('index', [0, 1])
async def test_real_old_empty_missing_policy_index_is_not_repaired(monkeypatch, index):
    async with owner_case(monkeypatch) as (raw, _, api, capture, _):
        await create_existing(raw, index, complete=False); before = await target_metadata(raw)
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        assert response.status_code == 503 and response.json()['detail']['code'].endswith('index_set_unproved')
        assert ddl_count(capture) == 0 and await target_metadata(raw) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'validator': {'foreign': {'$exists': True}}}, {'collation': {'locale': 'en'}}, {'capped': True, 'size': 1024}])
async def test_real_custom_collection_preserved_without_adoption(monkeypatch, options):
    async with owner_case(monkeypatch) as (raw, _, api, capture, _):
        await raw.create_collection(NAMES[2], **options); before = await target_metadata(raw)
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        assert response.status_code == 503 and response.json()['detail']['code'].endswith('collection_contract_unproved')
        assert ddl_count(capture) == 0 and await target_metadata(raw) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('applied', [False, True])
async def test_real_lost_prepare_ack_never_authorizes_or_sends_ddl_and_get_recovers_only_actual_row(monkeypatch, applied):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        async def fault(value):
            if value.get('insert') == RECEIPTS: raise ConnectionError('private_transport_sentinel')
        if applied: db.after = fault
        else: db.before = fault
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        assert response.status_code == 503 and 'private_transport_sentinel' not in response.text
        assert ddl_count(capture) == 0 and await target_metadata(raw) == {}
        db.before = db.after = None; bootstrap._active = None
        recovered = (await api.get(PREFIX + '/receipt')).json()
        assert recovered['status'] == ('prepared' if applied else 'no_preparation')
        assert recovered['execution_permitted'] is False
        if not applied:
            assert await raw[RECEIPTS].find_one({'_id': SLOT}) is None
            await prepare(api)  # A new explicit preparation remains preparation-only.
            assert ddl_count(capture) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('applied', [False, True])
async def test_real_lost_consumption_ack_has_no_ddl_and_durable_consumed_token_cannot_repeat(monkeypatch, applied):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        body = await prepare(api)
        async def fault(value):
            if state_update(value, 'consumed'): raise ConnectionError('private_claim_sentinel')
        if applied: db.after = fault
        else: db.before = fault
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 503 and 'private_claim_sentinel' not in response.text
        assert ddl_count(capture) == 0
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == ('consumed' if applied else 'prepared')
        db.before = db.after = None; bootstrap._active = None
        if applied:
            again = await api.post(PREFIX + '/execute', json=body)
            assert again.status_code == 409 and again.json()['detail']['code'].endswith('reconciliation_required')
        else:
            assert (await api.get(PREFIX + '/receipt')).json()['status'] == 'prepared'
        assert ddl_count(capture) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['create', 'createIndexes'])
async def test_real_lost_ddl_reply_aborts_targets_but_consumed_receipt_survives_restart(monkeypatch, command):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        body = await prepare(api)
        async def fault(value):
            if command in value: raise ConnectionError('private_ddl_sentinel')
        db.after = fault
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 503 and await target_metadata(raw) == {}
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == 'consumed'
        before = ddl_count(capture); db.after = None; bootstrap._active = None
        assert (await api.post(PREFIX + '/execute', json=body)).status_code == 409
        assert ddl_count(capture) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('applied', [False, True])
async def test_real_commit_reply_loss_never_recommits_or_recreates(monkeypatch, applied):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        body = await prepare(api)
        async def fault(value):
            if 'commitTransaction' in value: raise ConnectionError('private_commit_sentinel')
        if applied: db.after = fault
        else: db.before = fault
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 503 and 'private_commit_sentinel' not in response.text
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == ('applied' if applied else 'consumed')
        assert bool(await target_metadata(raw)) is applied
        commands = deepcopy(capture.commands); db.before = db.after = None; bootstrap._active = None
        assert (await api.post(PREFIX + '/execute', json=body)).status_code == 409
        assert capture.commands == commands
        assert (await api.get(PREFIX + '/receipt')).json()['status'] == 'reconciliation_required'


@pytest.mark.asyncio
@pytest.mark.parametrize('applied', [False, True])
async def test_real_final_receipt_reply_loss_recovers_history_without_new_ddl(monkeypatch, applied):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        body = await prepare(api)
        async def fault(value):
            if state_update(value, 'verified'): raise ConnectionError('private_final_sentinel')
        if applied: db.after = fault
        else: db.before = fault
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 503
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == ('verified' if applied else 'applied')
        before = ddl_count(capture); db.before = db.after = None; bootstrap._active = None
        again = await api.post(PREFIX + '/execute', json=body)
        assert again.status_code == (200 if applied else 409)
        assert ddl_count(capture) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'disabled': True}, {'role': 'viewer'}, {'password_updated_at': '2026-10-29T23:00:00Z'}])
async def test_real_owner_revocation_during_transaction_is_checked_outside_stale_snapshot(monkeypatch, change):
    async with owner_case(monkeypatch) as (raw, db, api, _, _):
        body = await prepare(api); changed = False
        async def revoke(value):
            nonlocal changed
            if 'create' in value and not changed:
                changed = True
                await raw.users.update_one({'id': 'owner'}, {'$set': change})
        db.after = revoke
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 403 and response.json()['detail']['code'] in (
            'google_namespace_bootstrap_owner_changed', 'google_namespace_bootstrap_owner_required')
        assert await target_metadata(raw) == {}
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == 'consumed'


@pytest.mark.asyncio
@pytest.mark.parametrize('index', range(4))
async def test_real_foreign_create_after_consumed_fence_is_rejected_without_adoption(monkeypatch, index):
    async with owner_case(monkeypatch) as (raw, db, api, _, _):
        body = await prepare(api); before = None
        async def race(value):
            nonlocal before
            if state_update(value, 'consumed') and before is None:
                await create_existing(raw, index)
                before = (await target_metadata(raw))[NAMES[index]]
        db.after = race
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 503 and before is not None
        assert await target_metadata(raw) == {NAMES[index]: before}
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == 'consumed'


@pytest.mark.asyncio
async def test_real_existing_uuid_replacement_after_consumption_aborts_new_names(monkeypatch):
    async with owner_case(monkeypatch) as (raw, db, api, _, _):
        await create_existing(raw, 2); original = (await target_metadata(raw))[NAMES[2]]
        body = await prepare(api)
        async def race(value):
            if state_update(value, 'consumed'):
                await raw.drop_collection(NAMES[2]); await create_existing(raw, 2)
        db.after = race
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 409 and response.json()['detail']['code'].endswith('existing_uuid_changed')
        after = await target_metadata(raw)
        assert set(after) == {NAMES[2]} and after[NAMES[2]]['info']['uuid'] != original['info']['uuid']


@pytest.mark.asyncio
async def test_real_independent_module_callers_share_global_reservation(monkeypatch):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        await raw.users.insert_one({'id': 'other', 'role': 'owner'})
        spec = importlib.util.spec_from_file_location('isolated_bootstrap_' + uuid.uuid4().hex, Path(bootstrap.__file__))
        other = importlib.util.module_from_spec(spec); spec.loader.exec_module(other)
        await prepare(api)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(mounted(db, other)), base_url='http://test',
            headers={'Authorization': 'Bearer other'}) as client:
            response = await client.post(PREFIX + '/prepare', json={'request_nonce': 'b' * 32, 'acknowledge_preparation_only': True})
        assert response.status_code == 200 and response.json()['status'] == 'reservation_owned_by_another_auth_context'
        assert 'execution_token' not in response.json()
        assert await raw[RECEIPTS].count_documents({'_id': SLOT}) == 1 and ddl_count(capture) == 0


@pytest.mark.asyncio
async def test_real_legacy_shaped_global_record_is_not_overwritten(monkeypatch):
    async with owner_case(monkeypatch) as (raw, _, api, capture, _):
        legacy = {'_id': SLOT, 'state': 'prepared', 'schema_version': True, 'unknown': 'preserve'}
        await raw[RECEIPTS].insert_one(legacy)
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        assert response.status_code == 503 and response.json()['detail']['code'].endswith('receipt_unproved')
        assert await raw[RECEIPTS].find_one({'_id': SLOT}) == legacy and ddl_count(capture) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['listCollections', 'listIndexes', 'engine_find', 'receipt_update'])
async def test_real_missing_metadata_or_receipt_privilege_never_guesses_absence(monkeypatch, missing):
    async with owner_case(monkeypatch) as (raw, _, api, capture, foreign):
        grants = (await raw.command({'rolesInfo': 'bounded_bootstrap', 'showPrivileges': True}))['roles'][0]['privileges']
        for grant in grants:
            target = grant['resource']['collection']
            action = missing
            applies = target == '' and missing in ('listCollections', 'listIndexes')
            if missing == 'engine_find': action, applies = 'find', target == NAMES[3]
            if missing == 'receipt_update': action, applies = 'update', target == RECEIPTS
            if applies and action in grant['actions']: grant['actions'].remove(action)
        await raw.command({'updateRole': 'bounded_bootstrap', 'privileges': [g for g in grants if g['actions']], 'roles': []})
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        assert response.status_code == 503
        assert ddl_count(capture) == 0 and await target_metadata(raw) == {}
        assert await raw[RECEIPTS].find({}).to_list(length=2) == [foreign]


@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['caller_cancel', 'deadline'])
@pytest.mark.parametrize('stage', ['create', 'commitTransaction'])
async def test_real_interrupted_reply_retains_original_task_and_never_replays_ddl(monkeypatch, interruption, stage):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        body = await prepare(api)
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold_reply(value):
            if stage in value:
                entered.set()
                await release.wait()
        db.after = hold_reply
        monkeypatch.setattr(bootstrap, 'TIMEOUT_SECONDS', 1)
        request = asyncio.create_task(api.post(PREFIX + '/execute', json=body))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            original = bootstrap._active['task']
            if interruption == 'caller_cancel':
                request.cancel()
                with pytest.raises(asyncio.CancelledError): await request
            else:
                response = await asyncio.wait_for(request, timeout=2)
                assert response.status_code == 503 and response.json()['detail']['code'].endswith('deadline')
            assert not original.done() and bootstrap._active['stop'] is True
            rejected = await api.post(PREFIX + '/execute', json=body)
            assert rejected.status_code == 503 and rejected.json()['detail']['code'].endswith('busy')
            release.set()
            with pytest.raises(HTTPException) as ended:
                await asyncio.wait_for(asyncio.shield(original), timeout=4)
            assert ended.value.detail['code'] in ('google_namespace_bootstrap_deadline',
                'google_namespace_bootstrap_session_cleanup_unconfirmed')
        finally:
            release.set()
            if not request.done():
                request.cancel()
                with pytest.raises(asyncio.CancelledError): await request
        committed = stage == 'commitTransaction'
        assert (await raw[RECEIPTS].find_one({'_id': SLOT}))['state'] == ('applied' if committed else 'consumed')
        assert bool(await target_metadata(raw)) is committed
        assert sum(name == 'commitTransaction' for name, _ in capture.commands) == int(committed)
        prior = deepcopy(capture.commands)
        db.after = None; bootstrap._active = None
        retry = await api.post(PREFIX + '/execute', json=body)
        assert retry.status_code == 409 and retry.json()['detail']['code'].endswith('reconciliation_required')
        assert capture.commands == prior


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['prepare_existing', 'execute_verified', 'receipt'])
async def test_real_owner_revoked_during_receipt_read_cannot_receive_token_or_history(monkeypatch, action):
    async with owner_case(monkeypatch) as (raw, db, api, capture, _):
        body = await prepare(api)
        if action == 'execute_verified':
            completed = await api.post(PREFIX + '/execute', json=body)
            assert completed.status_code == 200, completed.text
        before = deepcopy(capture.commands)
        async def revoke(value):
            if value.get('find') == RECEIPTS and value.get('filter') == {'_id': SLOT}:
                await raw.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
        db.after = revoke
        if action == 'prepare_existing':
            response = await api.post(PREFIX + '/prepare', json={'request_nonce': NONCE, 'acknowledge_preparation_only': True})
        elif action == 'execute_verified':
            response = await api.post(PREFIX + '/execute', json=body)
        else:
            response = await api.get(PREFIX + '/receipt')
        assert response.status_code == 403 and response.json()['detail']['code'].endswith('owner_required')
        assert 'execution_token' not in response.text and capture.commands == before
