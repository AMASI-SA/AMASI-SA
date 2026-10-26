"""Actual replica Mongo evidence for the six opt-in owner mutation routes.

Skipped without the explicit disposable loopback test URI. Mongo sessions,
transactions, commit, revision CAS, guard epoch and private field preservation
are real. Only native HTTP transport is fake; no real credentials/provider IO.
This does not prove runtime wiring or migration of automated budget increases.
"""
import asyncio
from copy import deepcopy

from fastapi import APIRouter, FastAPI, Header
import httpx
import pytest

from integrations_control_center import google_ads_automation_policy as standing
from integrations_control_center import google_ads_automation_creation_policy as creation
from integrations_control_center import google_ads_automation_activation as activation
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_automation_policy_mutation_adapter import GuardedGooglePolicyWriter
from tests.test_google_ads_automation_policy_mongo import ACCOUNT, automation_mongo_uri, mongo_db, request_body
from tests.test_google_ads_automation_creation_policy_mongo import mounted as prepare_native
from tests.test_google_ads_automation_creation_policy import NOW, body as creation_body


async def mounted(monkeypatch, db):
    hello = await db.command('hello')
    assert hello.get('setName'), 'Guarded owner-route tests require actual replica Mongo transactions'
    _, native = await prepare_native(monkeypatch, db)
    monkeypatch.setattr(standing, 'account_client', creation.account_client)
    monkeypatch.setattr(standing, 'now', lambda: NOW)
    monkeypatch.setattr(standing, 'automation_runtime_available', lambda: True)
    writer = GuardedGooglePolicyWriter(await guard.probe_google_budget_guard_transactions(db))
    async def current_user(x_test_owner: str = Header(default='owner-a')): return {'id': x_test_owner}
    app, router = FastAPI(), APIRouter()
    standing.attach_google_ads_automation_policy_routes(router, db, current_user, policy_mutation_writer=writer)
    activation.attach_google_ads_automation_activation_routes(router, db, current_user, policy_mutation_writer=writer)
    creation.attach_google_ads_automation_creation_policy_routes(router, db, current_user, policy_mutation_writer=writer)
    app.include_router(router)
    return app, native


def family(kind):
    return (f'/google_ads/automation/{"policy" if kind == "standing" else "creation-policy"}/{ACCOUNT}',
        standing.AUTOMATION_POLICIES if kind == 'standing' else creation.CREATION_POLICIES,
        request_body(enabled=False) if kind == 'standing' else creation_body(),
        {'acknowledge_execution': True} if kind == 'standing' else {'acknowledge_creation': True, 'acknowledge_creative': True})


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_actual_replica_all_six_routes_commit_epoch_and_revoke_disconnected(monkeypatch, mongo_db, kind):
    app, native = await mounted(monkeypatch, mongo_db)
    path, collection, config, ack = family(kind)
    scope = {'user_id': 'owner-a', 'account_id': ACCOUNT}
    private = {'_creation_claim': {'claim_id': 'keep-private'}, '_creation_ledger': {'original': {'private': True}}, '_creation_generation': 8}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        saved = await client.put(path, json=config)
        assert saved.status_code == 200, saved.text
        await mongo_db[collection].update_one(scope, {'$set': private})
        activated = await client.post(path + '/activate', json={'expected_revision': 1, **ack})
        assert activated.status_code == 200, activated.text
        await mongo_db.mezan_integration_accounts_v2.update_many({'user_id': 'owner-a'}, {'$set': {'connection_status': 'disconnected'}})
        async def forbidden(*args, **kwargs): raise AssertionError('revoke needs no OAuth, provider, or new transaction probe')
        monkeypatch.setattr(standing, 'available_accounts', forbidden)
        monkeypatch.setattr(standing, 'account_client', forbidden)
        monkeypatch.setattr(creation, 'account_client', forbidden)
        monkeypatch.setattr(guard, 'probe_google_budget_guard_transactions', forbidden)
        revoked = await client.post(path + '/revoke', json={'expected_revision': 2})
        assert revoked.status_code == 200, revoked.text
    row = await mongo_db[collection].find_one(scope)
    assert row['revision'] == 3 and row['enabled'] is False and len(row['events']) == 3
    assert all(row[key] == value for key, value in private.items())
    account_guard = await mongo_db[guard.BUDGET_GUARDS].find_one(scope)
    assert account_guard['epoch'] == 3 and account_guard['allocations'] == {} and account_guard['active_claim'] is None
    assert await mongo_db[guard.BUDGET_GUARDS].count_documents({'user_id': 'owner-b'}) == 0
    assert native['mutations'] == 0 and guard.budget_guard_runtime_available() is False


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_actual_replica_revoke_defeats_slow_save_without_epoch_or_private_loss(monkeypatch, mongo_db, kind):
    app, native = await mounted(monkeypatch, mongo_db)
    path, collection, config, _ = family(kind)
    scope = {'user_id': 'owner-a', 'account_id': ACCOUNT}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        assert (await client.put(path, json=config)).status_code == 200
        await mongo_db[collection].update_one(scope, {'$set': {'_creation_ledger': {'preserved': True}}})
        native['gate'], native['entered'] = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(client.put(path, json={**deepcopy(config), 'expected_revision': 1}))
        await asyncio.wait_for(native['entered'].wait(), 5)
        revoked = await client.post(path + '/revoke', json={'expected_revision': 1})
        native['gate'].set()
        assert revoked.status_code == 200, revoked.text
        stale = await asyncio.wait_for(pending, 10)
        assert stale.status_code == 409 and stale.json()['detail']['code'] in {'google_creation_policy_changed', 'google_automation_policy_changed'}
    row = await mongo_db[collection].find_one(scope)
    account_guard = await mongo_db[guard.BUDGET_GUARDS].find_one(scope)
    assert row['revision'] == 2 and row['enabled'] is False and row['_creation_ledger'] == {'preserved': True}
    assert account_guard['epoch'] == 2 and len(row['events']) == 2 and native['mutations'] == 0


@pytest.mark.asyncio
async def test_actual_replica_both_policy_families_share_one_account_guard(monkeypatch, mongo_db):
    app, native = await mounted(monkeypatch, mongo_db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        for kind in ('standing', 'creation'):
            path, _, config, _ = family(kind)
            saved = await client.put(path, json=config)
            assert saved.status_code == 200, saved.text
    scope = {'user_id': 'owner-a', 'account_id': ACCOUNT}
    assert await mongo_db[guard.BUDGET_GUARDS].count_documents(scope) == 1
    stored = await mongo_db[guard.BUDGET_GUARDS].find_one(scope)
    assert stored['epoch'] == 2 and stored['allocations'] == {}
    assert await mongo_db[standing.AUTOMATION_POLICIES].count_documents(scope) == 1
    assert await mongo_db[creation.CREATION_POLICIES].count_documents(scope) == 1
    assert native['mutations'] == 0 and guard.budget_guard_runtime_available() is False
