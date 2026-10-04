"""Preserve existing current-carrier dashboard counts; no shipping sync changes.

Only a random database on explicitly supplied loopback Mongo may be written.
The actual dashboard function is AST-extracted without server/bootstrap import.
"""
import os
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from dashboard_summary_fixture import BASE_SHA, extract_dashboard, seed_dashboard
from dashboard_order_reads import dashboard_order_read_scope


@pytest_asyncio.fixture
async def database():
    uri = os.environ['DASHBOARD_TEST_MONGO_URI']
    assert uri.startswith('mongodb://127.0.0.1:')
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = client['dashboard_current_carrier_' + uuid.uuid4().hex]
    await client.admin.command('ping')
    try:
        yield db
    finally:
        await client.drop_database(db.name)
        client.close()


def carrier_counts(response):
    return {row['name']: row['orders_count'] for row in response['shipping_breakdown']}


@pytest.mark.asyncio
async def test_current_carrier_change_preserves_dashboard_counts_across_requests(database):
    await seed_dashboard(database, count=0)
    old_snapshot = {'shipment': {'id': 'fixture-old-imile', 'courier': 'iMile'}}
    await database.unified_orders.insert_many([
        {
            'user_id': 'owner', 'order_number': str(number),
            'order_date': '2026-09-15', 'order_date_inferred': False,
            'order_status': 'completed', 'payment_method': 'mada',
            'shipping_company': 'iMile', 'total_amount': 100, 'currency': 'SAR',
            'shipping_cost': 15, 'data_source': 'salla_direct',
            'total_product_cost': 20,
            'products': [{'product_id': 'p1', 'name': 'Fixture', 'quantity': 1, 'price': 100}],
            'raw_by_source': {'salla_direct': old_snapshot},
        }
        for number in (1, 2)
    ])
    baseline = extract_dashboard(database, revision=BASE_SHA)
    current = extract_dashboard(database)
    kwargs = {
        'user': {'id': 'owner'}, 'from_date': '2026-09-01', 'to_date': '2026-09-30',
        'include_legacy_analyses': False, 'allow_self_heal': False,
    }
    before_baseline = await baseline(**kwargs)
    async with dashboard_order_read_scope(bounded=True):
        before_current = await current(**kwargs)
    assert before_current == before_baseline
    assert carrier_counts(before_current) == {'iMile': 2}
    assert before_current['totals']['total_orders'] == 2

    # Simulate the already-working synchronization result, not a Salla call.
    # This write is exclusively within the random isolated test database.
    changed = await database.unified_orders.update_one(
        {'user_id': 'owner', 'order_number': '1'},
        {'$set': {'shipping_company': 'Store Courier'}},
    )
    assert changed.modified_count == 1
    after_baseline = await baseline(**kwargs)
    async with dashboard_order_read_scope(bounded=True):
        after_current = await current(**kwargs)
    assert after_current == after_baseline
    assert carrier_counts(after_current) == {'Store Courier': 1, 'iMile': 1}
    assert after_current['totals']['total_orders'] == 2
    stored = await database.unified_orders.find_one({'user_id': 'owner', 'order_number': '1'})
    assert stored['raw_by_source']['salla_direct'] == old_snapshot
    assert stored['shipping_company'] == 'Store Courier'

    # A third independent request must not resurrect request-local stale counts.
    async with dashboard_order_read_scope(bounded=True):
        repeated = await current(**kwargs)
    assert carrier_counts(repeated) == carrier_counts(after_current)
    assert await database.unified_orders.count_documents({'user_id': 'owner'}) == 2
