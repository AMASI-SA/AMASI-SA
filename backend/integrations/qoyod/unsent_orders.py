"""Iter-2026-02.rev36 — "طلبات لم تُرسل إلى قيود" — source of truth.

User directive (simplification): the daily-ops screen shows every
order with ONE of FOUR human statuses only:

    أُرسل   — real قيود invoice exists for the order
    لم يُرسل — eligible/held/pending/dry — with a clear Arabic reason
    فشل     — FAILED_* / DEAD_LETTER / totals blocked — with reason
    مكرر    — a real invoice already existed; the send was blocked

Internal pipeline stages stay developer-only. READ-ONLY module.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

SENT      = "أُرسل"
UNSENT    = "لم يُرسل"
FAILED    = "فشل"
DUPLICATE = "مكرر"


def _order_created_date(row: dict) -> date | None:
    """Salla order CREATION date for an inbox row — same priority as
    eligible_orders._extract_order_created_at (canonical order_date →
    raw_payload data.date.date → data.created_at)."""
    from integrations.qoyod.eligible_orders import _parse_iso_date
    canon = row.get("canonical_payload") or {}
    d = _parse_iso_date(canon.get("order_date")) \
        or _parse_iso_date(canon.get("created_at"))
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
    received = row.get("received_at")
    if hasattr(received, "date"):
        return received.date()
    return None


_FAIL_REASONS_AR = {
    "FAILED_VALIDATION":    "فشل التحقق من بيانات الطلب",
    "FAILED_NORMALIZATION": "فشل قراءة بيانات الطلب",
    "FAILED_ENRICHMENT":    "فشل جلب تفاصيل الطلب من سلة",
    "FAILED_CUSTOMER":      "فشل إنشاء العميل",
    "FAILED_PRODUCT":       "فشل إنشاء المنتج",
    "FAILED_INVOICE":       "فشل إنشاء الفاتورة",
    "FAILED_RECEIPT":       "فشل تسجيل السداد",
    "FAILED_INVOICE_PAYMENT": "فشل تسجيل السداد",
}


def _is_real(v) -> bool:
    s = str(v or "")
    return bool(s) and not s.upper().startswith(("DRY:", "PREVIEW:"))


def simplify_row(row: dict) -> dict:
    """Map one integration_inbox row to the 4-status contract."""
    stage = row.get("pipeline_stage") or ""
    qid   = row.get("qoyod_invoice_id")
    real  = _is_real(qid)
    err   = row.get("pipeline_error") or {}
    err_code = str(err.get("code") or "")
    fail_stage = str((row.get("dead_letter_evidence") or {})
                     .get("fail_stage") or "")

    # ── مكرر ─────────────────────────────────────────────────────────
    dup = row.get("duplicate_of_invoice")
    if dup:
        return {"status": DUPLICATE,
                "reason": ("فاتورة موجودة مسبقاً في قيود "
                           f"(#{dup.get('qoyod_invoice_number') or dup.get('qoyod_invoice_id')})")}

    # ── أُرسل ────────────────────────────────────────────────────────
    if real and stage in ("COMPLETED", "COMPLETED_WITH_ROUNDING_WARNING"):
        note = ("" if stage == "COMPLETED"
                else " (مع تنبيه فرق تقريب مقبول)")
        return {"status": SENT,
                "reason": f"فاتورة قيود #{qid} والسداد مسجّل{note}"}
    if real and stage in ("INVOICE_CREATED", "INVOICE_PAYMENT_CREATED",
                          "RECEIPT_CREATED", "LEGACY_RECEIPT_CREATED"):
        return {"status": SENT,
                "reason": f"الفاتورة #{qid} في قيود والسداد قيد الإكمال"}
    if real and stage == "INVOICE_CREATED_TOTAL_MISMATCH":
        return {"status": FAILED,
                "reason": ("فرق مبلغ أكبر من 0.01 ريال بعد الإنشاء — "
                           "موقوف بانتظار مراجعة المحاسب")}

    # ── فشل ──────────────────────────────────────────────────────────
    if err_code == "totals_precheck_mismatch":
        return {"status": FAILED,
                "reason": "فرق مبلغ أكبر من 0.01 ريال — أُوقف الإرسال"}
    if stage.startswith("FAILED_"):
        return {"status": FAILED,
                "reason": _FAIL_REASONS_AR.get(stage, "فشل — يحتاج مراجعة")}
    if stage == "DEAD_LETTER":
        base = _FAIL_REASONS_AR.get(fail_stage, "فشل نهائي — يحتاج مراجعة")
        return {"status": FAILED, "reason": base}
    if stage == "PARTIAL_FAILURE":
        return {"status": FAILED, "reason": "اكتمل جزئياً — يحتاج مراجعة"}

    # ── لم يُرسل ─────────────────────────────────────────────────────
    if stage in ("COMPLETED", "COMPLETED_WITH_ROUNDING_WARNING") and not real:
        return {"status": UNSENT,
                "reason": "أُكمل تجريبياً بدون إرسال (وضع Dry) — لم يصل قيود"}
    if row.get("canary_budget_hold"):
        return {"status": UNSENT,
                "reason": "موقوف مؤقتاً: نافذة الإرسال محدودة بطلب واحد"}
    if stage == "LOCKED_AWAITING_APPROVAL":
        return {"status": UNSENT,
                "reason": "بانتظار موافقة يدوية (قفل الإنتاج مفعّل)"}
    if stage == "SKIPPED":
        gate = row.get("selective_auto_send_gate") or {}
        note = ""
        for h in reversed(row.get("stage_history") or []):
            if isinstance(h, dict) and h.get("to_stage") == "SKIPPED":
                note = str(h.get("note") or "")
                break
        if "status" in str(gate.get("reason") or "") or \
                "not_billable" in note or "under_review" in note.lower():
            return {"status": UNSENT,
                    "reason": "حالة الطلب في سلة غير مؤهلة للفوترة بعد"}
        if "canary" in note.lower() or "scope" in note.lower():
            return {"status": UNSENT,
                    "reason": "خارج نطاق الإرسال الحالي (طريقة الدفع)"}
        if "backfill" in note.lower() or "pre-activation" in note.lower():
            return {"status": UNSENT,
                    "reason": "طلب سابق لتفعيل الربط — لا يُرسل تلقائياً"}
        return {"status": UNSENT,
                "reason": "استُثني بقاعدة عمل — لم يُرسل"}
    if stage in ("NEW", "RECEIVED", "VALIDATED"):
        return {"status": UNSENT, "reason": "قيد الاستقبال والمعالجة"}
    # NORMALIZED / RULES_APPLIED / CUSTOMER_RESOLVED / PRODUCT_RESOLVED
    # / RETRYING / NEEDS_ENRICHMENT / anything else pre-send.
    return {"status": UNSENT, "reason": "بانتظار الإرسال إلى قيود"}


async def list_unsent_orders(
    db, *, user_id: str, days: int = 30, limit: int = 500,
    status: str | None = None,
) -> dict:
    """All recent orders mapped to the 4-status contract + counts.

    INTEGRATION START DATE RULE (user decree): orders created BEFORE
    `qoyod_sync_start_date` (settings override, default 2026-07-01)
    are OUT OF SCOPE for the قيود integration entirely — they are
    ignored at the data source: no counts, no listing, no failed, no
    pending."""
    from integrations.qoyod.eligible_orders import (
        QOYOD_SYNC_START_DATE, _parse_iso_date,
    )
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"qoyod_sync_start_date": 1}) or {}
    sync_start = (_parse_iso_date(settings.get("qoyod_sync_start_date"))
                  or date.fromisoformat(QOYOD_SYNC_START_DATE))

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=max(1, min(days, 365)))
    counts = {SENT: 0, UNSENT: 0, FAILED: 0, DUPLICATE: 0}
    excluded_pre_sync = 0
    orders: list[dict] = []

    cursor = db.integration_inbox.find(
        {"user_id": user_id, "received_at": {"$gte": cutoff}},
        {"_id": 0, "id": 1, "trace_id": 1, "salla_order_number": 1,
         "received_at": 1, "pipeline_stage": 1, "pipeline_error": 1,
         "qoyod_invoice_id": 1, "duplicate_of_invoice": 1,
         "canary_budget_hold": 1, "dead_letter_evidence": 1,
         "selective_auto_send_gate": 1,
         "stage_history": {"$slice": -6},
         "raw_payload.data.date": 1,
         "raw_payload.data.created_at": 1,
         "canonical_payload.order_date": 1,
         "canonical_payload.created_at": 1,
         "canonical_payload.total_amount": 1,
         "canonical_payload.payment_method_native": 1,
         "canonical_payload.payment_method": 1,
         "canonical_payload.order_status_native": 1},
    ).sort("received_at", -1).limit(max(1, min(limit, 2000)))

    async for row in cursor:
        # ── Integration start date — hard scope boundary ─────────────
        order_date = _order_created_date(row)
        if order_date is not None and order_date < sync_start:
            excluded_pre_sync += 1
            continue
        s = simplify_row(row)
        counts[s["status"]] += 1
        if status and s["status"] != status:
            continue
        canon = row.get("canonical_payload") or {}
        received = row.get("received_at")
        orders.append({
            "order_number":   row.get("salla_order_number"),
            "received_at":    (received.isoformat()
                               if hasattr(received, "isoformat")
                               else received),
            "total_amount":   canon.get("total_amount"),
            "payment_method": (canon.get("payment_method_native")
                               or canon.get("payment_method")),
            "salla_status":   canon.get("order_status_native"),
            "status":         s["status"],
            "reason":         s["reason"],
            "qoyod_invoice_id": (row.get("qoyod_invoice_id")
                                 if _is_real(row.get("qoyod_invoice_id"))
                                 else None),
            "trace_id":       row.get("trace_id"),
        })

    return {"ok": True, "days": days, "counts": counts,
            "total": sum(counts.values()),
            "sync_start_date": sync_start.isoformat(),
            "excluded_pre_sync_start": excluded_pre_sync,
            "orders": orders}
