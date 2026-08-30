"""Combined operational search and financially-safe summaries for orders."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import OrderDTO
from .repository import OrderRepository
from .service import _map_row

SCAN_LIMIT = 5000
ACCOUNT_TIMEZONE = ZoneInfo("America/New_York")
_CANCELLED = ("cancel", "deleted", "ملغ", "محذوف")
_REFUNDED = ("refund", "return", "مسترج", "استرجاع")
_PENDING_PAYMENT = ("payment pending", "pending payment", "بانتظار الدفع", "بإنتظار الدفع")


@dataclass(frozen=True)
class SearchResult:
    items: list[OrderDTO]
    summary: dict[str, Any]


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _contains(value: Any, expected: str | None) -> bool:
    return not expected or _text(expected) in _text(value)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # datetime-local values from the marketing filter are account-local.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ACCOUNT_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _status(order: OrderDTO) -> str:
    return _text(order.status_native or order.status)


def _has(status: str, aliases: tuple[str, ...]) -> bool:
    return any(alias in status for alias in aliases)


def _is_paid(order: OrderDTO) -> bool:
    status = _status(order)
    if _has(status, _CANCELLED + _REFUNDED + _PENDING_PAYMENT):
        return False
    return order.payment.collection_status == "paid" or order.payment.paid_amount > 0


def _matches(order: OrderDTO, filters: dict[str, str | None], start: datetime | None, end: datetime | None) -> bool:
    source = order.source
    if start and order.created_at < start:
        return False
    if end and order.created_at >= end:
        return False
    products = " ".join(item.name for item in order.items)
    skus = " ".join(item.sku or "" for item in order.items)
    raw_utm = " ".join(value or "" for value in source.utm_raw.values())
    general = " ".join(filter(None, (
        order.order_number, products, skus, source.source, source.platform,
        source.campaign_name, source.campaign_id, source.ad_squad_name,
        source.ad_squad_id, source.ad_name, source.ad_id, raw_utm,
    )))
    checks = (
        (general, filters.get("q")), (products, filters.get("product")),
        (skus, filters.get("sku")), (_status(order), filters.get("status")),
        (order.payment.collection_status or order.payment.status, filters.get("payment_status")),
        (source.source or source.platform, filters.get("provider")),
        (source.campaign_name, filters.get("campaign")), (source.campaign_id, filters.get("campaign_id")),
        (source.ad_squad_name, filters.get("ad_squad")), (source.ad_squad_id, filters.get("ad_squad_id")),
        (source.ad_name, filters.get("ad")), (source.ad_id, filters.get("ad_id")),
        (raw_utm, filters.get("utm")), (source.match_status, filters.get("attribution_status")),
    )
    return all(_contains(value, expected) for value, expected in checks)


def _summary(items: list[OrderDTO], cutoff: datetime | None) -> dict[str, Any]:
    paid = [item for item in items if _is_paid(item)]
    pending = [item for item in items if _has(_status(item), _PENDING_PAYMENT)]
    cancelled = [item for item in items if _has(_status(item), _CANCELLED)]
    refunded = [item for item in items if _has(_status(item), _REFUNDED)]
    revenue = round(sum(item.totals.total for item in paid), 2)
    products = sum(len(item.items) for item in items)
    units = sum(sum(row.quantity for row in item.items) for item in items)
    before = [item for item in items if cutoff and item.created_at <= cutoff]
    after = [item for item in items if cutoff and item.created_at > cutoff]
    return {
        "orders": len(items), "paid_orders": len(paid), "pending_payment_orders": len(pending),
        "cancelled_orders": len(cancelled), "refunded_orders": len(refunded),
        "paid_sales": revenue, "average_basket": round(revenue / len(paid), 2) if paid else 0.0,
        "product_lines": products, "units": units,
        "unattributed_or_conflicted": sum(item.source.match_status != "matched" for item in items),
        "baseline_cutoff_at": cutoff, "orders_at_or_before_cutoff": len(before),
        "orders_after_cutoff": len(after), "current_total": len(items),
        "financial_policy": "paid_only_excludes_pending_cancelled_refunded",
    }


async def search_orders(repository: OrderRepository, *, user_id: str, filters: dict[str, str | None]) -> SearchResult:
    rows = await repository.list_salla_orders(user_id=user_id, limit=SCAN_LIMIT)
    start, end, cutoff = _parse(filters.get("created_from")), _parse(filters.get("created_to")), _parse(filters.get("baseline_cutoff_at"))
    # Webhook retries, reconciliation and rerunnable backfills all converge on
    # the Salla order reference. A legacy duplicate must not inflate results.
    unique_rows = {}
    for row in rows:
        unique_rows.setdefault(str(row.order_number), row)
    mapped = [_map_row(row.salla_raw, current_status=row.current_status) for row in unique_rows.values()]
    items = [item for item in mapped if _matches(item, filters, start, end)]
    return SearchResult(items=items, summary=_summary(items, cutoff))
