"""Public review authorization keeps app grants separate from browser authority."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import accounting_driver_payment_review as native
import operational_driver_payment_review as service
from mobile_app_permissions import MOBILE_APP_ACCESS, MOBILE_APP_CLIENT
from mobile_app_request_context import mobile_app_request_user
from operational_balance_store import STATES, digest
from operational_driver_review_store import EVENTS, ReviewDatabase
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
from .test_store_delivery_noncash_review import NOW, run_mock, seed

APP_GRANTS = ['app.page.couriers', 'app.action.couriers.payment_review']


async def staff(db, *, role='employee', legacy=False, grants=None):
    user = {'id': 'reviewer', 'role': role, 'is_active': True}
    if not legacy:
        user['created_by'] = 'owner'
    else:
        await db.mezan_employees_v2.insert_one({'user_id': 'owner', 'account_user_id': user['id']})
    await db.users.insert_one(user)
    await db[MOBILE_APP_ACCESS].insert_one({'owner_user_id': 'owner', 'user_id': user['id'],
        'enabled': True, 'permissions': APP_GRANTS if grants is None else grants,
        'operational_banks': {'bank_ids': ['bank']}})
    return user


async def app_principal(db, user):
    return await mobile_app_request_user(db, {**user, '_session_client': MOBILE_APP_CLIENT},
        path='/api/store-delivery/payment-review/pending', method='GET')


def client_for(db, principal):
    async def current_user():
        return deepcopy(principal)
    app = FastAPI()
    app.include_router(make_store_delivery_payment_review_router(db, current_user))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic')


@pytest.mark.parametrize('legacy,grants', [(False, APP_GRANTS), (True, APP_GRANTS),
                                         (False, ['app.role.manager'])])
def test_live_app_grant_reaches_public_options_with_real_actor(monkeypatch, legacy, grants):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def check(db):
        assignment, _ = await seed(db)
        actor = await staff(db, legacy=legacy, grants=grants)
        principal = await app_principal(db, actor)
        before = await db[STATES].find_one({})
        async with client_for(db, principal) as client:
            response = await client.get(f'/store-delivery/payment-review/{assignment}/approval-options')
        assert response.status_code == 200, response.text
        assert response.json()['can_approve'] is True
        assert response.json()['session_scope'] == digest(['owner', actor['id']])
        assert await db[STATES].find_one({}) == before
        assert await db[EVENTS].count_documents({}) == 0
        assert (await db.users.find_one({'id': actor['id']})).get('extra_permissions') is None
    run_mock(check)


def test_app_grant_does_not_grant_browser_review_authority(monkeypatch):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def check(db):
        assignment, payload = await seed(db)
        actor = await staff(db)
        payload.expected_session_scope = digest(['owner', actor['id']])
        async with client_for(db, actor) as client:
            assert (await client.get(f'/store-delivery/payment-review/{assignment}/approval-options')).status_code == 403
            assert (await client.post(f'/store-delivery/payment-review/{assignment}',
                json=payload.model_dump(mode='json'))).status_code == 403
        assert await db[EVENTS].count_documents({}) == 0
    run_mock(check)


@pytest.mark.parametrize('case', ['revoked', 'action_removed', 'page_removed', 'disabled_user',
                                 'foreign_grant', 'foreign_link', 'denied_review'])
def test_stale_app_principal_cannot_keep_revoked_review_authority(monkeypatch, case):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def check(db):
        assignment, payload = await seed(db)
        actor = await staff(db)
        principal = await app_principal(db, actor)
        if case == 'revoked':
            await db[MOBILE_APP_ACCESS].update_one({}, {'$set': {'enabled': False}})
        elif case in {'action_removed', 'page_removed'}:
            remaining = ['app.page.couriers'] if case == 'action_removed' else ['app.action.couriers.payment_review']
            await db[MOBILE_APP_ACCESS].update_one({}, {'$set': {'permissions': remaining}})
        elif case == 'foreign_grant':
            await db[MOBILE_APP_ACCESS].update_one({}, {'$set': {'owner_user_id': 'foreign'}})
        elif case == 'foreign_link':
            await db.users.update_one({'id': actor['id']}, {'$set': {'created_by': 'foreign'}})
        elif case == 'disabled_user':
            await db.users.update_one({'id': actor['id']}, {'$set': {'disabled': True}})
        else:
            await db.users.update_one({'id': actor['id']}, {'$set': {'denied_permissions': ['store_delivery.payments.review']}})
        before = await db[STATES].find_one({})
        payload.expected_session_scope = digest(['owner', actor['id']])
        async with client_for(db, principal) as client:
            for path in ('/pending', '/bank-accounts', f'/{assignment}/approval-options'):
                assert (await client.get('/store-delivery/payment-review' + path)).status_code == 403
            response = await client.post(f'/store-delivery/payment-review/{assignment}',
                json=payload.model_dump(mode='json'))
            assert response.status_code == 403, response.text
        assert await db[STATES].find_one({}) == before
        assert await db[EVENTS].count_documents({}) == 0
    run_mock(check)


@pytest.mark.parametrize('mobile', [False, True])
def test_driver_identity_cannot_review_even_with_explicit_grants(monkeypatch, mobile):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def check(db):
        assignment, _ = await seed(db)
        actor = await staff(db, role='store_driver')
        await db.users.update_one({'id': actor['id']}, {'$set': {'extra_permissions': ['store_delivery.payments.review']}})
        principal = actor
        if mobile:
            # Even a stale merchant-shaped principal cannot bypass the writer's
            # fresh role check. The normal mobile bridge also rejects this role.
            principal = {'id': 'owner', 'role': 'owner', '_session_client': MOBILE_APP_CLIENT,
                         '_mobile_actor_id': actor['id'], '_mobile_owner_id': 'owner',
                         '_mobile_app_permissions': APP_GRANTS}
            with pytest.raises(HTTPException):
                await app_principal(db, actor)
        async with client_for(db, principal) as client:
            response = await client.get(f'/store-delivery/payment-review/{assignment}/approval-options')
        assert response.status_code == 403, response.text
    run_mock(check)


def test_app_review_action_preserves_native_accounting_gate():
    async def check(db):
        await seed(db)
        actor = await staff(db)
        await db.users.update_one({'id': actor['id']}, {'$set': {'accounting_permissions': ['accounting.shipping.view']}})
        with pytest.raises(HTTPException) as error:
            await native.review_actor(db, 'owner', actor['id'], request_client=MOBILE_APP_CLIENT)
        assert error.value.status_code == 403
        assert error.value.detail['code'] == 'accounting_permission_required'
        assert error.value.detail['permission'] == 'accounting.settlements.post'
    run_mock(check)


@pytest.mark.parametrize('collection,query', [
    (MOBILE_APP_ACCESS, {'user_id': 'reviewer'}),
    (MOBILE_APP_ACCESS, {'owner_user_id': 'foreign', 'user_id': 'reviewer'}),
    (MOBILE_APP_ACCESS, {'owner_user_id': 'owner', 'user_id': {'$ne': ''}}),
    ('mezan_employees_v2', {'user_id': 'owner'}),
    ('mezan_employees_v2', {'user_id': 'foreign', 'account_user_id': 'reviewer'}),
])
def test_transaction_permission_reads_remain_owner_and_actor_scoped(collection, query):
    async def check(db):
        scoped = ReviewDatabase(db, object(), 'owner')
        with pytest.raises(HTTPException):
            await scoped[collection].find_one(query)
        assert scoped.failed
    run_mock(check)


def test_review_grant_cannot_read_or_approve_unassigned_bank(monkeypatch):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def check(db):
        assignment, payload = await seed(db, method='bank_transfer')
        actor = await staff(db)
        principal = await app_principal(db, actor)
        payload.expected_session_scope = digest(['owner', actor['id']])
        await db[MOBILE_APP_ACCESS].update_one({'user_id':actor['id']}, {'$set':{'operational_banks':{'bank_ids':[]}}})
        before = await db[STATES].find_one({})
        async with client_for(db, principal) as client:
            pending = await client.get('/store-delivery/payment-review/pending')
            assert pending.status_code == 200 and pending.json()['items'] == [] and pending.json()['total'] == 0
            assert (await client.get(f'/store-delivery/payment-review/{assignment}/approval-options')).status_code == 403
            assert (await client.post(f'/store-delivery/payment-review/{assignment}', json=payload.model_dump(mode='json'))).status_code == 403
        assert await db[STATES].find_one({}) == before
        assert await db[EVENTS].count_documents({}) == 0
    run_mock(check)


def test_receipt_token_does_not_bypass_bank_scope(monkeypatch):
    from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def check(db):
        await seed(db, method='bank_transfer')
        actor = await staff(db, role='accountant')
        await db[MOBILE_APP_ACCESS].update_one({'user_id':actor['id']}, {'$set':{'operational_banks':{'bank_ids':[]}}})
        app = FastAPI()
        async def current(): return actor
        app.include_router(make_store_delivery_payment_evidence_router(db, current))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic') as client:
            response = await client.get('/store-delivery/evidence/receipt/token-1')
        assert response.status_code == 403
    run_mock(check)
