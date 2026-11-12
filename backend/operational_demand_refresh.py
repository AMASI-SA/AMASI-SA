"""Visible-page refresh lifetime; no scheduler, polling or detached work.

The HTTP request owns reads, calculation, a short distributed lease, and its
disconnect listener. Cancellation joins each owned task. Once an aggregate
commit starts, its exact result is observed before releasing the lease.
Ordinary operational writers never enter this context and retain their gates.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import logging
import time
from threading import Event
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from operational_balance_store import AUTHORIZATION_GUARD, fail


LEASES = "operational_balance_refresh_leases_v1"
LEASE_SECONDS = 120
REQUEST_SECONDS = 85
_CURRENT = ContextVar("operational_visible_refresh", default=None)
_LOG = logging.getLogger(__name__)


async def _join(task):
    """Observe an owned task even if the HTTP task is cancelled repeatedly."""
    while not task.done():
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
    return task.result()


class DemandRefreshContext:
    def __init__(self, request, db, owner, actor, verify, *, clock=None):
        self.request, self.db = request, db
        self.owner, self.actor, self.verify = owner, actor, verify
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cancelled = Event()
        self.disconnected = False
        self.committing = False
        self.committed = False
        self.listener = None
        self.work = None
        self.token = uuid4().hex
        self.lease_attempted = False
        self.leased = False

    def cancel(self, *, disconnected=False):
        self.disconnected = self.disconnected or disconnected
        self.cancelled.set()
        if not self.committing and self.work is not None and not self.work.done():
            self.work.cancel()

    def require_connected(self):
        if self.cancelled.is_set() and not self.committing:
            raise asyncio.CancelledError

    async def checkpoint(self):
        self.require_connected()
        # Once listening, receive has a single consumer. The listener records
        # http.disconnect synchronously before cancelling any source work;
        # calling Request.is_disconnected concurrently could consume its event.
        if self.listener is None and not self.committing:
            if await self.request.is_disconnected():
                self.cancel(disconnected=True)
                raise asyncio.CancelledError
        await self.verify()
        if self.listener is not None and not self.committing:
            # A ready/cached async read need not yield. Give the sole receive
            # listener one loop turn before admitting a write. sleep(0) is a
            # cooperative yield, not a timer, polling loop or background job.
            await asyncio.sleep(0)
        self.require_connected()
        if self.leased:
            stamp = self.clock()
            changed = await self.db[LEASES].update_one(
                {"_id": self.owner, "owner_id": self.owner, "actor_id": self.actor,
                 "token": self.token, "expires_at": {"$gt": stamp}},
                {"$set": {"expires_at": stamp + timedelta(seconds=LEASE_SECONDS)}})
            if changed.matched_count != 1:
                fail("operational_refresh_lease_lost", "انتهت مهلة التحديث؛ أعد المحاولة من الصفحة", 409)
            if self.listener is not None and not self.committing:
                await asyncio.sleep(0)
        self.require_connected()

    async def acquire(self):
        await self.checkpoint()
        stamp = self.clock()
        self.lease_attempted = True
        try:
            saved = await self.db[LEASES].find_one_and_update(
                {"_id": self.owner, "$or": [{"expires_at": {"$lte": stamp}}, {"token": self.token}]},
                {"$set": {"owner_id": self.owner, "actor_id": self.actor, "token": self.token,
                          "expires_at": stamp + timedelta(seconds=LEASE_SECONDS)}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            fail("operational_refresh_in_progress", "يوجد تحديث جارٍ لهذا الحساب؛ انتظر اكتماله", 409)
        if not saved or saved.get("token") != self.token:
            fail("operational_refresh_in_progress", "يوجد تحديث جارٍ لهذا الحساب؛ انتظر اكتماله", 409)
        self.leased = True
        self.require_connected()

    async def release(self):
        if self.lease_attempted:
            # A cancelled/unknown acquire may have succeeded. Compare-token
            # cleanup also covers that case and can never release a successor.
            await self.db[LEASES].delete_one(
                {"_id": self.owner, "owner_id": self.owner, "token": self.token})
        self.leased = False

    async def listen(self):
        while True:
            message = await self.request.receive()
            if message["type"] == "http.disconnect":
                self.cancel(disconnected=True)
                return


def demand_context():
    return _CURRENT.get()


@contextmanager
def refresh_stage(stage):
    """One timing record per awaited phase, only inside a visible refresh.

    Call sites pass fixed stage names, never owner/order data or exception text.
    This observes the existing request; it starts no task or additional read.
    """
    if _CURRENT.get() is None:
        yield
        return
    started = time.monotonic()
    outcome = "complete"
    try:
        yield
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except BaseException:
        outcome = "failed"
        raise
    finally:
        _LOG.info("operational_refresh_stage stage=%s duration_ms=%.3f outcome=%s",
                  stage, (time.monotonic() - started) * 1000, outcome)


async def checkpoint():
    """Revalidate the visible request between read/calculation stages."""
    context = _CURRENT.get()
    if context is not None:
        await context.checkpoint()


def calculation_checkpoint():
    """Cooperative cancellation for pure CPU loops (including to_thread)."""
    # The same reducer is also used by read-only supplier previews. Their
    # request-owned stop event must end the calculation without a refresh lease.
    from operational_financial_activity_runtime import preparation_checkpoint
    preparation_checkpoint()
    context = _CURRENT.get()
    if context is not None:
        context.require_connected()


async def run_calculation(function, *args, **kwargs):
    context = _CURRENT.get()
    if context is None:
        return await asyncio.to_thread(function, *args, **kwargs)
    await context.checkpoint()
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        context.cancelled.set()
        try:
            await _join(task)
        except asyncio.CancelledError:
            pass
        raise
    await context.checkpoint()
    return result


async def run_commit(operation):
    """Run one archive-stage/CAS attempt; outside demand, await unchanged."""
    context = _CURRENT.get()
    if context is None:
        return await operation()
    await context.checkpoint()
    context.committing = True
    task = asyncio.create_task(operation())
    try:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            context.cancelled.set()
            result = await _join(task)
        context.committed = context.committed or result is True
        return result
    finally:
        context.committing = False


async def run_visible_refresh(request, db, owner, actor, verify, operation):
    """Execute one visible HTTP refresh and finish all its owned cleanup."""
    context = DemandRefreshContext(request, db, owner, actor, verify)
    token = _CURRENT.set(context)
    guard_token = AUTHORIZATION_GUARD.set(context.checkpoint)
    result = None
    try:
        # The request body is parsed before this function. Probe once before
        # starting a single event-driven receive listener; there is no timer.
        if await request.is_disconnected():
            context.disconnected = True
            fail("operational_refresh_cancelled", "أُلغي تحديث الصفحة", 499)

        async def work():
            try:
                # Source/CPU work cannot outlive the short lease. This timer
                # belongs to this request and is removed on exit. run_commit
                # observes an already admitted atomic result even at deadline.
                async with asyncio.timeout(REQUEST_SECONDS):
                    await context.acquire()
                    return await operation()
            except TimeoutError:
                context.cancelled.set()
                fail("operational_refresh_timeout", "انتهت مهلة التحديث؛ احتُفظ بآخر لقطة ناجحة", 408)

        context.work = asyncio.create_task(work())
        context.listener = asyncio.create_task(context.listen())
        try:
            result = await asyncio.shield(context.work)
        except asyncio.CancelledError:
            context.cancel()
            try:
                result = await _join(context.work)
            except asyncio.CancelledError:
                if context.disconnected:
                    fail("operational_refresh_cancelled", "أُلغي تحديث الصفحة", 499)
                raise
            if not context.committed:
                if context.disconnected:
                    fail("operational_refresh_cancelled", "أُلغي تحديث الصفحة", 499)
                raise
        if context.cancelled.is_set() and not context.committed:
            fail("operational_refresh_cancelled", "أُلغي تحديث الصفحة", 499)
        return result
    finally:
        if context.listener is not None:
            context.listener.cancel()
            try:
                await _join(context.listener)
            except asyncio.CancelledError:
                pass
        # Cleanup remains request-owned and is observed before returning. A DB
        # outage leaves only a short expiring lease, never a detached retry.
        cleanup = asyncio.create_task(asyncio.wait_for(context.release(), timeout=5))
        try:
            await _join(cleanup)
        except (PyMongoError, asyncio.TimeoutError):
            _LOG.warning("Operational refresh lease cleanup unavailable; lease will expire")
        finally:
            AUTHORIZATION_GUARD.reset(guard_token)
            _CURRENT.reset(token)
