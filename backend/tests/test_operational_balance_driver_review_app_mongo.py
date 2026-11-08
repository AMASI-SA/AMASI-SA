"""Real local replica-set acceptance for app review authority and rollback."""
import pytest

import operational_driver_payment_review as service
from mobile_app_permissions import MOBILE_APP_ACCESS
from operational_balance_store import STATES, digest
from operational_balance_service import report
from operational_driver_review_store import EVENTS, ReviewDatabase
from .test_operational_driver_review_mongo import run, snapshot
from .test_store_delivery_noncash_review import seed, NOW
from .test_operational_balance_driver_review_app_permissions import staff, app_principal, client_for


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
@pytest.mark.parametrize('legacy', [False, True])
def test_real_app_grant_approves_once_as_employee_without_browser_permission(monkeypatch, method, legacy):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        assignment, payload = await seed(db, method)
        actor = await staff(db, legacy=legacy)
        principal = await app_principal(db, actor)
        before = await db[STATES].find_one({'_id': 'owner'})
        url = '/store-delivery/payment-review/' + assignment
        async with client_for(db, principal) as client:
            response = await client.get(url + '/approval-options')
            assert response.status_code == 200, response.text
            options = response.json()
            assert options['session_scope'] == digest(['owner', actor['id']])
            payload.expected_session_scope = options['session_scope']
            first = await client.post(url, json=payload.model_dump(mode='json'))
            assert first.status_code == 200, first.text
            retry = await client.post(url, json=payload.model_dump(mode='json'))
            assert retry.status_code == 200 and retry.json() == first.json()
        state = await db[STATES].find_one({'_id': 'owner'})
        assert state['openings'] == before['openings']
        assert state['engine']['obligations']['shipping:order-1'] == before['engine']['obligations']['shipping:order-1']
        assert report(state)['summary']['actual_liquidity'] == ('1200.00' if method == 'bank_transfer' else '1000.00')
        fact = next(iter(state[service.FACTS].values()))
        assert fact['approved_by'] == actor['id']
        assert fact['destination_type'] == ('bank' if method == 'bank_transfer' else 'pos_receivable')
        saved_review = await db.store_delivery_payment_reviews.find_one({'assignment_id': assignment})
        assert saved_review['reviewed_by'] == actor['id'] and saved_review['status'] == 'approved'
        assert await db[EVENTS].count_documents({'kind': 'decision'}) == 1
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.mz2_shipping_native_events_v2.count_documents({}) == 0
        async with client_for(db, actor) as browser:
            denied = await browser.post(url, json=payload.model_dump(mode='json'))
            assert denied.status_code in {403, 409}
        assert await db[STATES].find_one({'_id': 'owner'}) == state
    run(scenario)


@pytest.mark.parametrize('revoked', ['app_grant', 'legacy_link', 'bank_assignment'])
def test_real_app_revocation_inside_approval_rolls_back_every_effect(monkeypatch, revoked):
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario(db):
        assignment, payload = await seed(db, 'bank_transfer')
        actor = await staff(db, legacy=revoked == 'legacy_link')
        principal = await app_principal(db, actor)
        payload.expected_session_scope = digest(['owner', actor['id']])
        await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'revision': 0}}, upsert=True)
        await db.create_collection(EVENTS)
        before = await snapshot(db)
        original_receipt = service._receipt
        changes = []

        async def revoke_after_transaction_authorizes(scoped, *args):
            result = await original_receipt(scoped, *args)
            if isinstance(scoped, ReviewDatabase) and not changes:
                changes.append(True)
                if revoked == 'app_grant':
                    await db[MOBILE_APP_ACCESS].update_one({'owner_user_id': 'owner', 'user_id': actor['id']},
                                                        {'$set': {'enabled': False}})
                elif revoked == 'bank_assignment':
                    await db[MOBILE_APP_ACCESS].update_one({'owner_user_id': 'owner', 'user_id': actor['id']},
                        {'$set': {'operational_banks': {'bank_ids': []}}})
                else:
                    await db.mezan_employees_v2.delete_one({'user_id': 'owner', 'account_user_id': actor['id']})
            return result

        monkeypatch.setattr(service, '_receipt', revoke_after_transaction_authorizes)
        async with client_for(db, principal) as client:
            response = await client.post('/store-delivery/payment-review/' + assignment,
                                         json=payload.model_dump(mode='json'))
        assert changes == [True]
        assert response.status_code == 403, response.text
        after = await snapshot(db)
        changed_collection = MOBILE_APP_ACCESS if revoked in {'app_grant', 'bank_assignment'} else 'mezan_employees_v2'
        assert {k: v for k, v in after.items() if k != changed_collection} == {
            k: v for k, v in before.items() if k != changed_collection}
        assert (await db.store_delivery_payment_reviews.find_one({'assignment_id': assignment}))['status'] == 'pending'
        assert await db[EVENTS].count_documents({}) == 0
        assert (await db.mz2_daily_movements.find_one({}))['status'] == 'unclassified'
    run(scenario)
