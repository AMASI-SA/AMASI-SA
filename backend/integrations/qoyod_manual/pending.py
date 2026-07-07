"""Plan-B Manual Send — list of orders eligible for MANUAL push.

Rules (immutable, per user directive 2026-02):
    • Salla status is "completed" (canonical `order_status`).
    • Order CREATION date >= 2026-07-01 (integration floor).
    • No real Qoyod invoice tracked in Mezan yet:
          - `manual_qoyod_invoice_id` is unset (this module's marker)
          - AND `qoyod_invoice_id` is unset OR starts with DRY:/PREVIEW:
    • DB-first check. A live Qoyod lookup runs ONLY when DB shows
      "not sent" AND `verify_live=True` in the query params (used by
      the "verify" button next to a row when the operator is unsure).
"""
from __future__ import annotations

from datetime import datetime, date, timedelta, timezone
from typing import Optional

# Reuse the SAME floor-date + date-extraction helpers so Plan B and
# the frozen pipeline see the same universe of dates.
from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE,
)
from integrations.qoyod.unsent_orders import (
    _order_created_date,
    _is_real,
)

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)


def _is_completed(row: dict) -> bool:
    """Salla status filter — only "completed" (canonical slug)."""
    canon = row.get("canonical_payload") or {}
    slug = str(canon.get("order_status") or "").strip().lower()
    if slug == "completed":
        return True
    # Legacy rows may carry the native Arabic string too. Trust the
    # canonical slug first; the native check is a defensive fallback.
    native = str(canon.get("order_status_native") or "").strip()
    return native in ("تم التنفيذ", "منتهي", "مكتمل")


def _already_sent(row: dict) -> tuple[bool, Optional[str]]:
    """Return (already_sent, invoice_ref). Uses the DB-only markers:
      • `manual_qoyod_invoice_id` — set by this module on success.
      • `qoyod_invoice_id` — set by the legacy pipeline; must be a real
        (non-DRY/PREVIEW) numeric-like id to count.
    """
    manual_id = row.get("manual_qoyod_invoice_id")
    if manual_id:
        return True, str(manual_id)
    legacy_id = row.get("qoyod_invoice_id")
    if legacy_id and _is_real(legacy_id):
        return True, str(legacy_id)
    return False, None


async def list_pending_orders(
    db, *, user_id: str, days: int = 60, limit: int = 200,
    search: Optional[str] = None,
) -> dict:
    """Return orders that meet ALL 3 Plan-B criteria (completed +
    on/after floor date + no real invoice)."""
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 1000))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query: dict = {"user_id": user_id, "received_at": {"$gte": cutoff}}
    if search and str(search).strip():
        import re
        query["salla_order_number"] = {
            "$regex": re.escape(str(search).strip())}

    projection = {
        "_id": 0, "id": 1, "trace_id": 1,
        "salla_order_number": 1, "received_at": 1,
        "manual_qoyod_invoice_id": 1,
        "qoyod_invoice_id": 1,
        "raw_payload.data.date": 1,
        "raw_payload.data.created_at": 1,
        "canonical_payload.order_date": 1,
        "canonical_payload.created_at": 1,
        "canonical_payload.total_amount": 1,
        "canonical_payload.currency": 1,
        "canonical_payload.payment_method": 1,
        "canonical_payload.payment_method_native": 1,
        "canonical_payload.order_status": 1,
        "canonical_payload.order_status_native": 1,
        "canonical_payload.customer.name": 1,
        "canonical_payload.customer.phone": 1,
    }

    # Sorted by received_at desc so newest orders surface first.
    cursor = db.integration_inbox.find(query, projection) \
        .sort("received_at", -1).limit(limit)

    seen_orders: set[str] = set()
    pending: list[dict] = []
    scanned = 0
    excluded_pre_floor = 0
    excluded_not_completed = 0
    excluded_already_sent = 0

    async for row in cursor:
        scanned += 1
        order_number = str(row.get("salla_order_number") or "").strip()
        # De-duplicate: keep only the newest row per order_number
        # (there can be several inbox rows for the same Salla order
        # due to status-transition webhooks).
        if not order_number or order_number in seen_orders:
            continue
        seen_orders.add(order_number)

        # Floor date
        odate = _order_created_date(row)
        if odate is None or odate < _FLOOR_DATE:
            excluded_pre_floor += 1
            continue

        # Completed
        if not _is_completed(row):
            excluded_not_completed += 1
            continue

        # Not sent
        already, invoice_ref = _already_sent(row)
        if already:
            excluded_already_sent += 1
            continue

        canon = row.get("canonical_payload") or {}
        received = row.get("received_at")
        pending.append({
            "order_number":    order_number,
            "trace_id":        row.get("trace_id"),
            "row_id":          row.get("id"),
            "order_date":      odate.isoformat() if odate else None,
            "received_at":     (received.isoformat()
                                if hasattr(received, "isoformat")
                                else received),
            "total_amount":    canon.get("total_amount"),
            "currency":        canon.get("currency") or "SAR",
            "payment_method":  (canon.get("payment_method")
                                or canon.get("payment_method_native")),
            "payment_method_native": canon.get("payment_method_native"),
            "salla_status":    (canon.get("order_status_native")
                                or canon.get("order_status")),
            "customer_name":   ((canon.get("customer") or {}).get("name")),
            "customer_phone":  ((canon.get("customer") or {}).get("phone")),
        })

    return {
        "ok":            True,
        "floor_date":    _FLOOR_DATE.isoformat(),
        "days_window":   days,
        "counts": {
            "returned":              len(pending),
            "scanned_inbox_rows":    scanned,
            "excluded_pre_floor":    excluded_pre_floor,
            "excluded_not_completed": excluded_not_completed,
            "excluded_already_sent": excluded_already_sent,
        },
        "orders":        pending,
    }
