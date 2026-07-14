from order_engine.mapper import map_salla_order


def test_salla_order_item_nested_options_are_human_readable():
    raw = {
        "id": 1381699627,
        "reference_id": "272291728",
        "date": "2026-07-14T13:47:24+03:00",
        "status": {
            "slug": "under_review",
            "name": "بإنتظار المراجعة",
        },
        "amounts": {
            "total": {
                "amount": 97.20,
                "currency": "SAR",
            },
        },
        "items": [
            {
                "id": 261445298,
                "product_id": 1001,
                "product_sku_id": 2001,
                "sku": "AMS10660",
                "name": "سواره اختبار",
                "quantity": 1,
                "thumbnail": "https://example.com/item.jpg",
                "gtin": "628000000001",
                "amounts": {
                    "price_without_tax": {
                        "amount": 84.52,
                        "currency": "SAR",
                    },
                    "total": {
                        "amount": 97.20,
                        "currency": "SAR",
                    },
                },
                "options": [
                    {
                        "name": "اللون",
                        "value": {
                            "id": 1348006107,
                            "name": "ذهبي",
                            "price": {
                                "amount": 0,
                                "currency": "SAR",
                            },
                        },
                    },
                    {
                        "name": "الاسم",
                        "value": {
                            "name": "سارة",
                        },
                    },
                ],
            },
        ],
    }

    order = map_salla_order(raw)
    item = order.items[0]

    assert item.product_id == "1001"
    assert item.variant_id == "2001"
    assert item.sku == "AMS10660"
    assert item.barcode == "628000000001"
    assert item.image_url == "https://example.com/item.jpg"
    assert item.unit_price == 84.52
    assert item.total == 97.20

    assert item.options_normalized == {
        "اللون": "ذهبي",
        "الاسم": "سارة",
    }

    assert item.options_raw[0]["value"] == "ذهبي"
    assert item.options_raw[1]["value"] == "سارة"
    assert item.color == "ذهبي"
