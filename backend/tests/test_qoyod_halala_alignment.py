from integrations.qoyod.invoice_builder import build_invoice_payload


SETTINGS = {
    "default_inventory_id": "1",
    "default_shipping_product_id": "99",
    "invoice_total_policy": "match_salla_total",
    "qoyod_tax_percent": 15,
}


def _build(*, total: float, shipping: float):
    dto = {
        "order_id": "HALALA-TEST",
        "order_number": "HALALA-TEST",
        "currency": "SAR",
        "total_amount": total,
        "shipping_amount": shipping,
        "items": [{
            "sku": "PRODUCT-1",
            "name": "Product",
            "quantity": 1,
            "unit_price": 9.01,
            "discount_amount": 0,
            "tax_amount": 0,
            "total": round(total - shipping, 2),
        }],
    }
    return build_invoice_payload(
        dto_dict=dto,
        qoyod_customer_id="1",
        product_resolutions=[{
            "sku": "PRODUCT-1",
            "qoyod_product_id": "1",
        }],
        invoice_date=None,
        settings=SETTINGS,
    )


def test_halala_difference_is_absorbed_by_existing_shipping_line():
    payload = _build(total=10.01, shipping=1.01)
    alignment = payload["_diagnostics"]["halala_alignment"]

    assert alignment["applied"] is True
    assert alignment["target_line"] == "shipping"
    assert alignment["total_before"] == 10.02
    assert alignment["total_after"] == 10.01
    assert alignment["exact_match"] is True
    assert len(payload["invoice"]["line_items"]) == 2
    assert payload["invoice"]["line_items"][1]["description"] == "شحن (Shipping)"


def test_no_shipping_uses_last_product_without_creating_extra_line():
    payload = _build(total=10.01, shipping=0)
    alignment = payload["_diagnostics"]["halala_alignment"]

    assert alignment["target_line"] == "last_product"
    assert alignment["total_after"] == 10.01
    assert alignment["exact_match"] is True
    assert len(payload["invoice"]["line_items"]) == 1
