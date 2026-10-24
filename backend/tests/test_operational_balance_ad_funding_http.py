"""Real loopback Mongo + HTTP setup acceptance; executed by operational CI."""
import asyncio
import httpx
from fastapi import FastAPI

from test_operational_balance_integration import run
from operational_balance_routes import make_operational_balance_router
from mobile_app_permissions import OPERATIONAL_APP_WRITE as WRITE, OPERATIONAL_APP_READ as READ
from operational_balance_store import read, STATES


BASE = '/api/operational-balances'
SETUP = BASE + '/ad-funding-settings'


def client(db, actor='owner', mobile=False):
    app = FastAPI()
    async def principal():
        return {'id': actor, '_session_client': 'amasi_mobile' if mobile else 'web'}
    app.include_router(make_operational_balance_router(db, principal), prefix='/api')
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated')


async def grant(db, permissions):
    await db.mezan_mobile_app_access_v1.insert_one({'owner_user_id': 'owner', 'user_id': 'staff',
                                                  'enabled': True, 'permissions': permissions})


def payload(**extra):
    return {'request_id': 'ad-funding-http-1', 'version': 0,
            'integration_account_id': 'google', 'currency': 'SAR',
            'mode': 'wallet_first', 'evidence': 'Owner approved wallet-first funding', **extra}


async def seed(db):
    await db.mezan_integration_accounts_v2.insert_many([
        {'user_id': owner, 'provider': 'google_ads', 'mezan_integration_account_id': identity,
         'external_account_id': 'external-' + identity, 'display_name': identity,
         'connection_status': 'connected', 'mezan_selected': True, 'currency': 'SAR', 'timezone': 'UTC'}
        for owner, identity in [('owner', 'google'), ('other', 'foreign')]])


async def native_snapshot(db):
    names = ['mezan_integration_accounts_v2', 'mz2_financial_accounts', 'mz2_ad_account_bindings_v2',
             'mz2_ad_automation_policies_v2', 'mz2_ad_wallet_openings_v2', 'mz2_opening_facts_v2',
             'mz2_ad_spend_snapshots_v2', 'mz2_ad_postings_v2', 'general_ledger', 'accounting_journal_entries']
    return {name: await db[name].find({}).sort('_id', 1).to_list(100) for name in names}


def test_ad_funding_http_owner_and_mobile_boundaries_and_forged_identity():
    async def scenario(db):
        await seed(db)
        await grant(db, [WRITE, READ])
        native = await native_snapshot(db)
        for actor, mobile in [('staff', False), ('owner', True), ('staff', True)]:
            async with client(db, actor, mobile=mobile) as c:
                assert (await c.get(SETUP)).status_code == 403
                assert (await c.post(SETUP, json=payload())).status_code == 403
        async with client(db, 'owner', mobile=False) as c:
            context = await c.get(BASE + '/context')
            assert context.status_code == 200 and context.json()['can_configure_ad_funding'] is True
            choices = await c.get(SETUP)
            assert choices.status_code == 200
            assert [r['integration_account_id'] for r in choices.json()['items']] == ['google']
            for changed in [{'currency': 'USD'}, {'integration_account_id': 'foreign'}]:
                response = await c.post(SETUP, json=payload(**changed))
                assert response.status_code == 409, response.text
        assert await db[STATES].count_documents({}) == 0
        assert await native_snapshot(db) == native
    run(scenario)


def test_ad_funding_http_cas_replay_eligibility_and_opening_freeze():
    async def scenario(db):
        await seed(db)
        native = await native_snapshot(db)
        async with client(db, 'owner', mobile=False) as c:
            first, second = payload(), payload(request_id='ad-funding-http-2', evidence='Second simultaneous owner intent')
            responses = await asyncio.gather(c.post(SETUP, json=first), c.post(SETUP, json=second))
            assert sorted(r.status_code for r in responses) == [200, 409]
            index = next(i for i, response in enumerate(responses) if response.status_code == 200)
            winner = [first, second][index]
            result = responses[index].json()
            repeated = await c.post(SETUP, json=winner)
            assert repeated.status_code == 200 and repeated.json() == result
            assert (await c.post(SETUP, json={**winner, 'evidence': 'Changed replay contents'})).status_code == 409
            state = await read(db, 'owner')
            assert len(state['audit']) == 1 and not state['movements'] and not state['openings']
            assert state['ad_funding'][result['party_id']]['version'] == 1
            entities = await c.get(BASE + '/entities/ad_account')
            assert entities.status_code == 200, entities.text
            row = next(r for r in entities.json()['items'] if r['id'] == result['party_id'])
            assert row['settings_complete'] is True and row['funding_policy'] == 'wallet_first'
            opening = await c.post(BASE + '/openings', json={
                'request_id': 'wallet-opening-http-1', 'party_type': 'ad_account',
                'party_id': result['party_id'], 'direction': 'for_us', 'amount': '50', 'currency': 'SAR'})
            assert opening.status_code == 200, opening.text
            blocked = await c.post(SETUP, json=payload(request_id='ad-funding-http-3', version=1))
            assert blocked.status_code == 409
            assert blocked.json()['detail']['code'] == 'operational_ad_setup_in_use'
            item = (await c.get(SETUP)).json()['items'][0]
            assert item['configured'] and not item['editable']
            assert item['reason'] == 'operational_ad_setup_in_use'
            # Repeating the already committed setup remains safe after an opening.
            assert (await c.post(SETUP, json=winner)).json() == result
        assert await native_snapshot(db) == native
        assert len((await read(db, 'owner'))['openings']) == 1
    run(scenario)


def test_ad_funding_http_native_binding_prevents_parallel_authority():
    async def scenario(db):
        await seed(db)
        await db.mz2_ad_account_bindings_v2.insert_one({'user_id': 'owner', 'integration_account_id': 'google',
            'confirmed_by': 'owner', 'confirmed_at': '2026-10-20T00:00:00+00:00', 'status': 'active'})
        native = await native_snapshot(db)
        async with client(db, 'owner', mobile=False) as c:
            item = (await c.get(SETUP)).json()['items'][0]
            assert not item['editable'] and item['reason'] == 'operational_ad_native_binding_exists'
            rejected = await c.post(SETUP, json=payload())
            assert rejected.status_code == 409
            assert rejected.json()['detail']['code'] == 'operational_ad_native_binding_exists'
        assert await db[STATES].count_documents({}) == 0
        assert await native_snapshot(db) == native
    run(scenario)
