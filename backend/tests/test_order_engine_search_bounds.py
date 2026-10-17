"""Search regressions, hard resource budgets, and request fairness."""
import asyncio
from types import SimpleNamespace
import pytest
from order_engine import search as module
from order_engine.search import search_orders, OrderSearchError
from order_engine.repository import OrderDiscoveryRow, MongoOrderRepository


def row(number='1', product='123456', campaign='12345678901234567890'):
    return OrderDiscoveryRow(number, '2026-10-17', {
        'id': number, 'reference_id': number, 'created_at': '2026-10-17T10:00:00Z',
        'status': {'slug': 'completed'}, 'utm_campaign': campaign, 'utm_source': 'Snapchat',
        'items': [{'quantity': 1, 'product': {'id': product, 'name': 'منتج اختباري', 'sku': 'AMS-TEST'}}],
    })


class Stream:
    def __init__(self, rows): self.rows, self.closed = rows, False
    async def iter_salla_search_orders(self, **kwargs):
        self.kwargs = kwargs
        try:
            for value in self.rows: yield value
        finally: self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('filters', [{'q': '123456'}, {'product_id': '123456'},
    {'sku': 'AMS-TEST'}, {'campaign_id': '12345678901234567890'}])
async def test_product_ids_skus_and_utm_campaign_identity_match(filters):
    result = await search_orders(Stream([row()]), user_id='owner', filters=filters)
    assert [item.order_number for item in result.items] == ['1']


@pytest.mark.asyncio
async def test_identifiers_are_exact_not_partial_and_parent_variants_work():
    source = row()
    source.salla_raw['items'][0]['product']['parent_id'] = '987'
    source.salla_raw['items'][0]['variant_id'] = '654'
    for product in ('987', '654'):
        assert len((await search_orders(Stream([source]), user_id='owner', filters={'product_id': product})).items) == 1
    for filters in ({'product_id': '123'}, {'campaign_id': '123456'}):
        assert not (await search_orders(Stream([source]), user_id='owner', filters=filters)).items


@pytest.mark.asyncio
async def test_complete_summary_and_paging_do_not_drop_result_51():
    repo = Stream([row(str(index)) for index in range(61)])
    filters = {'sku': 'AMS-TEST'}
    first = await search_orders(repo, user_id='owner', filters=filters)
    second = await search_orders(repo, user_id='owner', filters=filters, cursor=first.next_cursor)
    assert len(first.items) == 50 and len(second.items) == 11
    assert len({item.order_number for item in first.items + second.items}) == 61
    assert first.summary['orders'] == second.summary['orders'] == 61
    assert second.next_cursor is None
    for tenant, changed in [('other', filters), ('owner', {'sku': 'other'})]:
        with pytest.raises(OrderSearchError, match='تغيّرت'):
            await search_orders(repo, user_id=tenant, filters=changed, cursor=first.next_cursor)


@pytest.mark.asyncio
async def test_limit_rejects_incomplete_results_and_closes_cursor(monkeypatch):
    monkeypatch.setattr(module, 'SCAN_LIMIT', 20)
    repo = Stream([row(str(index)) for index in range(21)])
    with pytest.raises(OrderSearchError) as exc:
        await search_orders(repo, user_id='owner', filters={'q': 'absent'})
    assert exc.value.code == 'search_period_too_broad'
    assert repo.closed and not module._SEARCH_SLOT.locked()


@pytest.mark.asyncio
async def test_timeout_and_concurrent_search_fail_fast_without_holding_slot(monkeypatch):
    started = asyncio.Event()
    class Slow(Stream):
        async def iter_salla_search_orders(self, **kwargs):
            try:
                started.set()
                await asyncio.sleep(100)
                yield row()
            finally: self.closed = True
    repo = Slow([])
    monkeypatch.setattr(module, 'SEARCH_TIMEOUT_SECONDS', 0.02)
    pending = asyncio.create_task(search_orders(repo, user_id='owner', filters={'q': 'x'}))
    await started.wait()
    with pytest.raises(OrderSearchError) as busy:
        await search_orders(Stream([]), user_id='owner', filters={})
    assert busy.value.code == 'search_busy' and busy.value.status_code == 429
    with pytest.raises(OrderSearchError) as timeout:
        await pending
    assert timeout.value.code == 'search_timeout'
    assert repo.closed and not module._SEARCH_SLOT.locked()


@pytest.mark.asyncio
async def test_search_yields_to_other_requests_before_completing():
    ticks = 0
    finished = False
    async def other_request():
        nonlocal ticks
        while not finished:
            ticks += 1
            await asyncio.sleep(0)
    other = asyncio.create_task(other_request())
    result = await search_orders(Stream([row(str(index)) for index in range(150)]), user_id='owner', filters={})
    finished = True
    await other
    assert result.summary['orders'] == 150 and ticks >= 10


@pytest.mark.asyncio
async def test_invalid_dates_fail_before_read_and_final_day_is_inclusive():
    repo = Stream([row()])
    for filters in ({'created_from': 'bad'}, {'created_from': '2026-10-18', 'created_to': '2026-10-17'}):
        with pytest.raises(OrderSearchError) as exc:
            await search_orders(repo, user_id='owner', filters=filters)
        assert exc.value.code == 'invalid_search_period'
    assert not hasattr(repo, 'kwargs')
    result = await search_orders(repo, user_id='owner', filters={
        'created_from': '2026-10-17T00:00:00+03:00', 'created_to': '2026-10-18T00:00:00+03:00',
    })
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_mongo_reader_uses_existing_index_small_batches_timeout_and_no_writes():
    class Cursor:
        def __init__(self): self.options = {}; self.closed = False
        def hint(self, value): self.options['hint'] = value; return self
        def sort(self, value): self.options['sort'] = value; return self
        def limit(self, value): self.options['limit'] = value; return self
        def batch_size(self, value): self.options['batch'] = value; return self
        def max_time_ms(self, value): self.options['timeout'] = value; return self
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
        async def close(self): self.closed = True
    class Collection:
        def find(self, query, projection): self.query = query; return cursor
    cursor, collection = Cursor(), Collection()
    repo = MongoOrderRepository(SimpleNamespace(unified_orders=collection))
    result = await search_orders(repo, user_id='owner', filters={'product_id': '123456', 'created_from': '2026-10-17T00:00:00+03:00'})
    assert result.items == []
    assert collection.query['user_id'] == 'owner'
    assert collection.query['order_date']['$gte'] == '2026-10-15'
    assert {'raw_by_source.salla_direct.items.product.id': {'$in': ['123456', 123456]}} in collection.query['$or']
    assert cursor.options == {'hint': [('user_id', 1), ('order_date', -1)], 'sort': [('order_date', -1)],
                              'limit': 5001, 'batch': 100, 'timeout': 1500}
    assert cursor.closed
