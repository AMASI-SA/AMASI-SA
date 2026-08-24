"""Shared constants and normalization helpers."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

_TENANT = "main"
_SENDER_CONNECTOR = "qoyod_unified_auto_sender"
_ORDER_NUMBER_RE = re.compile(r"^\d{8,12}$")
_TWO_PLACES = Decimal("0.01")

RETRYABLE_SYNC_FAILURE_CODES = frozenset({
    "salla_status_refresh_failed",
    "authoritative_order_missing_after_resync",
    "authoritative_payment_method_still_pending",
    "authoritative_payment_needs_verification",
    "legacy_sender_inbox_row_missing",
    "legacy_sender_inbox_update_missed",
    "authoritative_payment_refresh_failed",
    "unified_sender_row_upsert_failed",
    "qoyod_reference_reconciliation_failed",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _money(value: Any) -> Optional[float]:
    """Read a money-like value without turning malformed data into zero."""
    node = value
    for _ in range(6):
        if isinstance(node, dict):
            for key in (
                "amount", "value", "total", "price", "sub_total", "subtotal",
            ):
                if node.get(key) not in (None, ""):
                    node = node[key]
                    break
            else:
                return None
            continue
        break
    if node in (None, "") or isinstance(node, bool):
        return None
    try:
        parsed = Decimal(str(node))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return float(parsed.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _first_money(*values: Any, default: float = 0.0) -> float:
    for value in values:
        parsed = _money(value)
        if parsed is not None:
            return parsed
    return default


def _text(*values: Any) -> Optional[str]:
    for value in values:
        if value in (None, "", [], {}):
            continue
        result = str(value).strip()
        if result:
            return result
    return None


def _date_value(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return _date_value(value.get("date") or value.get("created_at"))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _raw_salla(row: dict[str, Any]) -> dict[str, Any]:
    raw = (row.get("raw_by_source") or {}).get("salla_direct") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _item_rows(row: dict[str, Any], raw_salla: dict[str, Any]) -> list[dict[str, Any]]:
    for candidate in (
        row.get("items"),
        row.get("products"),
        raw_salla.get("items"),
        raw_salla.get("products"),
    ):
        if isinstance(candidate, list) and candidate:
            return [dict(item) for item in candidate if isinstance(item, dict)]
    return []
