"""rev38 — Send Preflight (READ-ONLY). فحص ما قبل الإرسال لطلب واحد.

User contract (mada canary prep): before ANY live send the operator
runs this preflight which verifies — with ZERO writes and ZERO Qoyod
API calls:

  1. scope_check      — Salla creation date >= 2026-07-01 (actual date shown)
  2. payment_check    — canonical payment_method (+ optional expected match)
  3. duplicate_check  — no REAL قيود invoice exists for the order
  4. amount_check     — expected invoice total diff vs Salla <= 0.01
  5. returns trace_id, total_amount, invoice payload PREVIEW

NOTHING here sends. The payload preview is built by the SAME pure
`build_invoice_payload` the pipeline uses.
"""
from __future__ import annotations

from datetime import datetime, timezone

from integrations.qoyod.dry_rca_report import (
    _fetch_inbox_row, _find_real_customer_mapping,
    _find_real_product_in_external, _find_real_product_mapping,
)
from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date,
)
from integrations.qoyod.invoice_builder import build_invoice_payload
from integrations.qoyod.unsent_orders import (
    _is_real, _order_created_date,
)

_TOLERANCE = 0.01


async def _duplicate_check(db, user_id: str, order_number: str,
                           order_id) -> dict:
    """Same REAL-invoice semantics as the pipeline rev36 hard-stop."""
    refs = [v for v in {str(order_number or ""), str(order_id or "")} if v]
    q = {
        "user_id": user_id,
        "$or": [{"salla_order_number": {"$in": refs}},
                {"salla_order_id": {"$in": refs}}],
        "qoyod_invoice_id": {"$exists": True, "$nin": [None, ""],
                             "$not": {"$regex": "^(DRY:|PREVIEW:)"}},
    }
    ledger = await db.qoyod_invoices.find_one(
        q, {"_id": 0, "qoyod_invoice_id": 1, "qoyod_invoice_number": 1})
    inbox = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "$or": [{"salla_order_number": {"$in": refs}},
                 {"salla_order_id": {"$in": refs}}],
         "qoyod_invoice_id": {"$exists": True, "$nin": [None, ""],
                              "$not": {"$regex": "^(DRY:|PREVIEW:)"}}},
        {"_id": 0, "qoyod_invoice_id": 1})
    existing = (ledger or {}).get("qoyod_invoice_id") \
        or (inbox or {}).get("qoyod_invoice_id")
    if existing:
        return {"passed": False,
                "existing_qoyod_invoice_id": str(existing),
                "detail": f"توجد فاتورة قيود حقيقية مسبقاً (#{existing}) "
                          "— الإرسال سيُرفض لمنع التكرار"}
    return {"passed": True, "existing_qoyod_invoice_id": None,
            "detail": "لا توجد أي فاتورة قيود حقيقية لهذا الطلب"}


async def build_send_preflight(
    db, *, user_id: str, order_number: str,
    expected_payment_method: str | None = None,
) -> dict:
    row = await _fetch_inbox_row(db, user_id, str(order_number))
    if not row:
        return {"ok": False, "found": False,
                "order_number": order_number, "read_only": True,
                "error": "لا يوجد سجل لهذا الطلب في ميزان"}
    canonical = row.get("canonical_payload") or {}
    sync_start = _parse_iso_date(QOYOD_SYNC_START_DATE)

    # 1 ── scope (Salla CREATION date) ───────────────────────────────
    order_date = _order_created_date(row)
    scope_check = {
        "order_created_date": (order_date.isoformat()
                               if order_date else None),
        "sync_start_date":    sync_start.isoformat(),
        "passed": bool(order_date and order_date >= sync_start),
    }
    scope_check["detail"] = (
        f"تاريخ إنشاء الطلب في سلة {scope_check['order_created_date']} "
        + ("داخل نطاق التكامل" if scope_check["passed"]
           else "خارج نطاق التكامل — لا يُرسل")
        if order_date else "تعذر قراءة تاريخ إنشاء الطلب — لا يُرسل")

    # 2 ── payment method ────────────────────────────────────────────
    pm = str(canonical.get("payment_method") or "")
    pm_native = canonical.get("payment_method_native")
    payment_check = {
        "payment_method": pm or None,
        "payment_method_native": pm_native,
        "expected": expected_payment_method,
        "passed": (pm.lower() == str(expected_payment_method).lower()
                   if expected_payment_method else bool(pm)),
    }
    payment_check["detail"] = (
        f"طريقة الدفع: {pm or 'غير معروفة'}"
        + (f" — {'مطابقة' if payment_check['passed'] else 'غير مطابقة'} "
           f"للمتوقع ({expected_payment_method})"
           if expected_payment_method else ""))

    # 3 ── duplicate real invoice ────────────────────────────────────
    duplicate_check = await _duplicate_check(
        db, user_id, row.get("salla_order_number"),
        canonical.get("order_id") or row.get("salla_order_id"))

    # 3.5 ── rev39 — SKIPPED history (rev33 veto intel, READ-ONLY) ───
    history = [str(h.get("stage") or h) for h in
               row.get("stage_history") or []]
    has_skipped = ("SKIPPED" in history
                   or row.get("pipeline_stage") == "SKIPPED")
    # rev44 — transient skips (status/payment scope) do NOT block;
    # unclassified/legacy SKIPPED stays fatal (fail-closed).
    is_transient_skip = (has_skipped
                         and row.get("skip_class") == "transient")
    skipped_blocked = has_skipped and not is_transient_skip
    skipped_history_check = {
        "passed": not skipped_blocked,
        "pipeline_stage": row.get("pipeline_stage"),
        "skip_class": row.get("skip_class"),
        "detail": ("الصف سبق تخطيه (SKIPPED) — فيتو rev33 سيمنع أي "
                   "إرسال لهذا الصف" if skipped_blocked
                   else ("تخطٍ مؤقت (transient rev44) — لا يمنع "
                         "الإرسال؛ الاستئناف عبر one-shot مدقق فقط"
                         if is_transient_skip
                         else "لا يوجد SKIPPED في تاريخ الصف")),
    }

    # 3.6 ── rev39.2 — DEAD_LETTER / blocked-stage veto (rev32.1) ────
    # RCA of invoice #192: a DEAD_LETTER row was resurrected by a
    # side path. rev32.1 makes it an ABSOLUTE write veto — attempting
    # a send would trip the kill switch. Surface it here instead.
    from integrations.qoyod.rev32_hardening import (
        BLOCKED_FOR_WRITE_STAGES,
    )
    stage_now = row.get("pipeline_stage")
    dead_at = row.get("dead_lettered_at")
    blocked = bool(dead_at) or (
        stage_now in BLOCKED_FOR_WRITE_STAGES
        # rev44 — transient SKIPPED is resumable: one-shot resets the
        # stage BEFORE any write, so the rev32.1 veto never fires.
        and not (stage_now == "SKIPPED" and is_transient_skip))
    dead_letter_check = {
        "passed": not blocked,
        "pipeline_stage": stage_now,
        "dead_lettered_at": (str(dead_at) if dead_at else None),
        "detail": ((f"الصف بحالة {stage_now} "
                    + (f"(dead_lettered_at={dead_at}) " if dead_at else "")
                    + "— فيتو rev32.1 يمنع أي كتابة لهذا الصف "
                    "(محاولة الإرسال ستفعّل مفتاح الإيقاف)")
                   if blocked else
                   "الصف في حالة قابلة للكتابة — لا فيتو rev32.1"),
    }

    # 4 ── amount + payload preview (pure build, READ-ONLY lookups) ──
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    resolutions: list[dict] = []
    unmapped: list[str] = []
    for it in canonical.get("items") or []:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        m = await _find_real_product_mapping(db, user_id, sku)
        e = await _find_real_product_in_external(db, user_id, sku)
        pid = (m or e or {}).get("qoyod_product_id")
        if pid:
            resolutions.append({"sku": sku, "qoyod_product_id": pid})
        else:
            unmapped.append(sku)

    cust = canonical.get("customer") or {}
    phone = (cust.get("mobile") or cust.get("phone")
             or canonical.get("customer_mobile"))
    cust_map = await _find_real_customer_mapping(
        db, user_id, str(phone or "").strip() or None)
    customer_id = (row.get("qoyod_customer_id")
                   if _is_real(row.get("qoyod_customer_id")) else None) \
        or (cust_map or {}).get("qoyod_customer_id") \
        or "<PREVIEW_NEW_CUSTOMER>"

    invoice_payload_preview = None
    diagnostics = None
    amount_check: dict
    if unmapped:
        amount_check = {"passed": False, "difference": None,
                        "unmapped_skus": unmapped,
                        "detail": ("منتجات بدون ربط حقيقي في قيود: "
                                   + ", ".join(unmapped)
                                   + " — لا يمكن حساب الفرق")}
    else:
        built = build_invoice_payload(
            dto_dict=canonical, qoyod_customer_id=str(customer_id),
            product_resolutions=resolutions,
            invoice_date=datetime.now(timezone.utc),
            settings=settings)
        diagnostics = built.get("_diagnostics") or {}
        invoice_payload_preview = built.get("invoice")
        diff = diagnostics.get("difference")
        passed = diff is not None and abs(float(diff)) <= _TOLERANCE
        amount_check = {
            "passed": passed,
            "difference": diff,
            "salla_total": canonical.get("total_amount"),
            "detail": (f"الفرق المتوقع {diff} ريال — "
                       + ("ضمن الحد المسموح (0.01)" if passed
                          else "أكبر من 0.01 — الإرسال سيُحظر")
                       if diff is not None
                       else "تعذر حساب الفرق — الإرسال سيُحظر"),
        }

    checks = {"scope_check": scope_check,
              "payment_check": payment_check,
              "duplicate_check": duplicate_check,
              "skipped_history_check": skipped_history_check,
              "dead_letter_check": dead_letter_check,
              "amount_check": amount_check}
    return {
        "ok": True,
        "found": True,
        "order_number": str(row.get("salla_order_number")
                            or order_number),
        "trace_id": row.get("trace_id"),
        "pipeline_stage": row.get("pipeline_stage"),
        "salla_status": (canonical.get("order_status_native")
                         or canonical.get("order_status")),
        "total_amount": canonical.get("total_amount"),
        "checks": checks,
        "ready_to_send": all(c["passed"] for c in checks.values()),
        "invoice_payload_preview": invoice_payload_preview,
        "pricing_diagnostics": diagnostics,
        "preview_note": ("معاينة فقط — لم يُرسل شيء. تاريخ الفاتورة "
                         "الفعلي سيُختم بتاريخ الإرسال (الرياض) وقت "
                         "الإرسال الحقيقي."),
        "read_only": True,
        "no_qoyod_api_calls": True,
        "no_db_writes": True,
    }
