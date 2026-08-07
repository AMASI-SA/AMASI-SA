from __future__ import annotations

import pytest

from integrations.qoyod_manual.send import (
    ManualSendRefused,
    _build_invoice_payload,
    _distribute_residual_over_items,
    _predict_qoyod_document_total,
    _q2,
)


def _known_order_270457540() -> dict:
    return {
        "order_number": "270457540",
        "order_id": "270457540",
        "currency": "SAR",
        "total_amount": 1250.87,
        "shipping_amount": 0.0,
        "cod_fee_amount": 0.0,
        "items": [
            {"sku": "AMS11923", "name": "A", "quantity": 1,
             "unit_price": 299, "total": 247.03},
            {"sku": "AMS11237", "name": "B", "quantity": 1,
             "unit_price": 149, "total": 123.10},
            {"sku": "AMS12080", "name": "C", "quantity": 1,
             "unit_price": 269, "total": 222.24},
            {"sku": "AMS12040", "name": "D", "quantity": 1,
             "unit_price": 269, "total": 222.24},
            {"sku": "AMS11935", "name": "E", "quantity": 1,
             "unit_price": 269, "total": 222.24},
            {"sku": "AMS11914", "name": "F", "quantity": 1,
             "unit_price": 259, "total": 213.98},
            {"sku": "GIFT1", "name": "Gift", "quantity": 1,
             "unit_price": 0, "total": 0},
            {"sku": "GIFT2", "name": "Gift", "quantity": 1,
             "unit_price": 0, "total": 0},
            {"sku": "GIFT3", "name": "Gift", "quantity": 1,
             "unit_price": 0, "total": 0},
            {"sku": "GIFT4", "name": "Gift", "quantity": 1,
             "unit_price": 0, "total": 0},
        ],
    }


def _resolutions(items: list[dict]) -> dict[str, int]:
    return {item["sku"]: idx + 1 for idx, item in enumerate(items)}


def test_order_270457540_reaches_exact_parity_without_adjustment_line():
    canon = _known_order_270457540()
    payload, expected, breakdown = _build_invoice_payload(
        canon=canon,
        contact_id=99,
        line_resolutions=_resolutions(canon["items"]),
        settings={
            "qoyod_tax_percent": 15,
            # Even when configured, the legacy adjustment product must not be
            # used by the exact-parity path.
            "rounding_adjustment_product_id": 999,
        },
        send_date_iso="2026-07-10",
    )

    assert expected == 1250.87
    assert breakdown["difference"] == 0.0
    distribution = breakdown["rounding_distribution"]
    assert distribution["applied"] is True
    assert distribution["method"] == "item_line_lrm"
    assert _q2(sum(
        row["shift"] for row in distribution["shifted_lines"]
    )) == 0.06

    lines = payload["invoice"]["line_items"]
    assert _predict_qoyod_document_total(
        lines)["predicted_total"] == 1250.87
    assert len(lines) == len(canon["items"])
    assert all(line["product_id"] != 999 for line in lines)
    assert all(line["description"] != "تسوية فرق التقريب مع سلة"
               for line in lines)

    zero_rows = [
        row for row in breakdown["items"]
        if row["salla_line_total"] == 0
    ]
    assert len(zero_rows) == 4
    assert all(_q2(row.get("shift_from_original")) == 0.0
               for row in zero_rows)


def test_distributor_handles_positive_and_negative_four_cents():
    items = [
        {"sku": f"SKU-{idx}", "name": f"Item {idx}", "quantity": 1,
         "unit_price": 100, "total": 115.00}
        for idx in range(1, 5)
    ] + [
        {"sku": "FREE", "name": "Free", "quantity": 1,
         "unit_price": 0, "total": 0}
    ]
    resolutions = _resolutions(items)

    plus = _distribute_residual_over_items(
        items, resolutions, 1.15, 15, 0.04)
    minus = _distribute_residual_over_items(
        items, resolutions, 1.15, 15, -0.04)

    assert plus is not None
    assert minus is not None
    _, plus_rows, plus_total = plus
    _, minus_rows, minus_total = minus

    assert plus_total == 460.04
    assert minus_total == 459.96
    assert _q2(sum(_q2(row.get("shift_from_original"))
                   for row in plus_rows)) == 0.04
    assert _q2(sum(_q2(row.get("shift_from_original"))
                   for row in minus_rows)) == -0.04

    # Last row is a zero-value free item and must remain untouched.
    assert _q2(plus_rows[-1].get("shift_from_original")) == 0.0
    assert _q2(minus_rows[-1].get("shift_from_original")) == 0.0


def test_residual_above_single_item_dynamic_cap_remains_blocked():
    canon = {
        "order_number": "TOO-LARGE",
        "order_id": "TOO-LARGE",
        "currency": "SAR",
        "total_amount": 115.11,
        "shipping_amount": 0.0,
        "cod_fee_amount": 0.0,
        "items": [
            {"sku": "SKU", "name": "Item", "quantity": 1,
             "unit_price": 100, "total": 115.00},
        ],
    }

    with pytest.raises(ManualSendRefused) as exc:
        _build_invoice_payload(
            canon=canon,
            contact_id=1,
            line_resolutions={"SKU": 1},
            settings={"qoyod_tax_percent": 15},
            send_date_iso="2026-07-10",
        )

    assert exc.value.code == "totals_mismatch"
    assert exc.value.extra["rounding_distribution"]["reason"] == (
        "residual_exceeds_dynamic_item_cap")


def test_missing_shipping_product_is_not_hidden_by_lrm():
    canon = {
        "order_number": "SHIP-GAP",
        "order_id": "SHIP-GAP",
        "currency": "SAR",
        "total_amount": 125.00,
        "shipping_amount": 10.00,
        "cod_fee_amount": 0.0,
        "items": [
            {"sku": "SKU", "name": "Item", "quantity": 1,
             "unit_price": 100, "total": 115.00},
        ],
    }

    with pytest.raises(ManualSendRefused) as exc:
        _build_invoice_payload(
            canon=canon,
            contact_id=1,
            line_resolutions={"SKU": 1},
            settings={"qoyod_tax_percent": 15},
            send_date_iso="2026-07-10",
        )

    assert exc.value.code == "totals_mismatch"
    assert exc.value.extra["rounding_distribution"]["reason"] == (
        "shipping_configuration_gap")


def test_unrepresentable_negative_one_halalah_is_accepted():
    """Regression for order 273317793: 12.00 Salla vs 11.99 Qoyod.

    The only product is free, so item-line LRM cannot absorb the remaining
    halalah.  The inclusive public tolerance must still allow the invoice.
    """
    canon = {
        "order_number": "273317793",
        "order_id": "273317793",
        "currency": "SAR",
        "total_amount": 12.00,
        "shipping_amount": 4.63,
        "cod_fee_amount": 0.0,
        "items": [
            {
                "sku": "AMS11839",
                "name": "Free promotional product",
                "quantity": 1,
                "unit_price": 0.0,
                "total": 0.0,
            },
        ],
    }

    _payload, expected, breakdown = _build_invoice_payload(
        canon=canon,
        contact_id=1,
        line_resolutions={"AMS11839": 1},
        settings={
            "qoyod_tax_percent": 15,
            "default_shipping_product_id": 2,
        },
        send_date_iso="2026-07-21",
    )

    assert expected == 11.99
    assert breakdown["difference"] == -0.01
    distribution = breakdown["rounding_distribution"]
    assert distribution["applied"] is False
    assert distribution["accepted_within_tolerance"] is True
    assert distribution["tolerance_sar"] == 0.01
