import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from operational_aramex_owner_reconciliation import reconcile_selected
from operational_balance_store import STATES, digest, initial, read
from operational_balance_service import report
from test_operational_balance_prepaid_shipping import fixture, NOW


@pytest.mark.parametrize('status', ['تم التنفيذ', 'جاري التوصيل', 'تم التوصيل'])
def test_owner_selected_aramex_orders_are_applied_once_even_below_zero(status, monkeypatch):
    monkeypatch.setattr('operational_aramex_owner_reconciliation.now', lambda: NOW)

    async def run():
        db = AsyncMongoMockClient()['aramex_synthetic']
        data, state = fixture(old=False, current=status, at=None)
        owner = 'owner'
        await db[STATES].insert_one({**initial(owner), **state, 'revision': 1})
        await db.mz2_financial_accounts.insert_many(deepcopy(data['mz2_financial_accounts']))
        source = deepcopy(data['unified_orders'][0])
        source['g47_salla_snapshot'] = {'component_pending': True}
        await db.unified_orders.insert_one(source)
        scope = digest([owner, owner])

        async def action(numbers=('10',), **kw):
            return await reconcile_selected(db, owner, owner, numbers, scope, **kw)

        preview = await action()
        assert (preview['count'], preview['existing'], preview['total'], preview['skipped']) == (1, 0, '34.50', [])
        assert not (await read(db, owner))['movements']
        with pytest.raises(HTTPException):
            await action(preview_hash='0' * 64, request_id='tampered')
        with pytest.raises(HTTPException):
            await reconcile_selected(db, owner, 'staff', ['10'], scope)
        args = dict(preview_hash=preview['preview_hash'], request_id='owner-selection')
        result = await action(**args)
        assert result['applied'] and await action(**args) == result
        persisted = await read(db, owner)
        movement, = persisted['movements']
        assert movement['bank_id'] == 'salla-cash' and movement['amount'] == '34.50'
        assert movement['completed_at'] is None and movement['tariff_version_id'] == 'approved-aramex'
        assert movement['allocations'] == [{'obligation_id': 'shipping-prepaid:100', 'amount': '34.50'}]
        assert persisted['aramex_order_status_rule']['approved_by'] == owner and not persisted['openings']
        assert next(row for row in report(persisted)['parties'] if row['party_type'] == 'cash')['actual'] == '-34.50'
        repeated = await action()
        assert (repeated['count'], repeated['existing'], repeated['total']) == (0, 1, '0.00')

    asyncio.run(run())


@pytest.mark.parametrize('change', ['other_carrier', 'old', 'pending', 'foreign', 'changed_contract'])
def test_ineligible_or_unassigned_candidates_remain_unposted(change):
    async def run():
        db = AsyncMongoMockClient()['aramex_synthetic_hold']
        data, state = fixture(old=change == 'old', current='pending' if change == 'pending' else 'delivered', at=None)
        source = data['unified_orders'][0]
        if change == 'other_carrier':
            source['salla_shipping_current'].update(company_name='SMSA', company_code='smsa')
        if change == 'foreign':
            source['user_id'] = 'different'
        if change == 'changed_contract':
            state['courier_tariffs'][0]['effective_from'] = NOW
        await db[STATES].insert_one({**initial('owner'), **state, 'revision': 1})
        await db.mz2_financial_accounts.insert_many(data['mz2_financial_accounts'])
        await db.unified_orders.insert_one(source)
        result = await reconcile_selected(db, 'owner', 'owner', ['10'], digest(['owner', 'owner']))
        assert result['count'] == 0 and result['skipped'][0][0] == '10'
        assert not (await read(db, 'owner'))['movements']

    asyncio.run(run())
