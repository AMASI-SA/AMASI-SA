"""Opt-in policy storage seam never silently falls back after guard failure."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from integrations_control_center import google_ads_automation_policy_mutation_adapter as adapter
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_automation_policy import AUTOMATION_POLICIES
from integrations_control_center.google_ads_automation_creation_policy import CREATION_POLICIES
from tests.test_google_ads_automation_budget_guard import DB, supported


@pytest.mark.asyncio
async def test_guarded_writer_mutates_policy_and_guard_atomically_preserving_private_state():
    db = DB({CREATION_POLICIES: [{'user_id': 'owner', 'account_id': '123', 'revision': 2,
        '_creation_ledger': {'one': {'verified': True}}, '_creation_claim': {'held': True}, '_creation_generation': 4}]})
    capability = await supported(db)
    await adapter.mutate_google_automation_policy(db, policy_kind='creation', scope={'user_id': 'owner', 'account_id': '123'},
        expected_revision=2, values={'enabled': False, 'revision': 3}, event={'kind': 'revoked'},
        writer=adapter.GuardedGooglePolicyWriter(capability))
    row = db.rows[CREATION_POLICIES][0]
    assert row['revision'] == 3 and row['enabled'] is False
    assert row['_creation_claim'] == {'held': True} and row['_creation_generation'] == 4
    assert row['_creation_ledger'] == {'one': {'verified': True}}
    assert db.rows[guard.BUDGET_GUARDS][0]['epoch'] == 1
    assert db.sessions[-1].commit_calls == 1 and guard.budget_guard_runtime_available() is False


@pytest.mark.asyncio
async def test_missing_capability_denies_without_probe_session_legacy_cas_or_policy_write():
    db = DB()
    with pytest.raises(HTTPException) as caught:
        await adapter.mutate_google_automation_policy(db, policy_kind='standing', scope={'user_id': 'owner', 'account_id': '123'},
            expected_revision=0, values={'enabled': False}, writer=adapter.GuardedGooglePolicyWriter(None))
    assert caught.value.detail['code'] == 'google_budget_guard_transactions_unproved'
    assert not db.sessions and not db.rows


@pytest.mark.asyncio
async def test_guard_commit_ack_loss_never_falls_back_or_retries_policy_write():
    db = DB(); writer = adapter.GuardedGooglePolicyWriter(await supported(db)); db.lose_commit_ack = True
    with pytest.raises(HTTPException) as caught:
        await adapter.mutate_google_automation_policy(db, policy_kind='standing', scope={'user_id': 'owner', 'account_id': '123'},
            expected_revision=0, values={'enabled': False}, writer=writer)
    assert caught.value.detail['commit_attempted'] and caught.value.detail['automatic_retry_allowed'] is False
    assert caught.value.detail['operation_id'].startswith('google_policy_')
    assert db.rows[AUTOMATION_POLICIES][0]['revision'] == 1 and db.sessions[-1].commit_calls == 1


@pytest.mark.asyncio
async def test_revision_conflict_uses_existing_route_code_and_rolls_back_epoch():
    db = DB({AUTOMATION_POLICIES: [{'user_id': 'owner', 'account_id': '123', 'revision': 2}]})
    writer = adapter.GuardedGooglePolicyWriter(await supported(db)); before = deepcopy(db.rows)
    with pytest.raises(HTTPException) as caught:
        await adapter.mutate_google_automation_policy(db, policy_kind='standing', scope={'user_id': 'owner', 'account_id': '123'},
            expected_revision=1, values={'enabled': False}, writer=writer)
    assert caught.value.detail['code'] == 'google_automation_policy_changed' and db.rows == before


# Route-level collaboration exercises the actual guard, owner validation and
# native/catalog validators. This session model tests wiring, not Mongo isolation;
# replica transaction/concurrency evidence belongs to the separate guard suite.
from types import SimpleNamespace
import httpx
from fastapi import APIRouter, FastAPI
from integrations_control_center import google_ads_automation_policy as standing
from integrations_control_center import google_ads_automation_activation as activation
from integrations_control_center import google_ads_automation_creation_policy as creation
from tests.test_google_ads_automation_budget_guard import Collection as TransactionCollection
from tests.test_google_ads_automation_creation_policy import Collection, app as creation_app, body as creation_body, NOW
from tests.test_google_ads_automation_creation_contract import case as product_case
from tests.test_google_ads_automation_policy import body as standing_body


class RoutedCollection(TransactionCollection):
    def rows(self, session):
        return super().rows(session) if session is not None else self.db.rows.setdefault(self.name, [])
    async def find_one(self, query, projection=None, *, session=None):
        return await super().find_one(query, projection, session=session)
    def find(self, query, projection=None, *, session=None):
        return super().find(query, projection, session=session)
    async def insert_one(self, row, *, session=None):
        self.db.outside_writes += int(session is None)
        return await super().insert_one(row, session=session)
    async def update_one(self, query, update, *, session=None):
        self.db.outside_writes += int(session is None)
        return await super().update_one(query, update, session=session)
    async def create_index(self, *args, **kwargs): pass


class RouteDB(DB):
    def __init__(self):
        super().__init__(); self.outside_writes = 0
        self.users = Collection([{'id': 'owner', 'role': 'owner'}])
        self.products = Collection([product_case()['product']])
    def __getitem__(self, name):
        return self.products if name == 'mezan_products_v2' else RoutedCollection(self, name)


async def routed_app(monkeypatch, *, capability=True, guarded=True):
    db = RouteDB()
    _, _, calls = creation_app(monkeypatch, db=db)
    monkeypatch.setattr(standing, 'account_client', creation.account_client)
    monkeypatch.setattr(standing, 'now', lambda: NOW)
    monkeypatch.setattr(standing, 'automation_runtime_available', lambda: True)
    monkeypatch.setattr(creation, 'creation_runtime_available', lambda: True)
    writer = adapter.GuardedGooglePolicyWriter(await supported(db) if capability else None) if guarded else None
    async def current_user(): return {'id': 'owner'}
    app, router = FastAPI(), APIRouter()
    standing.attach_google_ads_automation_policy_routes(router, db, current_user, policy_mutation_writer=writer)
    activation.attach_google_ads_automation_activation_routes(router, db, current_user, policy_mutation_writer=writer)
    creation.attach_google_ads_automation_creation_policy_routes(router, db, current_user, policy_mutation_writer=writer)
    app.include_router(router)
    return app, db, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_all_six_guarded_owner_routes_share_epoch_and_keep_private_claims(monkeypatch, kind):
    app, db, calls = await routed_app(monkeypatch)
    path = '/google_ads/automation/' + ('policy' if kind == 'standing' else 'creation-policy') + '/123'
    collection = AUTOMATION_POLICIES if kind == 'standing' else CREATION_POLICIES
    config = standing_body() if kind == 'standing' else creation_body()
    ack = {'acknowledge_execution': True} if kind == 'standing' else {'acknowledge_creation': True, 'acknowledge_creative': True}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        saved = await client.put(path, json=config)
        assert saved.status_code == 200, saved.text
        db.rows[collection][0].update(_creation_claim={'held': True}, _creation_ledger={'original': {'private': True}}, _creation_generation=8)
        activated = await client.post(path + '/activate', json={'expected_revision': 1, **ack})
        assert activated.status_code == 200, activated.text
        # OAuth and native reads are forbidden for revocation; DB capability is
        # already established and no fresh probe is attempted by the adapter.
        async def forbidden(*args, **kwargs): raise AssertionError('revoke must not require OAuth/provider/probe')
        monkeypatch.setattr(standing, 'available_accounts', forbidden)
        monkeypatch.setattr(standing, 'account_client', forbidden)
        monkeypatch.setattr(creation, 'account_client', forbidden)
        monkeypatch.setattr(guard, 'probe_google_budget_guard_transactions', forbidden)
        revoked = await client.post(path + '/revoke', json={'expected_revision': 2})
        assert revoked.status_code == 200, revoked.text
    stored = db.rows[collection][0]
    assert stored['revision'] == 3 and stored['enabled'] is False
    assert stored['_creation_claim'] == {'held': True} and stored['_creation_ledger'] == {'original': {'private': True}}
    assert stored['_creation_generation'] == 8 and len(stored['events']) == 3
    assert db.rows[guard.BUDGET_GUARDS][0]['epoch'] == 3 and db.outside_writes == 0 and calls['mutations'] == 0
    assert guard.budget_guard_runtime_available() is False


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_guarded_route_missing_transactions_never_falls_back_to_current_cas(monkeypatch, kind):
    app, db, calls = await routed_app(monkeypatch, capability=False)
    path = '/google_ads/automation/' + ('policy' if kind == 'standing' else 'creation-policy') + '/123'
    config = standing_body() if kind == 'standing' else creation_body()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        reply = await client.put(path, json=config)
    assert reply.status_code == 409 and reply.json()['detail']['code'] == 'google_budget_guard_transactions_unproved'
    assert db.outside_writes == 0 and not db.rows.get(AUTOMATION_POLICIES) and not db.rows.get(CREATION_POLICIES)
    assert not db.sessions and calls['mutations'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_persisted_viewer_cannot_reach_guard_or_change_policy(monkeypatch, kind):
    app, db, calls = await routed_app(monkeypatch)
    sessions = len(db.sessions); db.users.rows[0]['role'] = 'viewer'
    path = '/google_ads/automation/' + ('policy' if kind == 'standing' else 'creation-policy') + '/123'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.put(path, json=standing_body() if kind == 'standing' else creation_body())
    assert response.status_code == 403 and db.outside_writes == 0
    assert len(db.sessions) == sessions and calls['metadata'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_route_commit_ack_loss_returns_unknown_and_never_retries(monkeypatch, kind):
    app, db, calls = await routed_app(monkeypatch); db.lose_commit_ack = True
    path = '/google_ads/automation/' + ('policy' if kind == 'standing' else 'creation-policy') + '/123'
    collection = AUTOMATION_POLICIES if kind == 'standing' else CREATION_POLICIES
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.put(path, json=standing_body() if kind == 'standing' else creation_body())
    assert response.status_code == 503 and response.json()['detail']['automatic_retry_allowed'] is False
    assert response.json()['detail']['commit_attempted'] is True and response.json()['detail']['operation_id']
    assert db.rows[collection][0]['revision'] == 1 and db.rows[guard.BUDGET_GUARDS][0]['epoch'] == 1
    assert db.outside_writes == 0 and db.sessions[-1].commit_calls == 1


@pytest.mark.asyncio
async def test_both_policy_families_share_same_account_epoch(monkeypatch):
    app, db, _ = await routed_app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        assert (await client.put('/google_ads/automation/policy/123', json=standing_body())).status_code == 200
        assert (await client.put('/google_ads/automation/creation-policy/123', json=creation_body())).status_code == 200
    assert len(db.rows[guard.BUDGET_GUARDS]) == 1 and db.rows[guard.BUDGET_GUARDS][0]['epoch'] == 2
    assert len(db.rows[AUTOMATION_POLICIES]) == len(db.rows[CREATION_POLICIES]) == 1 and db.outside_writes == 0


@pytest.mark.asyncio
async def test_default_none_is_explicit_legacy_compatibility_and_does_not_probe(monkeypatch):
    app, db, calls = await routed_app(monkeypatch, guarded=False)
    async def forbidden(*args, **kwargs): raise AssertionError('legacy compatibility must not silently choose transactions')
    monkeypatch.setattr(guard, 'probe_google_budget_guard_transactions', forbidden)
    monkeypatch.setattr(guard, 'run_google_budget_guard_transaction', forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.put('/google_ads/automation/policy/123', json=standing_body())
    assert response.status_code == 200 and db.outside_writes == 1 and not db.sessions
    assert guard.BUDGET_GUARDS not in db.rows and guard.budget_guard_runtime_available() is False
