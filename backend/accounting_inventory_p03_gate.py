"""Lightweight MZ2 P03 ownership gate for operational modules.

This file intentionally imports no accounting router/status/auth modules.  It
lets Inventory/Supplier Receiving decide whether P01 owns the financial
namespace without pulling the full accounting application into their focused
test/runtime boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

OPERATION_ID = "MZ2-FIN-CUTOVER-001"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _aware(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


async def p01_controls_purchase_accounting(
    db: Any,
    *,
    owner: str,
    mongo_session: Any = None,
) -> bool:
    """True once P01 has taken ownership of post-cutover financial journals."""
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
        session=mongo_session,
    )
    state = dict((row or {}).get("mezan2_financial_cutover") or {})
    return bool(
        state.get("operation_id") == OPERATION_ID
        and state.get("status") == "active"
        and state.get("opening_balance_txn_group_id")
        and _aware(state.get("cutover_at"))
    )
