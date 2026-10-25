"""Disposable Mongo integration; never uses the application's Mongo URL."""
import os
from uuid import uuid4
from types import SimpleNamespace
import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from order_engine.repository import MongoOrderRepository
from order_engine.search import search_orders
from tests.test_order_engine_search_bounds import row


@pytest.mark.asyncio
async def test_real_index_old_period_exact_ids_and_read_only_search():
    uri = os.environ.get('ORDER_SEARCH_TEST_MONGO_URI')
    if not uri:
        pytest.skip('isolated Mongo service not configured')
    # The workflow supplies a local disposable Mongo service, never production.
    assert uri in ('mongodb://127.0.0.1:27017', 'mongodb://localhost:27017')
    name = 'orders_search_test_' + uuid4().hex
    sync = MongoClient(uri, serverSelectionTimeoutMS=3000)
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    try:
        collection = sync[name].unified_orders
        collection.create_index([('user_id', 1), ('order_date', -1)])
        docs = []
        for i in range(6020):
            old = i >= 6000
            source = row(str(i), product='123456' if old else 'bulk').salla_raw
            if old: source['created_at'] = '2026-01-01T10:00:00Z'
            docs.append({'user_id': 'owner', 'order_number': str(i),
                'order_date': '2026-01-01' if old else '2026-10-17',
                'raw_by_source': {'salla_direct': source}})
        # Same identifiers in a different tenant must never be returned.
        docs.append({**docs[-1], 'user_id': 'other'})
        collection.insert_many(docs)
        class RecordingCollection:
            def find(self, query, projection):
                self.query = query
                return client[name].unified_orders.find(query, projection)
        recording = RecordingCollection()
        repository = MongoOrderRepository(SimpleNamespace(unified_orders=recording))
        filters = {'campaign_id': '12345678901234567890', 'created_from': '2026-01-01T00:00:00+03:00',
                   'created_to': '2026-01-02T00:00:00+03:00'}
        result = await search_orders(repository, user_id='owner', filters=filters)
        assert len(result.items) == result.summary['orders'] == 20
        assert {item.order_number for item in result.items} == {str(i) for i in range(6000, 6020)}
        plan = collection.find(recording.query).hint([('user_id', 1), ('order_date', -1)]).sort('order_date', -1).explain()
        assert plan['executionStats']['totalDocsExamined'] <= 20
        assert 'IXSCAN' in str(plan['queryPlanner']['winningPlan'])
        assert 'COLLSCAN' not in str(plan['queryPlanner']['winningPlan'])
        # Exercise the exact product selector on real Mongo array fields.
        selected = await search_orders(repository, user_id='owner', filters={'product_id': '123456'})
        assert len(selected.items) == 20
        assert collection.count_documents({}) == 6021
        assert collection.find_one({'order_number': '6019', 'user_id': 'owner'})['raw_by_source']['salla_direct'] == docs[6019]['raw_by_source']['salla_direct']
        print({'date_search_docs_examined': plan['executionStats']['totalDocsExamined'], 'fixture_orders': 6021,
               'matched_old_orders': result.summary['orders'], 'query_ms': plan['executionStats']['executionTimeMillis']})
    finally:
        client.close()
        sync.drop_database(name)
        sync.close()
