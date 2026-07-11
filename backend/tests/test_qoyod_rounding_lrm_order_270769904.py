from __future__ import annotations

from integrations.qoyod_manual.send import _build_invoice_payload, _q2


def _order_270769904() -> dict:
    repeated = [
        {
            "sku": "AMS11147",
            "name": "خاتم رجالي أنيق بالاسم",
            "quantity": 1,
            "unit_price": 97.00,
            "total": 89.05,
        }
        for _ in range(20)
    ]
    return {
        "order_number": "270769904",
        "order_id": "270769904",
        "currency": "SAR",
        "total_amount": 1870.00,
        "shipping_amount": 25.92,
        "cod_fee_amount": 0.0,
        "items": repeated + [
            {
                "sku": "AMS11147-EXACT",
                "name": "خاتم رجالي أنيق بالاسم",
                "quantity": 1,
                "unit_price": 68.72,
                "total": 63.08,
            },
            {
                "sku": "AMS11961",
                "name": "تغليف أنيق مع الورد - أماسي",
                "quantity": 21,
                "unit_price": 0.0,
                "total": 0.0,
            },
        ],
    }


def test_order_270769904_twenty_cent_gap_reaches_1870_exactly():
    canon = _order_270769904()
    resolutions = {
        "AMS11147": 101,
        "AMS11147-EXACT": 102,
        "AMS11961": 103,
    }

    payload, expected, breakdown = _build_invoice_payload(
        canon=canon,
        contact_id=99,
        line_resolutions=resolutions,
        settings={
            "qoyod_tax_percent": 15,
            "default_shipping_product_id": 777,
            "rounding_adjustment_product_id": 999,
        },
        send_date_iso="2026-07-11",
    )

    assert expected == 1870.00
    assert breakdown["qoyod_total_before_distribution"] == 1869.80
    assert breakdown["difference"] == 0.0

    distribution = breakdown["rounding_distribution"]
    assert distribution["applied"] is True
    assert distribution["method"] == "item_line_lrm"
    assert distribution["residual"] == 0.20
    assert distribution["qoyod_total_after"] == 1870.00

    shifted = distribution["shifted_lines"]
    assert shifted
    assert len(shifted) <= 20
    assert _q2(sum(row["shift"] for row in shifted)) == 0.20
    assert all(_q2(row["shift"]) > 0 for row in shifted)
    assert all(abs(_q2(row["shift"])) <= 0.02 for row in shifted)

    lines = payload["invoice"]["line_items"]
    assert len(lines) == len(canon["items"]) + 1  # products + shipping
    assert all(line["product_id"] != 999 for line in lines)
    assert all(line["description"] != "تسوية فرق التقريب مع سلة"
               for line in lines)

    free_rows = [
        row for row in breakdown["items"]
        if row["sku"] == "AMS11961"
    ]
    assert len(free_rows) == 1
    assert _q2(free_rows[0].get("shift_from_original")) == 0.0
