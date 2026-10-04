"""Request-local reuse of dashboard cohorts; the retained cohort is still capped at 100k.

Consumers must treat returned rows as read-only and apply their own accounting
filters. There is no cross-request payload cache or change to financial rules.
"""
from __future__ import annotations

import asyncio
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


@asynccontextmanager
async def dashboard_order_read_scope():
    """Own in-flight reads for exactly one summary invocation, including child tasks."""
    reads: dict = {}
    token = _cohorts.set(reads)
    try:
        yield
    finally:
        _cohorts.reset(token)
        pending = [task for task in reads.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        reads.clear()


def shared_dashboard_order_reads(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        async with dashboard_order_read_scope():
            return await function(*args, **kwargs)
    return wrapped


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
    reads = _cohorts.get()
    if reads is None:
        return await _load(db, snapshot, include_marketing_attribution)
    key = (id(db), json_util.dumps(snapshot, sort_keys=True), include_marketing_attribution)
    task = reads.get(key)
    if task is None:
        task = asyncio.create_task(_load(db, snapshot, include_marketing_attribution))
        reads[key] = task
    try:
        return await asyncio.shield(task)
    except Exception:
        if reads.get(key) is task:
            reads.pop(key, None)
        raise
