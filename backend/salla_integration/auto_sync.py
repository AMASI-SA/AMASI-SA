"""Throttled Salla Direct auto-sync for Mezan OS order screens.

Background synchronization must not open Salla Order Details because Salla may
interpret ``GET /orders/{id}`` as the merchant viewing the order and remove the
provider-side "new" indicator.  This module therefore uses only:

* ``GET /orders`` with ``format=light`` for order discovery and list facts.
* ``GET /orders/items?order_id=<internal id>`` for authoritative line items.

The explicit single-order resync path remains the detail/open path and may call
``GET /orders/{id}`` after the merchant actually opens an order in Mezan.

Safety invariants:
* Salla Direct only; Make is never read.
* No Qoyod API calls.
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

from orders_db import upsert_order

from .service import call_salla
from .sync import (
    _fetch_salla_order_items,
    _refresh_plan_b_status_snapshot,
    _salla_order_to_doc,
)

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


def _internal_id(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("id") or "").strip()


async def _discover_recent_orders(db, user_id: str) -> list[dict]:
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

    unique: dict[str, dict] = {}
    for row in rows[:MAX_DISCOVERED_ORDERS]:
        if not isinstance(row, dict):
            continue
        order_number = _reference_id(row)
        internal_id = _internal_id(row)
        if order_number and internal_id:
            unique.setdefault(order_number, dict(row))

    return list(unique.values())


async def _sync_light_order(db, user_id: str, light_order: dict) -> bool:
    """Persist one light order plus line items without opening Order Details."""
    order_number = _reference_id(light_order)
    internal_id = _internal_id(light_order)
    if not order_number or not internal_id:
        return False

    items = await _fetch_salla_order_items(db, user_id, internal_id)
    raw = dict(light_order)
    raw["items"] = items
    raw["mezan_background_sync"] = True

    doc = _salla_order_to_doc(raw)
    if not doc.get("order_number"):
        return False

    await upsert_order(
        db,
        user_id,
        doc["order_number"],
        doc,
        source="salla_direct",
        raw=raw,
    )
    await _refresh_plan_b_status_snapshot(
        db,
        user_id,
        doc["order_number"],
        doc,
    )

    post = await db.unified_orders.find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0, "order_number": 1, "products": 1, "total_amount": 1},
    )
    if post is not None:
        try:
            from product_costs import attach_cost_to_order_doc

            cost_patch = await attach_cost_to_order_doc(db, user_id, post)
            await db.unified_orders.update_one(
                {"user_id": user_id, "order_number": order_number},
                {"$set": cost_patch},
            )
        except Exception:
            log.exception(
                "salla.auto_sync.cost_enrichment_failed user_id=%s order_number=%s",
                user_id,
                order_number,
            )

    return True


async def _run_auto_sync(db, user_id: str) -> None:
    started_at = _utcnow()
    discovered = 0
    synced = 0
    failed = 0

    try:
        orders = await _discover_recent_orders(db, user_id)
        discovered = len(orders)

        for light_order in orders:
            order_number = _reference_id(light_order)
            try:
                if await _sync_light_order(db, user_id, light_order):
                    synced += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                log.warning(
                    "salla.auto_sync.order_failed user_id=%s order_number=%s error=%s",
                    user_id,
                    order_number,
                    str(exc)[:300],
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
