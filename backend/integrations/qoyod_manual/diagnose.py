"""Plan-B totals diagnose — RCA for `totals_mismatch` guard.

Runs the SAME quantisation + tax math the real send uses, but WITHOUT
touching قيود (no customer lookup, no product lookup, no invoice/
payment POST). The output is the exact `breakdown` object the guard
would attach to the failure — so an operator can inspect why the
0.01-SAR tolerance was breached for any order at will.
"""
from __future__ import annotations

from typing import Any, Optional

from integrations.qoyod_manual.pending import _salla_order_created_date
from integrations.qoyod_manual.send import (
    _build_invoice_payload, _q2, _riyadh_today_iso,
    ManualSendRefused,
)


async def diagnose_totals(db, *, user_id: str,
                          order_number: str) -> dict:
    """Compute the same breakdown the guard would surface.

    Returns a JSON-safe dict with the full RCA. Never raises for
    business errors — always returns a structured `ok=False` with a
    `code` when the order can't be evaluated (missing row / no date /
    pre-floor / no items).
    """
    row = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "salla_order_number": str(order_number)},
        sort=[("received_at", -1)],
    )
    if not row:
        return {"ok": False, "code": "order_not_found",
                "message": f"لم يُعثر على الطلب {order_number} في الاستلام"}
    canon = row.get("canonical_payload") or {}
    if not canon.get("items"):
        return {"ok": False, "code": "no_items",
                "message": "لا توجد بنود لهذا الطلب في الحمولة الأساسية"}

    odate = _salla_order_created_date(row)
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}

    # Feed the builder a placeholder product_id for each SKU so the
    # math runs. The ids never leave this function — they're only
    # here to satisfy the type signature.
    line_resolutions = {
        str(it.get("sku") or "").strip(): idx + 1
        for idx, it in enumerate(canon.get("items") or [])
    }
    try:
        _invoice, expected_total, breakdown = _build_invoice_payload(
            canon=canon, contact_id=0,
            line_resolutions=line_resolutions,
            settings=settings,
            send_date_iso=_riyadh_today_iso())
    except ManualSendRefused as exc:
        return {"ok": False, "code": exc.code,
                "message": exc.message, "detail": exc.extra}

    salla_total = _q2(canon.get("total_amount"))
    diff = _q2(expected_total - salla_total)
    would_pass = abs(diff) <= 0.01
    return {
        "ok":                 True,
        "order_number":       str(order_number),
        "order_date":         (odate.isoformat() if odate else None),
        "salla_total":        salla_total,
        "expected_qoyod_total": expected_total,
        "difference":         diff,
        "within_tolerance":   would_pass,
        "tolerance_sar":      0.01,
        "difference_source_hint": breakdown["difference_source_hint"],
        "breakdown":          breakdown,
        "canonical_summary": {
            "items_count":       len(canon.get("items") or []),
            "shipping_amount":   _q2(canon.get("shipping_amount")),
            "cod_fee_amount":    _q2(canon.get("cod_fee_amount")),
            "discount_amount":   _q2(canon.get("discount_amount")),
            "tax_amount":        _q2(canon.get("tax_amount")),
            "subtotal":          _q2(canon.get("subtotal")),
            "payment_method":    canon.get("payment_method")
                                 or canon.get("payment_method_native"),
        },
        "settings_used": {
            "qoyod_tax_percent":              settings.get("qoyod_tax_percent"),
            "default_shipping_product_id":    settings.get(
                                                 "default_shipping_product_id"),
            "default_cod_fee_product_id":     settings.get(
                                                 "default_cod_fee_product_id"),
        },
    }
