from salla_integration.sync import _money, _salla_order_to_doc


def test_money_accepts_scalar_values():
    assert _money(123.45) == 123.45
    assert _money("123.45") == 123.45


def test_money_accepts_standard_salla_money_object():
    assert _money({
        "amount": 123.45,
        "currency": "SAR",
    }) == 123.45


def test_money_accepts_nested_salla_money_object():
    assert _money({
        "amount": {
            "amount": 123.45,
            "currency": "SAR",
        },
        "currency": "SAR",
    }) == 123.45


def test_money_accepts_value_wrapped_money_object():
    assert _money({
        "value": {
            "amount": "87.30",
        },
    }) == 87.30


def test_money_rejects_invalid_shapes_safely():
    assert _money({"currency": "SAR"}) == 0.0
    assert _money({"amount": {"unexpected": "x"}}) == 0.0
    assert _money(True) == 0.0


def test_order_mapper_handles_nested_item_amounts():
    raw = {
        "id": 999,
        "reference_id": "272291728",
        "date": "2026-07-14T13:47:24+03:00",
        "status": {
            "name": "بإنتظار المراجعة",
            "slug": "under_review",
        },
        "customer": {
            "full_name": "عميل اختبار",
        },
        "amounts": {
            "total": {
                "amount": {
                    "amount": 150.0,
                    "currency": "SAR",
                },
            },
        },
        "items": [
            {
                "id": 1,
                "quantity": 1,
                "product": {
                    "id": 101,
                    "name": "منتج اختبار",
                    "sku": "SKU-101",
                },
                "amounts": {
                    "price_without_tax": {
                        "amount": {
                            "amount": 130.43,
                            "currency": "SAR",
                        },
                    },
                    "total": {
                        "amount": {
                            "amount": 150.0,
                            "currency": "SAR",
                        },
                    },
                },
            },
        ],
    }

    doc = _salla_order_to_doc(raw)

    assert doc["total_amount"] == 150.0
    assert len(doc["products"]) == 1
    assert doc["products"][0]["price"] == 130.43
    assert doc["products"][0]["total"] == 150.0
    assert doc["products"][0]["product_id"] == "101"
    assert doc["products"][0]["sku"] == "SKU-101"
