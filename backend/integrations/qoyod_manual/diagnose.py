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
from integrations.qoyod_manual.order_source import get_order_payment_facts
from integrations.qoyod_manual.send import (
    _build_invoice_payload, _q2, _riyadh_today_iso,
    _find_historical_positive_canon, _money_decimal,
    _normalized_recovery_items,
    _overlay_order_engine_facts, _prepare_sar_invoice_canon_from_inbox,
    _within_amount_tolerance, _assert_sar_currency, ManualSendRefused,
)


async def diagnose_totals(db, *, user_id: str,
                          order_number: str,
                          orders_user_id: Optional[str] = None,
                          allow_verified_salla_recovery: bool = False) -> dict:
    """Compute the same breakdown the guard would surface.

    Returns a JSON-safe dict with the full RCA. Never raises for
    business errors — always returns a structured response. A completed
    totals diagnosis returns ``ok=True`` even when exact parity fails, so
    the UI can render the complete LRM breakdown instead of only the
    refusal message.
    """
    inbox_owner_ids = list(dict.fromkeys(
        value for value in (str(user_id), str(orders_user_id or "").strip())
        if value
    ))
    row = await db.integration_inbox.find_one(
        {"user_id": {"$in": inbox_owner_ids},
         "salla_order_number": str(order_number)},
        sort=[("received_at", -1)],
    )
    if not row:
        return {"ok": False, "code": "order_not_found",
                "message": f"لم يُعثر على الطلب {order_number} في الاستلام"}
    canon = dict(row.get("canonical_payload") or {})
    live_total = _money_decimal(canon.get("total_amount"))
    live_items = _normalized_recovery_items(canon.get("items"))
    if live_total is not None:
        canon["total_amount"] = _q2(live_total)
    if live_items is not None:
        canon["items"] = live_items
    if allow_verified_salla_recovery:
        recovered = await _find_historical_positive_canon(
            db,
            owner_ids=inbox_owner_ids,
            order_number=str(order_number),
            live_canon=canon,
            unified_owner_id=str(orders_user_id or user_id),
            preferred_inbox_owner_id=str(row.get("user_id") or ""),
            prefer_verified_unified=True,
        )
        if recovered is not None:
            canon = recovered
    payment_facts = await get_order_payment_facts(
        db,
        user_id=orders_user_id or user_id,
        order_number=str(order_number),
    )
    canon = _overlay_order_engine_facts(canon, payment_facts)
    try:
        canon = await _prepare_sar_invoice_canon_from_inbox(
            db,
            canon=canon,
            representative_row=row,
            user_id=user_id,
            order_number=str(order_number),
            orders_user_id=orders_user_id,
        )
        _assert_sar_currency(canon)
    except ManualSendRefused as exc:
        return {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "detail": exc.extra,
        }
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
        extra = exc.extra or {}
        breakdown = extra.get("breakdown") or {}

        # A totals mismatch means the diagnosis itself completed
        # successfully. Return the same top-level shape as a passing
        # diagnosis so the existing UI renders TotalsBreakdown, while
        # preserving the refusal code/message and exact LRM metadata.
        if exc.code == "totals_mismatch" and breakdown:
            salla_total = _q2(extra.get(
                "salla_total", canon.get("total_amount")))
            expected_total = _q2(extra.get(
                "expected_qoyod_total",
                breakdown.get("expected_qoyod_total")))
            difference = _q2(extra.get(
                "difference", expected_total - salla_total))
            distribution = (
                extra.get("rounding_distribution")
                or breakdown.get("rounding_distribution")
                or {}
            )
            within_tolerance = _within_amount_tolerance(difference)
            return {
                "ok": True,
                "diagnosis_status": (
                    "pass" if within_tolerance else "blocked"),
                "code": exc.code,
                "message": exc.message,
                "order_number": str(order_number),
                "order_date": (odate.isoformat() if odate else None),
                "salla_total": salla_total,
                "expected_qoyod_total": expected_total,
                "difference": difference,
                "within_tolerance": within_tolerance,
                "tolerance_sar": 0.01,
                "difference_source_hint": breakdown.get(
                    "difference_source_hint"),
                "rounding_distribution": distribution,
                "reason": distribution.get("reason"),
                "residual": distribution.get("residual"),
                "shifted_lines": distribution.get("shifted_lines") or [],
                "qoyod_total_before": distribution.get(
                    "qoyod_total_before"),
                "qoyod_total_after": distribution.get(
                    "qoyod_total_after"),
                "breakdown": breakdown,
                "detail": extra,
                "canonical_summary": {
                    "items_count": len(canon.get("items") or []),
                    "shipping_amount": _q2(canon.get("shipping_amount")),
                    "cod_fee_amount": _q2(canon.get("cod_fee_amount")),
                    "discount_amount": _q2(canon.get("discount_amount")),
                    "tax_amount": _q2(canon.get("tax_amount")),
                    "subtotal": _q2(canon.get("subtotal")),
                    "payment_method": canon.get("payment_method")
                    or canon.get("payment_method_native"),
                },
                "settings_used": {
                    "qoyod_tax_percent": settings.get("qoyod_tax_percent"),
                    "default_shipping_product_id": settings.get(
                        "default_shipping_product_id"),
                    "default_cod_fee_product_id": settings.get(
                        "default_cod_fee_product_id"),
                },
            }

        return {"ok": False, "code": exc.code,
                "message": exc.message, "detail": extra}

    salla_total = _q2(canon.get("total_amount"))
    diff = _q2(expected_total - salla_total)
    would_pass = _within_amount_tolerance(diff)
    return {
        "ok":                 True,
        "diagnosis_status":   "pass" if would_pass else "blocked",
        "order_number":       str(order_number),
        "order_date":         (odate.isoformat() if odate else None),
        "salla_total":        salla_total,
        "expected_qoyod_total": expected_total,
        "difference":         diff,
        "within_tolerance":   would_pass,
        "tolerance_sar":      0.01,
        "difference_source_hint": breakdown.get("difference_source_hint"),
        "rounding_distribution": breakdown.get("rounding_distribution") or {},
        "reason": (breakdown.get("rounding_distribution") or {}).get("reason"),
        "residual": (breakdown.get("rounding_distribution") or {}).get("residual"),
        "shifted_lines": (breakdown.get("rounding_distribution") or {}).get("shifted_lines") or [],
        "qoyod_total_before": (breakdown.get("rounding_distribution") or {}).get("qoyod_total_before"),
        "qoyod_total_after": (breakdown.get("rounding_distribution") or {}).get("qoyod_total_after"),
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
