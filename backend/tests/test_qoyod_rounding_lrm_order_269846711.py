from __future__ import annotations

from integrations.qoyod_manual.send import _build_invoice_payload, _q2


def _resolutions(items: list[dict]) -> dict[str, int]:
    return {item["sku"]: idx + 1 for idx, item in enumerate(items)}


def test_order_269846711_two_cent_gap_reaches_221_03_exactly():
    # Production regression shape: two lines declared by Salla as 92.34 each
    # initially quantise in Qoyod to 92.35 each. A one-cent target per line is
    # not representable at 15% VAT; one actual line must absorb a two-cent
    # gross move while the invoice total remains exact.
    canon = {
        "order_number": "269846711",
        "order_id": "269846711",
        "currency": "SAR",
        "total_amount": 221.03,
        "shipping_amount": 0.0,
        "cod_fee_amount": 0.0,
        "items": [
            {"sku": "ORDER269846711-A", "name": "A", "quantity": 1,
             "unit_price": 99.00, "total": 92.34},
            {"sku": "ORDER269846711-B", "name": "B", "quantity": 1,
             "unit_price": 99.00, "total": 92.34},
            {"sku": "ORDER269846711-C", "name": "C", "quantity": 1,
             "unit_price": 39.00, "total": 36.35},
        ],
    }

    payload, expected, breakdown = _build_invoice_payload(
        canon=canon,
        contact_id=1,
        line_resolutions=_resolutions(canon["items"]),
        settings={
            "qoyod_tax_percent": 15,
            "rounding_adjustment_product_id": 999,
        },
        send_date_iso="2026-07-11",
    )

    assert breakdown["qoyod_total_before_distribution"] == 221.05
    assert expected == 221.03
    assert breakdown["expected_qoyod_total"] == 221.03
    assert breakdown["difference"] == 0.0

    distribution = breakdown["rounding_distribution"]
    assert distribution["applied"] is True
    assert distribution["method"] == "item_line_lrm"
    assert distribution["residual"] == -0.02
    assert distribution["qoyod_total_before"] == 221.05
    assert distribution["qoyod_total_after"] == 221.03
    assert _q2(sum(
        row["shift"] for row in distribution["shifted_lines"])) == -0.02

    lines = payload["invoice"]["line_items"]
    assert len(lines) == 3
    assert all(line["product_id"] != 999 for line in lines)
    assert all(line["description"] != "تسوية فرق التقريب مع سلة"
               for line in lines)
    assert breakdown["rounding_adjustment"]["reason"] == (
        "synthetic_adjustment_disabled_item_line_lrm_enabled")
