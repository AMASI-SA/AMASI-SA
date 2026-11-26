"""Bounded read work for operational snapshots, with no financial writers.

Deadlines apply only to source reads. They never cancel the aggregate CAS or a
financial mutation. Snapshot-session reads remain serial, while independent
ordinary reads use at most four concurrent operations.
"""
import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import inspect
import math
from threading import Event
import time

from motor.core import AgnosticBaseCursor
import pymongo
from pymongo.errors import PyMongoError

from operational_demand_refresh import demand_context
from operational_financial_activity_runtime import (
    join_task, preparation_checkpoint, preparation_scope, wait_for_cleanup,
)

_DEADLINE = ContextVar('operational_source_read_deadline', default=None)
_OWNED_SOURCE_READ = ContextVar('operational_owned_source_read', default=False)
_PRIMARY_ORDERS_READ = ContextVar('operational_primary_orders_read_observation', default=None)


def _observation_clock(observation):
    if observation is not None:
        try:
            return time.monotonic()
        except Exception:
            pass
    return None


def _observe(observation, **values):
    if observation is not None:
        try:
            observation.update(values)
        except Exception:
            pass


def _observe_elapsed(observation, key, started):
    if observation is not None and started is not None:
        try:
            observation[key] = (time.monotonic() - started) * 1000
        except Exception:
            pass


def _primary_orders_observation(enabled, name):
    # Only the primary financial source reader opts in. No owner, query, row,
    # cursor identity or exception text belongs in this request-local record.
    if not enabled or name != 'unified_orders':
        return None, None
    try:
        context = demand_context()
        if context is None:
            return None, None
        observation = {
            'collection': 'unified_orders', 'outcome': 'complete', 'cursor_kind': 'unknown',
            'queue_ms': None, 'budget_ms': 0, 'first_row_ms': None,
            'rows_retained': 0, 'last_checkpoint_rows': 0, 'last_checkpoint_ms': None,
            'read_elapsed_ms': None, 'cancel_join_ms': 0, 'close_ms': None,
            '_queued_at': time.monotonic(),
        }
        return context, observation
    except Exception:
        return None, None


class SourceReadError(ValueError):
    def __init__(self, code, collection):
        super().__init__(code)
        self.code, self.collection = code, collection


@contextmanager
def source_read_budget(seconds=60):
    deadline = time.monotonic() + max(float(seconds), 0)
    inherited = _DEADLINE.get()
    token = _DEADLINE.set(min(deadline, inherited) if inherited is not None else deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def _snapshot_session(db):
    # Inspect a declared attribute, not Motor's dynamic collection lookup.
    # This also avoids coupling the runtime to a specific preview service.
    return vars(db).get('session') is not None if hasattr(db, '__dict__') else False


def source_read_is_owned():
    return _OWNED_SOURCE_READ.get()


async def read_source_cursor(cursor, count):
    """Own Motor's active batch until it settles, then close the cursor.

    Cancelling Motor's to_list Future does not join the executor read behind
    it. Keep iteration shielded instead; cancellation requests a stop before
    another advance and waits for the active read and sequential close.
    """
    observation = _PRIMARY_ORDERS_READ.get()
    read_started = _observation_clock(observation)
    if not isinstance(cursor, AgnosticBaseCursor):
        # Deliberately non-Motor, coroutine-based adapters used by offline
        # readers own their awaited work directly. Do not silently accept a
        # Future-returning Motor proxy with the same cancellation gap.
        to_list = getattr(cursor, 'to_list', None)
        if not inspect.iscoroutinefunction(to_list):
            raise ValueError('operational_source_cursor_unsupported')
        _observe(observation, cursor_kind='coroutine')
        try:
            result = await to_list(count)
            if type(result) is list:
                _observe(observation, rows_retained=len(result))
            return result
        finally:
            _observe_elapsed(observation, 'read_elapsed_ms', read_started)
    if not callable(getattr(cursor, '__anext__', None)) or not callable(getattr(cursor, 'close', None)):
        raise ValueError('operational_source_cursor_unsupported')
    _observe(observation, cursor_kind='motor')

    stop = asyncio.Event()

    async def consume():
        result = []
        try:
            while len(result) < count and not stop.is_set():
                try:
                    row = await cursor.__anext__()
                except StopAsyncIteration:
                    break
                if stop.is_set():
                    break
                result.append(row)
                if observation is not None and len(result) == 1:
                    _observe_elapsed(observation, 'first_row_ms', read_started)
                if len(result) % 256 == 0:
                    _observe(observation, last_checkpoint_rows=len(result))
                    _observe_elapsed(observation, 'last_checkpoint_ms', read_started)
                    # Buffered rows can be synchronous. Let request timeout
                    # and disconnect callbacks run before advancing again.
                    await asyncio.sleep(0)
            return result
        finally:
            _observe(observation, rows_retained=len(result))
            # Never race killCursors/session cleanup with an active getMore.
            close_started = _observation_clock(observation)
            try:
                await cursor.close()
            finally:
                _observe_elapsed(observation, 'close_ms', close_started)

    task = asyncio.create_task(consume())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        join_started = _observation_clock(observation)
        try:
            await join_task(task)
        except (asyncio.CancelledError, Exception):
            pass  # Preserve cancellation after observing the owned read.
        finally:
            # This is the existing owned-task join, including sequential close;
            # it overlaps read_elapsed_ms and is not exclusive driver/DB time.
            _observe_elapsed(observation, 'cancel_join_ms', join_started)
        raise
    finally:
        _observe_elapsed(observation, 'read_elapsed_ms', read_started)


async def load_collections(db, owner, names, *, reader, concurrency=4, per_read_timeout=15,
                           per_read_timeouts=None, on_source_error=None, observe_primary_orders=False):
    names = tuple(names)
    if len(names) != len(set(names)):
        raise ValueError('operational_source_duplicate_collection')
    timeouts = {}
    if per_read_timeouts is not None:
        if not isinstance(per_read_timeouts, Mapping):
            raise ValueError('operational_source_read_budget_invalid')
        for name, seconds in per_read_timeouts.items():
            if name not in names or type(seconds) not in (int, float):
                raise ValueError('operational_source_read_budget_invalid')
            try:
                seconds = float(seconds)
            except (ValueError, OverflowError):
                raise ValueError('operational_source_read_budget_invalid') from None
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError('operational_source_read_budget_invalid')
            timeouts[name] = seconds
    limit = 1 if _snapshot_session(db) else max(1, min(int(concurrency), 4))
    semaphore = asyncio.Semaphore(limit)

    async def load(name):
        async with semaphore:
            observation = _PRIMARY_ORDERS_READ.get()
            if observation is not None:
                _observe_elapsed(observation, 'queue_ms', observation['_queued_at'])
            # This internal policy only allocates the existing outer budget.
            # Copy/validate it before I/O, then clamp again at admission: a
            # queued source cannot renew an inherited/request deadline.
            remaining = timeouts.get(name, per_read_timeout)
            if _DEADLINE.get() is not None:
                remaining = min(remaining, _DEADLINE.get() - time.monotonic())
            context = demand_context()
            if context is not None and context.read_deadline is not None:
                remaining = min(remaining, context.read_deadline - time.monotonic())
            if observation is not None:
                _observe(observation, budget_ms=max(remaining, 0) * 1000)
            if remaining <= 0:
                raise SourceReadError('operational_source_read_timeout', name)
            try:
                # CSOT reaches Motor's executor and bounds server/socket work,
                # not only its asyncio waiter. Its context ends before any
                # calculation or financial write can inherit this read budget.
                with pymongo.timeout(remaining):
                    token = _OWNED_SOURCE_READ.set(True)
                    try:
                        task = asyncio.create_task(reader(db, owner, name))
                        try:
                            result = await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
                        finally:
                            if not task.done():
                                task.cancel()
                            try:
                                await join_task(task)
                            except (asyncio.CancelledError, Exception):
                                pass  # The original outcome owns error reporting.
                    finally:
                        _OWNED_SOURCE_READ.reset(token)
                return name, result
            except asyncio.TimeoutError:
                raise SourceReadError('operational_source_read_timeout', name) from None
            except SourceReadError:
                raise
            except PyMongoError as error:
                if error.timeout:
                    raise SourceReadError('operational_source_read_timeout', name) from None
                raise
            except ValueError as error:
                code = str(error)
                if not code.startswith('operational_') or not all(c.islower() or c == '_' for c in code):
                    code = 'operational_source_read_invalid'
                raise SourceReadError(code, name) from None

    async def collect(name):
        context, observation = _primary_orders_observation(observe_primary_orders, name)
        observation_token = _PRIMARY_ORDERS_READ.set(observation)
        try:
            return await load(name)
        except SourceReadError as error:
            _observe(observation, outcome='failed')
            if on_source_error is None:
                raise
            # Independent report components may finish within the remaining
            # budget. Financial source collections still fail as a group.
            on_source_error(name, error)
            return name, None
        except asyncio.CancelledError:
            _observe(observation, outcome='cancelled')
            raise
        except BaseException:
            _observe(observation, outcome='failed')
            raise
        finally:
            _PRIMARY_ORDERS_READ.reset(observation_token)
            if observation is not None:
                if observation['queue_ms'] is None:
                    _observe_elapsed(observation, 'queue_ms', observation['_queued_at'])
                try:
                    context.record_primary_orders_read(observation)
                except Exception:
                    pass  # Diagnostics cannot replace a financial exception.

    tasks = [asyncio.create_task(collect(name)) for name in names]
    try:
        return dict(await asyncio.gather(*tasks))
    finally:
        # Owned source cursors join the active Motor read before releasing the
        # session, budget, or authorization scope, even on repeated cancellation.
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await join_task(asyncio.gather(*tasks, return_exceptions=True))


class SnapshotReadCache:
    """Small, short-lived cache for read-only estimates, never balance authority."""
    def __init__(self, *, ttl=20, max_entries=32, clock=time.monotonic):
        self.ttl, self.max_entries, self.clock = ttl, max_entries, clock
        self.entries, self.inflight = OrderedDict(), {}

    def clear(self):
        self.entries.clear()

    @staticmethod
    def key(db, owner, state, as_of):
        revision = state.get('revision')
        # Revision zero is an unsaved initial aggregate; callers can project
        # transient candidates there without creating a reusable snapshot.
        if (type(revision) is not int or revision <= 0 or state.get('owner_id') != owner
                or state.get('status') != 'active' or _snapshot_session(db)):
            return None
        return (id(db), owner, revision, state.get('started_at'),
                state.get('engine', {}).get('as_of'), str(as_of) if as_of is not None else None)

    async def get(self, db, owner, state, as_of, loader):
        key = self.key(db, owner, state, as_of)
        if key is None:
            return await loader()
        now = self.clock()
        for old_key, entry in list(self.entries.items()):
            if entry[1] <= now:
                self.entries.pop(old_key, None)
        entry = self.entries.get(key)
        if entry is not None and entry[0] is db:
            self.entries.move_to_end(key)
            return deepcopy(entry[2])
        task_key = (id(asyncio.get_running_loop()), key)
        running = self.inflight.get(task_key)
        while running is not None and running['stop'].is_set():
            # The final departing reader still owns cleanup. A new reader
            # waits for it instead of overlapping a replacement calculation.
            await wait_for_cleanup(running['task'])
            if self.inflight.get(task_key) is running:
                self.inflight.pop(task_key, None)
            running = self.inflight.get(task_key)
        if running is None:
            stop = Event()

            async def compute():
                # A shared build must not inherit the first HTTP reader's
                # stop event. It belongs to all current waiters together.
                with preparation_scope(stop):
                    preparation_checkpoint()
                    value = await loader()
                    preparation_checkpoint()
                    self.entries[key] = (db, self.clock() + self.ttl, deepcopy(value))
                    self.entries.move_to_end(key)
                    while len(self.entries) > self.max_entries:
                        self.entries.popitem(last=False)
                    return value
            # Capacity bounds cache entries and shared work. An excess distinct
            # request still reads normally rather than retaining unlimited tasks.
            if len(self.inflight) >= self.max_entries:
                return await loader()
            task = asyncio.create_task(compute())
            running = self.inflight[task_key] = {'task': task, 'waiters': 0, 'stop': stop}
        running['waiters'] += 1
        try:
            return deepcopy(await asyncio.shield(running['task']))
        finally:
            running['waiters'] -= 1
            if running['waiters'] == 0:
                if not running['task'].done():
                    running['stop'].set()
                    running['task'].cancel()
                    try:
                        await join_task(running['task'])
                    except (asyncio.CancelledError, Exception):
                        pass  # The closing reader still joins all source work.
                if self.inflight.get(task_key) is running:
                    self.inflight.pop(task_key, None)
