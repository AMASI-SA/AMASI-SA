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


async def _sent_in_any_local_record(
    db, *, user_id: str, order_number: str,
    salla_order_id: Optional[str] = None,
) -> Optional[str]:
    """Cross-trace guard (user directive 2026-07-08):

    A single Salla order can produce SEVERAL `integration_inbox` rows
    over time — one per status transition webhook (e.g. `completed`
    row #1 gets sent to قيود with a real `qoyod_invoice_id`, then a
    later `in_delivery` webhook creates row #2 with NO invoice id).

    `list_pending_orders` de-dupes to the NEWEST row per order_number
    and only inspects that row's markers — which means the newer
    "in_delivery" row leaks into Plan B even though the order is
    already invoiced.

    This helper answers a single question BEFORE we surface the row:
        "Does ANY local record — across all traces of this
         order_number — prove the order was already sent to قيود?"

    Checks are LOCAL ONLY (no قيود API call), across:
      1. integration_inbox: any trace w/ same salla_order_number
         carrying a real `qoyod_invoice_id`.
      2. integration_inbox: any trace w/ same salla_order_number
         carrying a real `manual_qoyod_invoice_id`.
      3. qoyod_invoices  : an entry keyed by salla_order_number OR
         salla_order_id with a real `qoyod_invoice_id`.

    Returns the invoice id (string) if found, else None.
    """
    if not order_number:
        return None

    # 1 + 2. Any inbox trace with either marker set. We ask Mongo
    #        only for rows where at least one field is non-null, then
    #        validate `_is_real` in Python (DRY:/PREVIEW: don't count).
    inbox_cursor = db.integration_inbox.find(
        {
            "user_id": user_id,
            "salla_order_number": str(order_number),
            "$or": [
                {"manual_qoyod_invoice_id": {"$nin": [None, ""]}},
                {"qoyod_invoice_id":        {"$nin": [None, ""]}},
            ],
        },
        {"_id": 0, "manual_qoyod_invoice_id": 1, "qoyod_invoice_id": 1},
    )
    async for r in inbox_cursor:
        mid = r.get("manual_qoyod_invoice_id")
        if mid and _is_real(mid):
            return str(mid)
        lid = r.get("qoyod_invoice_id")
        if lid and _is_real(lid):
            return str(lid)

    # 3. قيود-side invoice record (still local — this collection is
    #    written by BOTH the legacy pipeline AND Plan-B send.py).
    or_clauses: list[dict] = [{"salla_order_number": str(order_number)}]
    if salla_order_id:
        or_clauses.append({"salla_order_id": str(salla_order_id)})
    inv = await db.qoyod_invoices.find_one(
        {"user_id": user_id, "$or": or_clauses,
         "qoyod_invoice_id": {"$nin": [None, ""]}},
        {"_id": 0, "qoyod_invoice_id": 1},
    )
    if inv:
        qid = inv.get("qoyod_invoice_id")
        if qid and _is_real(qid):
            return str(qid)

    return None


async def list_pending_orders(
    db, *, user_id: str, days: int = 60, limit: int = 500,
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

    Limit-vs-filter ordering (2026-07-09):
    The Salla-status filter is pushed DOWN into the Mongo query so
    the `limit` cap applies to the STATUS-FILTERED subset. Previously
    limit=200 was applied first (sort by received_at desc), then the
    Python-side status matcher filtered — meaning heavy webhook
    traffic in unrelated statuses could evict eligible "delivered"
    orders from the window. Default `limit` also raised 200 → 500
    to match `list_unsent_orders`.
    """
    if status not in SUPPORTED_STATUSES:
        status = "completed"
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 1000))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query: dict = {"user_id": user_id, "received_at": {"$gte": cutoff}}

    # Status filter — Mongo-side. Matches EXACTLY the same set that
    # `_matches_status` would accept in Python, but does so BEFORE
    # the limit takes effect. This is the single change that fixes
    # the "24 vs 6" discrepancy observed on production 2026-07-09.
    canonical_set, native_set = _STATUS_MATCHERS[status]
    query["$or"] = [
        {"canonical_payload.order_status":
             {"$in": list(canonical_set)}},
        {"canonical_payload.order_status_native":
             {"$in": list(native_set)}},
    ]

    if search and str(search).strip():
        import re
        query["salla_order_number"] = {
            "$regex": re.escape(str(search).strip())}

    projection = {
        "_id": 0, "id": 1, "trace_id": 1,
        "salla_order_number": 1, "salla_order_id": 1,
        "received_at": 1,
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

        # Not sent — on THIS row (newest trace).
        already, invoice_ref = _already_sent(row)
        if already:
            excluded_already_sent += 1
            continue

        # Cross-trace guard: another trace of the SAME order_number
        # may already carry a real قيود invoice id. Simple local check
        # (no قيود API call, no self-heal, no diagnostic side-effects).
        cross_id = await _sent_in_any_local_record(
            db, user_id=user_id,
            order_number=order_number,
            salla_order_id=str(row.get("salla_order_id") or "") or None,
        )
        if cross_id:
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
