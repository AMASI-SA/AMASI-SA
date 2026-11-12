"""Bounded read work for operational snapshots, with no financial writers.

Deadlines apply only to source reads. They never cancel the aggregate CAS or a
financial mutation. Snapshot-session reads remain serial, while independent
ordinary reads use at most four concurrent operations.
"""
import asyncio
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from threading import Event
import time

from operational_financial_activity_runtime import (
    join_task, preparation_checkpoint, preparation_scope, wait_for_cleanup,
)

_DEADLINE = ContextVar('operational_source_read_deadline', default=None)


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


async def load_collections(db, owner, names, *, reader, concurrency=4, per_read_timeout=15):
    names = tuple(names)
    if len(names) != len(set(names)):
        raise ValueError('operational_source_duplicate_collection')
    limit = 1 if _snapshot_session(db) else max(1, min(int(concurrency), 4))
    semaphore = asyncio.Semaphore(limit)

    async def load(name):
        async with semaphore:
            remaining = per_read_timeout
            if _DEADLINE.get() is not None:
                remaining = min(remaining, _DEADLINE.get() - time.monotonic())
            if remaining <= 0:
                raise SourceReadError('operational_source_read_timeout', name)
            try:
                result = await asyncio.wait_for(reader(db, owner, name), timeout=remaining)
                return name, result
            except asyncio.TimeoutError:
                raise SourceReadError('operational_source_read_timeout', name) from None
            except SourceReadError:
                raise
            except ValueError as error:
                code = str(error)
                if not code.startswith('operational_') or not all(c.islower() or c == '_' for c in code):
                    code = 'operational_source_read_invalid'
                raise SourceReadError(code, name) from None

    tasks = [asyncio.create_task(load(name)) for name in names]
    try:
        return dict(await asyncio.gather(*tasks))
    finally:
        # A failed read never leaves sibling DB reads running after the caller
        # releases its session, budget, or authorization scope.
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


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
