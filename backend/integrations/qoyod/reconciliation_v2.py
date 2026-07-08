"""Reconciliation v2 — Salla orders (unified_orders) ↔ local
`qoyod_invoices`.

User directive (2026-07-09): the reconciliation page is the SINGLE
source of truth for Mezan/قيود parity. The comparison is between:

    (A) Salla-side orders from `unified_orders` under the JWT tenant
        — same source as /orders — filtered by:
          • order_date >= 2026-07-01
          • order_status ∈ {completed / in_delivery / delivered}
    (B) Local `qoyod_invoices` — kept fresh by `qoyod_invoices_sync`
        and by the Plan-B write-through hook.

`integration_inbox`, `manual_qoyod_invoice_id`, `qoyod_invoice_id`
are used ONLY as helper signals for the "Repair Marker" hint —
NEVER as the authoritative marker.

Five reconciliation outcomes (per user directive):
    • matched              — مطابق
    • needs_plan_b_send    — يحتاج إرسال Plan B
    • qoyod_only           — موجود في قيود فقط
    • needs_repair_marker  — يحتاج Repair Marker
    • amount_mismatch      — فرق مبلغ

READ-ONLY. No writes to قيود. No writes to `qoyod_invoices` here
(the sync module owns that). No writes to unified_orders.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date,
)
from integrations.qoyod.unsent_orders import _is_real
from integrations.qoyod_manual.missing_diagnostics import (
    _status_key_from_unified, _unified_salla_date,
)

MATCHED             = "مطابق"
NEEDS_PLAN_B_SEND   = "يحتاج إرسال Plan B"
QOYOD_ONLY          = "موجود في قيود فقط"
NEEDS_REPAIR_MARKER = "يحتاج Repair Marker"
AMOUNT_MISMATCH     = "فرق مبلغ"

_ALL_STATUSES = (MATCHED, NEEDS_PLAN_B_SEND, QOYOD_ONLY,
                 NEEDS_REPAIR_MARKER, AMOUNT_MISMATCH)

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)
_TOLERANCE = 0.01


async def _load_eligible_unified(db, *, user_id: str) -> dict[str, dict]:
    """Return `order_number → row` for every Salla order eligible
    for Plan-B (same rules as /orders + Plan-B pending)."""
    q = {
        "user_id": user_id,
        "order_date": {"$gte": _FLOOR_DATE.isoformat()},
    }
    projection = {
        "_id": 0, "order_number": 1, "order_id": 1,
        "order_status": 1, "order_status_slug": 1,
        "order_date": 1, "created_at": 1,
        "payment_method": 1, "total_amount": 1, "currency": 1,
        "customer_name": 1, "customer_mobile": 1,
    }
    out: dict[str, dict] = {}
    cursor = db.unified_orders.find(q, projection).sort("order_date", -1)
    async for u in cursor:
        on = str(u.get("order_number") or "").strip()
        if not on:
            continue
        d = _unified_salla_date(u)
        if d is None or d < _FLOOR_DATE:
            continue
        if _status_key_from_unified(u) is None:
            continue
        out.setdefault(on, u)
    return out


async def _load_local_qoyod_invoices(
    db, *, markers_user_id: str,
) -> dict[str, dict]:
    """Return `reference → newest invoice row`. `reference` is the
    Salla order_number that Plan-B / legacy pipelines write in the
    Qoyod invoice's `reference` field. If a row has no reference,
    it's still returned under a synthetic `__ORPHAN__:{qid}` key so
    it can surface as `qoyod_only` with an obvious indicator.
    """
    out: dict[str, dict] = {}
    cursor = db.qoyod_invoices.find(
        {"user_id": markers_user_id},
        {"_id": 0, "qoyod_invoice_id": 1, "invoice_number": 1,
         "reference": 1, "salla_order_number": 1,
         "customer_name": 1, "issue_date": 1,
         "total": 1, "paid_amount": 1, "remaining": 1,
         "status": 1, "source": 1, "last_sync_at": 1},
    ).sort([("issue_date", -1), ("qoyod_invoice_id", -1)])
    async for inv in cursor:
        ref = str(inv.get("reference")
                  or inv.get("salla_order_number") or "").strip()
        if not ref:
            key = f"__ORPHAN__:{inv.get('qoyod_invoice_id')}"
            out[key] = inv
            continue
        if ref in out:
            continue  # keep newest by issue_date sort
        out[ref] = inv
    return out


async def _has_marker_in_inbox(
    db, *, markers_user_id: str, order_number: str,
) -> tuple[bool, Optional[str]]:
    """Helper signal ONLY. Is there ANY inbox trace for this order
    that carries a real Plan-B / legacy marker?"""
    cursor = db.integration_inbox.find(
        {
            "user_id": markers_user_id,
            "salla_order_number": order_number,
            "$or": [
                {"manual_qoyod_invoice_id": {"$nin": [None, ""]}},
                {"qoyod_invoice_id":        {"$nin": [None, ""]}},
            ],
        },
        {"_id": 0, "manual_qoyod_invoice_id": 1, "qoyod_invoice_id": 1},
    )
    async for r in cursor:
        mid = r.get("manual_qoyod_invoice_id")
        if mid and _is_real(mid):
            return True, str(mid)
        lid = r.get("qoyod_invoice_id")
        if lid and _is_real(lid):
            return True, str(lid)
    return False, None


def _fmt(v):
    return None if v is None else round(float(v), 2)


async def run_reconciliation_v2(
    db, *,
    orders_user_id: str,
    markers_user_id: Optional[str] = None,
) -> dict:
    """The reconciliation report itself. Read-only."""
    if markers_user_id is None:
        markers_user_id = orders_user_id

    unified = await _load_eligible_unified(db, user_id=orders_user_id)
    local_inv = await _load_local_qoyod_invoices(
        db, markers_user_id=markers_user_id)

    counts: dict[str, int] = {k: 0 for k in _ALL_STATUSES}
    rows: list[dict] = []
    claimed_refs: set[str] = set()

    for on, u in unified.items():
        inv = local_inv.get(on)
        salla_total = _fmt(u.get("total_amount"))
        salla_date = u.get("order_date")
        customer = u.get("customer_name")
        salla_status = u.get("order_status") or u.get("order_status_slug")

        base = {
            "order_number":     on,
            "salla_date":       salla_date,
            "salla_status":     salla_status,
            "customer_name":    customer,
            "salla_total":      salla_total,
            "qoyod_invoice_id": None,
            "invoice_number":   None,
            "qoyod_date":       None,
            "qoyod_total":      None,
            "paid_amount":      None,
            "remaining":        None,
            "qoyod_status":     None,
            "match":            None,
            "note":             None,
        }

        if inv is None:
            counts[NEEDS_PLAN_B_SEND] += 1
            rows.append({**base,
                         "match": NEEDS_PLAN_B_SEND,
                         "note": ("طلب موجود في سلة (ضمن النطاق) "
                                  "لكن لا يوجد فاتورة مقابلة في "
                                  "قيود — يحتاج إرسال يدوي عبر Plan B")})
            continue

        claimed_refs.add(on)
        qoyod_total = _fmt(inv.get("total"))
        qoyod_paid = _fmt(inv.get("paid_amount"))
        qoyod_remaining = _fmt(inv.get("remaining"))
        qoyod_status = inv.get("status")
        qoyod_date = inv.get("issue_date")
        qoyod_invoice_id = inv.get("qoyod_invoice_id")
        invoice_number = inv.get("invoice_number") or qoyod_invoice_id

        # Marker check — helper signal only. If invoice exists in
        # قيود but NO marker in inbox → Mezan needs a Repair Marker.
        has_marker, _ = await _has_marker_in_inbox(
            db, markers_user_id=markers_user_id, order_number=on)

        base.update({
            "qoyod_invoice_id": qoyod_invoice_id,
            "invoice_number":   invoice_number,
            "qoyod_date":       qoyod_date,
            "qoyod_total":      qoyod_total,
            "paid_amount":      qoyod_paid,
            "remaining":        qoyod_remaining,
            "qoyod_status":     qoyod_status,
        })

        if not has_marker:
            counts[NEEDS_REPAIR_MARKER] += 1
            rows.append({**base,
                         "match": NEEDS_REPAIR_MARKER,
                         "note": ("فاتورة موجودة في قيود لكن لا يوجد "
                                  "marker في ميزان — شغّل "
                                  "repair-recon-markers")})
            continue

        diff = None
        if salla_total is not None and qoyod_total is not None:
            diff = round(salla_total - qoyod_total, 2)

        if diff is None or abs(diff) <= _TOLERANCE:
            counts[MATCHED] += 1
            note = "مطابق تماماً" if diff is not None else \
                "مطابق (المبلغ غير محدد)"
            rows.append({**base, "match": MATCHED, "note": note,
                         "difference": diff})
        else:
            counts[AMOUNT_MISMATCH] += 1
            rows.append({
                **base,
                "match": AMOUNT_MISMATCH,
                "difference": diff,
                "note": (f"فرق {diff:+.2f} ريال بين سلة وقيود — "
                         "يحتاج مراجعة"),
            })

    # قيود invoices that had NO matching Salla order eligible.
    for ref, inv in local_inv.items():
        if ref.startswith("__ORPHAN__:") or ref not in claimed_refs:
            if not ref.startswith("__ORPHAN__:") and ref in claimed_refs:
                continue
            counts[QOYOD_ONLY] += 1
            rows.append({
                "order_number":     (None if ref.startswith("__ORPHAN__:")
                                     else ref),
                "salla_date":       None,
                "salla_status":     None,
                "customer_name":    inv.get("customer_name"),
                "salla_total":      None,
                "qoyod_invoice_id": inv.get("qoyod_invoice_id"),
                "invoice_number":   inv.get("invoice_number"),
                "qoyod_date":       inv.get("issue_date"),
                "qoyod_total":      _fmt(inv.get("total")),
                "paid_amount":      _fmt(inv.get("paid_amount")),
                "remaining":        _fmt(inv.get("remaining")),
                "qoyod_status":     inv.get("status"),
                "match":            QOYOD_ONLY,
                "note": (
                    "فاتورة موجودة في قيود بلا مرجع سلة (reference فارغ)"
                    if ref.startswith("__ORPHAN__:") else
                    ("فاتورة قيود موجودة لكن لا يوجد طلب مقابل في سلة "
                     "ضمن النطاق — قد يكون طلب خارج النطاق أو تم "
                     "إنشاؤه من مسار خارجي")
                ),
            })

    all_matched = all(counts[k] == 0 for k in _ALL_STATUSES
                       if k != MATCHED)

    return {
        "ok":                    True,
        "run_at":                datetime.now(timezone.utc).isoformat(),
        "sync_start_date":       _FLOOR_DATE.isoformat(),
        "counts":                counts,
        "salla_orders_total":    len(unified),
        "qoyod_invoices_total":  len(local_inv),
        "all_matched":           all_matched,
        "rows":                  rows,
        "outcome_labels":        list(_ALL_STATUSES),
    }
