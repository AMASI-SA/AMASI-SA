"""Real replica-set snapshots and owner CAS for explicit completion review."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources
from operational_balance_store import STATES, initial, read
from operational_completed_shipping_admission import APPROVALS, MARKER
from operational_completed_shipping_service import approve, preview
from test_operational_balance_integration import run
from test_operational_completed_shipping_integration import deferred
from test_operational_balance_prepaid_shipping import fixture
from test_operational_completed_shipping_expansion import without_label
from test_operational_balance_delivery_cutover import START, NOW


async def prepare(db, mode):
    data, state = fixture() if mode == 'prepaid' else deferred(activated=False)
    without_label(data)
    seeded = {**initial('owner'), **state, 'revision': 1}
    await db[STATES].insert_one(seeded)
    for name, docs in data.items():
        if docs:
            await db[name].insert_many(deepcopy(docs))
    return seeded


def consent(result, request_id='completed-review-request'):
    return {'request_id': request_id, 'order_number': result['order_number'],
        'expected_preview_hash': result['preview_hash'], 'expected_session_scope': result['session_scope'],
        'evidence_ref': 'synthetic statement and opening reconciliation', 'confirm_opening_and_payouts': True}


@pytest.mark.parametrize('mode', ['prepaid', 'deferred'])
def test_real_mongo_preview_and_approval_change_no_financial_amount_or_activation(mode):
    async def scenario(db):
        original = await prepare(db, mode)
        result = await preview(db, 'owner', 'owner', '10', clock=NOW)
        assert result['can_approve'] and result['candidate']['amount'] == '34.50'
        assert result['candidate']['payment_mode'] == mode
        assert result['effects']['opening_change'] == '0.00'
        assert result['effects']['cash_outflow'] == ('34.50' if mode == 'prepaid' else '0.00')
        assert result['effects']['courier_payable_increase'] == ('0.00' if mode == 'prepaid' else '34.50')
        assert 'contract_snapshot' not in str(result) and 'contract_evidence' not in str(result)
        assert await read(db, 'owner') == original
        request = consent(result)
        approved = await approve(db, 'owner', 'owner', request, clock=NOW)
        assert approved['pending_reconciliation']
        assert await approve(db, 'owner', 'owner', request, clock=NOW) == approved
        saved = await read(db, 'owner')
        assert len(saved[APPROVALS]) == 1 and MARKER not in saved['engine']
        assert saved['engine'] == original['engine']
        assert saved['movements'] == original['movements'] and saved['openings'] == original['openings']
        assert len(saved['audit']) == 1
        repeated = await preview(db, 'owner', 'owner', '10', clock=NOW)
        assert not repeated['can_approve'] and repeated['already_approved']
        assert repeated['approval_state'] == 'pending_reconciliation'
        sources = await collect_sources(db, 'owner', START, NOW, operational_state=saved)
        calculated = reconcile(saved, sources, NOW)
        key = 'shipping-prepaid:100' if mode == 'prepaid' else 'shipping-completed:100'
        assert calculated['engine']['obligations'][key]['confirmed'] == '34.50'
        assert len(calculated['movements']) == (1 if mode == 'prepaid' else 0)
        assert calculated['openings'] == original['openings']
        assert await db.mz2_shipping_native_events_v2.count_documents({'user_id': 'owner'}) == 0
    run(scenario)


@pytest.mark.parametrize('change', ['opening', 'unallocated_payout', 'native_fee', 'source', 'cutover', 'owner_revoked'])
def test_real_mongo_changed_preview_cannot_release_stale_or_unauthorized_fee(change):
    async def scenario(db):
        original = await prepare(db, 'deferred')
        result = await preview(db, 'owner', 'owner', '10', clock=NOW)
        request = consent(result)
        if change == 'opening':
            await db[STATES].update_one({'_id': 'owner'}, {'$inc': {'revision': 1}, '$set': {'openings.extra': {
                'id': 'extra', 'party_type': 'courier', 'party_id': 'aramex', 'currency': 'SAR',
                'direction': 'for_party', 'amount': '5.00', 'name': 'added opening'}}})
        elif change == 'unallocated_payout':
            await db[STATES].update_one({'_id': 'owner'}, {'$inc': {'revision': 1}, '$push': {'movements': {
                'id': 'unallocated', 'party_type': 'courier', 'party_id': 'aramex', 'currency': 'SAR',
                'direction': 'outgoing', 'kind': 'payment', 'amount': '5.00', 'allocations': []}}})
        elif change == 'native_fee':
            await db.mz2_shipping_native_events_v2.insert_one({'user_id': 'owner', 'kind': 'fee', 'order_number': '10'})
        elif change == 'source':
            await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
                'raw_by_source.salla_direct.status_changed_at': '2026-10-06T10:00:00+00:00'}})
        elif change == 'cutover':
            await db[STATES].update_one({'_id': 'owner'}, {'$inc': {'revision': 1}, '$set': {'status': 'frozen'}})
        else:
            await db.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
        with pytest.raises(HTTPException):
            await approve(db, 'owner', 'owner', request, clock=NOW)
        saved = await read(db, 'owner')
        assert not saved.get(APPROVALS)
        assert saved['engine'] == original['engine']
        assert 'shipping-completed:100' not in saved['engine'].get('obligations', {})
    run(scenario)


def test_real_mongo_concurrent_approvals_and_retry_record_one_authorization():
    async def scenario(db):
        await prepare(db, 'deferred')
        result = await preview(db, 'owner', 'owner', '10', clock=NOW)
        request = consent(result)
        responses = await asyncio.gather(*(approve(db, 'owner', 'owner', request, clock=NOW) for _ in range(2)))
        assert responses[0] == responses[1]
        saved = await read(db, 'owner')
        assert len(saved[APPROVALS]) == len(saved['audit']) == 1
        assert saved['movements'] == [] and MARKER not in saved['engine']
    run(scenario)


def test_real_mongo_http_staff_and_other_owner_cannot_review_or_approve():
    from operational_balance_routes import make_operational_balance_router
    async def scenario(db):
        await prepare(db, 'deferred')
        app = FastAPI()
        async def current_user(x_test_user: str = Header(default='owner')):
            return {'id': x_test_user, '_session_client': 'web'}
        app.include_router(make_operational_balance_router(db, current_user), prefix='/api')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            path = '/api/operational-balances/completed-shipping-fees'
            for actor in ('staff', 'viewer'):
                denied = await client.post(path + '/preview', json={'order_number': '10'}, headers={'x-test-user': actor})
                assert denied.status_code == 403 and 'candidate' not in denied.json()
            denied = await client.post(path + '/preview', json={'order_number': '10'}, headers={'x-test-user': 'other'})
            assert denied.status_code in {403, 409} and 'candidate' not in denied.json()
            # No shortcut accepts just a number or a forged role from the body.
            denied = await client.post(path + '/approve', json={'order_number': '10', 'role': 'owner'})
            assert denied.status_code == 422
            assert not (await read(db, 'owner')).get(APPROVALS)
    run(scenario)
