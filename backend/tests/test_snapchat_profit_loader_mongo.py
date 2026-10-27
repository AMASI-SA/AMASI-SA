"""Opt-in real Motor/Mongo contract. Missing explicit test URI skips locally.

CI supplies the URI; ping/driver failures then fail instead of silently skipping.
"""
import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def mongo_db():
    uri = os.environ.get('TEST_SNAP_PROFIT_MONGO_URL')
    if not uri:
        pytest.skip('TEST_SNAP_PROFIT_MONGO_URL is required for real Mongo evidence')
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    await client.admin.command('ping')
    name = 'test_snap_profit_' + uuid4().hex
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_actual_motor_limit_cancel_and_close(mongo_db):
    from dashboard_order_loading import cooperative_order_rows
    await mongo_db.rows.insert_many([{'i': i} for i in range(1025)])
    cursor = mongo_db.rows.find().sort('i', 1)
    rows = await cooperative_order_rows(cursor, 257)
    assert [r['i'] for r in rows] == list(range(257))
    assert not cursor.alive

    read_batch = asyncio.Event()
    class ObservedCursor:
        def __init__(self, actual):
            self.actual, self.count = actual, 0
        def batch_size(self, n):
            self.actual.batch_size(n)
            return self
        def __aiter__(self):
            return self
        async def __anext__(self):
            row = await self.actual.__anext__()
            self.count += 1
            if self.count == 128:
                read_batch.set()
            return row
        async def close(self):
            await self.actual.close()
    actual = mongo_db.rows.find().sort('i', 1)
    observed = ObservedCursor(actual)
    task = asyncio.create_task(cooperative_order_rows(observed, 100000))
    await asyncio.wait_for(read_batch.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert observed.count < 1025 and not actual.alive
    assert await mongo_db.rows.count_documents({}) == 1025


@pytest.mark.asyncio
async def test_real_mongo_minimal_report_loader_cost_context_and_guard(mongo_db, monkeypatch):
    import dashboard_v2_routes as dashboard
    from integrations_control_center import snapchat_campaign_result_source_routes as routes
    from integrations_control_center import snapchat_salla_campaign_outcomes as salla
    from integrations_control_center import snapchat_campaign_profitability as base
    from integrations_control_center import snapchat_campaign_profitability_exact_reuse as exact
    async def settings(*args):
        return {'report_included_statuses': []}
    monkeypatch.setattr(dashboard, 'ensure_user_settings', settings)
    await mongo_db[base.PRODUCTS].insert_one({'user_id': 'u', 'id': 'p', 'salla_product_id': 'p',
        'name': 'item', 'sku': 'sku', 'cost_price_from_salla': 40, 'variants': []})
    await mongo_db.unified_orders.insert_many([{'user_id': 'u', 'order_number': str(i),
        'order_date': '2026-10-25', 'order_status': 'completed', 'currency': 'SAR',
        'total_amount': 100, 'utm_campaign': 'c', 'source': 'snapchat',
        'products': [{'product_id': 'p', 'quantity': 1, 'price': 100}]} for i in range(257)])
    async def minimal_report(db, user_id, **kwargs):
        campaigns, _, _, coverage = await routes._salla_outcomes(db, user_id,
            date_from='2026-10-25', date_to='2026-10-25',
            identities=[{'account_id': 'a', 'campaign_id': 'c'}])
        return {'date_from': '2026-10-25', 'date_to': '2026-10-25', 'result_source': 'salla',
            'source': {'salla_attribution': coverage}, 'totals': {'spend_sar': 10},
            'campaigns': [{'account_id': 'a', 'campaign_id': 'c', 'spend_sar': 10,
                           'salla_results': campaigns[('a', 'c')]}]}
    # monkeypatch records both globals before installers mutate them.
    monkeypatch.setattr(routes, '_salla_outcomes', salla.salla_campaign_outcomes)
    monkeypatch.setattr(routes, 'build_snapchat_result_source_report', minimal_report)
    salla.install_snapchat_salla_campaign_outcomes()
    base.install_snapchat_campaign_profitability()
    exact.install_exact_salla_profitability_reuse()
    result = await routes.build_snapchat_result_source_report(mongo_db, 'u', result_source='salla')
    campaign = result['campaigns'][0]
    assert campaign['salla_results'] == {'orders': 257, 'sales_sar': 25700.0}
    assert campaign['profitability']['sales_sar'] == 25700
    assert campaign['profitability']['product_cost_sar'] == 10280
    assert campaign['profitability']['contribution_profit_sar'] == 15410
    assert result['source']['campaign_profitability']['cache_reuse_enabled'] is False
    assert exact._MATCHED_ORDERS.get() is None
    assert exact._work_limiter()._value == 2
    assert await mongo_db.unified_orders.count_documents({}) == 257
