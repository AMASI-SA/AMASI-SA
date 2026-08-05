"""Shared active-Campaign filtering and global entity sorting for Snapchat Ads Manager."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

ACTIVE_PROVIDER_STATUSES = frozenset({"ACTIVE", "ENABLED", "DELIVERING"})
VALID_ENTITY_SORTS = frozenset({"orders", "spend", "newest", "active"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _ratio(numerator: float | None, denominator: float | None, scale: float = 1.0) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(float(numerator) / float(denominator) * scale, 6)


def is_active_provider_status(value: Any) -> bool:
    return _text(value).upper() in ACTIVE_PROVIDER_STATUSES


def normalize_entity_sort(value: Any, default: str = "orders") -> str:
    normalized = _text(value).lower()
    return normalized if normalized in VALID_ENTITY_SORTS else default


def _timestamp(row: dict[str, Any], fields: Iterable[str]) -> float:
    for field in fields:
        raw = _text(row.get(field))
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return 0.0


def sort_entity_rows(
    rows: list[dict[str, Any]],
    mode: str,
    *,
    name_field: str,
    status_field: str = "status",
    created_fields: tuple[str, ...] = (
        "created_at_provider",
        "start_time",
        "updated_at_provider",
    ),
) -> list[dict[str, Any]]:
    normalized = normalize_entity_sort(mode)

    def key(row: dict[str, Any]):
        orders = _number(row.get("orders")) or 0.0
        spend = _number(row.get("spend_sar")) or 0.0
        active = 1 if is_active_provider_status(row.get(status_field)) else 0
        created = _timestamp(row, created_fields)
        name = _text(row.get(name_field)).casefold()
        if normalized == "spend":
            return (-spend, -orders, -created, name)
        if normalized == "newest":
            return (-created, -spend, -orders, name)
        if normalized == "active":
            return (-active, -orders, -spend, -created, name)
        return (-orders, -spend, -created, name)

    return sorted((dict(row) for row in rows), key=key)


def aggregate_entity_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    numeric_fields = (
        "spend_sar",
        "spend_native",
        "sales_sar",
        "sales_native",
        "orders",
        "impressions",
        "swipes",
        "video_views",
    )
    totals: dict[str, float | None] = {}
    for field in numeric_fields:
        values = [number for row in rows if (number := _number(row.get(field))) is not None]
        totals[field] = round(sum(values), 6) if values else None
    orders = totals.get("orders")
    impressions = totals.get("impressions")
    swipes = totals.get("swipes")
    spend_sar = totals.get("spend_sar")
    spend_native = totals.get("spend_native")
    sales_sar = totals.get("sales_sar")
    totals.update({
        "orders": int(round(orders)) if orders is not None else None,
        "impressions": int(round(impressions)) if impressions is not None else None,
        "swipes": int(round(swipes)) if swipes is not None else None,
        "video_views": int(round(totals["video_views"])) if totals.get("video_views") is not None else None,
        "roas": _ratio(sales_sar, spend_sar),
        "cpa_sar": _ratio(spend_sar, orders),
        "cpa_native": _ratio(spend_native, orders),
        "cpc_sar": _ratio(spend_sar, swipes),
        "cpc_native": _ratio(spend_native, swipes),
        "cpm_sar": _ratio(spend_sar, impressions, 1000.0),
        "cpm_native": _ratio(spend_native, impressions, 1000.0),
        "ctr_pct": _ratio(swipes, impressions, 100.0),
        "observed_days": max((int(row.get("observed_days") or 0) for row in rows), default=0),
        "source_rows": sum(int(row.get("source_rows") or 0) for row in rows),
        "data_complete": bool(rows) and all(row.get("data_complete") is not False for row in rows),
    })
    return totals


__all__ = [
    "ACTIVE_PROVIDER_STATUSES",
    "VALID_ENTITY_SORTS",
    "aggregate_entity_rows",
    "is_active_provider_status",
    "normalize_entity_sort",
    "sort_entity_rows",
]
