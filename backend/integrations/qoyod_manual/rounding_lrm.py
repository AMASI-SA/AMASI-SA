"""Exact item-line rounding parity for the Plan-B Qoyod path.

This module installs a narrow wrapper around ``send._build_invoice_payload``.
It never adds a synthetic adjustment product. Small residuals (up to 0.10 SAR)
are absorbed into existing positive-value product lines in 0.01 SAR increments.
Shipping and COD lines are never used to hide product rounding differences.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

_TWO_PLACES = Decimal("0.01")
_MAX_RESIDUAL = Decimal("0.10")


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _q2(value: Any) -> float:
    return float(_d(value).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _distribute(send_module, items: list[dict], line_resolutions: dict,
                tax_factor: float, tax_percent: float,
                residual_to_absorb: float):
    """Return rebuilt item lines after absorbing ``residual_to_absorb``.

    Rules:
    - residual must be between -0.10 and +0.10 SAR;
    - only positive-value product lines are eligible;
    - zero-value gifts remain exactly zero;
    - cents are allocated from the last eligible line backwards, cycling when
      more than one cent per line is required;
    - negative adjustments may never push a line below zero.
    """
    residual = _d(residual_to_absorb).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP)
    cents = int(abs(residual) * 100)
    if cents == 0 or abs(residual) > _MAX_RESIDUAL:
        return None

    eligible = [
        idx for idx, item in enumerate(items)
        if _d(item.get("total")).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP) > 0
    ]
    if not eligible:
        return None

    sign = Decimal("0.01") if residual > 0 else Decimal("-0.01")
    shifts = {idx: Decimal("0.00") for idx in eligible}
    order = list(reversed(eligible))

    allocated = 0
    cursor = 0
    safety = max(40, cents * len(order) * 4)
    while allocated < cents and cursor < safety:
        idx = order[cursor % len(order)]
        proposed = shifts[idx] + sign
        original = _d(items[idx].get("total")).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP)
        if original + proposed >= 0:
            shifts[idx] = proposed
            allocated += 1
        cursor += 1

    if allocated != cents:
        return None

    payloads: list[dict] = []
    rows: list[dict] = []
    total = Decimal("0.00")
    for idx, item in enumerate(items):
        shift = shifts.get(idx, Decimal("0.00"))
        override = None
        if shift:
            override = float(
                (_d(item.get("total")) + shift).quantize(
                    _TWO_PLACES, rounding=ROUND_HALF_UP))
        payload, row, gross = send_module._compute_item_line(
            item, line_resolutions, tax_factor, tax_percent,
            target_gross_override=override)
        payloads.append(payload)
        rows.append(row)
        total += _d(gross)

    item_total = float(total.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    return payloads, rows, item_total


def install(send_module) -> None:
    """Install the exact-parity builder once for the imported send module."""
    if getattr(send_module, "_ITEM_LINE_LRM_INSTALLED", False):
        return

    original_builder = send_module._build_invoice_payload

    def exact_builder(*, canon: dict, contact_id: int,
                      line_resolutions: dict, settings: dict,
                      send_date_iso: str):
        # Disable the legacy synthetic adjustment-product fallback before the
        # original builder runs. The wrapper either reaches exact parity using
        # existing product lines or blocks the send.
        safe_settings = dict(settings or {})
        safe_settings.pop("rounding_adjustment_product_id", None)

        payload, expected_total, breakdown = original_builder(
            canon=canon,
            contact_id=contact_id,
            line_resolutions=line_resolutions,
            settings=safe_settings,
            send_date_iso=send_date_iso,
        )

        salla_total = send_module._q2(canon.get("total_amount"))
        expected_total = send_module._q2(expected_total)
        residual = send_module._q2(salla_total - expected_total)

        # Exact already: keep the original payload but expose explicit
        # distribution metadata for diagnostics.
        if residual == 0.0:
            breakdown["rounding_distribution"] = {
                "applied": False,
                "reason": "not_needed",
                "residual": 0.0,
                "qoyod_total_before": expected_total,
                "qoyod_total_after": expected_total,
                "salla_total": salla_total,
            }
            breakdown["rounding_adjustment"] = {
                "applied": False,
                "reason": "disabled_use_item_line_lrm",
            }
            breakdown["difference"] = 0.0
            return payload, expected_total, breakdown

        shipping = breakdown.get("shipping")
        cod = breakdown.get("cod_fee")
        shipping_gap = (
            isinstance(shipping, dict)
            and not shipping.get("included")
            and send_module._f(shipping.get("salla_declared_amount")) > 0
        )
        cod_gap = (
            isinstance(cod, dict)
            and not cod.get("included")
            and send_module._f(cod.get("salla_declared_amount")) > 0
        )

        reason = None
        if shipping_gap:
            reason = "shipping_configuration_gap"
        elif cod_gap:
            reason = "cod_configuration_gap"
        elif abs(_d(residual)) > _MAX_RESIDUAL:
            reason = "residual_exceeds_0_10"

        raw_items = canon.get("items") or []
        distributed = None if reason else _distribute(
            send_module,
            raw_items,
            line_resolutions,
            float(1 + send_module._q2(
                safe_settings.get("qoyod_tax_percent") or 15) / 100),
            send_module._q2(
                safe_settings.get("qoyod_tax_percent") or 15),
            residual,
        )

        if distributed is not None:
            item_lines, item_rows, item_total_after = distributed
            original_item_total = send_module._q2(sum(
                send_module._f(row.get("line_gross_after_tax"))
                for row in (breakdown.get("items") or [])
            ))
            non_item_total = send_module._q2(
                expected_total - original_item_total)
            candidate_total = send_module._q2(
                item_total_after + non_item_total)

            if candidate_total == salla_total:
                invoice = payload.get("invoice") or {}
                old_lines = invoice.get("line_items") or []
                invoice["line_items"] = item_lines + old_lines[len(raw_items):]
                shifted = [
                    {
                        "sku": row.get("sku"),
                        "shift": send_module._q2(
                            row.get("shift_from_original")),
                        "target_gross": row.get("target_gross_override"),
                    }
                    for row in item_rows
                    if send_module._q2(
                        row.get("shift_from_original")) != 0.0
                ]
                breakdown["items"] = item_rows
                breakdown["qoyod_total_before_distribution"] = expected_total
                breakdown["residual_before_distribution"] = residual
                breakdown["rounding_distribution"] = {
                    "applied": True,
                    "method": "item_line_lrm",
                    "residual": residual,
                    "qoyod_total_before": expected_total,
                    "qoyod_total_after": candidate_total,
                    "salla_total": salla_total,
                    "shifted_lines": shifted,
                }
                breakdown["rounding_adjustment"] = {
                    "applied": False,
                    "reason": "disabled_use_item_line_lrm",
                }
                breakdown["expected_qoyod_total"] = candidate_total
                breakdown["difference"] = 0.0
                breakdown["difference_source_hint"] = (
                    "تم توزيع فرق التقريب على أسطر المنتجات الأصلية")

                # Final line-count integrity: LRM never changes line count.
                if len(invoice["line_items"]) != len(old_lines):
                    raise send_module.ManualSendRefused(
                        "duplicated_invoice_items_detected",
                        "توزيع فرق التقريب غيّر عدد بنود الفاتورة — أُوقف الإرسال",
                        {
                            "before": len(old_lines),
                            "after": len(invoice["line_items"]),
                        },
                    )
                return payload, candidate_total, breakdown

            reason = "distribution_did_not_reach_exact_parity"
        elif reason is None:
            reason = "residual_not_distributable"

        breakdown["rounding_distribution"] = {
            "applied": False,
            "reason": reason,
            "residual": residual,
            "qoyod_total_before": expected_total,
            "salla_total": salla_total,
        }
        breakdown["rounding_adjustment"] = {
            "applied": False,
            "reason": "disabled_use_item_line_lrm",
        }
        breakdown["difference"] = send_module._q2(
            expected_total - salla_total)

        # Exact parity is mandatory. Raising here prevents the existing
        # <=0.01 guard from accidentally allowing a one-halalah mismatch.
        raise send_module.ManualSendRefused(
            "totals_mismatch",
            f"فرق المبلغ {abs(breakdown['difference'])} ريال — يجب أن يكون 0.00 قبل الإرسال",
            {
                "salla_total": salla_total,
                "expected_qoyod_total": expected_total,
                "difference": breakdown["difference"],
                "rounding_distribution": breakdown["rounding_distribution"],
                "breakdown": breakdown,
            },
        )

    send_module._distribute_residual_over_items = (
        lambda items, line_resolutions, tax_factor, tax_percent,
               residual_to_absorb: _distribute(
                   send_module, items, line_resolutions,
                   tax_factor, tax_percent, residual_to_absorb)
    )
    send_module._build_invoice_payload = exact_builder
    send_module._ITEM_LINE_LRM_INSTALLED = True
