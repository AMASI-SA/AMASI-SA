"""Real-Mongo approval atomicity; run explicitly with a disposable localhost RS.

Not a mock substitute: absence of the replica set fails this acceptance suite.
"""
import asyncio
from copy import deepcopy
from bson import json_util
from fastapi import FastAPI, HTTPException
import httpx
import pytest

from test_store_delivery_noncash_review import seed, NOW
from operational_balance_store import STATES, mutate
from operational_balance_service import report, create_movement
from operational_driver_review_store import EVENTS, ReviewDatabase
import operational_driver_payment_review as service


def run(fn):
    import os
    from urllib.parse import urlparse, parse_qs
    from uuid import uuid4
    from motor.motor_asyncio import AsyncIOMotorClient
    uri = os.environ.get('MZ2_TEST_MONGO_URI') or os.environ.get('OPERATIONAL_TEST_MONGO_URI', '')
    parsed = urlparse(uri)
    assert parsed.scheme == 'mongodb' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
    assert not parsed.username and not parsed.password and parse_qs(parsed.query).get('replicaSet')
    async def execute():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        database = client['operational_driver_review_test_' + uuid4().hex]
        try:
            assert (await client.admin.command('hello')).get('setName')
            return await fn(database)
        finally:
            await client.drop_database(database.name)
            client.close()
    return asyncio.run(execute())


async def snapshot(db):
    return {name: sorted(json_util.dumps(r, sort_keys=True) for r in await db[name].find({}).to_list(None))
            for name in await db.list_collection_names() if not name.startswith('system.')}


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
def test_real_concurrent_approval_is_exactly_once_and_preserves_old_order_fees(method):
    async def scenario(db):
        assignment, payload = await seed(db, method)
        before = await db[STATES].find_one({'_id': 'owner'})
        results = await asyncio.gather(*(service.review_payment(db, owner='owner', actor='owner',
            assignment_id=assignment, payload=payload, clock=NOW) for _ in range(3)))
        assert results[0] == results[1] == results[2]
        state = await db[STATES].find_one({'_id': 'owner'})
        assert state['openings'] == before['openings']
        assert state['engine']['obligations']['shipping:order-1'] == before['engine']['obligations']['shipping:order-1']
        projection = report(state)
        assert projection['summary']['actual_liquidity'] == ('1200.00' if method == 'bank_transfer' else '1000.00')
        pos = [r for r in projection['parties'] if r['party_type'] == 'pos_receivable']
        assert (pos[0]['outstanding_receivable'] if pos else '0.00') == ('200.00' if method == 'card_terminal' else '0.00')
        driver = next(r for r in projection['parties'] if r['party_type'] == 'store_driver')
        assert driver['outstanding_payable'] == '20.00' and driver['outstanding_receivable'] == '0.00'
        assert len(state.get(service.FACTS, {})) == 1
        assert await db[EVENTS].count_documents({'kind': 'decision'}) == 1
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.mz2_shipping_native_events_v2.count_documents({}) == 0
        assert (await db.store_delivery_assignments.find_one({'id': assignment}))['status'] == 'delivered'
        # Same request after activation does not switch financial backends.
        await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'ledger_backend_state': 'v2_active'}})
        assert await service.review_payment(db, owner='owner', actor='owner', assignment_id=assignment, payload=payload, clock=NOW) == results[0]
        assert await db[STATES].find_one({'_id': 'owner'}) == state
    run(scenario)


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
def test_real_failure_after_metadata_or_money_rolls_back_everything(method, monkeypatch):
    original = ReviewDatabase.__getitem__
    async def scenario(db):
        assignment, payload = await seed(db, method)
        # Precreate the fence and events collection so the exact snapshot is stable.
        await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'revision': 0}}, upsert=True)
        await db.create_collection(EVENTS)
        before = await snapshot(db)
        class RefuseState:
            def __init__(self, target): self.target = target
            def __getattr__(self, name): return getattr(self.target, name)
            async def replace_one(self, *args, **kwargs):
                raise HTTPException(409, detail={'code': 'synthetic_late_failure'})
        def inject(self, name):
            result = original(self, name)
            return RefuseState(result) if name == STATES else result
        monkeypatch.setattr(ReviewDatabase, '__getitem__', inject)
        with pytest.raises(HTTPException):
            await service.review_payment(db, owner='owner', actor='owner', assignment_id=assignment, payload=payload, clock=NOW)
        assert await snapshot(db) == before
    run(scenario)


@pytest.mark.parametrize('case', ['native_phase', 'wrong_scope', 'wrong_revision', 'revoked_actor', 'wrong_pos_amount', 'same_day_bank'])
def test_real_failures_have_no_partial_financial_or_review_effect(case):
    async def scenario(db):
        assignment, payload = await seed(db, 'bank_transfer' if case == 'same_day_bank' else 'card_terminal',
                                          day='2026-10-05' if case == 'same_day_bank' else '2026-10-06')
        await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'revision': 0}}, upsert=True)
        if case == 'native_phase': await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'ledger_backend_state': 'v2_active'}})
        if case == 'wrong_scope': payload.expected_session_scope = 'changed'
        if case == 'wrong_revision': payload.expected_review_revision = 2
        if case == 'revoked_actor': await db.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
        if case == 'wrong_pos_amount': payload.pos_reviewed_amount = '199.99'
        before = await snapshot(db)
        with pytest.raises(HTTPException):
            await service.review_payment(db, owner='owner', actor='owner', assignment_id=assignment, payload=payload, clock=NOW)
        assert await snapshot(db) == before
    run(scenario)


def test_real_pos_settlement_uses_existing_operational_allocation_without_second_driver_cash():
    async def scenario(db):
        assignment, payload = await seed(db)
        result = await service.review_payment(db, owner='owner', actor='owner', assignment_id=assignment, payload=payload, clock=NOW)
        request = {'request_id': 'synthetic-pos-settlement', 'party_type': 'pos_receivable',
                   'party_id': payload.destination_financial_id, 'currency': 'SAR', 'direction': 'incoming',
                   'kind': 'collection', 'amount': '150.00', 'bank_id': 'bank', 'note': 'Synthetic received payment',
                   'allocations': [{'obligation_id': result['obligation_id'], 'amount': '150.00'}]}
        await create_movement(db, 'owner', 'owner', request, clock=NOW)
        state = await db[STATES].find_one({'_id': 'owner'})
        result = report(state)
        assert result['summary']['actual_liquidity'] == '1150.00'
        assert next(r for r in result['parties'] if r['party_type'] == 'pos_receivable')['outstanding_receivable'] == '50.00'
        assert next(r for r in result['parties'] if r['party_type'] == 'store_driver')['outstanding_receivable'] == '0.00'
    run(scenario)


def test_real_http_dispatch_requires_pinned_operational_scope_and_supports_retry(monkeypatch):
    from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        assignment, payload = await seed(db)
        async def actor(): return {'id': 'owner', 'role': 'owner'}
        app = FastAPI(); app.include_router(make_store_delivery_payment_review_router(db, actor))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
            url = '/store-delivery/payment-review/' + assignment
            options = await client.get(url + '/approval-options')
            assert options.status_code == 200 and options.json()['financial_source'] == 'operational'
            assert (await client.post(url, json={'decision': 'approved'})).status_code == 409
            response = await client.post(url, json=payload.model_dump(mode='json'))
            assert response.status_code == 200, response.text
            assert (await client.post(url, json=payload.model_dump(mode='json'))).json() == response.json()
    run(scenario)


def test_real_rejection_has_no_money_and_cannot_be_replayed_as_approval():
    async def scenario(db):
        assignment, approval = await seed(db)
        rejected = approval.model_copy(update={'decision': 'rejected', 'note': 'Receipt is not acceptable'})
        before = await db[STATES].find_one({'_id': 'owner'})
        await service.review_payment(db, owner='owner', actor='owner', assignment_id=assignment, payload=rejected, clock=NOW)
        state = await db[STATES].find_one({'_id': 'owner'})
        assert state['engine'] == before['engine'] and state['movements'] == before['movements']
        with pytest.raises(HTTPException):
            await service.review_payment(db, owner='owner', actor='owner', assignment_id=assignment, payload=approval, clock=NOW)
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)
