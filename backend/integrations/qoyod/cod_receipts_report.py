"""Iter-293 — Diagnostic: COD orders that wrongly produced a receipt.

Purpose
-------
Before Iter-293 shipped, the pipeline created an `invoice_payment` for
EVERY order — including Cash-on-Delivery — as long as a Qoyod account
was mapped for the payment method. That booked COD orders as PAID in
Qoyod (balance = 0), which is wrong: COD is collected by the courier
later, so the correct accounting posture is "credit invoice, balance =
full total".

This report is **read-only**. It scans `qoyod_invoices` and lists every
row where:

    payment_method ∈ COD family  AND  qoyod_invoice_payment_id IS NOT NULL

So the merchant's accountant can manually delete the erroneous
invoice_payment in Qoyod (or use a future Repair tool that the user
explicitly opts into — NOT this report).

Endpoint
--------
    GET /api/integrations/qoyod/admin/cod-receipts-report

    Query params (all optional):
        from   ISO8601 date — only invoices created at/after.
        to     ISO8601 date — only invoices created at/before.
        limit  int          — max rows to return (default 500, max 5000).

    Response:
        {
          "ok":          true,
          "total":       <count of rows in scope>,
          "with_receipt": <count of those with qoyod_invoice_payment_id>,
          "rows":        [{salla_order_id, qoyod_invoice_id,
                           qoyod_invoice_payment_id, invoice_total,
                           paid_amount, remaining_amount, payment_method,
                           recommendation}, ...]
        }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .payment_methods import is_cod_family


async def cod_receipts_report(
    db,
    user_id: str,
    *,
    from_iso: Optional[str] = None,
    to_iso:   Optional[str] = None,
    limit:    int = 500,
) -> dict[str, Any]:
    """Build the diagnostic report. See module docstring for contract."""
    limit = max(1, min(int(limit or 500), 5000))

    # Build the time filter — we filter in Python after the query so we
    # can handle both ISO strings AND `datetime` storage shapes that may
    # have crept in across iterations.
    q: dict[str, Any] = {"user_id": user_id}
    # We can't filter `payment_method ∈ cod-family` in Mongo without
    # listing every alias variant, so we project the relevant fields
    # and do the family check in Python (cheap — runs once per row).
    projection = {
        "_id": 0,
        "id": 1,
        "salla_order_id": 1,
        "salla_order_number": 1,
        "qoyod_invoice_id": 1,
        "qoyod_invoice_payment_id": 1,
        "qoyod_receipt_id": 1,
        "payment_method": 1,
        "payment_method_native": 1,
        "total_amount": 1,
        "paid_amount": 1,
        "remaining_amount": 1,
        "status": 1,
        "pipeline_stage": 1,
        "posting_mode": 1,
        "created_at": 1,
        "updated_at": 1,
    }

    rows: list[dict[str, Any]] = []
    cursor = db.qoyod_invoices.find(q, projection).sort("created_at", -1)
    raw_total = 0  # rows in COD family
    with_receipt = 0
    async for doc in cursor:
        pm = doc.get("payment_method") or doc.get("payment_method_native")
        if not is_cod_family(pm):
            continue
        # Time-range filter (string ISO comparison works for ISO-8601).
        created = doc.get("created_at")
        if from_iso and isinstance(created, str) and created < from_iso:
            continue
        if to_iso and isinstance(created, str) and created > to_iso:
            continue
        raw_total += 1
        has_receipt = bool(doc.get("qoyod_invoice_payment_id")
                           or doc.get("qoyod_receipt_id"))
        if has_receipt:
            with_receipt += 1
        # Recommendation logic — three states:
        #   • mismatch: COD with a payment record → MUST unbook in Qoyod.
        #   • migrated: COD already on the new credit_invoice_only mode.
        #   • clean:    COD with no receipt + no posting_mode tag.
        if has_receipt:
            recommendation = (
                "MISMATCH — COD مرحّل كمدفوع. احذف سند القبض/المدفوعات من قيود "
                "يدوياً ليُصبح المبلغ المستحق على الفاتورة كاملاً (مدين على العميل/الكاش)."
            )
        elif doc.get("posting_mode") == "credit_invoice_only":
            recommendation = "OK — مرحّل صحيح كفاتورة آجلة (credit_invoice_only)."
        else:
            recommendation = "OK — لا يوجد سند قبض، لكن posting_mode غير مسجّل."

        if len(rows) < limit:
            rows.append({
                "id":                        doc.get("id"),
                "salla_order_id":            doc.get("salla_order_id"),
                "salla_order_number":        doc.get("salla_order_number"),
                "qoyod_invoice_id":          doc.get("qoyod_invoice_id"),
                "qoyod_invoice_payment_id":  doc.get("qoyod_invoice_payment_id"),
                "qoyod_receipt_id":          doc.get("qoyod_receipt_id"),
                "payment_method":            pm,
                "invoice_total":             doc.get("total_amount"),
                "paid_amount":               doc.get("paid_amount"),
                "remaining_amount":          doc.get("remaining_amount"),
                "status":                    doc.get("status"),
                "pipeline_stage":            doc.get("pipeline_stage"),
                "posting_mode":              doc.get("posting_mode"),
                "created_at":                doc.get("created_at"),
                "has_receipt":               has_receipt,
                "recommendation":            recommendation,
            })

    return {
        "ok":             True,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "filters":        {"from": from_iso, "to": to_iso, "limit": limit},
        "total_cod":      raw_total,
        "with_receipt":   with_receipt,
        "without_receipt": raw_total - with_receipt,
        "rows":           rows,
        "truncated":      raw_total > limit,
    }
