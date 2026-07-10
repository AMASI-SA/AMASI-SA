"""Plan-B — Read-only AUDIT of the sent-count discrepancy.

User directive (2026-07-09): the production diagnostic reports
`أُرسل عبر Plan B = 174` while the operator counts 202 real
`manual_qoyod_invoice_id` markers in Mezan. The user explicitly
asked to NOT change the current counter logic — just build a
report that lists the ~28 order_numbers the diagnostic is missing,
with a per-order reason.

Contract
────────
This module is 100% READ-ONLY. It performs NO writes, NO Qoyod
network calls, NO side effects. It mirrors the same two tenant
axes as `list_missing_from_plan_b`:

    • `orders_user_id`  — the JWT tenant (unified_orders)
    • `markers_user_id` — the webhook tenant (integration_inbox +
                          qoyod_invoices)

`Plan_B_Sent` (the reference set)
    = distinct salla_order_number in integration_inbox where
      `manual_qoyod_invoice_id` is a REAL (non-DRY) value.

`Diagnostic_Sent_Plan_B` (what the current diagnostic counts)
    = eligible unified_orders rows whose classifier assigned stage
      `already_sent_plan_b`.

The gap `Plan_B_Sent \\ Diagnostic_Sent_Plan_B` is exactly the set
the operator wants — with a specific exclusion reason per row.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from integrations.qoyod.unsent_orders import _is_real
from integrations.qoyod_manual.pending import _salla_order_created_date
from integrations.qoyod_manual.missing_diagnostics import (
    _FLOOR_DATE, _status_key_from_unified, _status_key_from_inbox,
    _unified_salla_date, _load_qoyod_invoice, _q_invoice_hit,
)


# Priority-ordered exclusion reasons. Kept SHORT so the UI can
# display them as chips.
_EXCLUSION_LABELS = {
    "not_in_unified_orders_for_tenant":  ("لم يُوجد في unified_orders "
                                          "تحت مستخدم /orders"),
    "unified_missing_order_date":        ("موجود في unified_orders لكن "
                                          "بدون order_date"),
    "unified_before_floor_date":         ("موجود في unified_orders لكن "
                                          "تاريخه قبل 2026-07-01"),
    "unified_status_not_in_plan_b_scope": ("حالة الطلب في unified_orders "
                                          "ليست ضمن (تم التنفيذ / "
                                          "جاري التوصيل / تم التوصيل)"),
    "inbox_marker_but_no_diagnostic_hit": ("علامة Plan-B موجودة في "
                                          "integration_inbox لكن التشخيص "
                                          "لم يُصنّفه — سبب غير محدد"),
}


async def _list_plan_b_marker_order_numbers(
    db, *, markers_user_id: str,
) -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
    """Return three views of the Plan-B marker universe:

      • `strict_by_number`  — order_number → newest inbox row.
        Passes the STRICT definition (user directive 2026-07-09):
          - manual_qoyod_invoice_id is real
          - Salla creation date (from inbox canonical) >= FLOOR
          - a matching `qoyod_invoices` entry with a real
            `qoyod_invoice_id` confirms the قيود side
      • `loose_by_number`  — same key, but only requires that a
        real `manual_qoyod_invoice_id` exists (previous logic).
      • `extras`  — list of {order_number, reason, ib_row}
        explaining WHY each loose entry failed the strict filter.
    """
    q = {
        "user_id": markers_user_id,
        "manual_qoyod_invoice_id": {"$nin": [None, ""]},
    }
    projection = {
        "_id": 0, "id": 1, "trace_id": 1,
        "salla_order_number": 1, "salla_order_id": 1,
        "received_at": 1,
        "manual_qoyod_invoice_id": 1, "qoyod_invoice_id": 1,
        "raw_payload.data.date": 1,
        "raw_payload.data.created_at": 1,
        "canonical_payload.order_date": 1,
        "canonical_payload.created_at": 1,
        "canonical_payload.order_status": 1,
        "canonical_payload.order_status_native": 1,
        "canonical_payload.total_amount": 1,
        "canonical_payload.currency": 1,
        "canonical_payload.payment_method_native": 1,
        "canonical_payload.payment_method": 1,
    }
    loose_by_number: dict[str, dict] = {}
    cursor = db.integration_inbox.find(q, projection) \
        .sort([("received_at", -1)])
    async for ib in cursor:
        mid = ib.get("manual_qoyod_invoice_id")
        if not (mid and _is_real(mid)):
            continue
        on = str(ib.get("salla_order_number") or "").strip()
        if not on or on in loose_by_number:
            continue
        loose_by_number[on] = ib

    # ── Strict filter ──────────────────────────────────────────────
    strict_by_number: dict[str, dict] = {}
    extras: list[dict] = []
    for on, ib in loose_by_number.items():
        # 1) Salla creation date must exist AND be >= FLOOR.
        sdate = _salla_order_created_date(ib)
        if sdate is None:
            extras.append({
                "order_number": on,
                "reason": "no_salla_order_date_in_inbox",
                "reason_label": ("لا يوجد تاريخ إنشاء لهذا الطلب في "
                                  "integration_inbox — لا يمكن تأكيد "
                                  "أنه ضمن نطاق Plan-B (>= 2026-07-01)"),
                "ib": ib,
                "sdate": None,
                "qoyod_confirmed": False,
            })
            continue
        if sdate < _FLOOR_DATE:
            extras.append({
                "order_number": on,
                "reason": "inbox_date_before_floor",
                "reason_label": (f"تاريخ سلة في inbox ({sdate.isoformat()}) "
                                  "قبل 2026-07-01 — خارج نطاق Plan-B"),
                "ib": ib,
                "sdate": sdate.isoformat(),
                "qoyod_confirmed": False,
            })
            continue
        # 2) Confirm the قيود side has a real invoice for this order.
        salla_order_id = str(ib.get("salla_order_id") or "") or None
        q_row = await _load_qoyod_invoice(
            db, markers_user_id, on, salla_order_id)
        qhit, _qid = _q_invoice_hit(q_row)
        if not qhit:
            extras.append({
                "order_number": on,
                "reason": "no_qoyod_invoice_confirmation",
                "reason_label": ("علامة manual_qoyod_invoice_id موجودة "
                                  "في inbox لكن لا توجد فاتورة مؤكَّدة "
                                  "في qoyod_invoices — قد تكون علامة "
                                  "قديمة/يتيمة لم تُوثَّق"),
                "ib": ib,
                "sdate": sdate.isoformat(),
                "qoyod_confirmed": False,
            })
            continue
        strict_by_number[on] = ib

    return strict_by_number, loose_by_number, extras


async def _list_diagnostic_plan_b_sent(
    db, *, orders_user_id: str, markers_user_id: str, days: int,
) -> tuple[set[str], dict]:
    """Return (set of order_numbers classified `already_sent_plan_b`,
    the diagnostic's counts dict). Uses the diagnostic's own logic
    verbatim so the two counts are directly comparable.
    """
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    res = await list_missing_from_plan_b(
        db, orders_user_id=orders_user_id,
        markers_user_id=markers_user_id,
        days=days, limit=5000,
        include_already_sent=True,
    )
    hits = {
        o["order_number"]
        for o in (res.get("orders") or [])
        if o.get("missing_stage") == "already_sent_plan_b"
    }
    return hits, (res.get("counts") or {})


async def _classify_missing_row(
    db, *, orders_user_id: str, order_number: str, ib: dict,
) -> tuple[str, dict]:
    """Explain WHY a marker-bearing order_number is not counted by
    the diagnostic. Returns (reason_code, detail_dict)."""
    u = await db.unified_orders.find_one(
        {"user_id": orders_user_id, "order_number": order_number},
        {"_id": 0, "order_number": 1, "order_id": 1,
         "order_status": 1, "order_status_slug": 1,
         "order_date": 1, "created_at": 1,
         "total_amount": 1, "currency": 1,
         "customer_name": 1, "customer_mobile": 1,
         "payment_method": 1},
    )
    if u is None:
        return "not_in_unified_orders_for_tenant", {
            "hint": ("الطلب مُرسل عبر Plan-B (توجد marker في inbox) "
                     "لكن لا يوجد صف مقابل في unified_orders تحت "
                     "user_id للـ /orders. غالباً لأن مزامنة سلة لم "
                     "تُدخل هذا الطلب بعد."),
        }
    odate = _unified_salla_date(u)
    if odate is None:
        return "unified_missing_order_date", {
            "unified_order_status": u.get("order_status"),
            "unified_order_status_slug": u.get("order_status_slug"),
            "raw_order_date": u.get("order_date"),
        }
    if odate < _FLOOR_DATE:
        return "unified_before_floor_date", {
            "unified_order_date": odate.isoformat(),
            "floor_date": _FLOOR_DATE.isoformat(),
        }
    if _status_key_from_unified(u) is None:
        return "unified_status_not_in_plan_b_scope", {
            "unified_order_status": u.get("order_status"),
            "unified_order_status_slug": u.get("order_status_slug"),
            "hint": ("الطلب أُرسل بنجاح، لكن الحالة الحالية في "
                     "unified_orders تغيّرت لحالة خارج نطاق Plan-B "
                     "(مثلاً: ملغي / مسترجع)."),
        }
    return "inbox_marker_but_no_diagnostic_hit", {
        "unified_order_status": u.get("order_status"),
        "unified_order_date": odate.isoformat(),
        "hint": ("الحقول تبدو صحيحة — قد يكون سبب فني نادر (مثل "
                 "de-dup على أحدث سطر من inbox بدون marker)."),
    }


async def audit_plan_b_vs_diagnostic(
    db, *,
    orders_user_id: str,
    markers_user_id: str,
    days: int = 365,
) -> dict:
    """Compute Plan-B-Sent (STRICT) vs diagnostic sent count, with
    per-order exclusion reasons AND a report of the entries that
    fell out of the strict filter (user directive 2026-07-09).
    """
    strict_by_number, loose_by_number, strict_extras = \
        await _list_plan_b_marker_order_numbers(
            db, markers_user_id=markers_user_id)
    plan_b_marker_rows = strict_by_number
    plan_b_sent_set: set[str] = set(plan_b_marker_rows)

    diagnostic_sent_plan_b, diag_counts = \
        await _list_diagnostic_plan_b_sent(
            db, orders_user_id=orders_user_id,
            markers_user_id=markers_user_id, days=days)

    missing_from_diag: list[str] = sorted(
        plan_b_sent_set - diagnostic_sent_plan_b)
    extra_in_diag: list[str] = sorted(
        diagnostic_sent_plan_b - plan_b_sent_set)

    # Per-order breakdown for missing (strict Plan-B ⊄ diagnostic)
    breakdown: list[dict] = []
    reason_hist: dict[str, int] = {}
    for on in missing_from_diag:
        ib = plan_b_marker_rows[on]
        canon = ib.get("canonical_payload") or {}
        reason, detail = await _classify_missing_row(
            db, orders_user_id=orders_user_id,
            order_number=on, ib=ib)
        reason_hist[reason] = reason_hist.get(reason, 0) + 1
        ib_date = _salla_order_created_date(ib)
        received = ib.get("received_at")
        breakdown.append({
            "order_number":        on,
            "salla_order_id":      str(ib.get("salla_order_id") or "") or None,
            "manual_qoyod_invoice_id": ib.get("manual_qoyod_invoice_id"),
            "trace_id":            ib.get("trace_id"),
            "received_at": (received.isoformat()
                            if hasattr(received, "isoformat")
                            else received),
            "salla_created_date_from_inbox":
                ib_date.isoformat() if ib_date else None,
            "salla_status_from_inbox": (canon.get("order_status_native")
                                        or canon.get("order_status")),
            "payment_method": (canon.get("payment_method_native")
                                or canon.get("payment_method")),
            "total_amount": canon.get("total_amount"),
            "currency": canon.get("currency") or "SAR",
            "exclusion_reason": reason,
            "exclusion_label": _EXCLUSION_LABELS.get(reason, reason),
            "detail": detail,
        })

    # Strict-filter extras report — orders that used to be in
    # loose_by_number but got dropped by the strict definition.
    strict_extras_out: list[dict] = []
    strict_reason_hist: dict[str, int] = {}
    for e in strict_extras:
        ib = e["ib"]
        canon = ib.get("canonical_payload") or {}
        strict_reason_hist[e["reason"]] = strict_reason_hist.get(
            e["reason"], 0) + 1
        strict_extras_out.append({
            "order_number":              e["order_number"],
            "manual_qoyod_invoice_id":   ib.get("manual_qoyod_invoice_id"),
            "salla_created_date_from_inbox": e.get("sdate"),
            "salla_status_from_inbox": (canon.get("order_status_native")
                                        or canon.get("order_status")),
            "total_amount":              canon.get("total_amount"),
            "currency":                  canon.get("currency") or "SAR",
            "qoyod_confirmed":           e.get("qoyod_confirmed", False),
            "exclusion_reason":          e["reason"],
            "exclusion_label":           e["reason_label"],
        })

    return {
        "ok":                True,
        "at":                datetime.now(timezone.utc).isoformat(),
        "plan_b_sent_count": len(plan_b_sent_set),                   # STRICT
        "plan_b_sent_count_loose": len(loose_by_number),             # for context
        "plan_b_sent_dropped_by_strict_filter": len(strict_extras),  # loose − strict
        "diagnostic_sent_plan_b_count": len(diagnostic_sent_plan_b),
        "diagnostic_sent_all_buckets_count":
            int(diag_counts.get("sent_to_qoyod", 0)),
        "missing_from_diagnostic_count": len(missing_from_diag),
        "extra_in_diagnostic_count":     len(extra_in_diag),
        "reason_histogram":  reason_hist,
        "strict_filter_reason_histogram": strict_reason_hist,
        "orders":            breakdown,
        "strict_filter_extras": strict_extras_out,
        "extra_in_diagnostic": extra_in_diag,
    }
