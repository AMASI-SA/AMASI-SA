"""Throttled Salla Direct auto-sync for Mezan OS order screens.

Background synchronization must not open Salla Order Details because Salla may
interpret ``GET /orders/{id}`` as the merchant viewing the order and remove the
provider-side "new" indicator. This module therefore uses only:

* ``GET /orders`` with ``format=light`` for discovery and status reconciliation.
* ``GET /orders/items?order_id=<internal id>`` for authoritative line items on
  newly discovered or recently updated orders only.

The explicit single-order resync path remains the detail/open path and may call
``GET /orders/{id}`` after the merchant actually opens an order in Mezan.

Safety invariants:
* Salla Direct only; Make is never read.
* No Qoyod API calls.
* Status reconciliation never opens Order Details.
* Status reconciliation never replaces products or costs.
* At most one task per user in this process.
* Requests are throttled so frontend polling does not hammer Salla.
* Failures are logged and never break the read-only Order Engine response.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from orders_db import upsert_order

from .service import SallaError, call_salla
from .sync import (
    _fetch_salla_order_items,
    _refresh_plan_b_status_snapshot,
    _salla_order_to_doc,
)

log = logging.getLogger("salla.auto_sync")

AUTO_SYNC_INTERVAL_SECONDS = 30.0
INITIAL_LOOKBACK_HOURS = 24
OVERLAP_MINUTES = 10
DISCOVERY_PER_PAGE = 60
MAX_DISCOVERY_PAGES = 10
MAX_DISCOVERED_ORDERS = 600
MAX_PROVIDER_READ_ATTEMPTS = 4
PROVIDER_BACKOFF_BASE_SECONDS = 0.25

RETRY_BATCH_SIZE = 20
RETRY_MAX_ATTEMPTS = 6
RETRY_BACKOFF_BASE_SECONDS = 30
RETRY_BACKOFF_MAX_SECONDS = 60 * 60
RETRY_COLLECTION = "salla_auto_sync_retry_ledger"
STATE_COLLECTION = "salla_auto_sync_state"

GAP_RECONCILIATION_MAX_HOURS = 48
GAP_RECONCILIATION_MAX_PAGES = 12
GAP_RECONCILIATION_MAX_ORDERS = 720

# Reconcile two lightweight pages per run. With 1,300 orders this completes one
# full pass in roughly 6-7 minutes while the orders screen is active.
STATUS_RECONCILE_PER_PAGE = 50
STATUS_RECONCILE_PAGES_PER_RUN = 2

_running_tasks: dict[str, asyncio.Task] = {}
_next_allowed_at: dict[str, float] = {}
_last_success_at: dict[str, datetime] = {}
_status_reconcile_page: dict[str, int] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _provider_updated_at(row: Any) -> datetime | None:
    if not isinstance(row, dict):
        return None
    value = row.get("updated_at")
    if isinstance(value, dict):
        value = value.get("date") or value.get("datetime")
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _order_sort_key(row: dict) -> tuple[str, str, str]:
    updated_at = _provider_updated_at(row)
    return (
        updated_at.isoformat() if updated_at else "",
        _reference_id(row),
        _internal_id(row),
    )


def _error_code(exc: Exception) -> tuple[str, bool]:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "provider_rate_limited", True
    if isinstance(status_code, int) and status_code >= 500:
        return "provider_unavailable", True
    if status_code in {401, 403}:
        return "provider_authorization_failed", False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "operation_timeout", True
    return "transient_order_failure", True


async def _call_salla_read_with_backoff(
    db,
    user_id: str,
    path: str,
    *,
    params: dict,
) -> tuple[dict, int]:
    """Run one bounded, read-only Salla request without retry storms."""
    attempts = 0
    while attempts < MAX_PROVIDER_READ_ATTEMPTS:
        attempts += 1
        try:
            response = await call_salla(
                db,
                user_id,
                "GET",
                path,
                params=params,
            )
            return response, attempts
        except SallaError as exc:
            status_code = getattr(exc, "status_code", None)
            retryable = status_code == 429 or (
                isinstance(status_code, int) and status_code >= 500
            )
            if not retryable or attempts >= MAX_PROVIDER_READ_ATTEMPTS:
                raise
            await asyncio.sleep(
                PROVIDER_BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
            )
    raise RuntimeError("bounded Salla read attempts exhausted")


def _pagination_values(response: dict, page: int) -> tuple[int, int]:
    pagination = response.get("pagination") or {}
    if not isinstance(pagination, dict):
        pagination = {}
    total_pages = int(
        pagination.get("totalPages")
        or pagination.get("total_pages")
        or pagination.get("last_page")
        or 0
    )
    current_page = int(
        pagination.get("currentPage")
        or pagination.get("current_page")
        or pagination.get("page")
        or page
    )
    return current_page, total_pages


async def _list_light_orders_bounded(
    db,
    user_id: str,
    *,
    filters: dict[str, Any],
    max_pages: int,
    max_orders: int,
) -> dict[str, Any]:
    """List and deduplicate light orders within explicit provider bounds."""
    unique: dict[str, dict] = {}
    internal_by_number: dict[str, str] = {}
    ambiguous: set[str] = set()
    pages_fetched = 0
    provider_calls = 0
    source_total_pages = 0
    exhausted = False

    for page in range(1, max_pages + 1):
        response, attempts = await _call_salla_read_with_backoff(
            db,
            user_id,
            "/orders",
            params={
                **filters,
                "page": page,
                "per_page": DISCOVERY_PER_PAGE,
                "format": "light",
            },
        )
        provider_calls += attempts
        pages_fetched += 1
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list) or not rows:
            exhausted = True
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            order_number = _reference_id(row)
            internal_id = _internal_id(row)
            if not order_number or not internal_id:
                continue
            previous_internal_id = internal_by_number.get(order_number)
            if previous_internal_id and previous_internal_id != internal_id:
                ambiguous.add(order_number)
                continue
            internal_by_number[order_number] = internal_id
            unique.setdefault(order_number, dict(row))
            if len(unique) >= max_orders:
                break

        current_page, total_pages = _pagination_values(response, page)
        source_total_pages = max(source_total_pages, total_pages)
        if len(unique) >= max_orders:
            break
        if total_pages and current_page >= total_pages:
            exhausted = True
            break

    truncated = not exhausted and (
        len(unique) >= max_orders
        or pages_fetched >= max_pages
        or source_total_pages > max_pages
    )
    return {
        "orders": sorted(
            (
                row
                for order_number, row in unique.items()
                if order_number not in ambiguous
            ),
            key=_order_sort_key,
        ),
        "pages_fetched": pages_fetched,
        "provider_calls": provider_calls,
        "source_total_pages": source_total_pages,
        "truncated": truncated,
        "ambiguous_order_numbers": sorted(ambiguous),
    }


def _reference_id(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("reference_id") or row.get("order_number") or "").strip()


def _internal_id(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("id") or "").strip()


def _status_values(row: Any) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(row, dict):
        return "", "", {}
    raw_status = row.get("status") or {}
    if isinstance(raw_status, dict):
        name = str(raw_status.get("name") or raw_status.get("customized") or "").strip()
        slug = str(raw_status.get("slug") or "").strip().lower()
        return name, slug, dict(raw_status)
    name = str(raw_status or "").strip()
    return name, "", {"name": name} if name else {}


async def _load_checkpoint(db, user_id: str) -> datetime | None:
    state = await getattr(db, STATE_COLLECTION).find_one(
        {"user_id": str(user_id)},
        {"_id": 0, "checkpoint_at": 1},
    )
    durable = _as_utc((state or {}).get("checkpoint_at"))
    return durable or _as_utc(_last_success_at.get(user_id))


async def _discover_recent_orders(db, user_id: str) -> dict[str, Any]:
    now = _utcnow()
    checkpoint = await _load_checkpoint(db, user_id)
    if checkpoint is None:
        updated_after = now - timedelta(hours=INITIAL_LOOKBACK_HOURS)
    else:
        # Salla's filter is strictly greater-than. Backing up by the overlap
        # keeps rows on an equal timestamp boundary discoverable.
        updated_after = checkpoint - timedelta(minutes=OVERLAP_MINUTES)

    result = await _list_light_orders_bounded(
        db,
        user_id,
        filters={
            "updated_at_gt": updated_after.strftime("%Y-%m-%d %H:%M:%S"),
        },
        max_pages=MAX_DISCOVERY_PAGES,
        max_orders=MAX_DISCOVERED_ORDERS,
    )
    result["updated_after"] = updated_after
    return result


def _retry_delay_seconds(attempt_count: int) -> int:
    exponent = max(0, min(int(attempt_count) - 1, 10))
    return min(
        RETRY_BACKOFF_BASE_SECONDS * (2**exponent),
        RETRY_BACKOFF_MAX_SECONDS,
    )


async def _record_retry_failure(
    db,
    user_id: str,
    light_order: dict,
    *,
    stage: str,
    error_code: str,
    retryable: bool,
    attempted_at: datetime,
) -> dict[str, Any]:
    """Durably preserve one discovered failure before advancing checkpoint."""
    order_number = _reference_id(light_order)
    internal_id = _internal_id(light_order)
    if not order_number or not internal_id:
        raise RuntimeError("cannot persist retry without Salla order identity")

    collection = getattr(db, RETRY_COLLECTION)
    existing = await collection.find_one(
        {"user_id": str(user_id), "order_number": order_number},
        {"_id": 0, "attempt_count": 1, "first_discovered_at": 1},
    )
    attempt_count = int((existing or {}).get("attempt_count") or 0) + 1
    automatic_retry = bool(retryable and attempt_count < RETRY_MAX_ATTEMPTS)
    next_retry_at = (
        attempted_at + timedelta(seconds=_retry_delay_seconds(attempt_count))
        if automatic_retry
        else None
    )
    await collection.update_one(
        {"user_id": str(user_id), "order_number": order_number},
        {
            "$set": {
                "internal_order_id": internal_id,
                "light_order": dict(light_order),
                "provider_updated_at": _provider_updated_at(light_order),
                "attempt_count": attempt_count,
                "last_error_code": str(error_code)[:80],
                "last_failure_stage": str(stage)[:80],
                "last_attempt_at": attempted_at,
                "next_retry_at": next_retry_at,
                "retryable": automatic_retry,
                "status": "retryable" if automatic_retry else "exhausted",
                "updated_at": attempted_at,
            },
            "$setOnInsert": {
                "user_id": str(user_id),
                "order_number": order_number,
                "first_discovered_at": attempted_at,
            },
        },
        upsert=True,
    )
    return {
        "order_number": order_number,
        "stage": str(stage)[:80],
        "error_code": str(error_code)[:80],
        "retryable": automatic_retry,
        "attempt_count": attempt_count,
        "next_retry_at": next_retry_at,
    }


async def _clear_retry_failure(db, user_id: str, order_number: str) -> None:
    await getattr(db, RETRY_COLLECTION).delete_one(
        {"user_id": str(user_id), "order_number": str(order_number)}
    )


async def _load_due_retry_orders(
    db,
    user_id: str,
    *,
    now: datetime,
) -> list[dict]:
    cursor = getattr(db, RETRY_COLLECTION).find(
        {
            "user_id": str(user_id),
            "status": "retryable",
            "retryable": True,
            "next_retry_at": {"$lte": now},
        },
        {
            "_id": 0,
            "order_number": 1,
            "light_order": 1,
            "next_retry_at": 1,
        },
    ).sort([("next_retry_at", 1), ("order_number", 1)]).limit(RETRY_BATCH_SIZE)
    rows: list[dict] = []
    async for item in cursor:
        light_order = item.get("light_order")
        if isinstance(light_order, dict):
            rows.append(dict(light_order))
    return rows


async def _persist_run_state(
    db,
    user_id: str,
    *,
    checkpoint_at: datetime | None,
    summary: dict[str, Any],
) -> None:
    now = _utcnow()
    patch: dict[str, Any] = {
        "last_run_at": now,
        "last_run": summary,
        "updated_at": now,
    }
    if checkpoint_at is not None:
        patch["checkpoint_at"] = checkpoint_at
    if summary.get("status_reconciliation_succeeded"):
        patch["last_successful_reconciliation_at"] = now
    await getattr(db, STATE_COLLECTION).update_one(
        {"user_id": str(user_id)},
        {
            "$set": patch,
            "$setOnInsert": {"user_id": str(user_id), "created_at": now},
        },
        upsert=True,
    )


class OrderSyncFailure(Exception):
    def __init__(self, stage: str, error_code: str, *, retryable: bool) -> None:
        super().__init__(error_code)
        self.stage = stage
        self.error_code = error_code
        self.retryable = retryable


async def _sync_light_order(db, user_id: str, light_order: dict) -> bool:
    """Persist one light order plus line items without opening Order Details."""
    order_number = _reference_id(light_order)
    internal_id = _internal_id(light_order)
    if not order_number or not internal_id:
        return False

    # Webhooks are the primary source for existing orders. The light Orders API
    # is only a discovery fallback for an order that Mezan has not received yet.
    # Never replace a webhook-backed order with a reduced format=light snapshot.
    try:
        existing = await db.unified_orders.find_one(
            {
                "user_id": str(user_id),
                "order_number": order_number,
            },
            {
                "_id": 0,
                "order_number": 1,
            },
        )
    except Exception as exc:
        error_code, retryable = _error_code(exc)
        raise OrderSyncFailure(
            "existing_order_lookup",
            error_code,
            retryable=retryable,
        ) from exc
    if existing:
        return True

    try:
        items = await _fetch_salla_order_items(db, user_id, internal_id)
    except Exception as exc:
        error_code, retryable = _error_code(exc)
        raise OrderSyncFailure(
            "items_fetch",
            error_code,
            retryable=retryable,
        ) from exc
    raw = dict(light_order)
    raw["items"] = items
    raw["mezan_background_sync"] = True

    try:
        doc = _salla_order_to_doc(raw)
    except Exception as exc:
        raise OrderSyncFailure(
            "normalization",
            "order_normalization_failed",
            retryable=False,
        ) from exc
    if not doc.get("order_number"):
        raise OrderSyncFailure(
            "normalization",
            "order_identity_missing",
            retryable=False,
        )

    try:
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
    except Exception as exc:
        error_code, retryable = _error_code(exc)
        raise OrderSyncFailure(
            "upsert",
            error_code,
            retryable=retryable,
        ) from exc

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


async def _reconcile_status_page(
    db,
    user_id: str,
    *,
    page: int,
) -> tuple[int, int, bool]:
    """Reconcile one Salla light-list page without opening order details.

    Returns ``(scanned, changed, exhausted)``. Only existing Mezan orders are
    patched. Product lines, costs, payment facts and shipping facts are untouched.
    """
    response = await call_salla(
        db,
        user_id,
        "GET",
        "/orders",
        params={
            "page": max(1, int(page)),
            "per_page": STATUS_RECONCILE_PER_PAGE,
            "format": "light",
        },
    )
    rows = response.get("data") if isinstance(response, dict) else None
    if not isinstance(rows, list) or not rows:
        return 0, 0, True

    normalized_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        order_number = _reference_id(row)
        if order_number:
            normalized_rows[order_number] = row

    if not normalized_rows:
        return 0, 0, len(rows) < STATUS_RECONCILE_PER_PAGE

    local_cursor = db.unified_orders.find(
        {
            "user_id": str(user_id),
            "order_number": {"$in": list(normalized_rows)},
            "raw_by_source.salla_direct": {"$exists": True},
        },
        {
            "_id": 0,
            "order_number": 1,
            "order_status": 1,
            "order_status_slug": 1,
        },
    )
    local_by_number = {
        str(row.get("order_number") or "").strip(): row
        async for row in local_cursor
    }

    changed = 0
    reconciled_at = _utcnow()
    for order_number, light_order in normalized_rows.items():
        local = local_by_number.get(order_number)
        if not local:
            continue

        status_name, status_slug, raw_status = _status_values(light_order)
        if not status_name and not status_slug:
            continue

        old_name = str(local.get("order_status") or "").strip()
        old_slug = str(local.get("order_status_slug") or "").strip().lower()
        if old_name == status_name and old_slug == status_slug:
            continue

        patch: dict[str, Any] = {
            "last_salla_direct_status_reconciled_at": reconciled_at,
            "raw_by_source.salla_direct.status": raw_status,
        }
        if status_name:
            patch["order_status"] = status_name
        if status_slug:
            patch["order_status_slug"] = status_slug

        provider_updated_at = light_order.get("updated_at")
        if provider_updated_at is not None:
            patch["raw_by_source.salla_direct.updated_at"] = provider_updated_at

        await db.unified_orders.update_one(
            {"user_id": str(user_id), "order_number": order_number},
            {"$set": patch},
        )

        # Update the read-only status snapshot only when the status actually
        # changed. This performs no Qoyod API call and remains ineligible to send.
        try:
            await _refresh_plan_b_status_snapshot(
                db,
                str(user_id),
                order_number,
                {
                    "order_status": status_name or status_slug,
                    "order_status_slug": status_slug or status_name,
                },
            )
        except Exception:
            log.exception(
                "salla.auto_sync.status_snapshot_failed user_id=%s order_number=%s",
                user_id,
                order_number,
            )

        changed += 1

    exhausted = len(rows) < STATUS_RECONCILE_PER_PAGE
    return len(normalized_rows), changed, exhausted


async def _reconcile_status_pages(db, user_id: str) -> tuple[int, int]:
    """Advance the tenant's rolling status-reconciliation cursor."""
    page = max(1, int(_status_reconcile_page.get(user_id, 1)))
    scanned_total = 0
    changed_total = 0

    for _ in range(STATUS_RECONCILE_PAGES_PER_RUN):
        scanned, changed, exhausted = await _reconcile_status_page(
            db,
            user_id,
            page=page,
        )
        scanned_total += scanned
        changed_total += changed

        if exhausted:
            page = 1
            break
        page += 1

    _status_reconcile_page[user_id] = page
    return scanned_total, changed_total


def _validated_gap_window(date_from: str, date_to: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(str(date_from))
        end = date.fromisoformat(str(date_to))
    except (TypeError, ValueError) as exc:
        raise ValueError("date_from and date_to must use YYYY-MM-DD") from exc
    days = (end - start).days + 1
    if days < 1 or days * 24 > GAP_RECONCILIATION_MAX_HOURS:
        raise ValueError("Salla order gap reconciliation is limited to 48 hours")
    return start, end


async def _local_orders_for_gap_window(
    db,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
) -> set[str]:
    cursor = db.unified_orders.find(
        {
            "user_id": str(user_id),
            "order_date": {"$gte": date_from, "$lte": date_to},
        },
        {"_id": 0, "order_number": 1},
    )
    return {
        str(row.get("order_number") or "").strip()
        async for row in cursor
        if str(row.get("order_number") or "").strip()
    }


async def _retry_diagnostics(db, user_id: str) -> dict[str, Any]:
    collection = getattr(db, RETRY_COLLECTION)
    cursor = collection.find(
        {"user_id": str(user_id), "status": {"$in": ["retryable", "exhausted"]}},
        {
            "_id": 0,
            "order_number": 1,
            "attempt_count": 1,
            "last_error_code": 1,
            "last_failure_stage": 1,
            "retryable": 1,
            "next_retry_at": 1,
            "first_discovered_at": 1,
        },
    ).sort([("first_discovered_at", 1), ("order_number", 1)]).limit(20)
    rows = [dict(row) async for row in cursor]
    safe_rows = [
        {
            "order_number": row.get("order_number"),
            "stage": row.get("last_failure_stage"),
            "error_code": row.get("last_error_code"),
            "attempt_count": int(row.get("attempt_count") or 0),
            "retryable": bool(row.get("retryable")),
            "next_retry_at": row.get("next_retry_at"),
        }
        for row in rows
    ]
    return {
        "failed_orders": safe_rows,
        "oldest_unresolved_failure": safe_rows[0] if safe_rows else None,
    }


async def reconcile_salla_order_gaps(
    db,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    recover_missing: bool = False,
) -> dict[str, Any]:
    """Compare bounded Salla light identities with tenant-scoped Mezan rows.

    The normal diagnostic path is read-only. Recovery is intentionally an
    internal opt-in used by a separately authorized workflow and only fetches
    items for identities proven missing from ``unified_orders``.
    """
    _validated_gap_window(date_from, date_to)
    listing = await _list_light_orders_bounded(
        db,
        str(user_id),
        filters={"from_date": date_from, "to_date": date_to},
        max_pages=GAP_RECONCILIATION_MAX_PAGES,
        max_orders=GAP_RECONCILIATION_MAX_ORDERS,
    )
    salla_by_number = {
        _reference_id(row): row
        for row in listing["orders"]
        if _reference_id(row)
    }
    mezan_numbers = await _local_orders_for_gap_window(
        db,
        str(user_id),
        date_from=date_from,
        date_to=date_to,
    )
    salla_numbers = set(salla_by_number)
    missing = sorted(salla_numbers - mezan_numbers)
    extra = sorted(mezan_numbers - salla_numbers)
    recovered: list[str] = []
    recovery_failures: list[dict[str, Any]] = []

    if recover_missing:
        if listing["truncated"]:
            raise RuntimeError("refusing recovery from truncated Salla coverage")
        if listing["ambiguous_order_numbers"]:
            raise RuntimeError("refusing recovery from ambiguous Salla identities")
        for order_number in missing:
            row = salla_by_number[order_number]
            attempted_at = _utcnow()
            try:
                await _sync_light_order(db, str(user_id), row)
                await _clear_retry_failure(db, str(user_id), order_number)
                recovered.append(order_number)
                mezan_numbers.add(order_number)
            except OrderSyncFailure as exc:
                failure = await _record_retry_failure(
                    db,
                    str(user_id),
                    row,
                    stage=exc.stage,
                    error_code=exc.error_code,
                    retryable=exc.retryable,
                    attempted_at=attempted_at,
                )
                recovery_failures.append(failure)
            except Exception as exc:
                error_code, retryable = _error_code(exc)
                failure = await _record_retry_failure(
                    db,
                    str(user_id),
                    row,
                    stage="unknown",
                    error_code=error_code,
                    retryable=retryable,
                    attempted_at=attempted_at,
                )
                recovery_failures.append(failure)

        missing = sorted(salla_numbers - mezan_numbers)

    state = await getattr(db, STATE_COLLECTION).find_one(
        {"user_id": str(user_id)},
        {
            "_id": 0,
            "checkpoint_at": 1,
            "last_successful_reconciliation_at": 1,
        },
    )
    retry_diagnostics = await _retry_diagnostics(db, str(user_id))
    return {
        "date_from": date_from,
        "date_to": date_to,
        "expected_count": len(salla_numbers),
        "salla_count": len(salla_numbers),
        "mezan_count": len(mezan_numbers),
        "matched_count": len(salla_numbers & mezan_numbers),
        "missing_count": len(missing),
        "missing_order_numbers": missing,
        "extra_order_numbers": extra,
        "ambiguous_order_numbers": listing["ambiguous_order_numbers"],
        "coverage_window": {"date_from": date_from, "date_to": date_to},
        "discovery_pages": listing["pages_fetched"],
        "provider_calls": listing["provider_calls"],
        "truncated": bool(listing["truncated"]),
        "recovered_count": len(recovered),
        "recovered_order_numbers": recovered,
        "recovery_failures": recovery_failures,
        "checkpoint": (state or {}).get("checkpoint_at"),
        "last_successful_reconciliation_at": (state or {}).get(
            "last_successful_reconciliation_at"
        ),
        **retry_diagnostics,
    }


async def _run_auto_sync(db, user_id: str) -> dict[str, Any]:
    started_at = _utcnow()
    discovered = 0
    synced = 0
    failed = 0
    status_scanned = 0
    status_changed = 0
    status_reconciliation_succeeded = False
    discovery_pages = 0
    retry_due = 0
    failed_orders: list[dict[str, Any]] = []
    checkpoint_safe = True
    truncated = False
    ambiguous_order_numbers: list[str] = []

    try:
        discovery = await _discover_recent_orders(db, user_id)
        if isinstance(discovery, dict):
            discovered_orders = list(discovery.get("orders") or [])
            discovery_pages = int(discovery.get("pages_fetched") or 0)
            truncated = bool(discovery.get("truncated"))
            ambiguous_order_numbers = list(
                discovery.get("ambiguous_order_numbers") or []
            )
        else:  # Backward-compatible seam for focused callers/tests.
            discovered_orders = list(discovery or [])
        discovered = len(discovered_orders)

        retry_orders = await _load_due_retry_orders(db, user_id, now=started_at)
        retry_due = len(retry_orders)
        orders_by_number: dict[str, dict] = {}
        for row in [*retry_orders, *discovered_orders]:
            order_number = _reference_id(row)
            if order_number:
                orders_by_number.setdefault(order_number, row)

        for order_number in sorted(orders_by_number):
            light_order = orders_by_number[order_number]
            order_number = _reference_id(light_order)
            try:
                await _sync_light_order(db, user_id, light_order)
                await _clear_retry_failure(db, user_id, order_number)
                synced += 1
            except OrderSyncFailure as exc:
                failed += 1
                try:
                    failed_orders.append(
                        await _record_retry_failure(
                            db,
                            user_id,
                            light_order,
                            stage=exc.stage,
                            error_code=exc.error_code,
                            retryable=exc.retryable,
                            attempted_at=started_at,
                        )
                    )
                except Exception:
                    checkpoint_safe = False
                    log.exception(
                        "salla.auto_sync.retry_ledger_failed user_id=%s "
                        "order_number=%s",
                        user_id,
                        order_number,
                    )
                log.warning(
                    "salla.auto_sync.order_failed user_id=%s order_number=%s "
                    "stage=%s error_code=%s retryable=%s",
                    user_id,
                    order_number,
                    exc.stage,
                    exc.error_code,
                    exc.retryable,
                )
            except Exception as exc:
                failed += 1
                error_code, retryable = _error_code(exc)
                try:
                    failed_orders.append(
                        await _record_retry_failure(
                            db,
                            user_id,
                            light_order,
                            stage="unknown",
                            error_code=error_code,
                            retryable=retryable,
                            attempted_at=started_at,
                        )
                    )
                except Exception:
                    checkpoint_safe = False
                    log.exception(
                        "salla.auto_sync.retry_ledger_failed user_id=%s "
                        "order_number=%s",
                        user_id,
                        order_number,
                    )

        try:
            status_scanned, status_changed = await _reconcile_status_pages(
                db,
                user_id,
            )
            status_reconciliation_succeeded = True
        except Exception:
            log.exception(
                "salla.auto_sync.status_reconciliation_failed user_id=%s",
                user_id,
            )

        checkpoint_safe = (
            checkpoint_safe and not truncated and not ambiguous_order_numbers
        )
        checkpoint_at = started_at if checkpoint_safe else None
        summary = {
            "status": "complete" if failed == 0 and not truncated else "partial",
            "discovered": discovered,
            "discovery_pages": discovery_pages,
            "retry_due": retry_due,
            "synced": synced,
            "failed": failed,
            "retryable": sum(
                1 for row in failed_orders if bool(row.get("retryable"))
            ),
            "failed_orders": failed_orders,
            "status_scanned": status_scanned,
            "status_changed": status_changed,
            "status_reconciliation_succeeded": status_reconciliation_succeeded,
            "truncated": truncated,
            "ambiguous_order_numbers": ambiguous_order_numbers,
            "checkpoint_safe": checkpoint_safe,
        }
        await _persist_run_state(
            db,
            user_id,
            checkpoint_at=checkpoint_at,
            summary=summary,
        )
        if checkpoint_at is not None:
            _last_success_at[user_id] = checkpoint_at
        log.info(
            "salla.auto_sync.completed user_id=%s discovered=%d synced=%d "
            "failed=%d retryable=%d checkpoint_safe=%s "
            "status_scanned=%d status_changed=%d",
            user_id,
            discovered,
            synced,
            failed,
            summary["retryable"],
            checkpoint_safe,
            status_scanned,
            status_changed,
        )
        return summary
    except Exception as exc:
        log.exception(
            "salla.auto_sync.failed user_id=%s discovered=%d synced=%d failed=%d",
            user_id,
            discovered,
            synced,
            failed,
        )
        return {
            "status": "failed",
            "discovered": discovered,
            "synced": synced,
            "failed": failed,
            "retryable": sum(
                1 for row in failed_orders if bool(row.get("retryable"))
            ),
            "failed_orders": failed_orders,
            "checkpoint_safe": False,
            "error_code": _error_code(exc)[0],
        }
    finally:
        _running_tasks.pop(user_id, None)


async def ensure_salla_auto_sync_indexes(db) -> None:
    """Create the small tenant-scoped checkpoint and retry-ledger indexes."""
    await getattr(db, STATE_COLLECTION).create_index(
        [("user_id", 1)],
        unique=True,
        name="salla_auto_sync_state_user_unique",
    )
    retry_collection = getattr(db, RETRY_COLLECTION)
    await retry_collection.create_index(
        [("user_id", 1), ("order_number", 1)],
        unique=True,
        name="salla_auto_sync_retry_identity_unique",
    )
    await retry_collection.create_index(
        [("user_id", 1), ("status", 1), ("next_retry_at", 1)],
        name="salla_auto_sync_retry_due",
    )


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
