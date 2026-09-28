"""Shared fail-closed order-creation fence for MZ2 sale recognition.

The financial cutover is based on the order's creation instant. Delivery,
capture, collection, receipt, or settlement after cutover cannot make a
pre-cutover order eligible for MZ2 sale/COD/receivable recognition.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")
OPERATION_ID = "MZ2-FIN-CUTOVER-001"


class OrderCutoverError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def order_creation_time(value: Any, *, source_timezone: bool) -> datetime:
    text = _text(value)
    if not text:
        raise OrderCutoverError("order_creation_timestamp_required")

    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass

    if parsed is None:
        raise OrderCutoverError("order_creation_timestamp_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not source_timezone:
            raise OrderCutoverError("order_creation_timestamp_invalid")
        parsed = parsed.replace(tzinfo=RIYADH)
    return parsed.astimezone(timezone.utc)


def cutover_time(value: Any) -> datetime:
    text = _text(value)
    if not text:
        raise OrderCutoverError("recognition_cutoff_not_configured")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise OrderCutoverError("recognition_cutoff_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OrderCutoverError("recognition_cutoff_invalid")
    return parsed.astimezone(timezone.utc)


def require_order_created_on_or_after_cutover(
    order_created_at: Any,
    cutover_at: Any,
    *,
    source_timezone: bool,
) -> datetime:
    created = order_creation_time(order_created_at, source_timezone=source_timezone)
    cut = cutover_time(cutover_at)
    if created < cut:
        raise OrderCutoverError("pre_cutover_order")
    return created


async def active_cutover_value(db: Any, owner: str) -> Any:
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
    )
    state = (row or {}).get("mezan2_financial_cutover") or {}
    if (
        state.get("operation_id") != OPERATION_ID
        or str(state.get("status") or "").strip().casefold() != "active"
        or not state.get("cutover_at")
    ):
        raise OrderCutoverError("recognition_cutoff_not_configured")
    # Validate the stored value before returning the original evidence.
    cutover_time(state.get("cutover_at"))
    return state["cutover_at"]


async def require_salla_order_creation_fence(
    db: Any,
    *,
    owner: str,
    order_number: str,
) -> tuple[dict[str, Any], datetime]:
    reference = _text(order_number)
    if not reference:
        raise OrderCutoverError("order_creation_evidence_required")
    rows = await db.mz2_salla_order_evidence.find(
        {"user_id": owner, "order_number": reference},
        {"_id": 0},
    ).limit(2).to_list(2)
    if len(rows) != 1:
        raise OrderCutoverError("unique_order_creation_evidence_required")
    evidence = rows[0]
    created = require_order_created_on_or_after_cutover(
        evidence.get("order_date_source_text"),
        await active_cutover_value(db, owner),
        source_timezone=True,
    )
    return evidence, created
