"""Combined operational search and financially-safe summaries for orders."""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import hashlib
import json
from contextlib import aclosing
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import OrderDTO
from .repository import OrderRepository
from .service import _map_row
from .campaign_enrichment import _looks_like_campaign_id
from pymongo.errors import ExecutionTimeout

SCAN_LIMIT = 5000
SEARCH_TIMEOUT_SECONDS = 3
_SEARCH_SLOT = asyncio.Semaphore(1)
ACCOUNT_TIMEZONE = ZoneInfo("America/New_York")
_CANCELLED = ("cancel", "deleted", "ملغ", "محذوف")
_REFUNDED = ("refund", "return", "مسترج", "استرجاع")
_PENDING_PAYMENT = ("payment pending", "pending payment", "بانتظار الدفع", "بإنتظار الدفع")


@dataclass(frozen=True)
class SearchResult:
    items: list[OrderDTO]
    summary: dict[str, Any]
    next_cursor: str | None = None


class OrderSearchError(ValueError):
    def __init__(self, code, message, status_code=422):
        super().__init__(message)
        self.code, self.status_code = code, status_code


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
    campaign_id = source.campaign_id
    if not campaign_id:
        raw_campaign = source.campaign_name or source.utm_campaign
        if _looks_like_campaign_id(raw_campaign):
            campaign_id = raw_campaign
    if start and order.created_at < start:
        return False
    if end and order.created_at >= end:
        return False
    products = " ".join(item.name for item in order.items)
    skus = " ".join(item.sku or "" for item in order.items)
    product_ids = {value for item in order.items for value in
                   (item.product_id, item.parent_product_id, item.variant_id) if value}
    if filters.get("product_id") and str(filters["product_id"]).strip() not in product_ids:
        return False
    if filters.get("campaign_id") and _text(filters["campaign_id"]) != _text(campaign_id):
        return False
    raw_utm = " ".join(value or "" for value in source.utm_raw.values())
    general = " ".join(filter(None, (
        order.order_number, products, skus, " ".join(product_ids), source.source, source.platform,
        source.campaign_name, campaign_id, source.ad_squad_name,
        source.ad_squad_id, source.ad_name, source.ad_id, raw_utm,
    )))
    checks = (
        (general, filters.get("q")), (products, filters.get("product")),
        (skus, filters.get("sku")), (_status(order), filters.get("status")),
        (order.payment.collection_status or order.payment.status, filters.get("payment_status")),
        (source.source or source.platform, filters.get("provider")),
        (source.campaign_name, filters.get("campaign")),
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
    sar_sales: list[float] = []
    unverified_currency_orders: list[str] = []
    for item in paid:
        amount = item.totals.total_sar
        if amount is None and item.totals.currency.upper() == "SAR":
            amount = item.totals.total
        if amount is None:
            unverified_currency_orders.append(item.order_number)
        else:
            sar_sales.append(amount)
    conversion_complete = not unverified_currency_orders
    revenue = round(sum(sar_sales), 2) if conversion_complete else None
    products = sum(len(item.items) for item in items)
    units = sum(sum(row.quantity for row in item.items) for item in items)
    before = [item for item in items if cutoff and item.created_at <= cutoff]
    after = [item for item in items if cutoff and item.created_at > cutoff]
    return {
        "orders": len(items), "paid_orders": len(paid), "pending_payment_orders": len(pending),
        "cancelled_orders": len(cancelled), "refunded_orders": len(refunded),
        "paid_sales": revenue,
        "average_basket": (
            round(revenue / len(paid), 2)
            if paid and revenue is not None
            else (0.0 if not paid else None)
        ),
        "product_lines": products, "units": units,
        "unattributed_or_conflicted": sum(item.source.match_status != "matched" for item in items),
        "baseline_cutoff_at": cutoff, "orders_at_or_before_cutoff": len(before),
        "orders_after_cutoff": len(after), "current_total": len(items),
        "financial_policy": "paid_only_excludes_pending_cancelled_refunded",
        "accounting_currency": "SAR",
        "currency_conversion_complete": conversion_complete,
        "unverified_currency_orders": unverified_currency_orders[:100],
        "unknown_is_zero": False,
    }


async def search_orders(repository: OrderRepository, *, user_id: str, filters: dict[str, str | None],
                        limit: int = 50, cursor: str | None = None) -> SearchResult:
    try:
        start, end, cutoff = (_parse(filters.get(key)) for key in
                              ("created_from", "created_to", "baseline_cutoff_at"))
        if start and end and start >= end:
            raise ValueError("reversed period")
    except (ValueError, TypeError, OverflowError) as exc:
        raise OrderSearchError("invalid_search_period", "اختر فترة تاريخ صحيحة للبحث.") from exc
    fingerprint = hashlib.sha256(json.dumps([str(user_id), filters], sort_keys=True).encode()).hexdigest()[:24]
    offset = 0
    if cursor:
        try:
            prefix, expected, offset_text = cursor.split(":")
            offset = int(offset_text)
            if prefix != "search" or expected != fingerprint or not 0 <= offset < SCAN_LIMIT:
                raise ValueError("cursor mismatch")
        except (ValueError, TypeError) as exc:
            raise OrderSearchError("invalid_search_cursor", "تغيّرت شروط البحث؛ اضغط بحث من جديد.", 400) from exc
    if _SEARCH_SLOT.locked():
        raise OrderSearchError("search_busy", "يوجد بحث قيد التنفيذ؛ حاول بعد لحظات.", 429)
    async with _SEARCH_SLOT:
        try:
            async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
                items, seen = [], set()
                async def candidates():
                    if hasattr(repository, "iter_salla_search_orders"):
                        async with aclosing(repository.iter_salla_search_orders(
                            user_id=user_id, start=start, end=end, product_id=filters.get("product_id"),
                        )) as stream:
                            async for row in stream:
                                yield row
                    else:
                        # Repository protocol test doubles; production always uses the streaming reader.
                        for row in await repository.list_salla_orders(user_id=user_id, limit=SCAN_LIMIT + 1):
                            yield row
                scanned = 0
                async with aclosing(candidates()) as rows:
                    async for row in rows:
                        scanned += 1
                        if scanned > SCAN_LIMIT:
                            raise OrderSearchError("search_period_too_broad", "نطاق البحث واسع؛ اختر فترة أقصر أو رقم المنتج ثم أعد البحث.")
                        if scanned % 10 == 0:
                            # Let order preparation and other requests run between small mapping batches.
                            await asyncio.sleep(0)
                        if row is None or str(row.order_number) in seen:
                            continue
                        seen.add(str(row.order_number))
                        item = _map_row(row.salla_raw, current_status=row.current_status)
                        if _matches(item, filters, start, end):
                            items.append(item)
                items.sort(key=lambda item: (item.created_at, item.order_number), reverse=True)
                limit = max(1, min(int(limit), 50))
                next_offset = offset + limit
                next_cursor = f"search:{fingerprint}:{next_offset}" if next_offset < len(items) else None
                return SearchResult(items=items[offset:next_offset], summary=_summary(items, cutoff), next_cursor=next_cursor)
        except (TimeoutError, ExecutionTimeout) as exc:
            raise OrderSearchError("search_timeout", "استغرق البحث وقتًا طويلًا؛ اختر فترة أقصر ثم أعد البحث.", 503) from exc
