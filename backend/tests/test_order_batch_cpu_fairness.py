"""Real order mapping yields between units; transport and CPU clock stay controlled."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from mongomock_motor import AsyncMongoMockClient
import pytest

import dashboard_cpu_budget as cpu_budget
import order_engine.service as service
from order_engine.repository import MongoOrderRepository, OrderDiscoveryRow
from preparation_piece_operations import _assembly_order_board
from test_fulfillment_read_performance import ReadCounter, seed_queue
from test_order_engine_repository import FakeAsyncCursor, FakeCollection
from test_order_engine_service import make_raw


class ControlledCPU:
    """Charge real completed mapper units, independent of wall-clock timing."""
    def __init__(self, monkeypatch):
        self.elapsed = 0.0
        monkeypatch.setattr(cpu_budget, 'time', SimpleNamespace(thread_time=lambda: self.elapsed))

    def charge(self):
        self.elapsed += .006  # One synthetic unit exceeds the existing 5ms parser budget.


class CloseableCursor(FakeAsyncCursor):
    def __init__(self, rows, *, gated_close=False):
        super().__init__(rows)
        self.advances = 0
        self.advance_active = False
        self.close_calls = 0
        self.closed = False
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        if not gated_close:
            self.close_release.set()

    async def __anext__(self):
        self.advance_active = True
        try:
            row = await super().__anext__()
            self.advances += 1
            return row
        finally:
            self.advance_active = False

    async def close(self):
        assert not self.advance_active, 'Never close concurrently with a pending cursor advance'
        self.close_calls += 1
        self.close_started.set()
        await self.close_release.wait()
        self.closed = True


class BufferedCollection(FakeCollection):
    def __init__(self, rows, *, gated_close=False):
        super().__init__(rows)
        self.gated_close = gated_close
        self.cursor = None

    def find(self, query, projection):
        raw = super().find(query, projection)
        self.cursor = CloseableCursor(raw.rows, gated_close=self.gated_close)
        return self.cursor


def database(rows, *, gated_close=False):
    return SimpleNamespace(unified_orders=BufferedCollection(rows, gated_close=gated_close))


def raw_rows(count=100):
    rows = [{'user_id': 'owner', 'order_number': str(index), 'order_date': '2026-11-22',
             'order_status': 'in_progress',
             'raw_by_source': {'salla_direct': make_raw(str(index), '2026-11-22 08:00:00')}}
            for index in range(count)]
    rows[4]['raw_by_source']['salla_direct']['timeline'] = [{'event': 'synthetic-history'}]
    rows[5]['shipping_company'] = 'Synthetic root carrier'
    return rows


def discovery_rows(rows):
    return [MongoOrderRepository._to_discovery_row(row) for row in rows]


class BufferedRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def get_salla_orders(self, *, user_id, order_numbers):
        self.calls.append((user_id, list(order_numbers)))
        return self.rows


def traced(monkeypatch, target, name, clock, calls, heartbeat, *, static=False, cancel=False):
    original = getattr(target, name)

    def transform(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            calls.append(1)
            clock.charge()
            if len(calls) == 1:
                asyncio.get_running_loop().call_soon(lambda: heartbeat.append(len(calls)))
                if cancel:
                    asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
    monkeypatch.setattr(target, name, staticmethod(transform) if static else transform)


def test_repository_yields_between_complete_hydrations_without_changing_rows(monkeypatch):
    async def exercise():
        rows = raw_rows()
        rows.append({**deepcopy(rows[0]), 'user_id': 'other'})
        rows.append({**deepcopy(rows[0]), 'order_date': ''})  # Invalid discovery row still skipped.
        numbers = [row['order_number'] for row in rows]
        baseline_db = database(rows)
        baseline = await MongoOrderRepository(baseline_db).get_salla_orders(user_id='owner', order_numbers=numbers)
        db = database(rows)
        calls, heartbeat = [], []
        clock = ControlledCPU(monkeypatch)
        traced(monkeypatch, MongoOrderRepository, '_to_discovery_row', clock, calls, heartbeat, static=True)
        actual = await MongoOrderRepository(db).get_salla_orders(user_id='owner', order_numbers=numbers)
        await asyncio.sleep(0)
        assert actual == baseline and len(actual) == 100
        assert db.unified_orders.last_query == baseline_db.unified_orders.last_query
        assert db.unified_orders.last_projection == baseline_db.unified_orders.last_projection
        assert db.unified_orders.rows == rows and db.unified_orders.write_calls == 0
        assert heartbeat == [1], heartbeat
    asyncio.run(exercise())


@pytest.mark.parametrize('all_invalid', [False, True])
def test_service_yields_between_real_dtos_preserving_full_output_and_duplicate_rules(monkeypatch, all_invalid):
    async def exercise():
        rows = discovery_rows(raw_rows())
        replacement = deepcopy(rows[0].salla_raw)
        replacement['customer']['full_name'] = 'Last valid duplicate'
        rows.extend([
            OrderDiscoveryRow('0', '2026-11-22', replacement),
            OrderDiscoveryRow('0', '2026-11-22', {}),  # Invalid later duplicate must not erase the valid row.
            OrderDiscoveryRow('invalid', '2026-11-22', {}),
        ])
        if all_invalid:
            rows = [OrderDiscoveryRow(str(index), '2026-11-22', {}) for index in range(100)]
        numbers = [row.order_number for row in rows]
        baseline_repo = BufferedRepository(rows)
        baseline = await service.get_orders(baseline_repo, user_id='owner', order_numbers=numbers)
        repo = BufferedRepository(rows)
        before = deepcopy(rows)
        calls, heartbeat = [], []
        clock = ControlledCPU(monkeypatch)
        traced(monkeypatch, service, '_map_row', clock, calls, heartbeat)
        actual = await service.get_orders(repo, user_id='owner', order_numbers=numbers)
        await asyncio.sleep(0)
        assert {key: dto.model_dump(mode='json') for key, dto in actual.items()} == {
            key: dto.model_dump(mode='json') for key, dto in baseline.items()}
        assert repo.calls == baseline_repo.calls and rows == before
        if not all_invalid:
            assert actual['0'].customer.name == 'Last valid duplicate'
            assert actual['4'].timeline == [{'event': 'synthetic-history'}]
            assert 'invalid' not in actual and len(actual) == 100
        assert heartbeat == [1], heartbeat
    asyncio.run(exercise())


def test_repository_checkpoint_cancellation_closes_cursor_before_return(monkeypatch):
    async def exercise():
        db = database(raw_rows())
        calls, heartbeat = [], []
        clock = ControlledCPU(monkeypatch)
        traced(monkeypatch, MongoOrderRepository, '_to_discovery_row', clock, calls, heartbeat,
               static=True, cancel=True)
        task = asyncio.create_task(MongoOrderRepository(db).get_salla_orders(
            user_id='owner', order_numbers=[str(index) for index in range(100)]))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == db.unified_orders.cursor.advances == 1
        assert db.unified_orders.cursor.closed and db.unified_orders.cursor.close_calls == 1
        assert db.unified_orders.write_calls == 0
    asyncio.run(exercise())


def test_repeated_cancellation_still_joins_checkpoint_cursor_close(monkeypatch):
    async def exercise():
        db = database(raw_rows(), gated_close=True)
        calls, heartbeat = [], []
        clock = ControlledCPU(monkeypatch)
        traced(monkeypatch, MongoOrderRepository, '_to_discovery_row', clock, calls, heartbeat,
               static=True, cancel=True)
        task = asyncio.create_task(MongoOrderRepository(db).get_salla_orders(
            user_id='owner', order_numbers=[str(index) for index in range(100)]))
        await asyncio.sleep(0)
        cursor = db.unified_orders.cursor
        started = asyncio.create_task(cursor.close_started.wait())
        try:
            await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
            assert cursor.close_started.is_set(), 'Cancelled mapping must enter cursor cleanup'
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not cursor.closed
            cursor.close_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cursor.closed and cursor.close_calls == cursor.advances == 1
        finally:
            cursor.close_release.set()
            started.cancel()
            await asyncio.gather(started, task, return_exceptions=True)
    asyncio.run(exercise())


def test_service_checkpoint_cancellation_stops_later_real_mapping(monkeypatch):
    async def exercise():
        repo = BufferedRepository(discovery_rows(raw_rows()))
        calls, heartbeat = [], []
        clock = ControlledCPU(monkeypatch)
        traced(monkeypatch, service, '_map_row', clock, calls, heartbeat, cancel=True)
        task = asyncio.create_task(service.get_orders(
            repo, user_id='owner', order_numbers=[str(index) for index in range(100)]))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1 and len(repo.calls) == 1
    asyncio.run(exercise())


def test_assembly_board_keeps_full_response_and_yields_inside_each_hydration_stage(monkeypatch):
    async def exercise():
        memory = AsyncMongoMockClient()['assembly_cpu_fairness']
        await seed_queue(memory, 'in_progress', 205)
        baseline_db = ReadCounter(memory)
        params = {'user_id': 'owner', 'state': 'in_progress', 'limit': 20, 'offset': 30}
        baseline = await _assembly_order_board(baseline_db, **params)
        before = {name: await memory[name].find({}).to_list(None)
                  for name in await memory.list_collection_names()}
        db = ReadCounter(memory)
        raw_calls, raw_heartbeat, dto_calls, dto_heartbeat = [], [], [], []
        clock = ControlledCPU(monkeypatch)
        traced(monkeypatch, MongoOrderRepository, '_to_discovery_row', clock,
               raw_calls, raw_heartbeat, static=True)
        traced(monkeypatch, service, '_map_row', clock, dto_calls, dto_heartbeat)
        actual = await _assembly_order_board(db, **params)
        assert actual == baseline and actual['total'] == 205
        assert db.reads == baseline_db.reads
        assert before == {name: await memory[name].find({}).to_list(None) for name in before}
        assert raw_heartbeat == dto_heartbeat == [1], (raw_heartbeat, dto_heartbeat)
        assert len(raw_calls) == len(dto_calls) == 205
    asyncio.run(exercise())
