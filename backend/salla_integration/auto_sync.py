"""Throttled Salla Direct auto-sync for Mezan OS order screens.

The manual 30-day sync discovers orders from ``GET /orders``.  That response
is intentionally light and is not the authoritative source for order lines.
This module discovers recently changed orders, then sends every discovered
order through ``resync_single_order`` which fetches:

* Order Details
* ``GET /orders/items?order_id=<internal id>``
* unified ``products`` and ``cost_items``

Safety invariants:
* Salla Direct only; Make is never read.
* No Qoyod calls.
* At most one task per user in this process.
* Requests are throttled so frontend polling does not hammer Salla.
* Failures are logged and never break the read-only Order Engine response.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .service import call_salla
from .sync import resync_single_order

log = logging.getLogger("salla.auto_sync")

AUTO_SYNC_INTERVAL_SECONDS = 30.0
INITIAL_LOOKBACK_HOURS = 24
OVERLAP_MINUTES = 10
MAX_DISCOVERED_ORDERS = 60

_running_tasks: dict[str, asyncio.Task] = {}
_next_allowed_at: dict[str, float] = {}
_last_success_at: dict[str, datetime] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _reference_id(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("reference_id") or row.get("order_number") or "").strip()


async def _discover_recent_order_numbers(db, user_id: str) -> list[str]:
    now = _utcnow()
    last_success = _last_success_at.get(user_id)

    if last_success is None:
        updated_after = now - timedelta(hours=INITIAL_LOOKBACK_HOURS)
    else:
        updated_after = last_success - timedelta(minutes=OVERLAP_MINUTES)

    response = await call_salla(
        db,
        user_id,
        "GET",
        "/orders",
        params={
            "page": 1,
            "per_page": MAX_DISCOVERED_ORDERS,
            "format": "light",
            "updated_at_gt": updated_after.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    rows = response.get("data") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        return []

    unique: dict[str, None] = {}
    for row in rows[:MAX_DISCOVERED_ORDERS]:
        order_number = _reference_id(row)
        if order_number:
            unique.setdefault(order_number, None)

    return list(unique)


async def _run_auto_sync(db, user_id: str) -> None:
    started_at = _utcnow()
    discovered = 0
    synced = 0
    failed = 0

    try:
        order_numbers = await _discover_recent_order_numbers(db, user_id)
        discovered = len(order_numbers)

        for order_number in order_numbers:
            result = await resync_single_order(db, user_id, order_number)
            if result.get("ok") and result.get("found"):
                synced += 1
            else:
                failed += 1
                log.warning(
                    "salla.auto_sync.order_failed user_id=%s order_number=%s stage=%s error=%s",
                    user_id,
                    order_number,
                    result.get("stage"),
                    result.get("error"),
                )

        _last_success_at[user_id] = started_at
        log.info(
            "salla.auto_sync.completed user_id=%s discovered=%d synced=%d failed=%d",
            user_id,
            discovered,
            synced,
            failed,
        )
    except Exception:
        log.exception(
            "salla.auto_sync.failed user_id=%s discovered=%d synced=%d failed=%d",
            user_id,
            discovered,
            synced,
            failed,
        )
    finally:
        _running_tasks.pop(user_id, None)


def schedule_salla_auto_sync(db, user_id: str) -> bool:
    """Schedule one non-blocking incremental sync when the throttle permits."""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return False

    current = _running_tasks.get(normalized_user_id)
    if current is not None and not current.done():
        return False

    now_monotonic = time.monotonic()
    if now_monotonic < _next_allowed_at.get(normalized_user_id, 0.0):
        return False

    _next_allowed_at[normalized_user_id] = (
        now_monotonic + AUTO_SYNC_INTERVAL_SECONDS
    )
    task = asyncio.create_task(
        _run_auto_sync(db, normalized_user_id),
        name=f"salla-auto-sync:{normalized_user_id}",
    )
    _running_tasks[normalized_user_id] = task
    return True
