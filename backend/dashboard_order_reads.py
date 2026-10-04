"""Request-local dashboard cohorts with bounded V2 buffers and legacy compatibility.

Consumers must treat returned rows as read-only and apply their own accounting
filters. There is no cross-request payload cache or change to financial rules.
"""
from __future__ import annotations

import asyncio
import uuid
from itertools import islice
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from typing import Any

from bson import json_util

from order_currency import SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION, attach_projected_salla_attribution,
)

ORDER_LIMIT = 100_000
PROOF_BATCH_SIZE = 128
_cohorts: ContextVar[dict | None] = ContextVar("dashboard_order_cohorts", default=None)
_spill: ContextVar[Any] = ContextVar("dashboard_spill", default=None)


@asynccontextmanager
async def dashboard_order_read_scope(*, bounded=False):
    """Own in-flight reads for exactly one summary invocation, including child tasks."""
    reads: dict = {}
    token = _cohorts.set(reads)
    store = None
    spill_token = None
    try:
        if bounded:
            from dashboard_spill import DashboardSpill
            store = DashboardSpill()
            spill_token = _spill.set(store)
        yield
    finally:
        _cohorts.reset(token)
        pending = [task for task in reads.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        reads.clear()
        if spill_token is not None:
            _spill.reset(spill_token)
            # No data survives this request; flushing discarded cache entries
            # during cleanup can mask the original budget/cancellation error.
            store.close(flush=False)


def shared_dashboard_order_reads(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        async with dashboard_order_read_scope():
            return await function(*args, **kwargs)
    return wrapped


def bounded_dashboard_reads(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        async with dashboard_order_read_scope(bounded=True):
            return await function(*args, **kwargs)
    return wrapped


def dashboard_spill():
    return _spill.get()


def bounded_rows(rows, label="rows"):
    store = dashboard_spill()
    if store is None:
        return list(rows)
    return store.sequence_from(label + "-" + uuid.uuid4().hex, rows)


async def _load_bounded(db, query, include_marketing_attribution):
    """One private replay snapshot; batches never retain the complete cohort.

    Global last-proof order is preserved even for numeric/space-padded historic
    order numbers. Narrow proof maps spill to disk, not a per-batch N+1 query.
    All matching rows are consumed; there is no 100k silent result cap.
    """
    store = dashboard_spill()
    scope = uuid.uuid4().hex
    source = store.sequence(scope + "-source")
    proofs = store.map(scope + "-fx")
    attributes = store.map(scope + "-attribution")
    cursor = db.unified_orders.find(query, {"_id": 0, "raw_by_source": 0}).batch_size(PROOF_BATCH_SIZE)
    try:
        while True:
            batch = await cursor.to_list(length=PROOF_BATCH_SIZE)
            if not batch:
                break
            source.extend(batch)
    finally:
        await cursor.close()
    if not source:
        return source
    projection = dict(SALLA_RAW_CURRENCY_PROJECTION)
    if include_marketing_attribution:
        projection.update(SALLA_RAW_ATTRIBUTION_PROJECTION)
    cursor = db.unified_orders.find(query, projection).batch_size(PROOF_BATCH_SIZE)
    try:
        async for row in cursor:
            number = str(row.get("order_number") or "").strip()
            if number:
                proofs[number] = row
                if include_marketing_attribution and isinstance(row.get("raw_by_source"), dict):
                    attributes[number] = row
    finally:
        await cursor.close()
    result = store.sequence(scope + "-hydrated")
    iterator = iter(source)
    while batch := list(islice(iterator, PROOF_BATCH_SIZE)):
        keys = [str(row.get("order_number") or "").strip() for row in batch]
        hydrate_order_currency_fields(batch, [proofs[key] for key in keys if key in proofs])
        if include_marketing_attribution:
            attach_projected_salla_attribution(batch, [attributes[key] for key in keys if key in attributes])
        result.extend(batch)
        await asyncio.sleep(0)
    store.discard_sequence(source.name)
    store.discard_map(proofs.name)
    store.discard_map(attributes.name)
    return result


async def _load(db: Any, query: dict, include_marketing_attribution: bool) -> list[dict]:
    # Keep the existing whole-cohort identity normalization semantics. A raw
    # $in per batch is not equivalent for historical numeric/space-padded IDs.
    # Reuse eliminates duplicate cohorts, not their remaining O(N) retention.
    cursor = db.unified_orders.find(query, {"_id": 0, "raw_by_source": 0})
    cursor = cursor.limit(ORDER_LIMIT).batch_size(PROOF_BATCH_SIZE)
    try:
        orders = await cursor.to_list(length=ORDER_LIMIT)
    finally:
        await cursor.close()
    if not orders:
        return orders
    projection = dict(SALLA_RAW_CURRENCY_PROJECTION)
    if include_marketing_attribution:
        projection.update(SALLA_RAW_ATTRIBUTION_PROJECTION)
    proof_cursor = db.unified_orders.find(query, projection).limit(ORDER_LIMIT).batch_size(PROOF_BATCH_SIZE)
    try:
        proofs = await proof_cursor.to_list(length=ORDER_LIMIT)
    finally:
        await proof_cursor.close()
    hydrate_order_currency_fields(orders, proofs)
    if include_marketing_attribution:
        attach_projected_salla_attribution(orders, proofs)
    return orders


async def load_dashboard_orders(
    db: Any, query: dict, *, include_marketing_attribution: bool = True,
) -> list[dict]:
    """Load an unfiltered cohort, sharing matching reads only inside a scope."""
    snapshot = deepcopy(query)
    loader = _load_bounded if dashboard_spill() is not None else _load
    reads = _cohorts.get()
    if reads is None:
        return await loader(db, snapshot, include_marketing_attribution)
    key = (id(db), json_util.dumps(snapshot, sort_keys=True), include_marketing_attribution)
    task = reads.get(key)
    if task is None:
        task = asyncio.create_task(loader(db, snapshot, include_marketing_attribution))
        reads[key] = task
    try:
        return await asyncio.shield(task)
    except Exception:
        if reads.get(key) is task:
            reads.pop(key, None)
        raise


async def read_recent_dashboard_analyses(db, user_id, *, include):
    """V2 discards legacy analysis summaries; never materialize their report blobs."""
    if not include:
        return []
    fields = ("id", "name", "date", "filename", "orders_imported",
              "report.summary.total_sales", "report.summary.net_profit",
              "report.summary.total_orders")
    return await db.analyses.find(
        {"user_id": user_id}, {"_id": 0, **{key: 1 for key in fields}},
    ).sort("created_at", -1).limit(5).to_list(length=5)
