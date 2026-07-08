"""Plan-B Manual Send — Diagnostic: orders MISSING from Plan B pending.

User directive (2026-02): the operator reports that:
    • Salla shows ~263 completed+in_delivery+delivered orders since
      2026-07-01.
    • Mezan shows ~195 sent to قيود and ~12 pending in Plan B.
    • Gap of ~56 orders is INVISIBLE — neither sent nor pending.

This module answers the SINGLE question:
    "Why is this Salla order NOT showing up in Plan B pending?"

It cross-references SIX independent sources of truth:
    1. `unified_orders`          — the Salla-side source of truth
    2. `integration_inbox`       — the webhook capture stream
    3. Plan-B pending logic      — `list_pending_orders` filters
    4. `qoyod_invoices`          — قيود-side invoice records
    5. `manual_qoyod_invoice_id` — Plan-B success marker on inbox
    6. `qoyod_invoice_id`        — legacy success marker on inbox

For every order it emits:
    • order_number, salla_status, salla_created_date, payment_method,
      salla_total, currency, customer_name
    • has_qoyod_invoice (bool + id if any)
    • visible_in_plan_b (bool)
    • missing_stage — WHERE in the pipeline the order dropped
    • reason — WHY it dropped (short code)
    • presence flags for each of the 6 sources

Read-only. NO writes. NO Qoyod network calls. NO send buttons.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date, _normalize_status,
)
from integrations.qoyod.unsent_orders import _is_real
from integrations.qoyod_manual.pending import (
    _matches_status, _salla_order_created_date, SUPPORTED_STATUSES,
)

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)

# The three statuses Plan B tabs support. Any other status is
# `status_not_supported_by_plan_b`.
_PLAN_B_STATUSES: tuple[str, ...] = SUPPORTED_STATUSES  # (completed, delivered, in_delivery)

# Native Arabic + English variants we accept as "one of the 3 statuses".
# Used ONLY to filter `unified_orders`; the inbox uses `_matches_status`
# on the canonical payload directly.
_PLAN_B_STATUS_TOKENS: frozenset[str] = frozenset({
    "completed", "delivered", "in_delivery", "in delivery",
    "shipping",  # legacy alias sometimes used for in_delivery
    "تم التنفيذ", "تم التوصيل", "جاري التوصيل",
    "منتهي", "مكتمل", "جاري_التوصيل", "جارٍ التوصيل",
})


def _norm(s: Any) -> str:
    return _normalize_status(s)


def _status_key_from_unified(row: dict) -> Optional[str]:
    """Map a unified_orders row to one of the 3 Plan-B status keys
    (`completed`, `delivered`, `in_delivery`) — or None if the status
    is outside the Plan-B universe."""
    for f in (row.get("order_status_slug"), row.get("order_status")):
        s = _norm(f)
        if not s:
            continue
        if s in ("completed", "تم التنفيذ", "منتهي", "مكتمل"):
            return "completed"
        if s in ("delivered", "تم التوصيل"):
            return "delivered"
        if s in ("in_delivery", "in delivery", "shipping",
                 "جاري التوصيل", "جارٍ التوصيل"):
            return "in_delivery"
    return None


def _status_key_from_inbox(row: dict) -> Optional[str]:
    """Same, but reading from an integration_inbox row (canonical
    payload). Returns None if outside the 3 supported statuses."""
    for k in _PLAN_B_STATUSES:
        if _matches_status(row, k):
            return k
    return None


def _inbox_salla_date(row: dict) -> Optional[date]:
    return _salla_order_created_date(row)


def _has_plan_b_marker(row: dict) -> tuple[bool, Optional[str], str]:
    """(sent?, invoice_id, marker_source).

    marker_source ∈ {"plan_b", "legacy", "none"}.
    """
    mid = row.get("manual_qoyod_invoice_id")
    if mid and _is_real(mid):
        return True, str(mid), "plan_b"
    lid = row.get("qoyod_invoice_id")
    if lid and _is_real(lid):
        return True, str(lid), "legacy"
    return False, None, "none"


def _q_invoice_hit(inv_row: Optional[dict]) -> tuple[bool, Optional[str]]:
    """Read a `qoyod_invoices` row and decide whether it counts as a
    real invoice hit (not DRY/PREVIEW)."""
    if not inv_row:
        return False, None
    qid = inv_row.get("qoyod_invoice_id")
    if qid and _is_real(qid):
        return True, str(qid)
    return False, None


async def _load_qoyod_invoice(db, user_id: str,
                              order_number: str,
                              order_id: Optional[str]) -> Optional[dict]:
    """Best-effort lookup in `qoyod_invoices` by order_number OR
    order_id. Returns the newest matching row, or None."""
    or_clauses: list[dict] = []
    if order_number:
        or_clauses.append({"salla_order_number": str(order_number)})
    if order_id:
        or_clauses.append({"salla_order_id": str(order_id)})
    if not or_clauses:
        return None
    return await db.qoyod_invoices.find_one(
        {"user_id": user_id, "$or": or_clauses},
        {"_id": 0, "qoyod_invoice_id": 1, "invoice_number": 1,
         "status": 1, "posting_mode": 1, "created_at": 1},
        sort=[("created_at", -1)],
    )


# ── Missing-stage classifier ────────────────────────────────────────
# Priority is intentionally FROM COARSEST FILTER → FINEST so the
# reason returned is the FIRST filter that blocks the row.
def _classify(  # noqa: C901 — 9 branches, each is a single check
    *,
    in_unified: bool,
    in_inbox: bool,
    salla_status_key: Optional[str],   # None if unsupported
    salla_status_raw: Optional[str],   # native/canonical string for display
    salla_created_date: Optional[date],
    marker_source: str,                # "plan_b" | "legacy" | "none"
    qoyod_side_hit: bool,
) -> tuple[str, str]:
    """Return (missing_stage, reason) — both short slugs.

    `missing_stage` = the SLICE of the pipeline where the row got
                      stuck. This is what the operator glances at.
    `reason`        = the specific rule that blocked it (finer-grain).
    """
    # 1. Already sent — from either marker.
    if marker_source == "plan_b":
        return "already_sent_plan_b", "already_sent"
    if marker_source == "legacy":
        return "already_sent_legacy", "already_sent"

    # 2. Order has a real invoice on the قيود side but Mezan's inbox
    #    didn't get the marker written back. This is the "reconciliation
    #    marker drift" case — the repair-recon-markers endpoint fixes it.
    if qoyod_side_hit:
        return "already_in_qoyod", "duplicate_invoice_in_qoyod"

    # 3. Missing from the Salla-side universe.
    if not in_unified and in_inbox:
        return "missing_from_unified_orders", "missing_from_unified_orders"
    if in_unified and not in_inbox:
        return "missing_from_integration_inbox", \
               "missing_from_integration_inbox"
    if not in_unified and not in_inbox:
        # Should NEVER happen — the enumerator only surfaces rows that
        # exist in AT LEAST one source. Keep a defensive branch.
        return "unknown", "unknown_reason"

    # 4. Salla-side filters applied by Plan-B pending.
    if salla_created_date is None:
        return "filtered_by_policy", "no_salla_order_date"
    if salla_created_date < _FLOOR_DATE:
        return "filtered_by_policy", "before_floor_date"
    if salla_status_key is None:
        return "filtered_by_policy", "status_not_supported_by_plan_b"

    # 5. In inbox, correct status, correct date, no marker → it SHOULD
    #    be visible in Plan B. If it isn't we surface it as unknown so
    #    the operator can dig further. (Rare — usually indicates a
    #    stale de-dup that hid the newer row.)
    return "missing_from_plan_b_pending", "unknown_reason"


async def list_missing_from_plan_b(
    db, *, user_id: str, days: int = 90, limit: int = 1000,
    search: Optional[str] = None,
    include_already_sent: bool = True,
) -> dict:
    """Enumerate Salla orders that DON'T show up in Plan B pending.

    The enumerator:
        1. Pulls the Plan-B "visible" order_numbers via the SAME
           pending logic (so what we call "visible" is authoritative).
        2. Pulls `unified_orders` rows with a Plan-B-relevant status
           since the floor date.
        3. Pulls the newest `integration_inbox` row per order_number
           inside the days-window (regardless of status — status
           filter is applied later per row).
        4. Union-by-order_number, drops any order visible in Plan B,
           then classifies each remaining row into (missing_stage,
           reason).

    Args
    ────
    days                : lookback for `integration_inbox.received_at`
                          (unified_orders uses the floor-date bound).
    limit               : hard cap on returned rows.
    search              : substring match on `order_number`.
    include_already_sent: default True — the operator wants to SEE
                          already-sent rows too so they know that's
                          why the row isn't in Plan B. Toggleable.

    Returns a dict with `orders`, `counts`, and `by_stage` /
    `by_reason` histograms.
    """
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 5000))
    now_utc = datetime.now(timezone.utc)
    since_dt = now_utc - timedelta(days=days)
    floor_iso = _FLOOR_DATE.isoformat()

    # ── Step 1: what does Plan B ALREADY show as pending? ─────────────
    # We import lazily to avoid circular imports and to always use the
    # canonical pending filter (whatever tab).
    from integrations.qoyod_manual.pending import list_pending_orders
    plan_b_visible: set[str] = set()
    for status_key in _PLAN_B_STATUSES:
        res = await list_pending_orders(
            db, user_id=user_id, days=days, limit=1000,
            status=status_key)
        for row in (res.get("orders") or []):
            on = str(row.get("order_number") or "").strip()
            if on:
                plan_b_visible.add(on)

    # ── Step 2: unified_orders universe ──────────────────────────────
    unified_by_number: dict[str, dict] = {}
    uni_query: dict = {
        "user_id": user_id,
        "$or": [
            {"order_date":  {"$gte": floor_iso}},
            {"created_at":  {"$gte": since_dt}},
        ],
    }
    if search and str(search).strip():
        uni_query["order_number"] = {
            "$regex": _re_escape(str(search).strip())}

    uni_cursor = db.unified_orders.find(
        uni_query,
        {"_id": 0, "order_number": 1, "order_id": 1,
         "order_status": 1, "order_status_slug": 1,
         "order_date": 1, "order_date_raw": 1,
         "order_date_inferred": 1, "created_at": 1,
         "payment_method": 1, "total_amount": 1, "currency": 1,
         "customer_name": 1, "customer_mobile": 1},
    ).sort([("order_date", -1)])
    async for u in uni_cursor:
        on = str(u.get("order_number") or "").strip()
        if not on:
            continue
        # Only keep orders whose CURRENT status is one of the 3
        # Plan-B-relevant statuses OR whose recent inbox will decide.
        # We keep ALL rows here — the classifier filters later.
        unified_by_number[on] = u
        if len(unified_by_number) >= 20000:
            break

    # ── Step 3: integration_inbox universe (newest row per order) ────
    inbox_by_number: dict[str, dict] = {}
    ib_query: dict = {"user_id": user_id, "received_at": {"$gte": since_dt}}
    if search and str(search).strip():
        ib_query["salla_order_number"] = {
            "$regex": _re_escape(str(search).strip())}
    ib_cursor = db.integration_inbox.find(
        ib_query,
        {"_id": 0, "id": 1, "trace_id": 1,
         "salla_order_number": 1, "salla_order_id": 1,
         "received_at": 1, "pipeline_stage": 1,
         "manual_qoyod_invoice_id": 1, "qoyod_invoice_id": 1,
         "qoyod_invoice_source": 1,
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
         "canonical_payload.customer.phone": 1},
    ).sort([("received_at", -1)])
    async for ib in ib_cursor:
        on = str(ib.get("salla_order_number") or "").strip()
        if not on:
            continue
        # Keep only the NEWEST inbox row per order_number (like Plan B
        # pending does).
        if on in inbox_by_number:
            continue
        inbox_by_number[on] = ib
        if len(inbox_by_number) >= 20000:
            break

    # ── Step 4: union + classify ─────────────────────────────────────
    all_numbers = set(unified_by_number.keys()) | set(inbox_by_number.keys())

    orders_out: list[dict] = []
    by_stage: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    universe_total = len(all_numbers)
    visible_in_plan_b_count = 0
    scanned = 0

    for on in all_numbers:
        scanned += 1
        u = unified_by_number.get(on)
        ib = inbox_by_number.get(on)

        # If it's visible in Plan B pending we skip — the user only
        # wants to see the INVISIBLE ones.
        if on in plan_b_visible:
            visible_in_plan_b_count += 1
            continue

        # Extract display fields — inbox is preferred because it
        # carries the raw Salla webhook; unified is a fallback.
        salla_status_key: Optional[str] = None
        salla_status_raw: Optional[str] = None
        salla_created_date: Optional[date] = None
        payment_method: Optional[str] = None
        total_amount: Any = None
        currency: str = "SAR"
        customer_name: Optional[str] = None
        customer_phone: Optional[str] = None
        salla_order_id: Optional[str] = None

        if ib:
            canon = ib.get("canonical_payload") or {}
            salla_status_key = _status_key_from_inbox(ib)
            salla_status_raw = (canon.get("order_status_native")
                                or canon.get("order_status"))
            salla_created_date = _inbox_salla_date(ib)
            payment_method = (canon.get("payment_method_native")
                              or canon.get("payment_method"))
            total_amount = canon.get("total_amount")
            currency = canon.get("currency") or "SAR"
            customer_name = (canon.get("customer") or {}).get("name")
            customer_phone = (canon.get("customer") or {}).get("phone")
            salla_order_id = str(ib.get("salla_order_id") or "") or None

        if u:
            # Fill any gaps from unified_orders.
            if salla_status_key is None:
                salla_status_key = _status_key_from_unified(u)
            if salla_status_raw is None:
                salla_status_raw = (u.get("order_status")
                                    or u.get("order_status_slug"))
            if salla_created_date is None:
                d = _parse_iso_date(u.get("order_date")) \
                    or _parse_iso_date(u.get("created_at"))
                salla_created_date = d
            if not payment_method:
                payment_method = u.get("payment_method")
            if total_amount is None:
                total_amount = u.get("total_amount")
            if currency in (None, "", "SAR"):
                currency = u.get("currency") or currency or "SAR"
            if not customer_name:
                customer_name = u.get("customer_name")
            if not customer_phone:
                customer_phone = u.get("customer_mobile")
            if not salla_order_id:
                salla_order_id = str(u.get("order_id") or "") or None

        # Marker check (from newest inbox row).
        marker_sent = False
        marker_id: Optional[str] = None
        marker_source = "none"
        if ib:
            marker_sent, marker_id, marker_source = _has_plan_b_marker(ib)

        # Cross-check قيود side directly (in case Mezan didn't get the
        # marker written but قيود does have an invoice).
        q_row = await _load_qoyod_invoice(
            db, user_id, on, salla_order_id)
        qoyod_side_hit, q_invoice_id = _q_invoice_hit(q_row)
        # If the inbox marker is missing but qoyod_invoices confirms a
        # real invoice, promote that to a marker for the classifier.
        if not marker_sent and qoyod_side_hit:
            # We DON'T claim `marker_source=plan_b` — this is a قيود-
            # side finding. The classifier already has a dedicated
            # branch for it.
            pass

        stage, reason = _classify(
            in_unified=u is not None,
            in_inbox=ib is not None,
            salla_status_key=salla_status_key,
            salla_status_raw=salla_status_raw,
            salla_created_date=salla_created_date,
            marker_source=marker_source,
            qoyod_side_hit=qoyod_side_hit,
        )

        # Optionally skip already-sent rows if the caller doesn't want
        # them cluttering the view.
        if not include_already_sent and stage.startswith("already_sent"):
            continue

        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1

        if len(orders_out) >= limit:
            continue

        orders_out.append({
            "order_number":       on,
            "salla_order_id":     salla_order_id,
            "salla_status":       salla_status_raw or "—",
            "salla_status_key":   salla_status_key,
            "salla_created_date": (salla_created_date.isoformat()
                                    if salla_created_date else None),
            "payment_method":     payment_method or "—",
            "total_amount":       total_amount,
            "currency":           currency or "SAR",
            "customer_name":      customer_name,
            "customer_phone":     customer_phone,
            # Presence bits
            "in_unified_orders":  u is not None,
            "in_integration_inbox": ib is not None,
            "visible_in_plan_b":  False,
            # قيود side + markers
            "has_qoyod_invoice":  bool(marker_sent or qoyod_side_hit),
            "qoyod_invoice_id":   marker_id or q_invoice_id,
            "marker_source":      marker_source,  # plan_b|legacy|none
            "qoyod_invoice_number":
                (q_row or {}).get("invoice_number") if q_row else None,
            # RCA
            "missing_stage":      stage,
            "reason":             reason,
            # Handy trace context
            "trace_id":           (ib or {}).get("trace_id"),
        })

    # Sort by salla_created_date desc, then order_number desc for
    # stable output. Rows with no date sink to the bottom.
    def _sort_key(r: dict) -> tuple[int, str, str]:
        d = r.get("salla_created_date") or ""
        return (0 if d else 1, d, r.get("order_number") or "")
    orders_out.sort(key=_sort_key, reverse=False)
    # Newest first (date desc). We reverse the primary group only.
    orders_out.sort(
        key=lambda r: (r.get("salla_created_date") or "", r.get(
            "order_number") or ""),
        reverse=True,
    )

    return {
        "ok":                True,
        "at":                now_utc.isoformat(),
        "floor_date":        floor_iso,
        "days_window":       days,
        "supported_statuses": list(_PLAN_B_STATUSES),
        "counts": {
            "universe_total":         universe_total,
            "visible_in_plan_b":      visible_in_plan_b_count,
            "returned":               len(orders_out),
            "scanned":                scanned,
        },
        "by_stage":          by_stage,
        "by_reason":         by_reason,
        "orders":            orders_out,
    }


def _re_escape(s: str) -> str:
    import re
    return re.escape(s)
