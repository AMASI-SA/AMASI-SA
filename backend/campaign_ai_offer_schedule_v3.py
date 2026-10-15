"""Pure offer/sale schedule evidence for Decision Intelligence V3.

This module contains no database or provider writes. It converts Product V2 sale
fields into auditable evidence and detects stale promotion wording conservatively.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import re
from typing import Any


PROMOTION_TERMS = (
    "خصم",
    "تخفيض",
    "تخفيضات",
    "عرض خاص",
    "سعر خاص",
    "وفر",
    "discount",
    "sale",
    "special offer",
    "save ",
    "% off",
)


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _plain_text(value: Any) -> str:
    raw = unescape(str(value or ""))
    raw = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(raw.split()).casefold()


def promotion_copy_evidence(product: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        _plain_text(product.get(key))
        for key in ("name", "short_description", "description")
    )
    matched = [term for term in PROMOTION_TERMS if term.casefold() in text]
    return {
        "detected": bool(matched),
        "matched_terms": matched[:8],
        "source_fields": ["name", "short_description", "description"],
        "contract": "promotion wording is evidence only; generic copy must not force a marketing action",
    }


def build_offer_schedule_evidence(
    product: dict[str, Any],
    *,
    now: datetime,
    expiring_hours: int = 24,
) -> dict[str, Any]:
    current = now.astimezone(timezone.utc)
    starts_at = _parse_dt(product.get("sale_starts_at"))
    ends_at = _parse_dt(product.get("sale_ends_at"))
    base_price = _number(product.get("price"))
    sale_price = _number(product.get("sale_price"))
    discounted = bool(
        base_price is not None
        and sale_price is not None
        and sale_price < base_price
    )

    hours_to_start = None
    hours_to_end = None
    if starts_at:
        hours_to_start = round((starts_at - current).total_seconds() / 3600.0, 3)
    if ends_at:
        hours_to_end = round((ends_at - current).total_seconds() / 3600.0, 3)

    if ends_at and ends_at <= current:
        state = "expired"
    elif starts_at and starts_at > current:
        state = "scheduled"
    elif ends_at and 0 < (ends_at - current).total_seconds() <= expiring_hours * 3600:
        state = "expiring"
    elif discounted and (not starts_at or starts_at <= current) and (not ends_at or ends_at > current):
        state = "active"
    elif starts_at or ends_at:
        state = "scheduled_without_verified_discount_price"
    else:
        state = "no_schedule"

    return {
        "state": state,
        "base_price": base_price,
        "sale_price": sale_price,
        "discounted_price_verified": discounted,
        "sale_starts_at": starts_at.isoformat() if starts_at else None,
        "sale_ends_at": ends_at.isoformat() if ends_at else None,
        "hours_to_start": hours_to_start,
        "hours_to_end": hours_to_end,
        "promotion_copy": promotion_copy_evidence(product),
        "evidence_only": True,
    }


__all__ = [
    "PROMOTION_TERMS",
    "build_offer_schedule_evidence",
    "promotion_copy_evidence",
]
