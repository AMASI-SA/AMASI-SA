"""Actual management router/auth integration; disposable authenticated Mongo only."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from integrations_control_center import google_namespace_bootstrap as bootstrap
from integrations_control_center import google_ads_storage_commit_readiness as diagnostic
from integrations_control_center import google_ads_namespace_prerequisites as audit
from tests.test_google_namespace_bootstrap_capabilities_mongo import fixture, NAMES, RECEIPTS, target_metadata
from tests.test_google_namespace_bootstrap_mongo import ObservedDB, create_existing, ddl_count
from tests.test_google_namespace_bootstrap_integration import PREFIX, management_app, fixture_auth
from tests.google_namespace_bootstrap_ui_fixtures import assert_public_fixture

OLD = '/integrations-v2/google_ads/automation/storage-commit-readiness'
AUDIT = '/integrations-v2/google_ads/automation/namespace-prerequisites'


class RoutedDB(ObservedDB):
    def __getitem__(self, name): return self.raw[name]


@asynccontextmanager
async def case(monkeypatch):
    fixture_auth(monkeypatch)
    bootstrap._active = diagnostic._active = audit._active = None
    async with fixture(with_owner=True) as (_, raw, _, db, capture, foreign):
        observed = RoutedDB(db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(management_app(observed)), base_url='http://test', headers={'Authorization': 'Bearer synthetic'}) as api:
            yield raw, observed, api, capture, foreign
        active = bootstrap._active
        assert active is None or active['task'].done()
    bootstrap._active = diagnostic._active = audit._active = None


async def prepare(api):
    response = await api.post(PREFIX + '/prepare', json={'request_nonce': 'a' * 32, 'acknowledge_preparation_only': True})
    assert response.status_code == 200, response.text
    assert_public_fixture(response.json(), 'prepared')
    return {key: response.json()[key] for key in ('request_nonce', 'execution_token', 'observation_digest', 'manifest_digest')} | {'acknowledge_storage_only': True}


@pytest.mark.asyncio
async def test_full_management_real_auth_flow_matches_ui_and_preserves_both_old_diagnostics(monkeypatch):
    async with case(monkeypatch) as (raw, _, api, capture, foreign):
        old = await api.get(OLD)
        assert old.status_code == 200 and old.json()['status'] == 'historical_completed_receipt'
        first = await api.get(PREFIX + '/receipt')
        assert first.status_code == 200; assert_public_fixture(first.json(), 'no_preparation')
        body = await prepare(api)
        assert ddl_count(capture) == 0 and await target_metadata(raw) == {}
        repeated = await api.post(PREFIX + '/prepare', json={'request_nonce': 'f' * 32, 'acknowledge_preparation_only': True})
        assert repeated.status_code == 200 and repeated.json()['request_nonce'] == body['request_nonce']
        assert repeated.json()['execution_token'] == body['execution_token']
        executed = await api.post(PREFIX + '/execute', json=body)
        assert executed.status_code == 200, executed.text
        assert_public_fixture(executed.json(), 'verified')
        metadata = await target_metadata(raw); assert len(metadata) == 4
        assert ddl_count(capture) == 6
        assert_public_fixture((await api.get(PREFIX + '/receipt')).json(), 'historical')
        assert_public_fixture((await api.post(PREFIX + '/execute', json=body)).json(), 'historical')
        assert (await api.get(OLD)).json() == old.json()
        inspected = await api.get(AUDIT)
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()['execution_authorized'] is inspected.json()['funding_ready'] is False
        assert inspected.json()['business_writes'] == inspected.json()['provider_writes'] == 0
        assert await raw[RECEIPTS].find_one({'_id': foreign['_id']}) == foreign
        assert await target_metadata(raw) == metadata and ddl_count(capture) == 6
        for name in NAMES: assert await raw[name].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('state,key', [('consumed', 'consumed'), ('applied', 'applied'), ('verified', 'historical'), ('expired', 'expired'), ('foreign', 'foreign')])
async def test_full_management_public_state_contract_preserves_opaque_pending_receipt(monkeypatch, state, key):
    async with case(monkeypatch) as (raw, _, api, capture, _):
        await prepare(api)
        before = await raw[RECEIPTS].find_one({'_id': bootstrap.SLOT_ID})
        if state == 'expired': changes = {'prepared_at': before['prepared_at'] - timedelta(hours=1), 'expires_at': before['expires_at'] - timedelta(hours=1)}
        elif state == 'foreign': changes = {'owner_id': 'another_owner'}
        else:
            changes = {'state': state, 'attempt_id': 'c' * 32}
            if state == 'verified': changes.update(verified_at=bootstrap._now(), verified_catalog_digest='d' * 64)
        await raw[RECEIPTS].update_one({'_id': bootstrap.SLOT_ID}, {'$set': changes})
        before = await raw[RECEIPTS].find_one({'_id': bootstrap.SLOT_ID})
        response = await api.get(PREFIX + '/receipt')
        assert response.status_code == 200, response.text
        assert_public_fixture(response.json(), key)
        assert await raw[RECEIPTS].find_one({'_id': bootstrap.SLOT_ID}) == before
        assert ddl_count(capture) == 0


@pytest.mark.asyncio
async def test_full_management_compatible_empty_is_noop_and_does_not_reserve(monkeypatch):
    async with case(monkeypatch) as (raw, _, api, capture, foreign):
        for index in range(4): await create_existing(raw, index)
        metadata = await target_metadata(raw)
        response = await api.post(PREFIX + '/prepare', json={'request_nonce': 'a' * 32, 'acknowledge_preparation_only': True})
        assert response.status_code == 200, response.text
        assert_public_fixture(response.json(), 'noop')
        assert await raw[RECEIPTS].find({}).to_list(length=2) == [foreign]
        assert await target_metadata(raw) == metadata and ddl_count(capture) == 0


@pytest.mark.asyncio
async def test_full_management_lost_actual_commit_ack_retains_applied_and_never_replays(monkeypatch):
    async with case(monkeypatch) as (raw, observed, api, capture, _):
        body = await prepare(api)
        async def lost(value):
            if 'commitTransaction' in value: raise ConnectionError('private_fixture_transport')
        observed.after = lost
        response = await api.post(PREFIX + '/execute', json=body)
        assert response.status_code == 503 and 'private_fixture_transport' not in response.text
        observed.after = None
        assert (await raw[RECEIPTS].find_one({'_id': bootstrap.SLOT_ID}))['state'] == 'applied'
        assert_public_fixture((await api.get(PREFIX + '/receipt')).json(), 'applied')
        assert (await api.post(PREFIX + '/execute', json=body)).status_code == 409
        assert len([c for name, c in capture.commands if name == 'commitTransaction']) == 1
        assert ddl_count(capture) == 6


@pytest.mark.asyncio
async def test_full_management_fresh_owner_revocation_during_read_never_returns_token(monkeypatch):
    async with case(monkeypatch) as (raw, observed, api, capture, _):
        await prepare(api)
        async def revoke(value):
            if value.get('find') == RECEIPTS:
                await raw.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
        observed.after = revoke
        response = await api.get(PREFIX + '/receipt')
        assert response.status_code == 403 and 'execution_token' not in response.text
        assert ddl_count(capture) == 0


@pytest.mark.asyncio
async def test_full_management_old_explicit_commit_probe_cannot_overwrite_global_bootstrap_receipt(monkeypatch):
    async with case(monkeypatch) as (raw, _, api, capture, _):
        await raw.create_collection(diagnostic.PAYLOADS)
        await raw.command({'grantPrivilegesToRole': 'bounded_bootstrap', 'privileges': [
            {'resource': {'db': raw.name, 'collection': diagnostic.PAYLOADS}, 'actions': ['find', 'insert', 'remove']}]})
        body = await prepare(api)
        assert (await api.post(PREFIX + '/execute', json=body)).status_code == 200
        before = await raw[RECEIPTS].find_one({'_id': bootstrap.SLOT_ID})
        metadata = await target_metadata(raw)
        old = await api.post(OLD, json={})
        assert old.status_code == 200, old.text
        assert old.json()['commit_majority_j_ack_verified'] is True
        assert old.json()['execution_authorized'] is old.json()['funding_ready'] is False
        assert await raw[RECEIPTS].find_one({'_id': bootstrap.SLOT_ID}) == before
        assert await raw[diagnostic.PAYLOADS].count_documents({}) == 0
        assert await target_metadata(raw) == metadata and ddl_count(capture) == 6
        for name in NAMES: assert await raw[name].count_documents({}) == 0
