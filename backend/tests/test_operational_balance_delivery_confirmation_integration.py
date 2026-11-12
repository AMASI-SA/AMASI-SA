"""Local replica Mongo + real HTTP: attestation is durable, scoped and nonposting."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Header

from operational_balance_archive import SNAPSHOT, compact_financial_snapshot
from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources
from operational_balance_routes import make_operational_balance_router
from operational_balance_service import refresh
from operational_balance_store import STATES, digest, initial, read
from operational_delivery_confirmation import PREFIX
from operational_delivery_confirmation_service import preview
from test_operational_balance_integration import run
from test_operational_balance_delivery_confirmation import data_for, active, NOW, START, LATER, fee_total, party, HardBoundaryDB

BASE = '/api/operational-balances'


def payload(kind='courier'):
    return {'order_id': '100', 'order_number': '10', 'party_type': kind,
            'party_id': 'carrier' if kind == 'courier' else 'driver',
            'visible': True, 'expected_session_scope': digest(['owner', 'owner'])}


def client_for(db):
    app = FastAPI()
    async def principal(x_test_user: str = Header(default='owner'), x_test_mobile: str = Header(default='no')):
        return {'id': 'owner' if x_test_mobile == 'staff_principal' else x_test_user,
                '_mobile_actor_id': 'staff' if x_test_mobile == 'staff_principal' else x_test_user,
                '_session_client': 'amasi_mobile' if x_test_mobile != 'no' else 'web'}
    app.include_router(make_operational_balance_router(db, principal), prefix='/api')
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated')


async def seed(db, kind='courier', *, packed=False):
    data = data_for(kind)
    sources = await collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=active())
    state = {**initial('owner'), **reconcile(active(), sources, NOW)}
    state['revision'] = 1
    state['engine']['as_of'] = NOW
    # Retain an unrelated financial fact to catch selective packet data loss.
    state['engine']['facts']['retained-proof'] = {'amount': '17.25', 'evidence': 'unchanged' * 1000}
    for name, docs in data.items():
        if docs:
            await db[name].insert_many(deepcopy(docs))
    await db[STATES].insert_one(compact_financial_snapshot(state, 12 * 1024 * 1024) if packed else state)
    return data, state


async def review_and_body(client, body=None):
    body = body or payload()
    response = await client.post(BASE + '/delivery-after-start/preview', json=body)
    assert response.status_code == 200, response.text
    review = response.json()
    assert review['can_confirm'] is True, review
    return review, {**body, 'expected_preview_hash': review['preview_hash'], 'request_id': 'manual-one',
                    'confirmed_after_start': True}


@pytest.mark.parametrize('kind,packed', [('courier', False), ('courier', True), ('native_driver', True)])
def test_real_preview_confirm_saved_get_and_refresh_preserve_money_and_replay(kind, packed, monkeypatch):
    import operational_delivery_confirmation_service as service
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        data, previous = await seed(db, kind, packed=packed)
        body = payload('store_driver' if kind == 'native_driver' else 'courier')
        async with client_for(db) as client:
            before = await read(db, 'owner')
            review, save = await review_and_body(client, body)
            assert await read(db, 'owner') == before
            saved = await client.post(BASE + '/delivery-after-start/confirm', json=save)
            assert saved.status_code == 200, saved.text
            assert saved.json()['confirmed_at'] == saved.json()['recorded_confirmation']['confirmed_at'] == NOW
            assert saved.json()['pending_reconciliation'] is True
            once = await read(db, 'owner')
            assert once['engine']['as_of'] == previous['engine']['as_of']
            assert once['engine']['obligations'] == previous['engine']['obligations']
            assert once['openings'] == previous['openings'] and once['movements'] == previous['movements']
            assert once['engine']['facts']['retained-proof'] == previous['engine']['facts']['retained-proof']
            assert len(once['audit']) == 1 and once['audit'][0]['actor_id'] == 'owner'
            assert (await client.post(BASE + '/delivery-after-start/confirm', json=save)).json() == saved.json()
            assert await read(db, 'owner') == once
            repeated = await client.post(BASE + '/delivery-after-start/confirm', json={**save, 'request_id': 'second-click'})
            assert repeated.status_code == 200 and repeated.json() == saved.json()
            assert await read(db, 'owner') == once
            # A failed refresh can leave the old financial snapshot. The proof
            # state is nevertheless served by the ordinary saved GET projection.
            position = await client.get(BASE + '/financial-position')
            assert position.status_code == 200, position.text
            result = position.json()
            assert result['permissions']['confirm_delivery_after_start'] is True
            table = next(t for t in result['currencies']['SAR']['tables'] if t['id'] == ('drivers' if kind == 'native_driver' else 'couriers'))
            pending = next(item for row in table['rows'] for item in row['pending_order_details'] if item['order_id'] == '100')
            assert pending['delivery_after_start_confirmation']['state'] == 'pending_reconciliation'
            assert pending['delivery_after_start_confirmation']['can_confirm'] is False
            assert 'unified_orders' not in str(pending) and 'source_hash' not in str(pending)
            # Repack the newly saved proof, then let the ordinary refresh consume it.
            await db[STATES].replace_one({'_id': 'owner'}, compact_financial_snapshot(once, 12 * 1024 * 1024))
            again = await client.post(BASE + '/delivery-after-start/preview', json=body)
            assert again.status_code == 200 and again.json()['confirmation_state'] == 'pending_reconciliation'
            refreshed = await refresh(db, 'owner', clock=LATER)
            assert fee_total(refreshed) == 10
            assert refreshed['engine']['facts'][PREFIX + '100'] == once['engine']['facts'][PREFIX + '100']
            assert refreshed['engine']['facts']['delivery_event:100']['delivered_at'] is None
            assert refreshed['engine']['facts']['retained-proof'] == previous['engine']['facts']['retained-proof']
            applied = await client.post(BASE + '/delivery-after-start/preview', json=body)
            assert applied.status_code == 200 and applied.json()['confirmation_state'] == 'applied'
            assert await db.general_ledger.count_documents({}) == 0
            assert await db[STATES].count_documents({}) == 1
            source = await db.unified_orders.find_one({'user_id': 'owner'})
            assert source['raw_by_source'] == data['unified_orders'][0]['raw_by_source']
    run(scenario)


def test_preview_hash_ignores_clock_advance_but_source_version_invalidates_it():
    async def scenario(db):
        await seed(db)
        first = await preview(db, 'owner', 'owner', payload(), clock=NOW)
        later = await preview(db, 'owner', 'owner', payload(), clock=LATER)
        assert first['can_confirm'] and first['preview_hash'] == later['preview_hash']
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {'raw_by_source.salla_direct.updated_at': LATER}})
        changed = await preview(db, 'owner', 'owner', payload(), clock=LATER)
        assert changed['can_confirm'] and changed['preview_hash'] != first['preview_hash']
    run(scenario)


@pytest.mark.parametrize('headers', [{'x-test-user': 'staff'}, {'x-test-user': 'viewer'},
                                   {'x-test-user': 'other'}, {'x-test-mobile': 'staff_principal'}])
def test_owner_and_real_mobile_actor_gates_reject_other_principals(headers, monkeypatch):
    import operational_delivery_confirmation_service as service
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        await seed(db)
        before = await read(db, 'owner')
        async with client_for(db) as client:
            _, body = await review_and_body(client)
            for suffix, content in [('preview', payload()), ('confirm', body)]:
                rejected = await client.post(BASE + '/delivery-after-start/' + suffix, json=content, headers=headers)
                assert rejected.status_code == 403, rejected.text
        assert await read(db, 'owner') == before
    run(scenario)


def test_mobile_owner_uses_exact_route_classification_and_session_scope(monkeypatch):
    import operational_delivery_confirmation_service as service
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            response = await client.post(BASE + '/delivery-after-start/preview', json=payload(), headers={'x-test-mobile': 'yes'})
            assert response.status_code == 200 and response.json()['can_confirm']
            body = {**payload(), 'request_id': 'native-owner', 'confirmed_after_start': True,
                    'expected_preview_hash': response.json()['preview_hash']}
            saved = await client.post(BASE + '/delivery-after-start/confirm', json=body, headers={'x-test-mobile': 'yes'})
            assert saved.status_code == 200, saved.text
            wrong = await client.post(BASE + '/delivery-after-start/preview', json={**payload(), 'expected_session_scope': 'a' * 64})
            assert wrong.status_code == 403
    run(scenario)


@pytest.mark.parametrize('change', ['order_number', 'party', 'start', 'status', 'revision'])
def test_fresh_confirmation_rejects_stale_preview_and_preserves_all_money(change, monkeypatch):
    import operational_delivery_confirmation_service as service
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            _, body = await review_and_body(client)
            if change == 'order_number':
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {'raw_by_source.salla_direct.reference_id': '11'}})
            elif change == 'party':
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {'salla_shipping_current.company_code': 'other'}})
            elif change == 'start':
                await db[STATES].update_one({'_id': 'owner'}, {'$set': {'started_at': '2026-10-05T01:00:00+00:00'}})
            elif change == 'status':
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {'raw_by_source.salla_direct.status': 'returned'}})
            else:
                await db[STATES].update_one({'_id': 'owner'}, {'$inc': {'revision': 1}})
            before = await read(db, 'owner')
            response = await client.post(BASE + '/delivery-after-start/confirm', json=body)
            assert response.status_code == 409, response.text
            assert await read(db, 'owner') == before
    run(scenario)


def test_owner_revoked_after_fresh_snapshot_cannot_reach_cas(monkeypatch):
    import operational_delivery_confirmation_service as service
    monkeypatch.setattr(service, 'now', lambda: NOW)
    original = service._preview
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            _, body = await review_and_body(client)
            before = await read(db, 'owner')
            async def revoke(*args, **kwargs):
                result = await original(*args, **kwargs)
                await db.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
                return result
            monkeypatch.setattr(service, '_preview', revoke)
            response = await client.post(BASE + '/delivery-after-start/confirm', json=body)
            assert response.status_code == 403
            assert await read(db, 'owner') == before
    run(scenario)


def test_source_change_after_snapshot_keeps_attestation_pending_and_never_posts_fee(monkeypatch):
    import operational_delivery_confirmation_service as service
    monkeypatch.setattr(service, 'now', lambda: NOW)
    original = service._preview
    async def scenario(db):
        await seed(db, 'native_driver', packed=True)
        async with client_for(db) as client:
            _, body = await review_and_body(client, payload('store_driver'))
            async def changed(*args, **kwargs):
                result = await original(*args, **kwargs)
                await db.store_delivery_assignments.update_one({'user_id': 'owner'}, {'$set': {'driver_id': 'replacement'}})
                return result
            monkeypatch.setattr(service, '_preview', changed)
            response = await client.post(BASE + '/delivery-after-start/confirm', json=body)
            assert response.status_code == 200
            saved = await read(db, 'owner')
            result = await refresh(db, 'owner', clock=LATER)
            assert fee_total(result) == 0 and 'delivery_event:100' not in result['engine']['facts']
            assert result['engine']['facts'][PREFIX + '100'] == saved['engine']['facts'][PREFIX + '100']
            assert result['openings'] == saved['openings'] and result['movements'] == saved['movements']
            assert any(i['code'] == 'operational_delivery_confirmation_source_changed' for i in result['engine']['issues'])
    run(scenario)


@pytest.mark.parametrize('duplicate_owner', ['owner', 'other'])
@pytest.mark.parametrize('introduced_after_preview', [False, True])
def test_duplicate_assignment_identity_is_checked_in_real_preview_and_confirm(duplicate_owner, introduced_after_preview, monkeypatch):
    import operational_delivery_confirmation_service as service
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        data, _ = await seed(db, 'contract_driver', packed=True)
        duplicate = {**deepcopy(data['store_delivery_assignments'][0]), 'user_id': duplicate_owner,
                     'order_id': 'other-order', 'order_number': 'other-number'}
        body = payload('store_driver')
        async with client_for(db) as client:
            if not introduced_after_preview:
                await db.store_delivery_assignments.insert_one(deepcopy(duplicate))
            before = await read(db, 'owner')
            preview_reply = await client.post(BASE + '/delivery-after-start/preview', json=body)
            assert preview_reply.status_code == 200, preview_reply.text
            review = preview_reply.json()
            blocked_preview = duplicate_owner == 'owner' and not introduced_after_preview
            assert review['can_confirm'] is (not blocked_preview)
            if blocked_preview:
                assert {issue['code'] for issue in review['issues']} == {'operational_delivery_assignment_ambiguous'}
            if introduced_after_preview:
                await db.store_delivery_assignments.insert_one(deepcopy(duplicate))
            saved = await client.post(BASE + '/delivery-after-start/confirm', json={**body,
                'request_id': 'duplicate-assignment-check', 'confirmed_after_start': True,
                'expected_preview_hash': review['preview_hash']})
            after = await read(db, 'owner')
            if duplicate_owner == 'owner':
                assert saved.status_code == 409, saved.text
                assert saved.json()['detail']['code'] == 'delivery_confirmation_preview_stale'
                assert after == before and PREFIX + '100' not in after['engine']['facts']
                assert fee_total(after) == 0
            else:
                assert saved.status_code == 200, saved.text
                assert after['engine']['facts'][PREFIX + '100']['party_id'] == 'driver'
                assert after['engine']['obligations'] == before['engine']['obligations']
                assert after['movements'] == before['movements'] and after['openings'] == before['openings']
            assert await db.general_ledger.count_documents({}) == 0
    run(scenario)
