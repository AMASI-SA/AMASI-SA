"""Bounded, request-owned timing for two existing read paths.

One completed record per sampled request, at most one active sample per scope
and one start per minute per worker. No payloads, identities, queries, new
tasks, database operations or runtime controls. Async spans measure wall time;
CPU is measured only around synchronous work on its executing thread.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import logging
import math
import os
from threading import Lock
import time


log = logging.getLogger("mezan.read_path_diagnostics")
SCOPES = frozenset({"operational_context", "assembly_board"})
CATALOG_KINDS = frozenset({
    "employee", "supplier", "external_person", "store_driver", "courier",
    "provider", "ad_account", "employee_custody", "bank", "cash",
    "pos_receivable", "operating_expense", "owner_withdrawal",
})
PHASES = {
    "operational_context": frozenset({
        "access_initial", "access_final", "state_read", "directory_state_read",
        "state_decode", "report", "navigation", "project",
        *(f"catalog.{kind}" for kind in CATALOG_KINDS),
    }),
    "assembly_board": frozenset({
        "piece_distinct", "workflow_read", "local_proofs", "piece_read",
        "hydration_cursor", "hydrate_row", "dto_map", "cooperative_yield",
        "virtual_pieces", "sort_group",
    }),
}
SYNC_PHASES = frozenset({"hydrate_row", "dto_map", "virtual_pieces", "sort_group", "navigation"})
THREAD_PHASES = frozenset({"state_decode", "report", "navigation", "project"})
MODES = {
    "operational_context": frozenset({"full", "home", "directory"}),
    "assembly_board": frozenset({"in_progress", "completed", "direct_preparation", "supplier_received"}),
}
COUNTS = frozenset({
    "resources", "workflows_acquired", "workflows_filtered", "physical_rows",
    "canonical_batches", "mapped_rows", "invalid_rows", "hydrated_rows",
    "invalid_discovery_rows", "invalid_mapping_rows", "virtual_pieces", "result_rows",
})
_last = {}
_active = set()
_sampling_lock = Lock()
_current = ContextVar("mezan_read_path_diagnostics", default=None)
_INTERVAL_SECONDS = 60.0
_MAX_COUNT = 2 ** 53 - 1


def _clock(function):
    try:
        value = function()
        return value if isinstance(value, (int, float)) and math.isfinite(value) else None
    except Exception:
        return None


def _milliseconds(started, finished):
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started) * 1000)


class _Trace:
    def __init__(self, scope, started, mode):
        self.scope = scope
        self.started = started
        self.mode = mode if isinstance(mode, str) and mode in MODES[scope] else None
        self.phases = {}
        self.counts = {}
        self.closed = False
        self.lock = Lock()

    def add(self, name, elapsed, cpu=None, queue=None):
        if not isinstance(name, str) or name not in PHASES[self.scope] or elapsed is None:
            return
        with self.lock:
            if self.closed:
                return
            item = self.phases.setdefault(name, {"calls": 0, "sum_ms": 0.0, "max_ms": 0.0})
            item["calls"] += 1
            item["sum_ms"] += elapsed
            item["max_ms"] = max(item["max_ms"], elapsed)
            if cpu is not None:
                item["sum_thread_cpu_ms"] = item.get("sum_thread_cpu_ms", 0.0) + cpu
            if queue is not None:
                item["queue_sum_ms"] = item.get("queue_sum_ms", 0.0) + queue
                item["queue_max_ms"] = max(item.get("queue_max_ms", 0.0), queue)

    def finish(self, outcome):
        with self.lock:
            self.closed = True
            payload = {
                "scope": self.scope, "mode": self.mode, "outcome": outcome,
                "pid": os.getpid(),
                "elapsed_ms": _milliseconds(self.started, _clock(time.monotonic)),
                "counts": dict(self.counts),
                "phases": {
                    name: {key: round(value, 3) if isinstance(value, float) else value
                           for key, value in metrics.items()}
                    for name, metrics in self.phases.items()
                },
            }
        # Observability must never substitute its failure for the read result.
        try:
            log.info("read_path_sample %s", json.dumps(payload, sort_keys=True, allow_nan=False))
        except Exception:
            pass


def current():
    record = _current.get()
    return record if record is not None and not record.closed else None


@contextmanager
def capture(scope, *, mode=None):
    record = None
    now = _clock(time.monotonic) if isinstance(scope, str) and scope in SCOPES else None
    if now is not None:
        with _sampling_lock:
            if scope not in _active and now - _last.get(scope, float("-inf")) >= _INTERVAL_SECONDS:
                record = _Trace(scope, now, mode)
                _last[scope] = now
                _active.add(scope)
    # A skipped nested scope must not append its phases to an outer sample.
    token = _current.set(record)
    outcome = "complete"
    try:
        yield record
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except BaseException:
        outcome = "failed"
        raise
    finally:
        _current.reset(token)
        if record is not None:
            try:
                record.finish(outcome)
            finally:
                with _sampling_lock:
                    _active.discard(scope)


async def sample(scope, operation, *, mode=None):
    with capture(scope, mode=mode):
        return await operation


def sampled(scope):
    """For internal async functions, never FastAPI route signature wrappers."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            return await sample(scope, function(*args, **kwargs))
        return wrapped
    return decorate


def set_mode(mode):
    record = current()
    if record is not None and isinstance(mode, str) and mode in MODES[record.scope]:
        record.mode = mode


def count(name, value):
    record = current()
    if record is None or not isinstance(name, str) or name not in COUNTS or type(value) is not int or value < 0:
        return
    with record.lock:
        if not record.closed:
            record.counts[name] = min(_MAX_COUNT, record.counts.get(name, 0) + value)


@contextmanager
def _phase(record, name, *, cpu=False):
    if record is None or not isinstance(name, str) or name not in PHASES[record.scope] or record.closed:
        yield
        return
    started = _clock(time.monotonic)
    cpu_started = _clock(time.thread_time) if cpu and name in SYNC_PHASES else None
    try:
        yield
    finally:
        elapsed = _milliseconds(started, _clock(time.monotonic))
        cpu_elapsed = _milliseconds(cpu_started, _clock(time.thread_time)) if cpu_started is not None else None
        record.add(name, elapsed, cpu_elapsed)


def phase(name, *, cpu=False):
    return _phase(current(), name, cpu=cpu)


def thread_call(name, function):
    """Wrap the existing worker callable; do not change scheduling or joining."""
    record = current()
    if record is None or not isinstance(name, str) or name not in THREAD_PHASES or name not in PHASES[record.scope]:
        return function
    enqueued = _clock(time.monotonic)

    @wraps(function)
    def wrapped(*args, **kwargs):
        started = _clock(time.monotonic)
        cpu_started = _clock(time.thread_time)
        try:
            return function(*args, **kwargs)
        finally:
            record.add(name, _milliseconds(started, _clock(time.monotonic)),
                       _milliseconds(cpu_started, _clock(time.thread_time)),
                       _milliseconds(enqueued, started))
    return wrapped


class _Iterator:
    def __init__(self, original, record, name):
        self.original = original
        self.record = record
        self.name = name

    def __aiter__(self):
        return self

    async def __anext__(self):
        with _phase(self.record, self.name):
            return await self.original.__anext__()


def iterator(name, original):
    record = current()
    if record is None or not isinstance(name, str) or name not in PHASES[record.scope]:
        return original
    return _Iterator(original.__aiter__(), record, name)
