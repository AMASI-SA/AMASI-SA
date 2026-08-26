from integrations.qoyod_manual.send import (
    _align_qoyod_document_total,
    _preflight_qoyod_invoice_payload,
)


def _order_274962873_lines():
    product_lines = [
        {
            "product_id": 10842,
            "description": "AMS10842",
            "quantity": 1,
            "unit_price": 97.00,
            "discount": 23.43,
            "discount_type": "amount",
            "tax_percent": 15.00,
        }
        for _ in range(6)
    ]
    product_lines.append({
        "product_id": 999,
        "description": "Shipping",
        "quantity": 1,
        "unit_price": 24.07,
        "discount": 1.50,
        "discount_type": "amount",
        "tax_percent": 15.00,
    })
    return product_lines


def test_aligns_qoyod_document_rounding_without_negative_values():
    lines = _order_274962873_lines()

    result = _align_qoyod_document_total(
        lines,
        salla_total=533.56,
        item_line_count=6,
        adjustment_product_id=777,
    )

    assert result["before"]["predicted_total"] == 533.59
    assert result["after"]["predicted_total"] == 533.56
    assert result["difference"] == 0.0
    assert result["changed_item_lines"] == [5, 4, 3]
    assert result["adjustment_amount"] == 0.01
    assert all(line["unit_price"] >= 0 for line in lines)
    assert all(line["discount"] >= 0 for line in lines)

    checked = _preflight_qoyod_invoice_payload(
        {"invoice": {"line_items": lines}},
        salla_total=533.56,
    )
    assert checked["difference"] == 0.0


def test_preflight_normalizes_mill_lrm_before_qoyod_write():
    payload = {"invoice": {"line_items": [
        {
            "product_id": 1, "quantity": 1, "unit_price": 95.0,
            "discount": 14.691, "tax_percent": 15,
        },
        {
            "product_id": 2, "quantity": 1, "unit_price": 23.15,
            "discount": 1.41, "tax_percent": 15,
        },
    ]}}

    result = _preflight_qoyod_invoice_payload(
        payload, salla_total=117.34)

    lines = payload["invoice"]["line_items"]
    assert result["qoyod_predicted_total"] == 117.33
    assert result["difference"] == -0.01
    assert lines[0]["discount"] == 14.70
    assert lines[1]["discount"] == 1.42
