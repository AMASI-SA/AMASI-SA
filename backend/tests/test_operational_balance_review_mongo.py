"""Real transaction coverage for the workspace's scoped review callback."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from .test_operational_driver_review_mongo import run
from .test_store_delivery_noncash_review import seed, NOW
from operational_balance_routes import make_operational_balance_router
from operational_balance_service import report
from operational_balance_store import STATES, digest
from operational_driver_review_store import EVENTS, ReviewDatabase
import operational_driver_payment_review as service


async def scoped_staff(db, destination, kind):
    policy = {'owner_id': 'owner', 'version': 1, 'enabled': True, 'operations': ['courier.review'],
        'resources': [
            {'kind': 'store_driver', 'id': 'driver', 'use': False, 'movements': 'all', 'balance': False},
            {'kind': 'bank', 'id': 'bank', 'use': True, 'movements': 'all', 'balance': False},
        ]}
    if kind == 'pos_receivable':
        policy['resources'].append({'kind': kind, 'id': destination, 'use': True, 'movements': 'none', 'balance': False})
    await db.users.insert_one({'id': 'staff', 'name': 'Synthetic reviewer', 'role': 'employee',
        'created_by': 'owner', 'is_active': True, 'operational_balance_access': policy})
    return policy


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
def test_workspace_scoped_staff_real_approval_is_once_without_courier_credit(method, monkeypatch):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        assignment, payload = await seed(db, method)
        await scoped_staff(db, payload.destination_financial_id, 'bank' if method == 'bank_transfer' else 'pos_receivable')
        payload.expected_session_scope = digest(['owner', 'staff'])
        async def principal(): return {'id': 'staff'}
        app = FastAPI(); app.include_router(make_operational_balance_router(db, principal), prefix='/api')
        before = await db[STATES].find_one({'_id': 'owner'})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
            path = '/api/operational-balances/workspace/reviews/' + assignment
            options = await client.get(path + '/options')
            assert options.status_code == 200, options.text
            assert options.json()['can_approve']
            response = await client.post(path, json=payload.model_dump(mode='json'))
            assert response.status_code == 200, response.text
            assert (await client.post(path, json=payload.model_dump(mode='json'))).json() == response.json()
            detail = (await client.get(path)).json()
            assert detail['status'] == 'approved' and detail['reviewed_by_name'] == 'Synthetic reviewer'
        saved = await db[STATES].find_one({'_id': 'owner'})
        assert saved['openings'] == before['openings']
        driver = next(row for row in report(saved)['parties'] if row['party_type'] == 'store_driver')
        assert driver['outstanding_payable'] == '20.00' and driver['outstanding_receivable'] == '0.00'
        assert await db[EVENTS].count_documents({'kind': 'decision'}) == 1
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


def test_workspace_permission_revocation_before_commit_rolls_back_real_money(monkeypatch):
    from operational_balance_reviews import review_access
    original_get = ReviewDatabase.__getitem__
    async def scenario(db):
        assignment, payload = await seed(db, 'bank_transfer')
        policy = await scoped_staff(db, 'bank', 'bank')
        payload.expected_session_scope = digest(['owner', 'staff'])
        before = await db[STATES].find_one({'_id': 'owner'})
        async def authorize(database):
            await review_access(database, {'id': 'staff'}, expected_owner='owner',
                assignment_id=assignment, destination='bank')
        class RevokeBeforeCommit:
            def __init__(self, target): self.target = target
            def __getattr__(self, name): return getattr(self.target, name)
            async def replace_one(self, *args, **kwargs):
                result = await self.target.replace_one(*args, **kwargs)
                revoked = deepcopy(policy); revoked.update(version=2, operations=[])
                await db.users.update_one({'id': 'staff'}, {'$set': {'operational_balance_access': revoked}})
                return result
        def get_collection(self, name):
            result = original_get(self, name)
            return RevokeBeforeCommit(result) if name == STATES else result
        monkeypatch.setattr(ReviewDatabase, '__getitem__', get_collection)
        with pytest.raises(HTTPException) as error:
            await service.review_payment(db, owner='owner', actor='staff', assignment_id=assignment,
                payload=payload, clock=NOW, authorize_review=authorize)
        assert error.value.status_code == 403
        assert await db[STATES].find_one({'_id': 'owner'}) == before
        assert (await db[service.REVIEWS].find_one({'assignment_id': assignment}))['status'] == 'pending'
        assert (await db.mz2_daily_movements.find_one({'id': payload.settlement_reference}))['status'] == 'unclassified'
        assert await db[EVENTS].count_documents({'kind': 'decision'}) == 0
    run(scenario)
