"""Current-status authority for the Plan-B manual pending list.

The legacy manual list is built from ``integration_inbox`` rows under tenant
``main``. Salla direct resyncs and Orders V2 keep the current order state in
``unified_orders`` under the authenticated Orders owner. A stale completed
inbox row must never keep an order visible after Salla moves it back to
processing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Optional

_STATUS_MATCHERS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "completed": (
        frozenset({"completed"}),
        frozenset({"تم التنفيذ", "منتهي", "مكتمل"}),
    ),
    "delivered": (
        frozenset({"delivered"}),
        frozenset({"تم التوصيل"}),
    ),
    "in_delivery": (
        frozenset({
            "in_delivery", "shipping", "delivering",
            "جاري_التوصيل", "جاري التوصيل",
        }),
        frozenset({"جاري التوصيل", "جارٍ التوصيل"}),
    ),
}


def current_status_matches(row: dict[str, Any], status_key: str) -> bool:
    matcher = _STATUS_MATCHERS.get(status_key)
    if matcher is None:
        return False
    canonical, native = matcher
    slug = str(
        row.get("order_status_slug")
        or row.get("status_slug")
        or row.get("order_status")
        or ""
    ).strip().lower()
    native_value = str(
        row.get("order_status_native")
        or row.get("status_native")
        or row.get("order_status")
        or ""
    ).strip()
    return slug in canonical or native_value in native


async def filter_pending_by_current_status(
    db: Any,
    result: dict[str, Any],
    *,
    user_id: str,
    orders_user_id: Optional[str],
    status: str,
) -> dict[str, Any]:
    """Remove rows whose current unified-order status no longer matches."""
    if result.get("source_authority") == "unified_orders":
        return result
    orders = list(result.get("orders") or [])
    if not orders:
        return result

    owner_id = str(orders_user_id or user_id)
    kept: list[dict[str, Any]] = []
    removed = 0

    # Fetch current states in one query. The previous per-order find_one loop
    # made each manual-status tab wait on hundreds of sequential Mongo calls.
    order_numbers = [
        str(item.get("order_number") or "").strip()
        for item in orders
        if str(item.get("order_number") or "").strip()
    ]
    unique_order_numbers = list(dict.fromkeys(order_numbers))
    current_by_order_number: dict[str, dict[str, Any]] = {}
    if unique_order_numbers:
        cursor = db.unified_orders.find(
            {
                "user_id": owner_id,
                "order_number": {"$in": unique_order_numbers},
            },
            {
                "_id": 0,
                "order_number": 1,
                "order_status": 1,
                "order_status_slug": 1,
                "order_status_native": 1,
                "status_slug": 1,
                "status_native": 1,
                "updated_at": 1,
            },
        )
        async for current in cursor:
            order_number = str(current.get("order_number") or "").strip()
            if order_number:
                current_by_order_number.setdefault(order_number, current)

    for item in orders:
        order_number = str(item.get("order_number") or "").strip()
        if not order_number:
            continue
        current = current_by_order_number.get(order_number)
        # Legacy orders without a unified row keep the inbox decision. For any
        # order known to Orders V2, the unified current state is authoritative.
        if current and not current_status_matches(current, status):
            removed += 1
            continue
        if current:
            item = {
                **item,
                "salla_status": (
                    current.get("order_status_native")
                    or current.get("order_status")
                    or current.get("order_status_slug")
                ),
                "status_source": "unified_orders_current",
            }
        kept.append(item)

    next_result = dict(result)
    next_result["orders"] = kept
    counts = dict(next_result.get("counts") or {})
    counts["returned"] = len(kept)
    counts["excluded_current_status_mismatch"] = removed
    next_result["counts"] = counts
    next_result["current_status_authority"] = "unified_orders"
    return next_result


def install_manual_list_current_status_patch() -> None:
    """Patch all Plan-B consumers that imported ``list_pending_orders``."""
    from integrations.qoyod_manual import pending as pending_module

    if getattr(pending_module, "_current_status_patch_installed", False):
        return

    original: Callable[..., Awaitable[dict[str, Any]]] = pending_module.list_pending_orders

    async def authoritative_list_pending_orders(
        db: Any,
        *,
        user_id: str,
        orders_user_id: Optional[str] = None,
        days: int = 60,
        limit: int = 500,
        search: Optional[str] = None,
        status: str = "completed",
        from_date: Any = None,
        to_date: Any = None,
        now: Optional[datetime] = None,
        open_quarantine_order_numbers: Optional[Iterable[str]] = None,
    ) -> dict[str, Any]:
        result = await original(
            db,
            user_id=user_id,
            orders_user_id=orders_user_id,
            days=days,
            limit=limit,
            search=search,
            status=status,
            from_date=from_date,
            to_date=to_date,
            now=now,
            open_quarantine_order_numbers=open_quarantine_order_numbers,
        )
        return await filter_pending_by_current_status(
            db,
            result,
            user_id=user_id,
            orders_user_id=orders_user_id,
            status=status,
        )

    pending_module.list_pending_orders = authoritative_list_pending_orders
    # These modules import the function directly, so update their bound symbol.
    try:
        from integrations.qoyod_manual import routes as routes_module
        routes_module.list_pending_orders = authoritative_list_pending_orders
    except Exception:
        pass
    try:
        from integrations.qoyod_manual import auto_send as auto_send_module
        auto_send_module.list_pending_orders = authoritative_list_pending_orders
    except Exception:
        pass

    pending_module._current_status_patch_installed = True
