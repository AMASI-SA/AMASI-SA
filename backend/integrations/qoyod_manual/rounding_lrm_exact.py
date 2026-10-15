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
_THREE_PLACES = Decimal("0.001")
_MAX_RESIDUAL = Decimal("0.10")
_SEARCH_CENTS = 12
_SEARCH_MILLS = 120


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _line_variants(send_module, item: dict, line_resolutions: dict,
                   tax_factor: float, tax_percent: float) -> dict[int, tuple]:
    """Return the cheapest sendable representation for each gross-cent delta.

    Search order:
      1. Existing 0.01 SAR unit/discount variants.
      2. Exact 0.001 SAR single-field fallback.
    """
    base_payload, base_row, base_gross = send_module._compute_item_line(
        item, line_resolutions, tax_factor, tax_percent)
    base_gross_dec = _d(base_gross).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP)
    original_total = _d(item.get("total")).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP)
    qty = _d(base_payload.get("quantity") or 1)

    base_unit = _d(base_payload.get("unit_price")).quantize(
        _THREE_PLACES, rounding=ROUND_HALF_UP)
    base_discount = _d(base_payload.get("discount")).quantize(
        _THREE_PLACES, rounding=ROUND_HALF_UP)

    variants: dict[int, tuple] = {}

    def consider(
        unit_price: Decimal,
        discount: Decimal,
        unit_shift_mills: int,
        discount_shift_mills: int,
    ) -> None:
        if unit_price < 0 or discount < 0:
            return

        line_net = unit_price * qty - discount
        if line_net < 0:
            return

        gross = _d(send_module._line_gross(
            unit_price=float(unit_price),
            quantity=float(qty),
            discount=float(discount),
            tax_percent=tax_percent,
        )).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)

        delta_cents = int((gross - base_gross_dec) * 100)
        if abs(delta_cents) > int(_MAX_RESIDUAL * 100):
            return

        payload = dict(base_payload)
        payload["unit_price"] = float(unit_price)
        payload["discount"] = float(discount)

        row = dict(base_row)
        net_q2 = line_net.quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP)
        row["qoyod_unit_price"] = float(unit_price)
        row["computed_discount"] = float(discount)
        row["line_net_after_discount"] = float(net_q2)
        row["line_tax_15pct"] = float(
            (gross - net_q2).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP))
        row["line_gross_after_tax"] = float(gross)
        row["delta_vs_salla_line"] = float(
            (gross - original_total).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP))
        row["target_gross_override"] = float(gross)
        row["shift_from_original"] = row["delta_vs_salla_line"]
        row["lrm_payload_adjustment"] = {
            "precision": "0.001",
            "unit_price_shift": float(
                Decimal(unit_shift_mills) * _THREE_PLACES),
            "discount_shift": float(
                Decimal(discount_shift_mills) * _THREE_PLACES),
            "gross_delta_from_baseline": float(
                (gross - base_gross_dec).quantize(
                    _TWO_PLACES, rounding=ROUND_HALF_UP)),
        }

        changed_fields = int(unit_shift_mills != 0) + int(
            discount_shift_mills != 0)
        score = (
            abs(unit_shift_mills) + abs(discount_shift_mills),
            changed_fields,
            abs(unit_shift_mills),
        )

        current = variants.get(delta_cents)
        if current is None or score < current[3]:
            variants[delta_cents] = (
                payload, row, float(gross), score)

    consider(base_unit, base_discount, 0, 0)

    for unit_shift in range(-_SEARCH_CENTS, _SEARCH_CENTS + 1):
        for discount_shift in range(
                -_SEARCH_CENTS, _SEARCH_CENTS + 1):
            if unit_shift == 0 and discount_shift == 0:
                continue

            consider(
                (
                    base_unit
                    + Decimal(unit_shift) * _TWO_PLACES
                ).quantize(
                    _THREE_PLACES, rounding=ROUND_HALF_UP),
                (
                    base_discount
                    + Decimal(discount_shift) * _TWO_PLACES
                ).quantize(
                    _THREE_PLACES, rounding=ROUND_HALF_UP),
                unit_shift * 10,
                discount_shift * 10,
            )

    for mill_shift in range(-_SEARCH_MILLS, _SEARCH_MILLS + 1):
        if mill_shift == 0 or mill_shift % 10 == 0:
            continue

        consider(
            base_unit,
            (
                base_discount
                + Decimal(mill_shift) * _THREE_PLACES
            ).quantize(
                _THREE_PLACES, rounding=ROUND_HALF_UP),
            0,
            mill_shift,
        )

        consider(
            (
                base_unit
                + Decimal(mill_shift) * _THREE_PLACES
            ).quantize(
                _THREE_PLACES, rounding=ROUND_HALF_UP),
            base_discount,
            mill_shift,
            0,
        )

    return variants


def _distribute(send_module, items: list[dict], line_resolutions: dict,
                tax_factor: float, tax_percent: float,
                residual_to_absorb: float):
    """Return rebuilt item lines after absorbing ``residual_to_absorb``."""
    residual = _d(residual_to_absorb).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP)
    target_cents = int(residual * 100)
    if target_cents == 0 or abs(residual) > _MAX_RESIDUAL:
        return None

    per_line: list[dict[int, tuple]] = []
    for item in items:
        if _d(item.get("total")).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP) <= 0:
            payload, row, gross = send_module._compute_item_line(
                item, line_resolutions, tax_factor, tax_percent)
            per_line.append({0: (payload, row, gross, (0, 0, 0))})
        else:
            variants = _line_variants(
                send_module, item, line_resolutions,
                tax_factor, tax_percent)
            if not variants:
                return None
            per_line.append(variants)

    states: dict[int, tuple[tuple[int, int, int], list[tuple]]] = {
        0: ((0, 0, 0), [])
    }
    limit = int(_MAX_RESIDUAL * 100)
    for variants in per_line:
        next_states: dict[int, tuple[tuple[int, int, int], list[tuple]]] = {}
        for accumulated, (score, chosen) in states.items():
            for delta, variant in variants.items():
                new_total = accumulated + delta
                if abs(new_total) > limit:
                    continue
                variant_score = variant[3]
                new_score = tuple(
                    score[idx] + variant_score[idx] for idx in range(3))
                current = next_states.get(new_total)
                if current is None or new_score < current[0]:
                    next_states[new_total] = (
                        new_score, chosen + [variant])
        states = next_states
        if not states:
            return None

    selected = states.get(target_cents)
    if selected is None:
        return None

    payloads: list[dict] = []
    rows: list[dict] = []
    total = Decimal("0.00")
    for payload, row, gross, _score in selected[1]:
        payloads.append(payload)
        rows.append(row)
        total += _d(gross)

    item_total = float(total.quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP))
    return payloads, rows, item_total


def install(send_module) -> None:
    """Install the exact-parity builder once for the imported send module."""
    if getattr(send_module, "_ITEM_LINE_LRM_INSTALLED", False):
        return

    original_builder = send_module._build_invoice_payload

    def exact_builder(*, canon: dict, contact_id: int,
                      line_resolutions: dict, settings: dict,
                      send_date_iso: str):
        safe_settings = dict(settings or {})
        safe_settings.pop("rounding_adjustment_product_id", None)

        payload, expected_total, breakdown = original_builder(
            canon=canon,
            contact_id=contact_id,
            line_resolutions=line_resolutions,
            settings=safe_settings,
            send_date_iso=send_date_iso,
        )

        # The merchant policy is fixed: every Qoyod invoice line is 15% VAT
        # inclusive, while the customer-paid Salla gross remains unchanged.
        # LRM used to rebuild product lines from the persisted setting, which
        # could be stale (for example 5%) even after the canonical invoice had
        # correctly selected 15%.  Use the builder's effective policy and
        # fail before any Qoyod write if either it or an outgoing line differs.
        effective_tax_percent = send_module._q2(
            breakdown.get("tax_percent"))

        def assert_fixed_tax_policy() -> None:
            invoice = payload.get("invoice") if isinstance(payload, dict) else None
            lines = invoice.get("line_items") if isinstance(invoice, dict) else None
            violations = []
            for index, line in enumerate(lines or []):
                actual = send_module._q2(
                    line.get("tax_percent") if isinstance(line, dict) else None)
                if actual != 15.0:
                    violations.append({"line_index": index, "tax_percent": actual})
            if (
                effective_tax_percent != 15.0
                or not isinstance(lines, list)
                or not lines
                or violations
            ):
                raise send_module.ManualSendRefused(
                    "qoyod_tax_policy_violation",
                    "ضريبة فاتورة قيود يجب أن تكون 15% على جميع البنود؛ "
                    "أُوقف الإرسال قبل أي كتابة.",
                    {
                        "expected_tax_percent": 15.0,
                        "effective_tax_percent": effective_tax_percent,
                        "violations": violations,
                        "qoyod_write_performed": False,
                    },
                )

        assert_fixed_tax_policy()

        salla_total = send_module._q2(canon.get("total_amount"))
        expected_total = send_module._q2(expected_total)
        residual = send_module._q2(salla_total - expected_total)

        adjustment_reason = (
            "synthetic_adjustment_disabled_item_line_lrm_enabled")

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
                "reason": adjustment_reason,
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
            float(1 + effective_tax_percent / 100),
            effective_tax_percent,
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
                            row.get("lrm_payload_adjustment", {}).get(
                                "gross_delta_from_baseline")),
                        "target_gross": row.get("target_gross_override"),
                        "payload_adjustment": row.get(
                            "lrm_payload_adjustment"),
                    }
                    for row in item_rows
                    if send_module._q2(
                        row.get("lrm_payload_adjustment", {}).get(
                            "gross_delta_from_baseline")) != 0.0
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
                    "reason": adjustment_reason,
                }
                breakdown["expected_qoyod_total"] = candidate_total
                breakdown["difference"] = 0.0
                breakdown["difference_source_hint"] = (
                    "تم توزيع فرق التقريب على أسطر المنتجات الأصلية")

                if len(invoice["line_items"]) != len(old_lines):
                    raise send_module.ManualSendRefused(
                        "duplicated_invoice_items_detected",
                        "توزيع فرق التقريب غيّر عدد بنود الفاتورة — أُوقف الإرسال",
                        {
                            "before": len(old_lines),
                            "after": len(invoice["line_items"]),
                        },
                    )
                assert_fixed_tax_policy()
                return payload, candidate_total, breakdown

            reason = "distribution_did_not_reach_exact_parity"
        elif reason is None:
            reason = "residual_not_representable_by_item_lines"

        breakdown["rounding_distribution"] = {
            "applied": False,
            "reason": reason,
            "residual": residual,
            "qoyod_total_before": expected_total,
            "salla_total": salla_total,
        }
        breakdown["rounding_adjustment"] = {
            "applied": False,
            "reason": adjustment_reason,
        }
        breakdown["difference"] = send_module._q2(
            expected_total - salla_total)

        # Item-line LRM is best-effort. The public Plan-B contract allows a
        # difference of exactly one halalah in either direction, so an
        # unrepresentable ±0.01 residual must continue to the normal send
        # guard instead of being rejected here by the stricter legacy
        # exact-parity rule.
        if send_module._within_amount_tolerance(
                breakdown["difference"]):
            breakdown["rounding_distribution"][
                "accepted_within_tolerance"] = True
            breakdown["rounding_distribution"][
                "tolerance_sar"] = float(send_module._AMOUNT_TOLERANCE)
            breakdown["expected_qoyod_total"] = expected_total
            return payload, expected_total, breakdown

        raise send_module.ManualSendRefused(
            "totals_mismatch",
            f"فرق المبلغ {abs(breakdown['difference'])} ريال أكبر من 0.01 — أُوقف الإرسال",
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
