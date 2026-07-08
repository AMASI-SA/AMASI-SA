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
from typing import Optional, Any

# Reuse the SAME floor-date + is_real helpers so Plan B and
# the frozen pipeline see the same universe.
from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE,
    _parse_iso_date,
)
from integrations.qoyod.unsent_orders import _is_real

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)


def _salla_order_created_date(row: dict) -> Optional[date]:
    """Salla-source-only creation date.

    User directive (2026-07-08): the floor-date filter MUST use the
    REAL Salla creation date. NEVER fall back to `received_at`,
    `updated_at`, or any webhook-timing field — those can be much
    newer than the actual order and would let pre-floor orders slip
    through Plan B (bug: order 268552119 leaked in).

    Priority (all from the Salla payload only):
        1. canonical_payload.order_date       (normalizer output)
        2. canonical_payload.created_at
        3. raw_payload.data.date.date         (Salla envelope)
        4. raw_payload.data.date              (Salla flat string)
        5. raw_payload.data.created_at

    Returns `None` when no Salla-source date is present — the caller
    MUST then exclude the row (never assume it belongs post-floor).
    """
    canon = row.get("canonical_payload") or {}
    d = _parse_iso_date(canon.get("order_date"))
    if d is not None:
        return d
    d = _parse_iso_date(canon.get("created_at"))
    if d is not None:
        return d
    raw = row.get("raw_payload") or {}
    data = raw.get("data") or {}
    if isinstance(data, dict):
        date_field = data.get("date")
        if isinstance(date_field, dict):
            d = _parse_iso_date(date_field.get("date"))
            if d is not None:
                return d
        elif date_field is not None:
            d = _parse_iso_date(date_field)
            if d is not None:
                return d
        d = _parse_iso_date(data.get("created_at"))
        if d is not None:
            return d
    # No Salla-source date → row is EXCLUDED from Plan B (never
    # promoted via received_at).
    return None


def _is_completed(row: dict) -> bool:
    """Salla status filter — only "completed" (canonical slug).

    Legacy alias kept for external callers; new code should use
    `_matches_status(row, "completed")` which is strictly equivalent.
    """
    return _matches_status(row, "completed")


# ── Multi-status support (user directive 2026-07-08) ────────────────
# Plan-B page now offers three tabs. Each tab filters on a Salla
# status. The mapping below is intentionally explicit — we match on
# BOTH the canonical slug and the native Arabic string so tenants
# with non-normalised rows still surface correctly:
#
#   completed    — تم التنفيذ (canonical: "completed")
#   delivered    — تم التوصيل (canonical: "delivered")
#   in_delivery  — جاري التوصيل (canonical fallback slug: "جاري_التوصيل")
#
# NEW statuses go here and here only — no other Plan-B code needs to
# change (send.py checks each row against `_matches_status` at request
# time, using the same helper).
_STATUS_MATCHERS: dict[str, tuple[frozenset, frozenset]] = {
    "completed": (
        frozenset({"completed"}),
        frozenset({"تم التنفيذ", "منتهي", "مكتمل"}),
    ),
    "delivered": (
        frozenset({"delivered"}),
        frozenset({"تم التوصيل"}),
    ),
    "in_delivery": (
        frozenset({"in_delivery", "جاري_التوصيل", "جاري التوصيل"}),
        frozenset({"جاري التوصيل", "جارٍ التوصيل"}),
    ),
}

SUPPORTED_STATUSES: tuple[str, ...] = tuple(_STATUS_MATCHERS.keys())


def _matches_status(row: dict, status_key: str) -> bool:
    matcher = _STATUS_MATCHERS.get(status_key)
    if matcher is None:
        return False
    canonical_set, native_set = matcher
    canon = row.get("canonical_payload") or {}
    slug = str(canon.get("order_status") or "").strip().lower()
    if slug in canonical_set:
        return True
    native = str(canon.get("order_status_native") or "").strip()
    return native in native_set


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
    status: str = "completed",
) -> dict:
    """Return orders that meet ALL 3 Plan-B criteria:
        • Salla status matches `status` (one of SUPPORTED_STATUSES).
        • Salla creation date >= 2026-07-01 (floor).
        • No real Qoyod invoice recorded on this row yet.

    `status` defaults to "completed" (backwards-compatible with the
    original 3-rule spec). NEW values: "delivered", "in_delivery".
    Unknown values fall back to "completed" — the endpoint validator
    forbids them at the API boundary too.
    """
    if status not in SUPPORTED_STATUSES:
        status = "completed"
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
    excluded_no_salla_date = 0
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

        # Floor date — Salla-source-only. Rows without a Salla date
        # are EXCLUDED (never promoted via received_at).
        odate = _salla_order_created_date(row)
        if odate is None:
            excluded_no_salla_date += 1
            continue
        if odate < _FLOOR_DATE:
            excluded_pre_floor += 1
            continue

        # Completed
        if not _matches_status(row, status):
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
        "status":        status,
        "supported_statuses": list(SUPPORTED_STATUSES),
        "floor_date":    _FLOOR_DATE.isoformat(),
        "days_window":   days,
        "counts": {
            "returned":              len(pending),
            "scanned_inbox_rows":    scanned,
            "excluded_pre_floor":    excluded_pre_floor,
            "excluded_no_salla_date": excluded_no_salla_date,
            "excluded_not_completed": excluded_not_completed,
            "excluded_already_sent": excluded_already_sent,
        },
        "orders":        pending,
    }
