"""Plan-B Manual Send — Diagnostic: orders MISSING from Plan B pending.

Scope contract (user directive 2026-07-09):
    The MAIN universe = Salla-side eligible orders ONLY:
        • present in `unified_orders`
        • REAL Salla creation date parses AND is >= 2026-07-01
        • order_status maps to one of the 3 Plan-B statuses
          (completed / delivered / in_delivery)

    An order with NO parseable Salla creation date does NOT enter the
    main counter — the operator cannot make a decision without a date.

    `integration_inbox` is used STRICTLY as a diagnostic aid to enrich
    the display of an ELIGIBLE unified order. It NEVER expands the
    universe. Orphan inbox rows (webhooks with no unified match) are
    surfaced in a SEPARATE bucket for visibility, but they do NOT
    inflate the "eligible" counter.

Invariant:
    eligible_salla_orders  ==  sent_to_qoyod
                             + visible_in_plan_b
                             + hidden_with_reason

Read-only. NO writes. NO Qoyod network calls. NO send buttons.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date, _normalize_status,
)
from integrations.qoyod.unsent_orders import _is_real
from integrations.qoyod.candidate_orders import (
    build_candidate_audit,
    json_safe_audit,
)
from integrations.qoyod_manual.pending import (
    _matches_status, _salla_order_created_date, SUPPORTED_STATUSES,
)

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)

_PLAN_B_STATUSES: tuple[str, ...] = SUPPORTED_STATUSES


def _norm(s: Any) -> str:
    return _normalize_status(s)


def _status_key_from_unified(row: dict) -> Optional[str]:
    """Map a unified_orders row to a Plan-B status key or None if
    the status is outside the 3-status universe."""
    for f in (row.get("order_status_slug"), row.get("order_status")):
        s = _norm(f)
        if not s:
            continue
        if s in ("completed", "تم التنفيذ", "منتهي", "مكتمل"):
            return "completed"
        if s in ("delivered", "تم التوصيل"):
            return "delivered"
        if s in ("in_delivery", "in delivery", "shipping", "delivering",
                 "جاري التوصيل", "جارٍ التوصيل"):
            return "in_delivery"
    return None


def _status_key_from_inbox(row: dict) -> Optional[str]:
    for k in _PLAN_B_STATUSES:
        if _matches_status(row, k):
            return k
    return None


def _unified_salla_date(row: dict) -> Optional[date]:
    """Salla creation date from a `unified_orders` row. `order_date`
    is stored as ISO string 'YYYY-MM-DD'. Never falls back to
    `created_at` (that's the Mezan-side write timestamp)."""
    return _parse_iso_date(row.get("order_date"))


def _has_plan_b_marker(row: Optional[dict]) -> tuple[bool, Optional[str], str]:
    if not row:
        return False, None, "none"
    mid = row.get("manual_qoyod_invoice_id")
    if mid and _is_real(mid):
        return True, str(mid), "plan_b"
    lid = row.get("qoyod_invoice_id")
    if lid and _is_real(lid):
        return True, str(lid), "legacy"
    return False, None, "none"


def _q_invoice_hit(inv_row: Optional[dict]) -> tuple[bool, Optional[str]]:
    if not inv_row:
        return False, None
    qid = inv_row.get("qoyod_invoice_id")
    if qid and _is_real(qid):
        return True, str(qid)
    return False, None


async def _load_qoyod_invoice(db, user_id: str,
                              order_number: str,
                              order_id: Optional[str]) -> Optional[dict]:
    """Strict match: `qoyod_invoices.reference == order_number`.

    User directive 2026-07-09: `order_number` is the SOLE match-key
    between Salla, Mezan, and قيود. We query multiple synonym fields
    because different write paths (webhook sync vs Plan-B write-
    through) may fill different subsets:

        • reference          — Qoyod's canonical field
        • salla_order_number — alias populated by both write paths
        • external_reference / source_reference — raw Qoyod aliases

    We do NOT fall back to customer / amount / notes / description
    here — those are matching lies. If none of the reference fields
    equal `order_number` for a given قيود invoice, the invoice is
    treated as `orphan` (surfaced in reconciliation, NOT in the
    Plan-B "needs send" bucket).
    """
    on = str(order_number).strip()
    if not on:
        return None
    or_clauses: list[dict] = [
        {"reference":          on},
        {"salla_order_number": on},
        {"external_reference": on},
        {"source_reference":   on},
    ]
    if order_id:
        or_clauses.append({"salla_order_id": str(order_id)})
    return await db.qoyod_invoices.find_one(
        {"user_id": user_id, "$or": or_clauses},
        {"_id": 0, "qoyod_invoice_id": 1, "invoice_number": 1,
         "reference": 1, "salla_order_number": 1,
         "status": 1, "posting_mode": 1, "created_at": 1,
         "total": 1, "paid_amount": 1, "remaining": 1,
         "issue_date": 1},
        sort=[("created_at", -1)],
    )


def _classify_eligible(  # noqa: C901 — trivial branches
    *,
    in_inbox: bool,
    marker_source: str,
    qoyod_side_hit: bool,
    in_plan_b_visible: bool,
) -> tuple[str, str, str]:
    """Classify an ELIGIBLE Salla order (date + status already
    validated upstream). Returns (bucket, missing_stage, reason).

    Bucket ∈ {"sent", "visible", "hidden"}. The invariant
    `eligible = sent + visible + hidden` is preserved by this
    function returning exactly one bucket per call.
    """
    if marker_source == "plan_b":
        return "sent", "already_sent_plan_b", "already_sent"
    if marker_source == "legacy":
        return "sent", "already_sent_legacy", "already_sent"
    if qoyod_side_hit:
        return "sent", "already_in_qoyod", "duplicate_invoice_in_qoyod"
    if in_plan_b_visible:
        return "visible", "visible_in_plan_b", "visible"
    if not in_inbox:
        return ("hidden",
                "missing_from_integration_inbox",
                "missing_from_integration_inbox")
    return "hidden", "missing_from_plan_b_pending", "unknown_reason"


async def _list_missing_from_plan_b_legacy(
    db, *,
    orders_user_id: str,
    markers_user_id: Optional[str] = None,
    days: int = 90, limit: int = 1000,
    search: Optional[str] = None,
    include_already_sent: bool = True,
) -> dict:
    """Enumerate Salla-eligible orders NOT visible in Plan B pending.

    Two `user_id` axes — because the /orders page and the Plan-B
    pipeline can live in DIFFERENT tenant namespaces on production:

      • `orders_user_id`  — the caller's JWT `user["id"]`. Used
        for `unified_orders`. This MUST match the tenant that the
        /orders page uses, otherwise this diagnostic returns an
        empty universe (webhook data was captured under a global
        `_TENANT="main"` while merchant orders synced under the
        real user id).
      • `markers_user_id` — where Plan-B stores its markers
        (`integration_inbox`, `qoyod_invoices`, `list_pending_orders`).
        Defaults to `orders_user_id` when omitted.

    Scope: unified_orders ONLY. `integration_inbox` is a diagnostic
    aid. See module docstring for the invariant.
    """
    if markers_user_id is None:
        markers_user_id = orders_user_id
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 5000))
    now_utc = datetime.now(timezone.utc)
    since_dt = now_utc - timedelta(days=days)
    floor_iso = _FLOOR_DATE.isoformat()

    # ── Step 1: authoritative "visible in Plan B" set ────────────────
    from integrations.qoyod_manual.pending import list_pending_orders
    plan_b_visible: set[str] = set()
    for status_key in _PLAN_B_STATUSES:
        res = await list_pending_orders(
            db, user_id=markers_user_id, days=days, limit=1000,
            status=status_key)
        for row in (res.get("orders") or []):
            on = str(row.get("order_number") or "").strip()
            if on:
                plan_b_visible.add(on)

    # ── Step 2: MAIN universe — eligible unified_orders ONLY ─────────
    # A row is eligible iff:
    #   • order_number is present
    #   • Salla `order_date` parses AND >= 2026-07-01
    #   • order_status maps to one of the 3 Plan-B statuses
    # This is the SOLE source of "الطلبات المفحوصة". integration_inbox
    # never expands this set.
    uni_query: dict = {
        "user_id": orders_user_id,
        "order_date": {"$gte": floor_iso},  # ISO 'YYYY-MM-DD' string
    }
    if search and str(search).strip():
        uni_query["order_number"] = {
            "$regex": _re_escape(str(search).strip())}

    projection = {
        "_id": 0, "order_number": 1, "order_id": 1,
        "order_status": 1, "order_status_slug": 1,
        "order_date": 1, "created_at": 1,
        "payment_method": 1, "total_amount": 1, "currency": 1,
        "customer_name": 1, "customer_mobile": 1,
    }

    eligible_unified: dict[str, dict] = {}
    # Tracks unified rows that PASSED the date filter but FAILED the
    # status filter — informational only (not counted in the main
    # eligible bucket, but useful to explain the gap).
    status_out_of_scope_count = 0

    uni_cursor = db.unified_orders.find(uni_query, projection) \
        .sort("order_date", -1)
    async for u in uni_cursor:
        on = str(u.get("order_number") or "").strip()
        if not on:
            continue
        odate = _unified_salla_date(u)
        if odate is None or odate < _FLOOR_DATE:
            # No Salla date → out of scope entirely (per directive #2).
            continue
        status_key = _status_key_from_unified(u)
        if status_key is None:
            status_out_of_scope_count += 1
            continue
        # First one wins (uniqueness on order_number is a Mongo
        # invariant per orders_db.py, but be defensive).
        if on in eligible_unified:
            continue
        eligible_unified[on] = u
        if len(eligible_unified) >= 20000:
            break

    # ── Step 3: inbox — diagnostic aid ONLY ──────────────────────────
    # We fetch inbox rows in the recent window to (a) enrich display
    # for eligible orders, (b) surface orphan webhooks separately.
    inbox_by_number: dict[str, dict] = {}
    orphan_inbox_rows: list[dict] = []

    ib_query: dict = {"user_id": markers_user_id,
                       "received_at": {"$gte": since_dt}}
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
        if not on or on in inbox_by_number:
            continue
        inbox_by_number[on] = ib

    # Detect orphans AFTER the eligible set is fully built.
    for on, ib in inbox_by_number.items():
        if on not in eligible_unified:
            orphan_inbox_rows.append(ib)

    # ── Step 3½: cross-trace marker maps ─────────────────────────────
    # User directive 2026-07-09: a Plan-B send is authoritatively
    # proven by ANY trace of the order carrying a real
    # `manual_qoyod_invoice_id` — not only the newest one. Before this
    # fix, an order whose newest trace was a later status webhook
    # WITHOUT the marker was mis-classified as "not sent" even though
    # an older trace was clearly sent. This mirrors the same fix
    # applied to `list_pending_orders` in pending.py.
    plan_b_marker_by_number: dict[str, str] = {}
    legacy_marker_by_number: dict[str, str] = {}
    payment_marker_by_number: dict[str, str] = {}
    marker_cursor = db.integration_inbox.find(
        {
            "user_id": markers_user_id,
            "$or": [
                {"manual_qoyod_invoice_id": {"$nin": [None, ""]}},
                {"manual_qoyod_payment_id": {"$nin": [None, ""]}},
                {"qoyod_invoice_id":        {"$nin": [None, ""]}},
            ],
        },
        {"_id": 0, "salla_order_number": 1,
         "manual_qoyod_invoice_id": 1,
         "manual_qoyod_payment_id": 1,
         "qoyod_invoice_id": 1},
    ).sort([("received_at", -1)])
    async for mrow in marker_cursor:
        on = str(mrow.get("salla_order_number") or "").strip()
        if not on:
            continue
        mid = mrow.get("manual_qoyod_invoice_id")
        if (mid and _is_real(mid)
                and on not in plan_b_marker_by_number):
            plan_b_marker_by_number[on] = str(mid)
        pid = mrow.get("manual_qoyod_payment_id")
        if (pid and _is_real(pid)
                and on not in payment_marker_by_number):
            payment_marker_by_number[on] = str(pid)
        lid = mrow.get("qoyod_invoice_id")
        if (lid and _is_real(lid)
                and on not in legacy_marker_by_number):
            legacy_marker_by_number[on] = str(lid)

    # ── Step 4: classify each eligible order ─────────────────────────
    sent_count = 0
    visible_count = 0
    hidden_count = 0
    orders_out: list[dict] = []
    by_stage: dict[str, int] = {}
    by_reason: dict[str, int] = {}

    for on, u in eligible_unified.items():
        ib = inbox_by_number.get(on)
        salla_order_id = str(u.get("order_id") or "") or None

        # Cross-trace marker check: does ANY trace of this order
        # (not just the newest) carry a real Plan-B / legacy marker?
        cross_plan_b_id = plan_b_marker_by_number.get(on)
        cross_legacy_id = legacy_marker_by_number.get(on)
        if cross_plan_b_id:
            marker_sent, marker_id, marker_source = (
                True, cross_plan_b_id, "plan_b")
        elif cross_legacy_id:
            marker_sent, marker_id, marker_source = (
                True, cross_legacy_id, "legacy")
        else:
            marker_sent, marker_id, marker_source = _has_plan_b_marker(ib)

        q_row = await _load_qoyod_invoice(db, markers_user_id, on, salla_order_id)
        qoyod_side_hit, q_invoice_id = _q_invoice_hit(q_row)

        bucket, stage, reason = _classify_eligible(
            in_inbox=ib is not None,
            marker_source=marker_source,
            qoyod_side_hit=qoyod_side_hit,
            in_plan_b_visible=(on in plan_b_visible),
        )

        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1

        if bucket == "sent":
            sent_count += 1
        elif bucket == "visible":
            visible_count += 1
        else:
            hidden_count += 1

        # Rows returned in `orders`:
        #   • Always the hidden bucket (the primary purpose of the page)
        #   • Optionally the sent bucket when include_already_sent=True
        #     (so the operator can VERIFY sent status per order)
        #   • Never the visible bucket (that's Plan B's own page)
        if bucket == "visible":
            continue
        if bucket == "sent" and not include_already_sent:
            continue

        if len(orders_out) >= limit:
            continue

        # Display fields — prefer inbox (raw webhook) over unified.
        canon = (ib or {}).get("canonical_payload") or {}
        payment_method = (canon.get("payment_method_native")
                          or canon.get("payment_method")
                          or u.get("payment_method"))
        total_amount = (canon.get("total_amount")
                        if canon.get("total_amount") is not None
                        else u.get("total_amount"))
        currency = (canon.get("currency") or u.get("currency") or "SAR")
        customer_name = ((canon.get("customer") or {}).get("name")
                         or u.get("customer_name"))
        customer_phone = ((canon.get("customer") or {}).get("phone")
                          or u.get("customer_mobile"))
        salla_status_raw = (canon.get("order_status_native")
                            or canon.get("order_status")
                            or u.get("order_status")
                            or u.get("order_status_slug"))
        salla_status_key = (_status_key_from_inbox(ib) if ib else None) \
            or _status_key_from_unified(u)
        odate = _unified_salla_date(u)

        orders_out.append({
            "order_number":       on,
            "salla_order_id":     salla_order_id,
            "salla_status":       salla_status_raw or "—",
            "salla_status_key":   salla_status_key,
            "salla_created_date": odate.isoformat() if odate else None,
            "payment_method":     payment_method or "—",
            "total_amount":       total_amount,
            "currency":           currency,
            "customer_name":      customer_name,
            "customer_phone":     customer_phone,
            "in_unified_orders":  True,
            "in_integration_inbox": ib is not None,
            "visible_in_plan_b":  False,
            "has_qoyod_invoice":  bool(marker_sent or qoyod_side_hit),
            "qoyod_invoice_id":   marker_id or q_invoice_id,
            "marker_source":      marker_source,
            "qoyod_invoice_number":
                (q_row or {}).get("invoice_number") if q_row else None,
            "missing_stage":      stage,
            "reason":             reason,
            "trace_id":           (ib or {}).get("trace_id"),
            # ── Debug bag (user directive 2026-07-09) ──
            # Exposes the EXACT fields the operator needs to verify
            # why an order was classified the way it was. `order_number`
            # is the sole match-key; everything else is here to prove
            # it either matched or diverged in قيود.
            "debug": {
                "order_number":     on,
                "qoyod_reference":  (q_row or {}).get("reference"),
                "invoice_id":       marker_id or q_invoice_id,
                "payment_id":       payment_marker_by_number.get(on),
                "remaining":        (q_row or {}).get("remaining"),
                "qoyod_total":      (q_row or {}).get("total"),
                "qoyod_paid":       (q_row or {}).get("paid_amount"),
                "qoyod_status":     (q_row or {}).get("status"),
                "match_source":     ("qoyod_invoices.reference"
                                     if qoyod_side_hit else marker_source),
            },
        })

    # Newest first by Salla creation date.
    orders_out.sort(
        key=lambda r: (r.get("salla_created_date") or "",
                       r.get("order_number") or ""),
        reverse=True,
    )

    # ── Step 5: shape orphan inbox rows for display ──────────────────
    orphans_out: list[dict] = []
    for ib in orphan_inbox_rows[:limit]:
        canon = ib.get("canonical_payload") or {}
        received = ib.get("received_at")
        # Best-effort Salla date from the inbox row (may be None).
        ibdate = _salla_order_created_date(ib)
        orphans_out.append({
            "order_number":     str(ib.get("salla_order_number") or ""),
            "salla_order_id":   str(ib.get("salla_order_id") or "") or None,
            "salla_status":     (canon.get("order_status_native")
                                 or canon.get("order_status")
                                 or "—"),
            "salla_created_date": ibdate.isoformat() if ibdate else None,
            "received_at":      (received.isoformat()
                                 if hasattr(received, "isoformat")
                                 else received),
            "trace_id":         ib.get("trace_id"),
            "note": ("موجود في integration_inbox لكن غير موجود في "
                     "unified_orders — يحتاج salla sync لإضافته إلى "
                     "قاعدة سلة المحلية"),
        })

    universe_total = len(eligible_unified)
    # Invariant sanity — never crash on it, just report.
    invariant_holds = (universe_total ==
                       sent_count + visible_count + hidden_count)

    return {
        "ok":                True,
        "at":                now_utc.isoformat(),
        "floor_date":        floor_iso,
        "days_window":       days,
        "supported_statuses": list(_PLAN_B_STATUSES),
        "counts": {
            # ── MAIN counters (contract) ──
            "eligible_salla_orders": universe_total,
            "sent_to_qoyod":         sent_count,
            "visible_in_plan_b":     visible_count,
            "hidden_with_reason":    hidden_count,
            # ── informational ──
            "webhooks_without_unified":     len(orphan_inbox_rows),
            "status_out_of_scope_unified":  status_out_of_scope_count,
            "returned":              len(orders_out),
        },
        "invariant_holds":   invariant_holds,
        "by_stage":          by_stage,
        "by_reason":         by_reason,
        "orders":            orders_out,
        "webhooks_without_unified": orphans_out,
    }


def _re_escape(s: str) -> str:
    import re
    return re.escape(s)


async def list_missing_from_plan_b(
    db, *,
    orders_user_id: str,
    markers_user_id: Optional[str] = None,
    days: int = 90,
    limit: int = 1000,
    search: Optional[str] = None,
    include_already_sent: bool = True,
    from_date: Any = None,
    to_date: Any = None,
    now: Optional[datetime] = None,
) -> dict:
    """Read-only diagnostic over the canonical exact-reference audit."""
    markers_user_id = str(markers_user_id or orders_user_id)
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 5000))
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    audit = await build_candidate_audit(
        db,
        orders_user_id=str(orders_user_id),
        markers_user_id=markers_user_id,
        marker_user_ids=(markers_user_id, str(orders_user_id)),
        from_date=from_date,
        to_date=to_date,
        days=days,
        now=now_utc,
        search=search,
    )
    safe = json_safe_audit(audit)
    sent_refs = audit["sent_references"]
    candidate_refs = audit["unsent_references"]
    legacy_visible_refs = {
        row["order_number"]
        for row in audit["orders"]
        if row.get("worker_candidate")
        and row.get("legacy_worker_visibility_reason")
        == "eligible_in_legacy_inbox_queue"
    }
    legacy_hidden_refs = candidate_refs - legacy_visible_refs
    legacy_by_stage: dict[str, int] = {}
    for row in audit["orders"]:
        if row["order_number"] not in legacy_hidden_refs:
            continue
        reason = str(row.get("legacy_worker_visibility_reason") or "unknown")
        legacy_by_stage[reason] = legacy_by_stage.get(reason, 0) + 1

    displayed: list[dict[str, Any]] = []
    for row in safe["orders"]:
        reference = row["order_number"]
        is_sent = reference in sent_refs
        if is_sent and not include_already_sent:
            continue
        displayed.append({
            **row,
            "salla_created_date": row.get("order_date"),
            "salla_status": row.get("current_status"),
            "salla_status_key": row.get("current_status_key"),
            "visible_in_plan_b": reference in candidate_refs,
            "has_qoyod_invoice": is_sent,
            "missing_stage": (
                "already_in_qoyod_by_exact_reference"
                if is_sent else "visible_in_plan_b"
            ),
            "reason": (
                "already_in_qoyod_by_exact_reference"
                if is_sent else row.get("candidate_reason")
            ),
        })
        if len(displayed) >= limit:
            break

    eligible_count = len(audit["eligible_references"])
    sent_count = len(sent_refs)
    visible_count = len(candidate_refs)
    return {
        "ok": True,
        "read_only": True,
        "at": now_utc.isoformat(),
        "floor_date": _FLOOR_DATE.isoformat(),
        "from_date": audit["from_date"],
        "to_date": audit["to_date"],
        "days_window": days,
        "supported_statuses": list(_PLAN_B_STATUSES),
        "source_authority": "unified_orders",
        "match_contract": audit["match_contract"],
        "captured_at": audit["captured_at"],
        "snapshot_fingerprint": audit["snapshot_fingerprint"],
        "status_counts": audit["status_counts"],
        "status_display_counts": audit["status_display_counts"],
        "worker_candidate_status_counts": audit[
            "worker_candidate_status_counts"
        ],
        "worker_candidate_status_display_counts": audit[
            "worker_candidate_status_display_counts"
        ],
        "counts": {
            "eligible_salla_orders": eligible_count,
            "sent_to_qoyod": sent_count,
            "visible_in_plan_b": visible_count,
            "hidden_with_reason": 0,
            "legacy_visible_in_plan_b": len(legacy_visible_refs),
            "legacy_hidden_before_fix": len(legacy_hidden_refs),
            "webhooks_without_unified": 0,
            "status_out_of_scope_unified": audit[
                "unified_exclusions"
            ]["status_not_eligible"],
            "returned": len(displayed),
        },
        "invariant_holds": eligible_count == sent_count + visible_count,
        "by_stage": {
            "already_in_qoyod_by_exact_reference": sent_count,
            "visible_in_plan_b": visible_count,
        },
        "by_reason": {
            "already_in_qoyod_by_exact_reference": sent_count,
            "eligible_unified_missing_exact_qoyod_reference": visible_count,
        },
        "legacy_hidden_by_reason": legacy_by_stage,
        "orders": displayed,
        "audit_orders": safe["orders"][:limit],
        "reference_sets": safe["reference_sets"],
        "duplicate_qoyod_references": safe["duplicate_qoyod_references"],
        "webhooks_without_unified": [],
    }
