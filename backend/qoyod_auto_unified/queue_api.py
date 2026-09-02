"""Bounded, coalesced unsent-orders response with authoritative queue counts."""
from __future__ import annotations

import asyncio
import copy
import time
import weakref
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Optional

from integrations.qoyod.unsent_orders import DUPLICATE, FAILED, SENT, UNSENT

from .common import RETRYABLE_SYNC_FAILURE_CODES
from .queue_counts import _proof_classification, _queue_audit


QOYOD_DASHBOARD_MAX_CONCURRENCY = 2
QOYOD_DASHBOARD_CACHE_TTL_SECONDS = 20.0
_LOOP_STATES: weakref.WeakKeyDictionary[Any, dict[str, Any]] = (
    weakref.WeakKeyDictionary()
)


def _loop_state() -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    state = _LOOP_STATES.get(loop)
    if state is None:
        state = {
            "semaphore": asyncio.Semaphore(QOYOD_DASHBOARD_MAX_CONCURRENCY),
            "inflight": {},
            "cache": {},
        }
        _LOOP_STATES[loop] = state
    now = time.monotonic()
    expired = [
        key for key, (expires_at, _value) in state["cache"].items()
        if expires_at <= now
    ]
    for key in expired:
        state["cache"].pop(key, None)
    return state


def _key_part(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _query_key(
    original: Callable[..., Awaitable[dict[str, Any]]],
    db: Any,
    *,
    user_id: str,
    days: int,
    limit: int,
    orders_user_id: Optional[str],
    status: Optional[str],
    salla_status: Optional[str],
    search: Optional[str],
    now: Optional[datetime],
    from_date: Any,
    to_date: Any,
) -> tuple[Any, ...]:
    return (
        id(original),
        id(db),
        str(user_id),
        str(orders_user_id or user_id),
        int(days),
        int(limit),
        _key_part(status),
        _key_part(salla_status),
        _key_part(search),
        _key_part(now),
        _key_part(from_date),
        _key_part(to_date),
    )


async def _execute_list(
    original: Callable[..., Awaitable[dict[str, Any]]],
    db: Any,
    *,
    user_id: str,
    days: int = 30,
    limit: int = 500,
    orders_user_id: Optional[str] = None,
    status: Optional[str] = None,
    salla_status: Optional[str] = None,
    search: Optional[str] = None,
    now: Optional[datetime] = None,
    from_date: Any = None,
    to_date: Any = None,
) -> dict[str, Any]:
    """Execute one read model build and reuse its private audit for counts."""
    from integrations.qoyod import unsent_orders as unsent_module

    effective_owner = str(orders_user_id or user_id)
    result = await original(
        db,
        user_id=user_id,
        days=days,
        limit=limit,
        orders_user_id=orders_user_id,
        status=None,
        salla_status=salla_status,
        search=search,
        now=now,
        from_date=from_date,
        to_date=to_date,
        _include_internal=True,
    )
    audit = result.pop("_candidate_audit")
    failures = result.pop("_manual_failures")
    audit, failures, queue = await _queue_audit(
        db,
        user_id=str(user_id),
        orders_user_id=effective_owner,
        days=days,
        search=search,
        now=now,
        from_date=from_date,
        to_date=to_date,
        audit=audit,
        failures=failures,
    )
    proof_by_reference = {
        str(proof.get("order_number") or ""): proof
        for proof in audit.get("orders") or []
    }

    counts = {SENT: 0, UNSENT: 0, FAILED: 0, DUPLICATE: 0}
    classification_by_reference: dict[str, dict[str, Any]] = {}
    for reference, proof in proof_by_reference.items():
        classification = _proof_classification(
            unsent_module, proof, failures.get(reference)
        )
        classification_by_reference[reference] = classification
        counts[classification["status"]] += 1

    rows: list[dict[str, Any]] = []
    for entry in result.get("orders") or []:
        reference = str(entry.get("order_number") or "")
        classification = classification_by_reference.get(reference)
        if classification:
            entry = {
                **entry,
                "status": classification["status"],
                "reason": classification["reason"],
                "failure_code": classification.get("failure_code"),
                "failure_source": classification.get("failure_source"),
                "retry_allowed": bool(
                    classification.get("retry_allowed", False)
                ),
                "payment_eligibility": (
                    proof_by_reference.get(reference) or {}
                ).get("payment_eligibility"),
            }
        if status and entry.get("status") != status:
            continue
        rows.append(entry)

    matched = sum(
        1
        for classification in classification_by_reference.values()
        if not status or classification["status"] == status
    )
    result.update({
        "counts": counts,
        "total": sum(counts.values()),
        "queue_counts": queue,
        "queue_policy": {
            "source_authority": "unified_orders",
            "selection_order": "oldest_first",
            "retryable_sync_failure_codes": sorted(
                RETRYABLE_SYNC_FAILURE_CODES
            ),
        },
        "matched_order_count": matched,
        "returned_order_count": len(rows),
        "truncated": matched > len(rows),
        "orders": rows,
    })
    return result


async def _run_bounded(
    state: dict[str, Any],
    original: Callable[..., Awaitable[dict[str, Any]]],
    db: Any,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    async with state["semaphore"]:
        return await _execute_list(original, db, **kwargs)


async def _list_unsent_orders_with_queue_counts(
    original: Callable[..., Awaitable[dict[str, Any]]],
    db: Any,
    *,
    user_id: str,
    days: int = 30,
    limit: int = 500,
    orders_user_id: Optional[str] = None,
    status: Optional[str] = None,
    salla_status: Optional[str] = None,
    search: Optional[str] = None,
    now: Optional[datetime] = None,
    from_date: Any = None,
    to_date: Any = None,
) -> dict[str, Any]:
    """Coalesce an identical tenant/query and cap distinct heavy reads at two."""
    kwargs = {
        "user_id": user_id,
        "days": days,
        "limit": limit,
        "orders_user_id": orders_user_id,
        "status": status,
        "salla_status": salla_status,
        "search": search,
        "now": now,
        "from_date": from_date,
        "to_date": to_date,
    }
    key = _query_key(original, db, **kwargs)
    state = _loop_state()
    cached = state["cache"].get(key)
    if cached is not None and cached[0] > time.monotonic():
        return copy.deepcopy(cached[1])

    task = state["inflight"].get(key)
    if task is None:
        task = asyncio.create_task(
            _run_bounded(state, original, db, kwargs),
            name="qoyod-unsent-orders-read",
        )
        state["inflight"][key] = task

        def completed(done: asyncio.Task) -> None:
            if state["inflight"].get(key) is done:
                state["inflight"].pop(key, None)
            if done.cancelled():
                return
            try:
                value = done.result()
            except BaseException:
                return
            state["cache"][key] = (
                time.monotonic() + QOYOD_DASHBOARD_CACHE_TTL_SECONDS,
                copy.deepcopy(value),
            )

        task.add_done_callback(completed)

    return copy.deepcopy(await asyncio.shield(task))


def _reset_query_coordinator_for_tests() -> None:
    """Clear loop-local state; production never calls this helper."""
    _LOOP_STATES.clear()
