from integrations.qoyod_manual.send import (
    _prepare_qoyod_invoice_payload_for_write,
)


def test_final_write_payload_is_the_normalized_object():
    payload = {"invoice": {"line_items": [
        {
            "product_id": 1,
            "quantity": 1,
            "unit_price": 95.0,
            "discount": 14.691,
            "tax_percent": 15,
        },
        {
            "product_id": 2,
            "quantity": 1,
            "unit_price": 23.15,
            "discount": 1.41,
            "tax_percent": 15,
        },
    ]}}

    expected_total, result = _prepare_qoyod_invoice_payload_for_write(
        payload, salla_total=117.34)

    lines = payload["invoice"]["line_items"]
    assert expected_total == 117.33
    assert result["difference"] == -0.01
    assert lines[0]["discount"] == 14.70
    assert lines[1]["discount"] == 1.42
