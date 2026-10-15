"""Isolated Salla order activity ledger.

Safety contract:
- Reads canonical order identity from unified_orders.
- NEVER writes to unified_orders.
- NEVER calls Qoyod.
- Salla HTTP calls occur only during explicit/manual refresh.
- Writes only to:
    order_activity_events_v2
    order_payment_transactions_v2
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


class OrderActivityNotFoundError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, dict):
        for key in ("name", "title", "label", "value", "slug", "code"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip()
        return ""

    return str(value).strip()


def _date_value(value: Any) -> str:
    if not value:
        return ""

    if isinstance(value, dict):
        return str(
            value.get("date")
            or value.get("datetime")
            or value.get("value")
            or ""
        ).strip()

    return str(value).strip()


def _money(value: Any) -> tuple[float, str]:
    currency = "SAR"

    if isinstance(value, dict):
        currency = str(
            value.get("currency")
            or value.get("currency_code")
            or "SAR"
        ).upper()

        value = (
            value.get("amount")
            if value.get("amount") is not None
            else value.get("value")
        )

    try:
        return round(float(value or 0), 3), currency
    except (TypeError, ValueError):
        return 0.0, currency


def _fingerprint(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        f"{prefix}:{canonical}".encode("utf-8")
    ).hexdigest()


def _actor_name(row: dict[str, Any]) -> str:
    # Deliberately avoids customer blocks.
    for key in (
        "employee",
        "created_by",
        "updated_by",
        "admin",
        "user",
        "actor",
    ):
        value = row.get(key)

        if isinstance(value, dict):
            for name_key in (
                "name",
                "full_name",
                "username",
                "display_name",
            ):
                name = str(value.get(name_key) or "").strip()
                if name:
                    return name

        elif isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _history_title(row: dict[str, Any]) -> str:
    for key in (
        "description",
        "message",
        "action",
        "note",
        "event",
        "status",
        "type",
    ):
        value = _text(row.get(key))
        if value:
            return value

    return "حدث في الطلب"


def _history_event_type(row: dict[str, Any], title: str) -> str:
    search_blob = " ".join(
        [
            title,
            _text(row.get("event")),
            _text(row.get("type")),
            _text(row.get("action")),
            _text(row.get("status")),
        ]
    ).lower()

    if any(token in search_blob for token in (
        "تم إنشاء الطلب",
        "انشاء الطلب",
        "إنشاء الطلب",
        "order created",
        "order.created",
    )):
        return "order_created"

    if any(token in search_blob for token in (
        "أضاف",
        "اضاف",
        "إضافة منتج",
        "اضافة منتج",
        "product added",
        "item added",
    )):
        return "item_added"

    if any(token in search_blob for token in (
        "حذف منتج",
        "إزالة منتج",
        "ازالة منتج",
        "product removed",
        "item removed",
    )):
        return "item_removed"

    if any(token in search_blob for token in (
        "تعديل منتج",
        "عدل منتج",
        "product updated",
        "item updated",
    )):
        return "item_updated"

    if any(token in search_blob for token in (
        "كوبون",
        "coupon",
        "discount code",
    )):
        return "coupon_updated"

    if any(token in search_blob for token in (
        "الدفع",
        "payment",
    )):
        return "payment_updated"

    if any(token in search_blob for token in (
        "الحالة",
        "status",
    )):
        return "status_changed"

    return "history_event"


def normalize_history_row(
    row: dict[str, Any],
    *,
    order_number: str,
    provider_order_id: str,
) -> dict[str, Any]:
    title = _history_title(row)

    occurred_at = _date_value(
        row.get("created_at")
        or row.get("date")
        or row.get("updated_at")
    )

    source_event_id = str(
        row.get("id")
        or row.get("history_id")
        or ""
    ).strip()

    actor_name = _actor_name(row)

    base = {
        "provider_order_id": provider_order_id,
        "source_event_id": source_event_id,
        "event_type": _history_event_type(row, title),
        "title": title,
        "occurred_at": occurred_at,
        "actor_name": actor_name,
    }

    return {
        "order_number": order_number,
        "provider_order_id": provider_order_id,
        "source": "salla_history",
        **base,
        "fingerprint": _fingerprint("salla_history", base),
    }


def normalize_transaction_row(
    row: dict[str, Any],
    *,
    order_number: str,
    provider_order_id: str,
) -> dict[str, Any]:
    amount, currency_from_amount = _money(
        row.get("amount")
        if row.get("amount") is not None
        else row.get("total")
    )

    currency = str(
        row.get("currency")
        or currency_from_amount
        or "SAR"
    ).upper()

    payment_method = _text(
        row.get("payment_method")
        or row.get("method")
        or row.get("gateway")
    )

    status = _text(
        row.get("status")
        or row.get("state")
    )

    occurred_at = _date_value(
        row.get("paid_at")
        or row.get("created_at")
        or row.get("date")
        or row.get("updated_at")
    )

    transaction_id = str(
        row.get("id")
        or row.get("transaction_id")
        or row.get("reference_id")
        or ""
    ).strip()

    base = {
        "provider_order_id": provider_order_id,
        "transaction_id": transaction_id,
        "amount": amount,
        "currency": currency,
        "payment_method": payment_method,
        "status": status,
        "occurred_at": occurred_at,
    }

    return {
        "order_number": order_number,
        "provider_order_id": provider_order_id,
        "source": "salla_transactions",
        **base,
        "fingerprint": _fingerprint("salla_transaction", base),
    }


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if not isinstance(payload, dict):
        return []

    data = payload.get("data")

    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]

    if isinstance(data, dict):
        for key in (
            "data",
            "items",
            "histories",
            "transactions",
            "results",
        ):
            rows = data.get(key)
            if isinstance(rows, list):
                return [
                    row for row in rows
                    if isinstance(row, dict)
                ]

    for key in (
        "items",
        "histories",
        "transactions",
        "results",
    ):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [
                row for row in rows
                if isinstance(row, dict)
            ]

    return []


def _page_signature(rows: list[dict[str, Any]]) -> str:
    compact = [
        {
            "id": row.get("id"),
            "date": row.get("created_at") or row.get("date"),
            "status": row.get("status"),
            "action": row.get("action"),
        }
        for row in rows
    ]

    return hashlib.sha256(
        json.dumps(
            compact,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


async def _fetch_salla_pages(
    db: Any,
    user_id: str,
    path: str,
    *,
    base_params: dict[str, Any] | None = None,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    # Import locally so unit tests for pure normalizers do not need
    # a configured Salla connection.
    from salla_integration.service import call_salla

    result: list[dict[str, Any]] = []
    seen_pages: set[str] = set()

    for page in range(1, max_pages + 1):
        params = dict(base_params or {})
        params["page"] = page

        payload = await call_salla(
            db,
            user_id,
            "GET",
            path,
            params=params,
        )

        rows = _extract_rows(payload)

        if not rows:
            break

        signature = _page_signature(rows)

        # Protect against an API ignoring page and repeating page 1.
        if signature in seen_pages:
            break

        seen_pages.add(signature)
        result.extend(rows)

    return result


async def _resolve_provider_order(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> tuple[str, dict[str, Any]]:
    row = await db.unified_orders.find_one(
        {
            "user_id": str(user_id),
            "order_number": str(order_number),
        },
        {
            "_id": 0,
            "order_number": 1,
            "salla_order_id": 1,
            "raw_by_source.salla_direct": 1,
        },
    )

    if not isinstance(row, dict):
        raise OrderActivityNotFoundError(order_number)

    raw_by_source = row.get("raw_by_source") or {}
    raw = raw_by_source.get("salla_direct") or {}

    provider_order_id = str(
        raw.get("id")
        or row.get("salla_order_id")
        or ""
    ).strip()

    if not provider_order_id:
        raise OrderActivityNotFoundError(
            f"missing provider order id: {order_number}"
        )

    return provider_order_id, raw


async def read_order_activity(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    order_number = str(order_number).strip()

    events = await (
        db.order_activity_events_v2
        .find(
            {
                "user_id": str(user_id),
                "order_number": order_number,
            },
            {"_id": 0},
        )
        .sort([("occurred_at", 1), ("created_at", 1)])
        .to_list(length=500)
    )

    payments = await (
        db.order_payment_transactions_v2
        .find(
            {
                "user_id": str(user_id),
                "order_number": order_number,
            },
            {"_id": 0},
        )
        .sort([("occurred_at", 1), ("created_at", 1)])
        .to_list(length=100)
    )

    return {
        "ok": True,
        "order_number": order_number,
        "events": events,
        "payments": payments,
        "events_count": len(events),
        "payments_count": len(payments),
    }


async def refresh_order_activity_from_salla(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Explicit read-only Salla refresh into isolated ledgers."""

    order_number = str(order_number).strip()

    provider_order_id, _ = await _resolve_provider_order(
        db,
        user_id=str(user_id),
        order_number=order_number,
    )

    history_rows = await _fetch_salla_pages(
        db,
        str(user_id),
        f"/orders/{provider_order_id}/histories",
    )

    events = [
        normalize_history_row(
            row,
            order_number=order_number,
            provider_order_id=provider_order_id,
        )
        for row in history_rows
    ]

    payment_rows: list[dict[str, Any]] = []
    payment_fetch_error = ""

    try:
        payment_rows = await _fetch_salla_pages(
            db,
            str(user_id),
            "/transactions",
            base_params={
                "order_id": provider_order_id,
            },
        )
    except Exception:
        # transactions.read may not be granted. This must NEVER cause
        # order-history refresh to fail.
        payment_fetch_error = "transactions_unavailable"

    payments = [
        normalize_transaction_row(
            row,
            order_number=order_number,
            provider_order_id=provider_order_id,
        )
        for row in payment_rows
    ]

    now = _now_iso()

    for event in events:
        await db.order_activity_events_v2.update_one(
            {
                "user_id": str(user_id),
                "order_number": order_number,
                "fingerprint": event["fingerprint"],
            },
            {
                "$setOnInsert": {
                    **event,
                    "user_id": str(user_id),
                    "created_at": now,
                },
                "$set": {
                    "last_seen_at": now,
                },
            },
            upsert=True,
        )

    for payment in payments:
        await db.order_payment_transactions_v2.update_one(
            {
                "user_id": str(user_id),
                "order_number": order_number,
                "fingerprint": payment["fingerprint"],
            },
            {
                "$setOnInsert": {
                    **payment,
                    "user_id": str(user_id),
                    "created_at": now,
                },
                "$set": {
                    "last_seen_at": now,
                },
            },
            upsert=True,
        )

    result = await read_order_activity(
        db,
        user_id=str(user_id),
        order_number=order_number,
    )

    result.update(
        {
            "refreshed": True,
            "provider_order_id": provider_order_id,
            "history_rows_seen": len(history_rows),
            "payment_rows_seen": len(payment_rows),
            "payment_fetch_error": payment_fetch_error or None,
            "no_unified_orders_write": True,
            "no_qoyod_calls": True,
        }
    )

    return result
