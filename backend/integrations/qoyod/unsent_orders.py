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


def _include_in_daily_report(entry: dict) -> bool:
    """Return whether an entry is an actionable daily Qoyod result."""
    if entry.get("status") != UNSENT:
        return True
    current = entry.get("salla_status_slug") or entry.get("salla_status")
    # Preserve unknown legacy rows for investigation; only exclude a row
    # when Salla supplied a definite non-eligible current status.
    if not current:
        return True
    from integrations.qoyod.eligible_orders import _is_eligible_status
    return _is_eligible_status(current)


def simplify_row(row: dict, *,
                 in_qoyod_by_reference: bool = False) -> dict:
    """Map one integration_inbox row to the 4-status contract.

    `in_qoyod_by_reference` (user directive 2026-07-09): True when
    the caller has pre-loaded `qoyod_invoices` under this tenant and
    confirmed that `qoyod_invoices.reference == salla_order_number`
    for this row. When True, the row is forced into the SENT bucket
    regardless of the local pipeline_stage / marker state — this is
    the STRICT match rule: any order that already has an invoice in
    قيود with matching reference is "sent", full stop.
    """
    stage = row.get("pipeline_stage") or ""
    qid   = row.get("qoyod_invoice_id")
    real  = _is_real(qid)
    # Plan-B marker equivalent — a real `manual_qoyod_invoice_id`
    # is as authoritative as the unified `qoyod_invoice_id`.
    mid   = row.get("manual_qoyod_invoice_id")
    real_manual = _is_real(mid)
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

    # ── أُرسل (STRICT rule: reference match in قيود trumps everything) ─
    if in_qoyod_by_reference:
        inv_id_shown = qid if real else (mid if real_manual else None)
        note = f"فاتورة قيود #{inv_id_shown}" if inv_id_shown \
            else "فاتورة موجودة في قيود بنفس رقم الطلب"
        return {"status": SENT,
                "reason": f"{note} — مطابق برقم الطلب"}
    # ── أُرسل (Plan-B marker present) ────────────────────────────────
    if real_manual:
        return {"status": SENT,
                "reason": f"فاتورة قيود #{mid} (Plan B) — مرسلة يدوياً"}

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
    salla_status: str | None = None,
    search: str | None = None,
) -> dict:
    """All recent orders mapped to the 4-status contract + counts.

    INTEGRATION START DATE — FOUNDATIONAL PROJECT CONSTANT (user
    decree 2026-02): orders created BEFORE 2026-07-01 are OUT OF
    SCOPE for the قيود integration entirely — ignored at the data
    source: no counts, no listing, no failed, no pending. This is
    NOT a setting: it is fixed in code and NO settings value can
    change it."""
    from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE
    sync_start = date.fromisoformat(QOYOD_SYNC_START_DATE)

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=max(1, min(days, 365)))
    excluded_pre_sync = 0

    # ── Preload قيود reference set (user directive 2026-07-09) ──
    # STRICT rule: any inbox row whose salla_order_number matches a
    # `reference` in `qoyod_invoices` under this tenant is SENT, no
    # matter what the local pipeline_stage says. We build this map
    # ONCE per request (bulk find_one → set) so `simplify_row` can
    # short-circuit without any extra DB lookups per row.
    ref_set: set[str] = set()
    ref_cursor = db.qoyod_invoices.find(
        {"user_id": user_id},
        {"_id": 0, "reference": 1, "salla_order_number": 1,
         "external_reference": 1, "source_reference": 1},
    )
    async for _inv in ref_cursor:
        for _f in ("reference", "salla_order_number",
                    "external_reference", "source_reference"):
            v = str(_inv.get(_f) or "").strip()
            if v:
                ref_set.add(v)

    # rev37.1 — ONE entry per SALLA ORDER, not per inbox row. The
    # inbox intentionally stores a row per status transition
    # (idempotency key includes status_slug), so a single order can
    # have 2+ rows. Group by salla_order_number; representative
    # status priority: أُرسل > مكرر > فشل > لم يُرسل. Ties keep the
    # most recent row (cursor is sorted received_at desc).
    _PRIORITY = {SENT: 3, DUPLICATE: 2, FAILED: 1, UNSENT: 0}
    grouped: dict[str, dict] = {}
    order_keys: list[str] = []

    query: dict = {"user_id": user_id, "received_at": {"$gte": cutoff}}
    if search and str(search).strip():
        import re as _re
        query["salla_order_number"] = {
            "$regex": _re.escape(str(search).strip())}

    cursor = db.integration_inbox.find(
        query,
        {"_id": 0, "id": 1, "trace_id": 1, "salla_order_number": 1,
         "received_at": 1, "pipeline_stage": 1, "pipeline_error": 1,
         "qoyod_invoice_id": 1, "duplicate_of_invoice": 1,
         "manual_qoyod_invoice_id": 1,
         "manual_qoyod_payment_id": 1,
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
         "canonical_payload.order_status": 1,
         "canonical_payload.order_status_native": 1},
    ).sort("received_at", -1).limit(max(1, min(limit, 2000)))

    async for row in cursor:
        # ── Integration start date — hard scope boundary ─────────────
        order_date = _order_created_date(row)
        if order_date is not None and order_date < sync_start:
            excluded_pre_sync += 1
            continue
        on = str(row.get("salla_order_number") or "").strip()
        in_qoyod = bool(on) and (on in ref_set)
        s = simplify_row(row, in_qoyod_by_reference=in_qoyod)
        canon = row.get("canonical_payload") or {}
        received = row.get("received_at")
        # Debug bag (user directive 2026-07-09): every row surfaces
        # the exact fields needed to prove the classification.
        _mid = row.get("manual_qoyod_invoice_id")
        _pid = row.get("manual_qoyod_payment_id")
        _qid = row.get("qoyod_invoice_id")
        entry = {
            "order_number":   row.get("salla_order_number"),
            "received_at":    (received.isoformat()
                               if hasattr(received, "isoformat")
                               else received),
            "total_amount":   canon.get("total_amount"),
            "payment_method": (canon.get("payment_method_native")
                               or canon.get("payment_method")),
            "salla_status":   canon.get("order_status_native"),
            "salla_status_slug": canon.get("order_status"),
            "status":         s["status"],
            "reason":         s["reason"],
            "qoyod_invoice_id": (row.get("qoyod_invoice_id")
                                 if _is_real(row.get("qoyod_invoice_id"))
                                 else None),
            "trace_id":       row.get("trace_id"),
            "events_count":   1,
            "debug": {
                "order_number":    on or None,
                "qoyod_reference": on if in_qoyod else None,
                "invoice_id":      (str(_qid) if _is_real(_qid) else
                                     (str(_mid) if _is_real(_mid) else None)),
                "payment_id":      (str(_pid) if _is_real(_pid) else None),
                "remaining":       None,   # unsent view has no ledger
                "match_source":    ("qoyod_invoices.reference"
                                     if in_qoyod
                                     else ("manual_qoyod_invoice_id"
                                            if _is_real(_mid)
                                            else ("qoyod_invoice_id"
                                                   if _is_real(_qid)
                                                   else "none"))),
            },
        }
        key = str(row.get("salla_order_number") or "") \
            or f"__row__{row.get('id')}"
        prev = grouped.get(key)
        if prev is None:
            grouped[key] = entry
            order_keys.append(key)
            continue
        prev["events_count"] += 1
        if _PRIORITY[entry["status"]] > _PRIORITY[prev["status"]]:
            entry["events_count"] = prev["events_count"]
            grouped[key] = entry
        else:
            # Keep prev (more recent or higher priority) — but fill
            # gaps from older rows (e.g. status-update row has no
            # canonical totals yet the created row does).
            for f in ("total_amount", "payment_method", "salla_status",
                      "salla_status_slug"):
                if prev.get(f) in (None, "") \
                        and entry.get(f) not in (None, ""):
                    prev[f] = entry[f]

    # Daily operations must only call an order "لم يُرسل" when its
    # CURRENT Salla status is eligible for invoicing. Historical inbox
    # rows for awaiting-review/payment, cancelled, deleted, etc. remain
    # available in the source collections but are not actionable Qoyod
    # exceptions and must not inflate the unsent count.
    visible_keys = [
        key for key in order_keys if _include_in_daily_report(grouped[key])
    ]
    excluded_not_eligible = len(order_keys) - len(visible_keys)

    # rev37.3 — Salla-status facet: distinct statuses ACTUALLY present
    # in the actionable report (computed BEFORE the salla_status filter)
    # + optional backend-side filter.
    salla_status_counts: dict[str, int] = {}
    for key in visible_keys:
        e = grouped[key]
        label = e.get("salla_status") or e.get("salla_status_slug")
        if label:
            salla_status_counts[label] = salla_status_counts.get(label, 0) + 1

    def _salla_match(e: dict) -> bool:
        if not salla_status:
            return True
        return salla_status in (e.get("salla_status"),
                                e.get("salla_status_slug"))

    counts = {SENT: 0, UNSENT: 0, FAILED: 0, DUPLICATE: 0}
    orders: list[dict] = []
    for key in visible_keys:
        e = grouped[key]
        if not _salla_match(e):
            continue
        counts[e["status"]] += 1
        if status and e["status"] != status:
            continue
        orders.append(e)

    return {"ok": True, "days": days, "counts": counts,
            "total": sum(counts.values()),
            "sync_start_date": sync_start.isoformat(),
            "excluded_pre_sync_start": excluded_pre_sync,
            "excluded_not_eligible": excluded_not_eligible,
            "salla_status_counts": salla_status_counts,
            "orders": orders}
