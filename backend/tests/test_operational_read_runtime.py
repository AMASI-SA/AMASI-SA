"""Bounded read concurrency, snapshot isolation and cancellation guarantees."""
import asyncio
from collections import deque
from copy import deepcopy
import threading
import time
from types import SimpleNamespace
import struct

from bson import BSON
from motor.core import AgnosticBaseCursor
from motor.frameworks import asyncio as motor_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCursor
import pymongo
from pymongo.errors import ExecutionTimeout, NetworkTimeout, OperationFailure
import pytest

import operational_read_runtime as runtime
from operational_read_runtime import (
    SourceReadError, SnapshotReadCache, load_collections, read_source_cursor, source_read_budget,
)


class ThreadedMotorCursor(AgnosticBaseCursor):
    """Real Motor iteration/to_list scheduling around a controlled read worker.

    This boundary deliberately differs from an asyncio Event-only fake: the
    active executor operation cannot be cancelled by cancelling to_list(). No
    database, socket, provider, or production configuration is used.
    """
    _framework = motor_asyncio

    def __init__(self, batches, *, block=True, block_close=False):
        cursor = self
        class Delegate:
            def __init__(self):
                self._data = deque()

            def __next__(self):
                cursor.consumed += 1
                return self._data.popleft()

        super().__init__(Delegate(), None)
        self.loop = asyncio.get_running_loop()
        self.batches, self.index = batches, 0
        self.entered = asyncio.Event()
        self.release, self.worker_finished = threading.Event(), threading.Event()
        if not block:
            self.release.set()
        self.operations, self.driver_budgets = [], []
        self.close_calls, self.closed_during_read = 0, False
        self.consumed = 0
        self.close_entered, self.close_release = asyncio.Event(), asyncio.Event()
        if not block_close:
            self.close_release.set()

    def get_io_loop(self):
        return self.loop

    @property
    def alive(self):
        return not self.closed and (bool(self.delegate._data) or self.index < len(self.batches))

    def _data(self):
        return self.delegate._data

    def _query_flags(self):
        return 0

    def _refresh(self):
        self.worker_finished.clear()
        def read_batch():
            self.driver_budgets.append(pymongo._csot.remaining())
            self.loop.call_soon_threadsafe(self.entered.set)
            try:
                if not self.release.wait(2):
                    raise AssertionError('test did not release its owned read worker')
                self.delegate._data.extend(self.batches[self.index])
                self.index += 1
                return len(self.delegate._data)
            finally:
                self.worker_finished.set()
        future = motor_asyncio.run_on_executor(self.loop, read_batch)
        self.operations.append(future)
        return future

    async def _async_close(self):
        self.close_calls += 1
        self.closed_during_read = any(not operation.done() for operation in self.operations)
        self.close_entered.set()
        await self.close_release.wait()


class MotorSourceDB:
    def __init__(self, cursor, *, collection='unified_orders'):
        self.cursor, self.queries = cursor, []
        self.collection = collection

    def __getitem__(self, name):
        assert name == self.collection
        return self

    def find(self, query, projection=None):
        self.queries.append((deepcopy(query), deepcopy(projection)))
        return self.cursor


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['caller_cancel', 'reader_deadline'])
@pytest.mark.parametrize('reader_name', ['refresh_source_rows', 'rows'])
async def test_actual_motor_read_worker_is_joined_before_source_reader_returns(stop, reader_name):
    import operational_balance_sources as sources
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}], [{'user_id': 'owner'}]])
    database = MotorSourceDB(cursor)
    task = asyncio.create_task(load_collections(database, 'owner', ['unified_orders'],
        reader=getattr(sources, reader_name), per_read_timeout=.03 if stop == 'reader_deadline' else 1))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        if stop == 'caller_cancel':
            task.cancel()
            for _ in range(12):
                await asyncio.sleep(0)
        else:
            await asyncio.sleep(.05)
        # On the old implementation the caller has returned, but Motor's
        # independent getMore Future and its worker are still running.
        assert not cursor.worker_finished.is_set()
        assert not task.done(), 'source reader returned before its Motor read worker finished'
        cursor.release.set()
        if stop == 'caller_cancel':
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(SourceReadError) as failure:
                await task
            assert (failure.value.code, failure.value.collection) == (
                'operational_source_read_timeout', 'unified_orders')
        assert cursor.worker_finished.is_set()
        assert all(operation.done() for operation in cursor.operations)
        assert len(cursor.operations) == 1  # No second getMore after cancellation.
        assert cursor.close_calls == 1 and not cursor.closed_during_read
        assert database.queries[0][0] == {'user_id': 'owner'}
        assert (database.queries[0][1] is None) == (reader_name == 'rows')
        assert len(database.queries) == 1  # No replay while draining.
    finally:
        cursor.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*cursor.operations, return_exceptions=True)


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_snapshot_owned_through_read_and_sequential_close():
    from operational_balance_sources import refresh_source_rows
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}], [{'user_id': 'owner'}]], block_close=True)
    database = MotorSourceDB(cursor)
    database.session = object()
    second_started = False

    async def reader(db, owner, name):
        nonlocal second_started
        if name == 'queued':
            second_started = True
            return []
        return await refresh_source_rows(db, owner, name)

    task = asyncio.create_task(load_collections(database, 'owner', ['unified_orders', 'queued'], reader=reader))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        for _ in range(3):
            task.cancel()
            for _ in range(12):
                await asyncio.sleep(0)
            assert not task.done() and not second_started
        cursor.release.set()
        await asyncio.wait_for(cursor.close_entered.wait(), 1)
        task.cancel()
        for _ in range(12):
            await asyncio.sleep(0)
        assert not task.done() and not second_started
        assert all(operation.done() for operation in cursor.operations)
        cursor.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(cursor.operations) == len(database.queries) == cursor.close_calls == 1
        assert not cursor.closed_during_read and not second_started
    finally:
        cursor.release.set()
        cursor.close_release.set()
        task.cancel()
        await asyncio.gather(task, *cursor.operations, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_sibling_read_joins_actual_motor_operation_before_propagating_error():
    from operational_balance_sources import refresh_source_rows
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}], [{'user_id': 'owner'}]])
    database = MotorSourceDB(cursor)

    async def reader(db, owner, name):
        if name == 'invalid':
            await cursor.entered.wait()
            raise ValueError('operational_source_owner_mismatch')
        return await refresh_source_rows(db, owner, name)

    task = asyncio.create_task(load_collections(database, 'owner', ['unified_orders', 'invalid'], reader=reader))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        for _ in range(20):
            await asyncio.sleep(0)
        assert not task.done() and not cursor.worker_finished.is_set()
        cursor.release.set()
        with pytest.raises(SourceReadError) as failure:
            await task
        assert (failure.value.code, failure.value.collection) == ('operational_source_owner_mismatch', 'invalid')
        assert len(cursor.operations) == cursor.close_calls == 1
        assert all(operation.done() for operation in cursor.operations) and not cursor.closed_during_read
    finally:
        cursor.release.set()
        task.cancel()
        await asyncio.gather(task, *cursor.operations, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(('per_read', 'inherited', 'request_seconds'), [(.2, 1, 1), (1, .2, 1), (1, 1, .2)])
async def test_read_budget_reaches_real_motor_worker_and_ends_before_unrelated_work(monkeypatch, per_read, inherited, request_seconds):
    from operational_balance_sources import refresh_source_rows
    context = SimpleNamespace(read_deadline=time.monotonic() + request_seconds)
    monkeypatch.setattr(runtime, 'demand_context', lambda: context)
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}]], block=False)
    assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()
    with source_read_budget(inherited):
        result = await load_collections(MotorSourceDB(cursor), 'owner', ['unified_orders'],
                                       reader=refresh_source_rows, per_read_timeout=per_read)
    assert result == {'unified_orders': [{'user_id': 'owner'}]}
    assert len(cursor.driver_budgets) == 1 and 0 < cursor.driver_budgets[0] <= .2
    assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()
    # The caller and a subsequent task (including a writer) inherit no budget.
    assert await asyncio.to_thread(pymongo._csot.remaining) is None


@pytest.mark.asyncio
async def test_one_driver_budget_covers_all_batches_without_query_replay():
    from operational_balance_sources import refresh_source_rows
    documents = [{'user_id': 'owner', 'order_number': str(index)} for index in range(3)]
    cursor = ThreadedMotorCursor([[document] for document in documents])
    database = MotorSourceDB(cursor)
    task = asyncio.create_task(load_collections(database, 'owner', ['unified_orders'],
        reader=refresh_source_rows, per_read_timeout=1))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        await asyncio.sleep(.025)
        cursor.release.set()
        assert await task == {'unified_orders': documents}
        assert len(database.queries) == cursor.close_calls == 1
        assert len(cursor.driver_budgets) == 3
        assert 0 < cursor.driver_budgets[-1] < cursor.driver_budgets[0] - .02
        assert not cursor.closed_during_read and all(operation.done() for operation in cursor.operations)
    finally:
        cursor.release.set()
        task.cancel()
        await asyncio.gather(task, *cursor.operations, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('expired', ['per_read', 'inherited', 'request'])
async def test_expired_budget_never_enters_driver_or_turns_zero_into_unbounded_timeout(monkeypatch, expired):
    called = False
    deadline = time.monotonic() - 1 if expired == 'request' else None
    monkeypatch.setattr(runtime, 'demand_context', lambda: SimpleNamespace(read_deadline=deadline))

    async def reader(*args):
        nonlocal called
        called = True
        return []

    with source_read_budget(0 if expired == 'inherited' else 1):
        with pytest.raises(SourceReadError) as failure:
            await load_collections(object(), 'owner', ['unified_orders'], reader=reader,
                                   per_read_timeout=0 if expired == 'per_read' else 1)
    assert failure.value.code == 'operational_source_read_timeout' and not called
    assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [NetworkTimeout('synthetic'), ExecutionTimeout('synthetic', code=50),
                                  OperationFailure('synthetic', code=13)])
async def test_only_driver_timeout_errors_become_source_timeout(error):
    async def reader(*args):
        assert pymongo._csot.remaining() is not None and runtime.source_read_is_owned()
        raise error

    with pytest.raises(SourceReadError if error.timeout else OperationFailure) as failure:
        await load_collections(object(), 'owner', ['unified_orders'], reader=reader)
    if error.timeout:
        assert (failure.value.code, failure.value.collection) == ('operational_source_read_timeout', 'unified_orders')
    else:
        assert failure.value is error
    assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['__anext__', 'close'])
async def test_unsupported_motor_cursor_cannot_fall_back_to_unowned_to_list(missing):
    cursor = ThreadedMotorCursor([], block=False)
    called = False

    async def to_list(count):
        nonlocal called
        called = True
        return []

    cursor.to_list = to_list
    setattr(cursor, missing, None)
    with pytest.raises(ValueError, match='^operational_source_cursor_unsupported$'):
        await read_source_cursor(cursor, 1)
    assert not called and not cursor.operations


@pytest.mark.asyncio
async def test_future_returning_non_motor_proxy_is_not_a_compatibility_adapter():
    class Proxy:
        def to_list(self, count):
            raise AssertionError('must reject before starting unowned work')

    with pytest.raises(ValueError, match='^operational_source_cursor_unsupported$'):
        await read_source_cursor(Proxy(), 1)


@pytest.mark.asyncio
async def test_production_read_wrappers_preserve_motor_cursor_and_explicit_snapshot_session():
    from operational_supplier_invoice_preview import SnapshotReads
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    client = AsyncIOMotorClient('mongodb://127.0.0.1:1', connect=False)
    cursors, session = [], None
    try:
        database = AcceptanceConfigDatabase(client['owned_read_type_test'])
        # The driver creates an empty server session lazily, with no I/O.
        session = await client.start_session(snapshot=True)
        for wrapped in (database, SnapshotReads(database, 'owner', session)):
            for collection in ('unified_orders', 'mezan_product_cost_profiles_v2'):
                cursor = wrapped[collection].find({'user_id': 'owner'}, {'_id': 0, 'user_id': 1})
                cursors.append(cursor)
                assert isinstance(cursor, AgnosticBaseCursor)
                assert callable(cursor.__anext__) and callable(cursor.close)
                assert cursor.delegate.session is (session.delegate if isinstance(wrapped, SnapshotReads) else None)
    finally:
        for cursor in cursors:
            await cursor.close()
        if session is not None:
            await session.end_session()
        client.close()


class MongoReadWire:
    """Disposable loopback Mongo wire responder, with no database or data files.

    The real driver receives a handshake and synthetic batches. One selected
    read deliberately gets no response so its real CSOT socket budget must
    terminate it. All waits, sockets, and handler tasks are owned by the test.
    """
    def __init__(self, stall):
        self.stall = stall
        self.commands, self.errors = [], []
        self.handlers, self.writers = set(), set()
        self.read_disconnected = asyncio.Event()
        self.response_id = 0

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        for task in tuple(self.handlers):
            task.cancel()
        await asyncio.gather(*tuple(self.handlers), return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        try:
            while True:
                header = await asyncio.wait_for(reader.readexactly(16), 3)
                length, request_id, _, opcode = struct.unpack('<iiii', header)
                assert 16 <= length <= 1024 * 1024
                payload = await asyncio.wait_for(reader.readexactly(length - 16), 3)
                if opcode == 2004:  # Initial OP_QUERY handshake.
                    namespace_end = payload.index(b'\0', 4)
                    command = BSON(payload[namespace_end + 9:]).decode()
                else:
                    assert opcode == 2013 and payload[4] == 0  # OP_MSG, one body.
                    command = BSON(payload[5:]).decode()
                name = next(iter(command)).lower()
                if name in {'ismaster', 'hello'}:
                    reply = {'ok': 1.0, 'ismaster': True, 'isWritablePrimary': True,
                             'helloOk': True, 'minWireVersion': 0, 'maxWireVersion': 25,
                             'logicalSessionTimeoutMinutes': 30,
                             'maxBsonObjectSize': 16777216, 'maxMessageSizeBytes': 48000000,
                             'maxWriteBatchSize': 100000}
                else:
                    self.commands.append(command)
                    reads = [item for item in self.commands if 'getMore' in item]
                    if (name == 'find' and self.stall == 'find'
                            or name == 'getmore' and len(reads) == 2):
                        # Observe the real driver close its timed-out socket.
                        assert await asyncio.wait_for(reader.read(1), 3) == b''
                        self.read_disconnected.set()
                        return
                    if name in {'find', 'getmore'}:
                        await asyncio.sleep(.06)
                        reply = {'ok': 1.0, 'cursor': {'id': 71, 'ns': 'owned_read_wire.unified_orders',
                            'firstBatch' if name == 'find' else 'nextBatch':
                                [{'user_id': 'owner', 'order_number': str(len(reads))}]}}
                    elif name == 'killcursors':
                        reply = {'ok': 1.0, 'cursorsKilled': command['cursors'],
                                 'cursorsUnknown': [], 'cursorsAlive': [], 'cursorsNotFound': []}
                    elif name == 'endsessions':
                        reply = {'ok': 1.0}
                    else:
                        raise AssertionError('unexpected synthetic-wire command: ' + name)
                encoded = BSON.encode(reply)
                body = (struct.pack('<iqii', 0, 0, 0, 1) + encoded if opcode == 2004
                        else struct.pack('<iB', 0, 0) + encoded)
                self.response_id += 1
                writer.write(struct.pack('<iiii', len(body) + 16, self.response_id, request_id,
                                         1 if opcode == 2004 else 2013) + body)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        except Exception as error:
            self.errors.append(error)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            finally:
                self.writers.discard(writer)
                self.handlers.discard(task)


@pytest.mark.asyncio
@pytest.mark.parametrize(('reader_name', 'stall'), [('refresh_source_rows', 'find'), ('rows', 'getMore')])
async def test_real_driver_wire_read_ends_with_one_cumulative_csot_budget(monkeypatch, reader_name, stall):
    import operational_balance_sources as sources
    from pymongo.synchronous.pool import Connection
    from review_acceptance_config_guard import AcceptanceConfigDatabase

    operations, socket_budgets = [], []
    get_more, apply_timeout = AsyncIOMotorCursor._get_more, Connection.apply_timeout

    def observed_get_more(cursor):
        operation = get_more(cursor)
        operations.append(operation)
        return operation

    def observed_timeout(connection, client, cmd):
        budget = apply_timeout(connection, client, cmd)
        if budget is not None and (cmd is None or 'find' in cmd):
            socket_budgets.append((budget, time.monotonic() + budget))
        return budget

    monkeypatch.setattr(AsyncIOMotorCursor, '_get_more', observed_get_more)
    monkeypatch.setattr(Connection, 'apply_timeout', observed_timeout)
    wire = await MongoReadWire(stall).start()
    client = AsyncIOMotorClient(f'mongodb://127.0.0.1:{wire.port}/?directConnection=true',
                               connect=False, serverSelectionTimeoutMS=2000,
                               connectTimeoutMS=2000, socketTimeoutMS=2000)
    try:
        database = AcceptanceConfigDatabase(client['owned_read_wire'])
        with pytest.raises(SourceReadError) as failure:
            await load_collections(database, 'owner', ['unified_orders'],
                reader=getattr(sources, reader_name), per_read_timeout=1)
        assert (failure.value.code, failure.value.collection) == ('operational_source_read_timeout', 'unified_orders')
        assert operations and all(operation.done() and not operation.cancelled() for operation in operations)
        await asyncio.wait_for(wire.read_disconnected.wait(), 1)
        finds = [command for command in wire.commands if 'find' in command]
        assert len(finds) == 1 and finds[0]['filter'] == {'user_id': 'owner'}
        assert 0 < finds[0]['maxTimeMS'] <= 1000
        assert len(operations) == (1 if stall == 'find' else 3)
        assert len([command for command in wire.commands if 'getMore' in command]) == len(operations) - 1
        assert socket_budgets and all(0 < budget <= 1 for budget, _ in socket_budgets)
        if stall == 'getMore':
            assert len(socket_budgets) == 3
            assert socket_budgets[-1][0] < socket_budgets[0][0] - .1
            deadlines = [deadline for _, deadline in socket_budgets]
            assert max(deadlines) - min(deadlines) < .02  # No fresh budget per batch.
        assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()
    finally:
        await asyncio.wait_for(asyncio.to_thread(client.close), 3)
        await wire.close()
    assert not wire.errors and not wire.handlers and not wire.writers
    assert len([command for command in wire.commands if 'find' in command]) == 1


def state(owner='owner', revision=1, as_of='2026-11-12T10:00:00+00:00'):
    return {'owner_id': owner, 'revision': revision, 'status': 'active',
            'started_at': '2026-11-01T00:00:00+00:00', 'engine': {'as_of': as_of}}


@pytest.mark.asyncio
async def test_independent_reads_run_at_most_four_at_once_without_losing_any_collection():
    active, peak = 0, 0
    async def reader(db, owner, name):
        nonlocal active, peak
        assert owner == 'owner'
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.001)
            return [name]
        finally:
            active -= 1
    names = tuple('collection_' + str(i) for i in range(12))
    result = await load_collections(object(), 'owner', names, reader=reader, concurrency=20)
    assert result == {name: [name] for name in names}
    assert peak == 4 and active == 0


@pytest.mark.asyncio
async def test_snapshot_transaction_reads_stay_serial():
    class Snapshot:
        session = None
        def __init__(self):
            self.session = object()
    active, peak = 0, 0
    async def reader(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return []
    await load_collections(Snapshot(), 'owner', ['one', 'two', 'three'], reader=reader)
    assert peak == 1


@pytest.mark.asyncio
async def test_scope_failure_keeps_collection_context_and_cancels_sibling_reads():
    stopped = set()
    async def reader(db, owner, name):
        if name == 'oversized':
            await asyncio.sleep(0)
            raise ValueError('operational_source_scope_too_large')
        try:
            await asyncio.Event().wait()
        finally:
            stopped.add(name)
    with pytest.raises(SourceReadError) as error:
        await load_collections(object(), 'owner', ['oversized', 'other', 'another'], reader=reader)
    assert error.value.code == 'operational_source_scope_too_large'
    assert error.value.collection == 'oversized'
    assert stopped == {'other', 'another'}


@pytest.mark.asyncio
async def test_read_budget_times_out_only_reads_and_identifies_the_collection():
    cancelled = asyncio.Event()
    async def reader(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with source_read_budget(.01):
        with pytest.raises(SourceReadError) as error:
            await load_collections(object(), 'owner', ['slow'], reader=reader)
    assert cancelled.is_set()
    assert error.value.code == 'operational_source_read_timeout' and error.value.collection == 'slow'
    assert 'owner' not in str(error.value)


@pytest.mark.asyncio
async def test_estimate_cache_coalesces_identical_snapshot_reads_and_returns_independent_values():
    cache, database, calls = SnapshotReadCache(), object(), 0
    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(.01)
        return {'groups': [{'amount': '20.00'}]}
    first, second = await asyncio.gather(*(cache.get(database, 'owner', state(), None, loader) for _ in range(2)))
    assert calls == 1 and first == second
    first['groups'][0]['amount'] = '999.00'
    third = await cache.get(database, 'owner', state(), None, loader)
    assert third == second and third['groups'][0]['amount'] == '20.00' and calls == 1


@pytest.mark.asyncio
async def test_estimate_cache_never_reuses_other_owner_database_revision_or_snapshot():
    cache, database, calls = SnapshotReadCache(), object(), 0
    async def loader():
        nonlocal calls
        calls += 1
        return calls
    assert await cache.get(database, 'owner', state(), None, loader) == 1
    assert await cache.get(database, 'other', state('other'), None, loader) == 2
    assert await cache.get(object(), 'owner', state(), None, loader) == 3
    assert await cache.get(database, 'owner', state(revision=2), None, loader) == 4
    assert await cache.get(database, 'owner', state(as_of='2026-11-12T10:05:00+00:00'), None, loader) == 5
    assert await cache.get(database, 'owner', state(), '2026-11-12T10:03:00+00:00', loader) == 6
    altered_start = deepcopy(state())
    altered_start['started_at'] = '2026-11-02T00:00:00+00:00'
    assert await cache.get(database, 'owner', altered_start, None, loader) == 7


@pytest.mark.asyncio
async def test_cache_expires_and_failed_reads_are_never_retained():
    clock, calls = [0], 0
    cache, database = SnapshotReadCache(ttl=20, clock=lambda: clock[0]), object()
    async def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError('source incomplete')
        return calls
    with pytest.raises(ValueError):
        await cache.get(database, 'owner', state(), None, loader)
    assert await cache.get(database, 'owner', state(), None, loader) == 2
    clock[0] = 19
    assert await cache.get(database, 'owner', state(), None, loader) == 2
    clock[0] = 20
    assert await cache.get(database, 'owner', state(), None, loader) == 3


@pytest.mark.asyncio
async def test_one_cancelled_reader_does_not_cancel_another_reader_of_the_same_snapshot():
    cache, database, release, entered = SnapshotReadCache(), object(), asyncio.Event(), asyncio.Event()
    async def loader():
        entered.set()
        await release.wait()
        return {'amount': '20.00'}
    first = asyncio.create_task(cache.get(database, 'owner', state(), None, loader))
    second = asyncio.create_task(cache.get(database, 'owner', state(), None, loader))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == {'amount': '20.00'}
    assert not cache.inflight


@pytest.mark.asyncio
async def test_cancelling_the_last_reader_drains_its_read_only_work():
    cache, database, entered, stopped = SnapshotReadCache(), object(), asyncio.Event(), asyncio.Event()
    async def loader():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task = asyncio.create_task(cache.get(database, 'owner', state(), None, loader))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and not cache.inflight and not cache.entries


@pytest.mark.asyncio
async def test_optional_cost_projection_bounds_its_late_reads_without_writing_state(monkeypatch):
    import operational_open_cost_exposure as exposure
    stopped = asyncio.Event()
    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(exposure, '_project', slow)
    monkeypatch.setattr(exposure, 'READ_TIMEOUT', .01)
    saved = state()
    before = deepcopy(saved)
    with pytest.raises(SourceReadError) as error:
        await exposure.project(object(), 'owner', saved)
    assert stopped.is_set() and saved == before
    assert error.value.code == 'operational_source_read_timeout'
    assert error.value.collection == 'open_cost_exposure'
