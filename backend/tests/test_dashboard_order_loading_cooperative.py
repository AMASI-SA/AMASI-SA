"""Real loader/hydration contracts, using only in-memory Mongo cursor transport."""
from copy import deepcopy
from types import SimpleNamespace
import asyncio
import pytest
import dashboard_v2_routes as dashboard
from dashboard_order_loading import cooperative_order_rows


class Cursor:
    def __init__(self, rows):
        self.rows, self.index, self.closed, self.batch = deepcopy(rows), 0, False, None
    def batch_size(self, value):
        self.batch = value
        return self
    def __aiter__(self):
        return self
    async def __anext__(self):
        if self.index == len(self.rows):
            raise StopAsyncIteration
        row = self.rows[self.index]
        self.index += 1
        return row
    async def to_list(self, length):
        return self.rows[:length]
    async def close(self):
        self.closed = True


class Orders:
    def __init__(self, rows, projections):
        self.rows, self.projections, self.cursors, self.queries = rows, projections, [], []
    def find(self, query, projection):
        self.queries.append((deepcopy(query), deepcopy(projection)))
        cursor = Cursor(self.rows if projection == {'_id': 0, 'raw_by_source': 0} else self.projections)
        self.cursors.append(cursor)
        return cursor


@pytest.mark.asyncio
async def test_real_loader_matches_original_duplicate_projection_and_filters(monkeypatch):
    async def settings(*args):
        return {'report_included_statuses': ['completed'], 'hide_inferred_date_orders': True}
    monkeypatch.setattr(dashboard, 'ensure_user_settings', settings)
    rows = [{'order_number': str(i % 17), 'currency': 'SAR', 'total_amount': i,
             'order_status': 'completed' if i % 3 else 'cancelled',
             'payment_method': 'mada' if i % 2 else 'visa', 'shipping_company': 'smsa'}
            for i in range(1000)]
    projected = [{'order_number': str(i), 'raw_by_source': {'salla_direct': {
        'currency': 'USD', 'exchange_rate': 3.75, 'total': {'amount': 100, 'currency': 'USD'},
        'utm_campaign': 'old'}}} for i in range(17)]
    # Currency and attribution intentionally have different last-wins semantics.
    projected += [{'order_number': '1', 'raw_by_source': {'salla_direct': {'utm_campaign': 'new'}}},
                  {'order_number': '1'}, {'order_number': '', 'raw_by_source': {}}]
    async def run(cooperative):
        orders = Orders(rows, projected)
        result = await dashboard._filtered_orders(SimpleNamespace(unified_orders=orders), 'u',
            from_date='2026-10-01', to_date='2026-10-27', payment_methods='mada',
            shipping_companies='smsa', include_marketing_attribution=True, cooperative=cooperative)
        return result, orders
    old, old_db = await run(False)
    current, current_db = await run(True)
    assert current == old
    assert current_db.queries == old_db.queries
    assert current and all(o['order_status'] == 'completed' for o in current)
    assert all(c.closed and c.batch == 128 for c in current_db.cursors)
    assert next(o for o in current if o['order_number'] == '1')['raw_by_source']['salla_direct']['utm_campaign'] == 'new'


@pytest.mark.asyncio
async def test_cursor_limit_and_cancellation_close_without_consuming_more():
    cursor = Cursor([{'id': i} for i in range(1001)])
    assert len(await cooperative_order_rows(cursor, 257)) == 257
    assert cursor.index == 257 and cursor.closed and cursor.batch == 128
    cursor = Cursor([{'id': i} for i in range(20000)])
    task = asyncio.create_task(cooperative_order_rows(cursor, 100000))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cursor.index == 128 and cursor.closed


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_cursor_close():
    cursor = Cursor([{} for _ in range(20000)])
    closing, finish = asyncio.Event(), asyncio.Event()
    async def close():
        closing.set()
        await finish.wait()
        cursor.closed = True
    cursor.close = close
    task = asyncio.create_task(cooperative_order_rows(cursor, 100000))
    await asyncio.sleep(0)
    task.cancel()
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not cursor.closed
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cursor.closed


@pytest.mark.asyncio
async def test_real_loader_20k_heartbeat_concurrency_and_unchanged_results(monkeypatch):
    import time
    async def settings(*args):
        return {'report_included_statuses': []}
    monkeypatch.setattr(dashboard, 'ensure_user_settings', settings)
    rows = [{'order_number': str(i), 'currency': 'SAR', 'total_amount': 100,
             'order_status': 'completed', 'payment_method': 'mada'} for i in range(20000)]
    projections = [{'order_number': str(i), 'raw_by_source': {'salla_direct': {
        'utm_campaign': 'c', 'currency': 'SAR', 'total': {'amount': 100, 'currency': 'SAR'}}}}
                   for i in range(20000)]
    class BorrowedOrders(Orders):
        def find(self, query, projection):
            cursor = Cursor([])
            cursor.rows = self.rows if projection == {'_id': 0, 'raw_by_source': 0} else self.projections
            self.cursors.append(cursor)
            return cursor
    async def run(cooperative, count):
        dbs = [SimpleNamespace(unified_orders=BorrowedOrders(deepcopy(rows), deepcopy(projections)))
               for _ in range(count)]
        stamps, done = [], asyncio.Event()
        async def heartbeat():
            while not done.is_set():
                stamps.append(time.perf_counter())
                await asyncio.sleep(0)
        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        start = time.perf_counter()
        try:
            results = await asyncio.gather(*(dashboard._filtered_orders(db, 'u', from_date=None,
                to_date=None, payment_methods=None, shipping_companies=None,
                include_marketing_attribution=True, cooperative=cooperative) for db in dbs))
        finally:
            done.set()
            await beat
        stamps.append(time.perf_counter())
        return results, len(stamps), max(b-a for a,b in zip(stamps, stamps[1:])), time.perf_counter()-start
    baseline, _, old_gap, old_wall = await run(False, 1)
    current, turns, gap, wall = await run(True, 2)
    assert current[0] == current[1] == baseline[0]
    assert turns >= 150
    print(f'20k loader: legacy wall={old_wall:.3f}s gap={old_gap:.3f}s; '
          f'concurrent2 cooperative wall={wall:.3f}s gap={gap:.3f}s turns={turns}')


@pytest.mark.asyncio
async def test_prepare_cancellation_stops_before_next_chunk(monkeypatch):
    async def settings(*args):
        return {}
    monkeypatch.setattr(dashboard, 'ensure_user_settings', settings)
    rows = [{'order_number': str(i), 'currency': 'SAR', 'total_amount': 10} for i in range(1000)]
    collection = Orders(rows, [])
    hydrate = dashboard.hydrate_order_currency_fields
    processed = []
    task = None
    def cancel_after_chunk(chunk, projections):
        processed.extend(chunk)
        hydrate(chunk, projections)
        asyncio.get_running_loop().call_soon(task.cancel)
    monkeypatch.setattr(dashboard, 'hydrate_order_currency_fields', cancel_after_chunk)
    task = asyncio.create_task(dashboard._filtered_orders(SimpleNamespace(unified_orders=collection),
        'u', from_date=None, to_date=None, payment_methods=None, shipping_companies=None, cooperative=True))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(processed) == 128
    assert all(c.closed for c in collection.cursors)


@pytest.mark.asyncio
async def test_cursor_error_closes_before_propagating():
    class FailedCursor(Cursor):
        async def __anext__(self):
            raise RuntimeError('transport failed')
    cursor = FailedCursor([])
    with pytest.raises(RuntimeError, match='transport failed'):
        await cooperative_order_rows(cursor, 100000)
    assert cursor.closed
