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
from typing import Any

from integrations.qoyod.candidate_orders import build_candidate_audit
from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE

SENT      = "أُرسل"
UNSENT    = "لم يُرسل"
FAILED    = "فشل"
DUPLICATE = "مكرر"


def _order_created_date(row: dict) -> date | None:
    """Salla order CREATION date for an inbox row — same priority as
    eligible_orders._extract_order_created_at (canonical order_date →
    raw_payload data.date.date → data.created_at).

    Never fall back to ``received_at`` here.  That is the time the event
    reached Mezan, not the time Salla created the order.  Using it as an order
    date can make a pre-integration legacy order look newer than the fixed
    2026-07-01 Qoyod floor and incorrectly place it in both the yellow header
    alert and the unsent-orders page.
    """
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
    # A real Qoyod invoice or duplicate evidence remains useful history even
    # when the order later moves to a non-billable Salla status.  A merely
    # pending/failed local attempt does not: the *current* Salla status is the
    # final authority on whether the order is an actionable exception.
    if entry.get("status") in (SENT, DUPLICATE):
        return True
    current = entry.get("salla_status_slug") or entry.get("salla_status")
    # Preserve unknown legacy rows for investigation; only exclude a row
    # when Salla supplied a definite non-eligible current status.  This also
    # prevents an old quarantine/manual-send failure from exposing a retry
    # button after the order became non-eligible in Salla.
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


def _overlay_manual_failure(
    classification: dict, failure: dict | None,
) -> dict:
    """Make Plan-B retry/quarantine failures visible in the four-state view.

    A real invoice or duplicate marker always wins.  Everything else with a
    persisted failed send is actionable and belongs in ``لم يُرسل`` with the
    exact reason and a safe manual retry action.  This keeps the operator's
    list aligned with the runtime policy: the order stopped, not Qoyod.
    """
    if not failure or classification.get("status") in (SENT, DUPLICATE):
        return classification
    message = str(failure.get("message") or "").strip()
    if not message:
        message = "فشل الإرسال إلى قيود — يمكن إعادة الفحص والإرسال"
    return {
        "status": UNSENT,
        "reason": message,
        "failure_code": failure.get("code"),
        "failure_source": failure.get("source"),
        "retry_allowed": True,
    }


async def _list_unsent_orders_from_inbox_legacy(
    db, *, user_id: str, days: int = 30, limit: int = 500,
    orders_user_id: str | None = None,
    status: str | None = None,
    salla_status: str | None = None,
    search: str | None = None,
    now: datetime | None = None,
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

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    requested_days = max(1, min(days, 365))
    requested_start = (
        current_time.astimezone(timezone.utc) - timedelta(
            days=requested_days)
    ).date()
    # The integration itself cannot expose orders before 2026-07-01, even
    # when the operator selects a wider period such as 90 days.
    period_start = max(sync_start, requested_start)
    # Query by the earliest possible receive time, then enforce the requested
    # period with Salla's order creation date below.  A backfill received today
    # must not make an old Salla order look like a "last 7 days" order.
    scan_cutoff = datetime.combine(
        period_start, datetime.min.time(), tzinfo=timezone.utc,
    )
    excluded_pre_sync = 0
    excluded_missing_order_date = 0
    excluded_outside_requested_period = 0

    # Qoyod accounting markers historically live under the singleton
    # ``main`` tenant, while the live Salla refresh stores its newest inbox
    # trace under the merchant's Orders owner.  Manual Plan-B sends therefore
    # may place the authoritative marker on either side.  Read only those two
    # explicit owners and de-duplicate by order number below; never scan every
    # tenant for a matching order number.
    inbox_user_ids = list(dict.fromkeys(
        value
        for value in (
            str(user_id or "").strip(),
            str(orders_user_id or "").strip(),
        )
        if value
    ))
    if not inbox_user_ids:
        inbox_user_ids = [str(user_id)]

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

    # Plan-B automatic sends keep order-local failures outside the legacy
    # integration stages.  Overlay those open quarantines (and failed manual
    # send locks) so the operator sees them under "فشل" with the safe retry
    # action instead of a misleading "لم يُرسل".
    failure_by_order: dict[str, dict] = {}
    quarantine_cursor = db.qoyod_manual_auto_quarantines.find(
        {"user_id": user_id, "status": "open"},
        {
            "_id": 0,
            "order_number": 1,
            "code": 1,
            "message": 1,
            "attempt_count": 1,
        },
    )
    async for failed in quarantine_cursor:
        failed_order = str(failed.get("order_number") or "").strip()
        if failed_order:
            failure_by_order[failed_order] = {
                "source": "auto_quarantine",
                "code": failed.get("code"),
                "message": failed.get("message"),
            }

    lock_cursor = db.qoyod_manual_send_locks.find(
        {
            "user_id": user_id,
            "status": {"$in": ["failed", "partial_payment_failed"]},
        },
        {
            "_id": 0,
            "order_number": 1,
            "status": 1,
            "last_error": 1,
        },
    )
    async for failed in lock_cursor:
        failed_order = str(failed.get("order_number") or "").strip()
        if not failed_order or failed_order in failure_by_order:
            continue
        last_error = failed.get("last_error") or {}
        failure_by_order[failed_order] = {
            "source": "manual_send_lock",
            "code": last_error.get("code") or failed.get("status"),
            "message": last_error.get("message"),
        }

    # rev37.1 — ONE entry per SALLA ORDER, not per inbox row. The
    # inbox intentionally stores a row per status transition
    # (idempotency key includes status_slug), so a single order can
    # have 2+ rows. Group by salla_order_number; representative
    # status priority: أُرسل > مكرر > فشل > لم يُرسل. Ties keep the
    # most recent row (cursor is sorted received_at desc).
    _PRIORITY = {SENT: 3, DUPLICATE: 2, FAILED: 1, UNSENT: 0}
    grouped: dict[str, dict] = {}
    order_keys: list[str] = []

    inbox_owner_query: str | dict = inbox_user_ids[0]
    if len(inbox_user_ids) > 1:
        inbox_owner_query = {"$in": inbox_user_ids}
    query: dict = {
        "user_id": inbox_owner_query,
        "received_at": {"$gte": scan_cutoff},
    }
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
    )
    # Do not apply the public row limit to raw inbox events.  One Salla order
    # can have several status traces, so limiting here makes 30 and 90 days
    # collapse to the same newest-event window.  We group and classify the
    # complete requested period first, then cap only the returned table rows.
    cursor = cursor.sort("received_at", -1)

    async for row in cursor:
        # ── Integration start date — hard scope boundary ─────────────
        order_date = _order_created_date(row)
        # The unsent alert/page are operational accounting surfaces.  An
        # order must prove its Salla creation date is inside the integration
        # period; an unknown date is not eligible and must not be represented
        # as an actionable unsent invoice.
        if order_date is None:
            excluded_missing_order_date += 1
            continue
        if order_date < sync_start:
            excluded_pre_sync += 1
            continue
        if order_date < period_start:
            excluded_outside_requested_period += 1
            continue
        on = str(row.get("salla_order_number") or "").strip()
        in_qoyod = bool(on) and (on in ref_set)
        s = simplify_row(row, in_qoyod_by_reference=in_qoyod)
        s = _overlay_manual_failure(s, failure_by_order.get(on))
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
            "failure_code":   s.get("failure_code"),
            "failure_source": s.get("failure_source"),
            "retry_allowed":  bool(
                s.get("retry_allowed", s["status"] == FAILED)
            ),
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

    # Daily operations must only call an unsent/failed order actionable when
    # its CURRENT Salla status is eligible for invoicing. Historical inbox
    # rows for awaiting-review/payment, cancelled, shipped, deleted, etc.
    # remain available in the source collections but are not actionable
    # Qoyod exceptions and must not inflate the unsent/failed counts.
    visible_keys = [
        key for key in order_keys
        if _include_in_daily_report(grouped[key])
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
    matched_order_count = 0
    for key in visible_keys:
        e = grouped[key]
        if not _salla_match(e):
            continue
        # Count the full period before limiting table rows.  The cards must
        # reflect the selected 7/30/90-day range, not the display page size.
        counts[e["status"]] += 1
        if status and e["status"] != status:
            continue
        matched_order_count += 1
        if len(orders) < limit:
            orders.append(e)

    return {"ok": True, "days": days, "counts": counts,
            "total": sum(counts.values()),
            "sync_start_date": sync_start.isoformat(),
            "requested_order_start_date": period_start.isoformat(),
            "excluded_pre_sync_start": excluded_pre_sync,
            "excluded_missing_order_date": excluded_missing_order_date,
            "excluded_outside_requested_period":
                excluded_outside_requested_period,
            "excluded_not_eligible": excluded_not_eligible,
            "matched_order_count": matched_order_count,
            "returned_order_count": len(orders),
            "truncated": matched_order_count > len(orders),
            "salla_status_counts": salla_status_counts,
            "orders": orders}


async def _manual_failure_evidence(
    db: Any, *, markers_user_id: str,
) -> dict[str, dict[str, Any]]:
    """Load open local failures without expanding the candidate universe."""
    failures: dict[str, dict[str, Any]] = {}
    quarantines = getattr(db, "qoyod_manual_auto_quarantines", None)
    if quarantines is not None:
        cursor = quarantines.find(
            {"user_id": markers_user_id, "status": "open"},
            {"_id": 0, "order_number": 1, "code": 1, "message": 1},
        )
        async for row in cursor:
            reference = str(row.get("order_number") or "").strip()
            if reference:
                failures[reference] = {
                    "source": "auto_quarantine",
                    "code": row.get("code"),
                    "message": row.get("message"),
                }
    locks = getattr(db, "qoyod_manual_send_locks", None)
    if locks is not None:
        cursor = locks.find(
            {
                "user_id": markers_user_id,
                "status": {"$in": ["failed", "partial_payment_failed"]},
            },
            {"_id": 0, "order_number": 1, "status": 1, "last_error": 1},
        )
        async for row in cursor:
            reference = str(row.get("order_number") or "").strip()
            if not reference or reference in failures:
                continue
            error = row.get("last_error") or {}
            failures[reference] = {
                "source": "manual_send_lock",
                "code": error.get("code") or row.get("status"),
                "message": error.get("message"),
            }
    return failures


async def list_unsent_orders(
    db, *, user_id: str, days: int = 30, limit: int = 500,
    orders_user_id: str | None = None,
    status: str | None = None,
    salla_status: str | None = None,
    search: str | None = None,
    now: datetime | None = None,
    from_date: Any = None,
    to_date: Any = None,
) -> dict:
    """Map canonical unified candidates to the four read-only UI states."""
    effective_orders_user_id = str(orders_user_id or user_id)
    limit = max(1, min(int(limit), 5000))
    audit = await build_candidate_audit(
        db,
        orders_user_id=effective_orders_user_id,
        markers_user_id=str(user_id),
        marker_user_ids=(str(user_id), effective_orders_user_id),
        from_date=from_date,
        to_date=to_date,
        days=days,
        now=now,
        search=search,
    )
    failures = await _manual_failure_evidence(
        db, markers_user_id=str(user_id)
    )

    entries: list[dict[str, Any]] = []
    for proof in audit["orders"]:
        reference = proof["order_number"]
        inbox_row = proof.get("inbox_row")
        invoice = proof.get("qoyod_invoice")
        invoice_count = int(
            proof.get("qoyod_invoice_count_for_reference") or 0
        )
        if invoice_count > 1:
            classification = {
                "status": DUPLICATE,
                "reason": (
                    "يوجد أكثر من فاتورة قيود تحمل reference نفسه؛ "
                    "الإرسال محظور حتى المراجعة"
                ),
                "retry_allowed": False,
            }
        elif invoice is not None and failures.get(reference):
            failure = failures[reference]
            classification = {
                "status": FAILED,
                "reason": str(failure.get("message") or (
                    "الفاتورة موجودة في قيود لكن السداد المحلي غير مكتمل"
                )),
                "failure_code": failure.get("code"),
                "failure_source": failure.get("source"),
                "retry_allowed": True,
            }
        elif (
            invoice is not None
            and str(invoice.get("status") or "").lower()
            == "invoice_sent_receipt_failed"
        ):
            classification = {
                "status": FAILED,
                "reason": (
                    "الفاتورة موجودة في قيود لكن سند السداد لم يكتمل"
                ),
                "failure_code": "invoice_sent_receipt_failed",
                "failure_source": "qoyod_invoices",
                "retry_allowed": False,
            }
        elif invoice is not None:
            classification = {
                "status": SENT,
                "reason": (
                    f"فاتورة قيود #{invoice.get('qoyod_invoice_id')} — "
                    "مطابقة دقيقة لرقم الطلب في reference"
                ),
                "retry_allowed": False,
            }
        elif inbox_row is not None:
            classification = simplify_row(
                inbox_row, in_qoyod_by_reference=False
            )
            if classification.get("status") == SENT:
                classification = {
                    "status": UNSENT,
                    "reason": (
                        "توجد علامة محلية لكن لا يوجد مرجع فاتورة مطابق "
                        "في qoyod_invoices"
                    ),
                    "retry_allowed": True,
                }
            classification = _overlay_manual_failure(
                classification, failures.get(reference)
            )
        else:
            classification = {
                "status": UNSENT,
                "reason": (
                    "طلب مؤهل في unified_orders وغير موجود في "
                    "integration_inbox؛ لم يكن العامل القديم يراه"
                ),
                "retry_allowed": False,
            }

        received_at = (inbox_row or {}).get("received_at")
        entries.append({
            "order_number": reference,
            "order_date": proof.get("order_date"),
            "received_at": (
                received_at.isoformat()
                if hasattr(received_at, "isoformat") else received_at
            ),
            "total_amount": proof.get("total_amount"),
            "payment_method": proof.get("payment_method"),
            "salla_status": proof.get("current_status"),
            "salla_status_slug": proof.get("current_status_key"),
            "status": classification["status"],
            "reason": classification["reason"],
            "failure_code": classification.get("failure_code"),
            "failure_source": classification.get("failure_source"),
            "retry_allowed": bool(classification.get("retry_allowed", False)),
            "qoyod_invoice_id": proof.get("qoyod_invoice_id"),
            "trace_id": proof.get("trace_id"),
            "events_count": int(
                proof.get("integration_inbox_event_count") or 0
            ),
            "in_unified_orders": True,
            "in_integration_inbox": proof.get("in_integration_inbox"),
            "has_qoyod_reference_match": proof.get(
                "has_qoyod_reference_match"
            ),
            "worker_candidate": proof.get("worker_candidate"),
            "automation_visibility_reason": proof.get(
                "legacy_worker_visibility_reason"
            ),
            "debug": {
                "order_number": reference,
                "qoyod_reference": proof.get("qoyod_reference"),
                "invoice_id": proof.get("qoyod_invoice_id"),
                "match_source": (
                    "qoyod_invoices.reference" if invoice else "none"
                ),
            },
        })

    salla_status_counts: dict[str, int] = {}
    for entry in entries:
        label = entry.get("salla_status") or entry.get("salla_status_slug")
        if label:
            salla_status_counts[str(label)] = (
                salla_status_counts.get(str(label), 0) + 1
            )
    filtered = [
        entry for entry in entries
        if not salla_status or salla_status in (
            entry.get("salla_status"), entry.get("salla_status_slug")
        )
    ]
    counts = {SENT: 0, UNSENT: 0, FAILED: 0, DUPLICATE: 0}
    for entry in filtered:
        counts[entry["status"]] += 1
    table = [
        entry for entry in filtered
        if not status or entry["status"] == status
    ]
    matched_order_count = len(table)
    table = table[:limit]
    return {
        "ok": True,
        "read_only": True,
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
        "reference_hashes": audit["reference_hashes"],
        "days": days,
        "from_date": audit["from_date"],
        "to_date": audit["to_date"],
        "counts": counts,
        "total": sum(counts.values()),
        "worker_candidate_count": len(audit["unsent_references"]),
        "sync_start_date": QOYOD_SYNC_START_DATE,
        "requested_order_start_date": audit["from_date"],
        "excluded_pre_sync_start": 0,
        "excluded_missing_order_date": audit["unified_exclusions"][
            "missing_or_inferred_order_date"
        ],
        "excluded_outside_requested_period": audit["unified_exclusions"][
            "outside_requested_date_range"
        ],
        "excluded_not_eligible": audit["unified_exclusions"][
            "status_not_eligible"
        ],
        "matched_order_count": matched_order_count,
        "returned_order_count": len(table),
        "truncated": matched_order_count > len(table),
        "salla_status_counts": salla_status_counts,
        "duplicate_qoyod_reference_count": audit["counts"][
            "duplicate_qoyod_references"
        ],
        "orders": table,
    }
