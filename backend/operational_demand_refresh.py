"""Owned refresh lifetimes; the visible HTTP contract remains request-owned.

The HTTP request owns reads, calculation, a short distributed lease, and its
disconnect listener. Cancellation joins each owned task. Once an aggregate
commit starts, its exact result is observed before releasing the lease.
Ordinary operational writers never enter this context and retain their gates.
An explicitly enabled owner scheduler has a separate non-HTTP lifecycle below;
it shares the same lease, deadline, source limits, and joined calculation/CAS.
"""
import asyncio
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import logging
import math
import time
from threading import Event
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from operational_balance_store import AUTHORIZATION_GUARD, fail
from operational_source_diagnostics import source_failure_detail


LEASES = "operational_balance_refresh_leases_v1"
LEASE_SECONDS = 120
REQUEST_SECONDS = 85
_CURRENT = ContextVar("operational_visible_refresh", default=None)
_LOG = logging.getLogger(__name__)

# Request-local diagnostic vocabulary. No identifiers or exception text can
# enter the timeout response; nested stages overlap and must not be summed.
TIMING_STAGES = frozenset({
    "native_evidence", "aggregate", "source_collection", "source_collision",
    "reconciliation", "profit_snapshot", "bank_claims", "party_catalogs",
    "payment_evidence_link",
    "bundle_order_normalization", "bundle_cost_authority",
    "snapshot_load", "snapshot_compare", "snapshot_pack", "snapshot_commit",
    "snapshot_raw_read", "snapshot_unpack", "snapshot_copy_prepare",
    "orders_cursor_build", "orders_cursor_read", "orders_rows_validate",
    "source_primary_reads", "source_fee_settings", "source_setup",
    "source_statements", "source_delivery_history", "source_transactions",
    "source_native_links", "source_bundles", "source_option_catalog",
    "source_orders", "source_order_indexes", "source_bank_catalog",
    "source_invoices", "source_other_sources", "source_native_overlaps",
    "source_finalize",
})
TIMING_OUTCOMES = frozenset({"complete", "cancelled", "failed"})
TIMING_LIMIT = 64
TIMING_MAX_MS = 300000
PRIMARY_ORDERS_READ_LIMIT = 12
PRIMARY_ORDERS_ROW_LIMIT = 20001
PRIMARY_ORDERS_READ_TIMES = (
    "queue_ms", "budget_ms", "first_row_ms", "last_checkpoint_ms",
    "read_elapsed_ms", "cancel_join_ms", "close_ms",
)
PRIMARY_ORDERS_NULLABLE_TIMES = frozenset({
    "first_row_ms", "last_checkpoint_ms", "read_elapsed_ms", "close_ms",
})


def _safe_primary_orders_read(value):
    """Reconstruct a bounded observation; never serialize a cursor or row.

    Read/first-row timings include driver scheduling and delivery. Queue time
    is the existing source semaphore, not Mongo pool wait. Join/close overlap
    read elapsed time. Rows/first-row describe retained rows, excluding a row
    delivered after stop. Coroutine adapters expose no per-row boundaries.
    None means that boundary was not observed.
    """
    if (type(value) is not dict or value.get("collection") != "unified_orders"
            or type(value.get("collection")) is not str
            or type(value.get("outcome")) is not str
            or value["outcome"] not in TIMING_OUTCOMES
            or type(value.get("cursor_kind")) is not str
            or value["cursor_kind"] not in {"motor", "coroutine", "unknown"}):
        return None
    result = {key: value[key] for key in ("collection", "outcome", "cursor_kind")}
    for key in ("rows_retained", "last_checkpoint_rows"):
        count = value.get(key)
        if type(count) is not int or not 0 <= count <= PRIMARY_ORDERS_ROW_LIMIT:
            return None
        result[key] = count
    if result["last_checkpoint_rows"] > result["rows_retained"]:
        return None
    for key in PRIMARY_ORDERS_READ_TIMES:
        number = value.get(key)
        if number is None and key in PRIMARY_ORDERS_NULLABLE_TIMES:
            result[key] = None
        elif (type(number) in (int, float) and math.isfinite(number)
              and 0 <= number <= TIMING_MAX_MS):
            result[key] = round(number, 3)
        else:
            return None
    return result


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
        self.stage_timings = deque(maxlen=TIMING_LIMIT)
        self.primary_orders_reads = deque(maxlen=PRIMARY_ORDERS_READ_LIMIT)
        self.read_deadline = None

    def record_stage(self, stage, duration_ms, outcome):
        if (not isinstance(stage, str) or stage not in TIMING_STAGES
                or not isinstance(outcome, str) or outcome not in TIMING_OUTCOMES
                or isinstance(duration_ms, bool)
                or not isinstance(duration_ms, (int, float))
                or not 0 <= duration_ms <= TIMING_MAX_MS
                or not math.isfinite(duration_ms)):
            return
        # Keep the tail: cancellation unwinds the innermost bottleneck first,
        # followed by its few enclosing stages. Historical repetitions drop.
        self.stage_timings.append({"stage": stage,
                                   "duration_ms": round(duration_ms, 3),
                                   "outcome": outcome})

    def record_primary_orders_read(self, value):
        # Diagnostics must never replace the source/financial outcome.
        try:
            safe = _safe_primary_orders_read(value)
            if safe is not None:
                self.primary_orders_reads.append(safe)
        except Exception:
            pass

    def primary_read_details(self):
        try:
            rows = [_safe_primary_orders_read(value) for value in self.primary_orders_reads]
            rows = [row for row in rows if row is not None]
            return {"primary_orders_reads": rows} if rows else {}
        except Exception:
            return {}

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
    context = _CURRENT.get()
    if context is None:
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
        try:
            duration_ms = (time.monotonic() - started) * 1000
            context.record_stage(stage, duration_ms, outcome)
            _LOG.info("operational_refresh_stage stage=%s duration_ms=%.3f outcome=%s",
                      stage, duration_ms, outcome)
        except Exception:
            pass  # Evidence failure cannot change the observed operation.


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
                context.read_deadline = time.monotonic() + REQUEST_SECONDS
                async with asyncio.timeout(REQUEST_SECONDS):
                    await context.acquire()
                    return await operation()
            except TimeoutError:
                context.cancelled.set()
                raise HTTPException(status_code=408, detail={
                    "code": "operational_refresh_timeout",
                    "message": "انتهت مهلة التحديث؛ احتُفظ بآخر لقطة ناجحة",
                    "stage_timings": list(context.stage_timings),
                    **context.primary_read_details(),
                })
            except HTTPException as error:
                detail = error.detail
                # Only the canonical sanitized source error gains evidence.
                # At this boundary all operation stages, including aggregate,
                # have unwound. Unrelated errors retain their exact contract.
                if (error.status_code == 409 and type(detail) is dict and len(detail) == 4
                        and all(type(key) is str and type(value) is str
                                for key, value in detail.items())
                        and detail == source_failure_detail(detail.get("cause"), detail.get("source_collection"))):
                    raise HTTPException(status_code=error.status_code, headers=error.headers,
                                        detail={**detail, "stage_timings": list(context.stage_timings),
                                                **context.primary_read_details()}) from None
                raise

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


class ScheduledRefreshContext(DemandRefreshContext):
    """An enabled owner capability, never an invented HTTP Request.

    ``verify`` must refresh the persisted opt-in generation, current owner and
    accounting gate. The supervising child owns cancellation and all cleanup.
    """
    def __init__(self, db, owner, actor, verify, *, stop_event=None):
        super().__init__(None, db, owner, actor, verify)
        self.stop_event = stop_event

    def require_connected(self):
        if self.stop_event is not None and self.stop_event.is_set():
            self.cancelled.set()
        super().require_connected()

    async def checkpoint(self):
        self.require_connected()
        await self.verify()
        await asyncio.sleep(0)
        self.require_connected()
        if self.leased:
            from pymongo import timeout as mongo_timeout
            stamp = self.clock()
            with mongo_timeout(3):
                async with asyncio.timeout(3):
                    changed = await self.db[LEASES].update_one(
                        {"_id": self.owner, "owner_id": self.owner, "actor_id": self.actor,
                         "token": self.token, "expires_at": {"$gt": stamp}},
                        {"$set": {"expires_at": stamp + timedelta(seconds=LEASE_SECONDS)}})
            if changed.matched_count != 1:
                fail("operational_refresh_lease_lost", "انتهت مهلة حجز الاحتساب التلقائي", 409)
        self.require_connected()

    async def listen(self):
        # This event is set by SIGTERM or the real parent pipe's EOF. No HTTP
        # receive emulation and no page/session lifetime is involved.
        await self.stop_event.wait()
        self.cancel()


async def run_scheduled_refresh(db, owner, actor, verify, operation, *, stop_event=None):
    """Run one opted-in owner job with the unchanged 85s/120s boundaries.

    This is a private worker seam. Its caller supplies current persisted owner
    authorization at every checkpoint; it cannot confer permission by itself.
    A cancelled source is joined, while an already admitted atomic commit is
    observed before releasing the same lease used by visible manual refresh.
    """
    if not isinstance(owner, str) or not owner or actor != owner:
        fail("operational_automation_owner_required", "الاحتساب التلقائي متاح للمالك فقط", 403)
    context = ScheduledRefreshContext(db, owner, actor, verify, stop_event=stop_event)
    token = _CURRENT.set(context)
    guard_token = AUTHORIZATION_GUARD.set(context.checkpoint)
    try:
        context.require_connected()

        async def work():
            try:
                context.read_deadline = time.monotonic() + REQUEST_SECONDS
                async with asyncio.timeout(REQUEST_SECONDS):
                    await context.acquire()
                    return await operation()
            except TimeoutError:
                context.cancelled.set()
                raise HTTPException(408, {
                    "code": "operational_refresh_timeout",
                    "message": "انتهت مهلة الاحتساب التلقائي؛ احتُفظ بآخر لقطة ناجحة",
                    "stage_timings": list(context.stage_timings),
                    **context.primary_read_details(),
                }) from None
            except HTTPException as error:
                detail = error.detail
                if (error.status_code == 409 and type(detail) is dict and len(detail) == 4
                        and all(type(key) is str and type(value) is str for key, value in detail.items())
                        and detail == source_failure_detail(detail.get("cause"), detail.get("source_collection"))):
                    raise HTTPException(error.status_code, {
                        **detail, "stage_timings": list(context.stage_timings),
                        **context.primary_read_details(),
                    }, headers=error.headers) from None
                raise

        context.work = asyncio.create_task(work())
        if stop_event is not None:
            context.listener = asyncio.create_task(context.listen())
        try:
            result = await asyncio.shield(context.work)
        except asyncio.CancelledError:
            context.cancel()
            result = await _join(context.work)
            if not context.committed:
                raise
        if context.cancelled.is_set() and not context.committed:
            raise asyncio.CancelledError
        return result
    finally:
        if context.listener is not None:
            context.listener.cancel()
            try:
                await _join(context.listener)
            except asyncio.CancelledError:
                pass
        cleanup = asyncio.create_task(asyncio.wait_for(context.release(), timeout=5))
        try:
            await _join(cleanup)
        except (PyMongoError, asyncio.TimeoutError):
            _LOG.warning("Operational scheduled lease cleanup unavailable; lease will expire")
        finally:
            AUTHORIZATION_GUARD.reset(guard_token)
            _CURRENT.reset(token)
