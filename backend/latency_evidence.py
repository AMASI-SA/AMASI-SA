"""Opt-in, bounded latency evidence. No payloads, tenant IDs or CPU estimates.

The existing lag monitor refreshes the permit once per second. Removing the
permit stops recording after the loop resumes; this is not a stall watchdog.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import logging
import os
import stat
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger("mezan.latency_evidence")
_configured = os.environ.get("LATENCY_EVIDENCE_ENABLED") == "1"
_permit = os.environ.get("LATENCY_EVIDENCE_PERMIT_FILE", "")
_enabled = False
_last = {}
_active = {}
_current = contextvars.ContextVar("latency_evidence", default=None)
_identity = None
_worker = (os.getpid(), uuid.uuid4().hex)
STAGES = frozenset({"dashboard", "snapchat"})
PHASES = frozenset({"governor_wait", "mongo_cursor_await", "provider_http_await",
                    "computation_wall", "mongo_write_await", "account_refresh_mixed",
                    "cooperative_computation_wall"})


def refresh() -> None:
    global _enabled
    _enabled = False
    if not _configured or not _permit:
        return
    try:
        info = os.lstat(_permit)
        # Local regular owner-only permit, finite lifetime; never read contents.
        _enabled = (stat.S_ISREG(info.st_mode) and info.st_mode & 0o077 == 0
                    and (not hasattr(os, "getuid") or info.st_uid == os.getuid())
                    and 0 <= time.time() - info.st_mtime <= 900)
    except OSError:
        pass


def _emit(event, **fields):
    global _identity, _worker
    if not _enabled:
        return
    try:
        if _worker[0] != os.getpid():
            _worker = (os.getpid(), uuid.uuid4().hex)
        if _identity is None:
            from release_identity import BOOT_RELEASE_IDENTITY
            _identity = {key: BOOT_RELEASE_IDENTITY.get(key) for key in
                         ("source_git_sha", "deployment_git_sha", "release_id", "boot_started_at")}
        log.info("latency_evidence %s", json.dumps({
            "event": event, "at_utc": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(), "worker_instance": _worker[1], **_identity, **fields,
        }, sort_keys=True))
    except Exception:
        # Diagnostics must not replace a business result or exception.
        pass


def lag_observed(lag_ms):
    if not _enabled or lag_ms < 250:
        return
    now = time.monotonic()
    if now - _last.get("lag", float("-inf")) < 30:
        return
    _last["lag"] = now
    _emit("event_loop_lag", lag_ms=round(lag_ms, 2),
          active=[{"stage": key, "trace": value} for key, value in _active.items()])


@contextlib.contextmanager
def capture(stage):
    now = time.monotonic() if _enabled else 0
    if (not _enabled or stage not in STAGES or stage in _active
            or now - _last.get(stage, float("-inf")) < 30):
        yield
        return
    _last[stage] = now
    trace = uuid.uuid4().hex
    record = {"stage": stage, "trace": trace, "phases": {},
              "started_at_utc": datetime.now(timezone.utc).isoformat()}
    token = _current.set(record)
    _active[stage] = trace
    status = "complete"
    try:
        yield
    except BaseException:
        status = "failed_or_cancelled"
        raise
    finally:
        _active.pop(stage, None)
        _current.reset(token)
        _emit("stage_sample", **record, status=status,
              elapsed_ms=round((time.monotonic() - now) * 1000, 3))


@contextlib.contextmanager
def phase(name):
    record = _current.get() if _enabled else None
    if record is None or name not in PHASES:
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        if _enabled:
            entry = record["phases"].setdefault(name, {"calls": 0, "sum_ms": 0.0, "max_ms": 0.0})
            elapsed = (time.monotonic() - started) * 1000
            entry["calls"] += 1
            entry["sum_ms"] = round(entry["sum_ms"] + elapsed, 3)
            entry["max_ms"] = round(max(entry["max_ms"], elapsed), 3)


def timed(name):
    def decorate(function):
        @functools.wraps(function)
        async def wrapped(*args, **kwargs):
            if not _enabled:
                return await function(*args, **kwargs)
            with phase(name):
                return await function(*args, **kwargs)
        return wrapped
    return decorate


def timed_sync(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if not _enabled:
                return function(*args, **kwargs)
            with phase(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


@contextlib.asynccontextmanager
async def admission(manager):
    # Preserve the original context manager's exception/cancellation semantics.
    with phase("governor_wait"):
        await manager.__aenter__()
    try:
        yield
    except BaseException as exc:
        if not await manager.__aexit__(type(exc), exc, exc.__traceback__):
            raise
    else:
        await manager.__aexit__(None, None, None)
