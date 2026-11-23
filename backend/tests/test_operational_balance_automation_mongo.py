"""CI real-Mongo gates for opt-in, distributed fencing and saved-state CAS."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from operational_balance_store import STATES, initial, mutate
from test_qoyod_real_mongo import real_db


@pytest.mark.asyncio
async def test_real_mongo_owner_opt_in_serial_lease_and_single_atomic_result(real_db, monkeypatch):
    import operational_balance_automation as automatic
    import operational_balance_service
    import operational_visible_evidence
    from operational_demand_refresh import LEASES

    db, _ = real_db
    owner = 'synthetic-automation-owner'
    state = initial(owner)
    manual = {'id': 'synthetic-correct-manual-movement', 'amount': '17.50'}
    state.update(status='active', revision=1, movements=[manual],
                 started_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    await db[STATES].insert_one(state)
    await db.users.insert_one({'id': owner, 'role': 'owner', 'is_active': True})
    assert (await automatic.read_status(db, owner))['enabled'] is False
    entered, released = asyncio.Event(), asyncio.Event()
    calls = []

    async def native(database, identity):
        assert database is db and identity == owner
        calls.append('native')
        entered.set()
        await released.wait()
        return {'status': 'completed'}

    async def refresh(database, identity):
        async def apply(value):
            value['engine']['as_of'] = datetime.now(timezone.utc).isoformat()
            return value
        calls.append('refresh')
        return await mutate(database, identity, apply)

    async def authorized():
        pass

    monkeypatch.setattr(operational_visible_evidence, 'acquire_visible_evidence', native)
    monkeypatch.setattr(operational_balance_service, 'refresh', refresh)
    await automatic.set_enabled(db, owner, True, authorized)
    first = asyncio.create_task(automatic.tick(db))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert await automatic.tick(db) == {'outcome': 'busy'}
        lease = await db[LEASES].find_one({'_id': owner})
        assert lease['owner_id'] == owner and lease['actor_id'] == owner
        released.set()
        assert (await asyncio.wait_for(first, 10))['outcome'] == 'success'
    finally:
        released.set()
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
    assert await automatic.tick(db) == {'outcome': 'idle'}
    saved = await db[STATES].find_one({'_id': owner})
    assert saved['revision'] == 2 and saved['movements'] == [manual]
    assert calls == ['native', 'refresh']
    assert await db[LEASES].count_documents({}) == 0
    assert await db[automatic.SERIAL_LEASES].count_documents({}) == 0
    current = await automatic.read_status(db, owner)
    assert current['enabled'] and current['last_outcome'] == 'success'
    assert current['financial_coverage_verified'] is False
    await automatic.set_enabled(db, owner, False, authorized)
    assert await automatic.tick(db) == {'outcome': 'idle'}
    assert (await db[STATES].find_one({'_id': owner}))['revision'] == 2


@pytest.mark.asyncio
async def test_real_mongo_quarantines_only_exact_malformed_config(real_db):
    import operational_balance_automation as automatic
    db, _ = real_db
    await db[automatic.SETTINGS].insert_one({'_id': 'synthetic-invalid',
        'owner_id': 'synthetic-invalid', 'enabled': True,
        'next_due_at': datetime.now(timezone.utc) - timedelta(days=1)})
    assert await automatic.tick(db) == {'outcome': 'ineligible'}
    row = await db[automatic.SETTINGS].find_one({'_id': 'synthetic-invalid'})
    assert row['enabled'] is False
    assert row['last_error_code'] == 'operational_automation_configuration_invalid'
    assert await automatic.tick(db) == {'outcome': 'idle'}
    assert await db[STATES].count_documents({}) == 0
