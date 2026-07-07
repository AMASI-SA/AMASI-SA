"""rev37 — تقرير المطابقة (Reconciliation) ميزان ↔ قيود. READ-ONLY.

Proves that every successful MEZAN order (real qoyod_invoice_id) has a
matching REAL invoice in قيود with the same total, and surfaces any
قيود invoice (issue_date >= 2026-07-01) with no MEZAN record.

Scope (user decree):
  • MEZAN side: Salla order CREATION date >= 2026-07-01 (NOT the
    status-change date).
  • قيود side: invoices with issue_date >= 2026-07-01 up to today.

NO writes to قيود. Only GET /invoices via the existing paginator.
"""
from __future__ import annotations

from datetime import datetime, timezone

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date,
)
from integrations.qoyod.fresh_start_audit import _coerce_float, _paginate
from integrations.qoyod.unsent_orders import _is_real, _order_created_date

MATCHED          = "مطابق"
AMOUNT_MISMATCH  = "فرق مبلغ"
MEZAN_ONLY       = "في ميزان فقط"
QOYOD_ONLY       = "في قيود فقط"

_TOLERANCE = 0.01


def _qoyod_invoice_view(it: dict) -> dict:
    return {
        "qoyod_invoice_id": str(it.get("id") or ""),
        "invoice_number":   it.get("invoice_number") or it.get("number")
                            or it.get("reference") or "",
        "reference":        str(it.get("reference")
                                or it.get("external_reference")
                                or it.get("source_reference") or ""),
        "issue_date":       it.get("issue_date") or "",
        "total":            _coerce_float(it.get("total")
                                          or it.get("total_amount")),
        "status":           it.get("status") or "",
    }


async def _mezan_sent_orders(db, user_id: str, sync_start) -> list[dict]:
    """MEZAN rows that claim a REAL قيود invoice, scoped by Salla
    order CREATION date >= 2026-07-01."""
    out: list[dict] = []
    seen: set[tuple] = set()  # rev37.1 — inbox has a row per status
    # transition; the same order+invoice must be counted ONCE.
    cursor = db.integration_inbox.find(
        {"user_id": user_id,
         "qoyod_invoice_id": {"$exists": True, "$nin": [None, ""]}},
        {"_id": 0, "id": 1, "salla_order_number": 1, "received_at": 1,
         "pipeline_stage": 1, "qoyod_invoice_id": 1,
         "raw_payload.data.date": 1, "raw_payload.data.created_at": 1,
         "canonical_payload.order_date": 1,
         "canonical_payload.created_at": 1,
         "canonical_payload.total_amount": 1},
    ).sort("received_at", -1).limit(5000)
    async for row in cursor:
        if not _is_real(row.get("qoyod_invoice_id")):
            continue
        order_date = _order_created_date(row)
        if order_date is not None and order_date < sync_start:
            continue
        canon = row.get("canonical_payload") or {}
        key = (str(row.get("salla_order_number") or ""),
               str(row.get("qoyod_invoice_id")))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "order_number":     str(row.get("salla_order_number") or ""),
            "order_date":       (order_date.isoformat()
                                 if order_date else None),
            "qoyod_invoice_id": str(row.get("qoyod_invoice_id")),
            "mezan_total":      _coerce_float(canon.get("total_amount")),
            "pipeline_stage":   row.get("pipeline_stage"),
        })
    return out


async def _qoyod_invoices_in_scope(api_client, sync_start) -> list[dict]:
    items = await _paginate(
        api_client.list_invoices, page_size=50, max_pages=200,
        extract_keys=("invoices", "data", "items"))
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        v = _qoyod_invoice_view(it)
        d = _parse_iso_date(v["issue_date"])
        if d is not None and d < sync_start:
            continue
        out.append(v)
    return out


# rev37.2 — user-frozen forensic evidence (leak RCA). NEVER touch.
_FROZEN_EVIDENCE_INVOICE_IDS = {
    "188", "189", "190", "191", "192", "193", "194", "195"}


async def _diagnose_qoyod_only(db, user_id: str, reference: str,
                               sync_start) -> str:
    """READ-ONLY RCA for a قيود invoice with no MEZAN match — looks
    the order up in integration_inbox WITHOUT the date scope."""
    if not reference:
        return ("فاتورة بدون مرجع طلب سلة — الأرجح فاتورة يدوية "
                "أُنشئت مباشرة في قيود")
    row = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "$or": [{"salla_order_number": reference},
                 {"salla_order_id": reference}]},
        {"_id": 0, "qoyod_invoice_id": 1, "pipeline_stage": 1,
         "received_at": 1,
         "raw_payload.data.date": 1, "raw_payload.data.created_at": 1,
         "canonical_payload.order_date": 1,
         "canonical_payload.created_at": 1},
        sort=[("received_at", -1)])
    if row is None:
        return ("لا يوجد أي سجل لهذا الطلب في ميزان — الأرجح فاتورة "
                "يدوية أو أُنشئت قبل تفعيل الويبهوك")
    order_date = _order_created_date(row)
    if order_date is not None and order_date < sync_start:
        return (f"الطلب موجود في ميزان لكن تاريخ إنشائه في سلة "
                f"{order_date.isoformat()} قبل بداية التكامل "
                f"{sync_start.isoformat()} — فاتورة تاريخية/تسريب سابق "
                "خارج نطاق التكامل")
    if _is_real(row.get("qoyod_invoice_id")):
        return ("الطلب موجود في ميزان بفاتورة مختلفة "
                f"(#{row.get('qoyod_invoice_id')}) — يحتاج مراجعة يدوية")
    return ("الطلب موجود في ميزان ضمن النطاق لكن دون رقم فاتورة "
            f"مسجّل (حالة: {row.get('pipeline_stage')}) — تسريب محتمل: "
            "فاتورة كُتبت في قيود دون تحديث سجل ميزان")


async def run_reconciliation_report(db, *, user_id: str, api_client) -> dict:
    """Compare + persist. READ-ONLY towards قيود."""
    from integrations.qoyod.api_client import QoyodAPIError
    sync_start = _parse_iso_date(QOYOD_SYNC_START_DATE)
    mezan = await _mezan_sent_orders(db, user_id, sync_start)
    try:
        qoyod = await _qoyod_invoices_in_scope(api_client, sync_start)
    except QoyodAPIError as exc:
        return {"ok": False,
                "error": ("تعذر الاتصال بقيود لجلب الفواتير — "
                          "تحقق من صلاحية مفتاح API "
                          f"(HTTP {exc.status_code})"),
                "sync_start_date": sync_start.isoformat(),
                "mezan_sent_total": len(mezan)}

    qoyod_by_id = {v["qoyod_invoice_id"]: v for v in qoyod}
    claimed_ids: set[str] = set()
    rows: list[dict] = []
    counts = {MATCHED: 0, AMOUNT_MISMATCH: 0, MEZAN_ONLY: 0, QOYOD_ONLY: 0}

    for m in mezan:
        q = qoyod_by_id.get(m["qoyod_invoice_id"])
        if q is None:
            # Fallback: match by reference == Salla order number.
            q = next((v for v in qoyod
                      if v["reference"] == m["order_number"]
                      and v["qoyod_invoice_id"] not in claimed_ids), None)
        if q is None:
            counts[MEZAN_ONLY] += 1
            rows.append({**m, "qoyod_total": None, "difference": None,
                         "invoice_number": None, "issue_date": None,
                         "status": MEZAN_ONLY,
                         "note": ("مسجّل في ميزان كمُرسل لكن لا توجد "
                                  "فاتورة مطابقة في قيود")})
            continue
        claimed_ids.add(q["qoyod_invoice_id"])
        diff = round(m["mezan_total"] - q["total"], 2)
        if abs(diff) <= _TOLERANCE:
            counts[MATCHED] += 1
            status, note = MATCHED, "مطابق تماماً"
        else:
            counts[AMOUNT_MISMATCH] += 1
            status = AMOUNT_MISMATCH
            note = f"فرق {diff:+.2f} ريال بين ميزان وقيود"
        rows.append({**m,
                     "qoyod_total":    q["total"],
                     "difference":     diff,
                     "invoice_number": q["invoice_number"],
                     "issue_date":     q["issue_date"],
                     "status":         status,
                     "note":           note})

    for v in qoyod:
        if v["qoyod_invoice_id"] in claimed_ids:
            continue
        counts[QOYOD_ONLY] += 1
        note = await _diagnose_qoyod_only(
            db, user_id, v["reference"], sync_start)
        if v["qoyod_invoice_id"] in _FROZEN_EVIDENCE_INVOICE_IDS:
            note = ("🧊 ضمن الأدلة المجمّدة بقرارك (188-195) — "
                    "لا تُمس. " + note)
        rows.append({
            "order_number":     v["reference"] or None,
            "order_date":       None,
            "qoyod_invoice_id": v["qoyod_invoice_id"],
            "mezan_total":      None,
            "qoyod_total":      v["total"],
            "difference":       None,
            "invoice_number":   v["invoice_number"],
            "issue_date":       v["issue_date"],
            "pipeline_stage":   None,
            "status":           QOYOD_ONLY,
            "note":             note,
        })

    report = {
        "ok": True,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "sync_start_date": sync_start.isoformat(),
        "counts": counts,
        "mezan_sent_total": len(mezan),
        "qoyod_invoices_total": len(qoyod),
        "all_matched": (counts[AMOUNT_MISMATCH] == 0
                        and counts[MEZAN_ONLY] == 0
                        and counts[QOYOD_ONLY] == 0),
        "rows": rows,
    }
    await db.qoyod_reconciliation_reports.insert_one({
        "user_id": user_id, **{k: v for k, v in report.items() if k != "ok"},
    })
    return report
