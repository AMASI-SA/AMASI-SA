from salla_integration.sync import _salla_order_to_doc


def test_unified_products_preserve_authoritative_salla_item_data():
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
                "gtin": "628000000001",
                "name": "سواره اختبار",
                "quantity": 1,
                "product_thumbnail": {
                    "url": "https://example.com/item.jpg",
                },
                "amounts": {
                    "price_without_tax": {
                        "amount": 84.52,
                        "currency": "SAR",
                    },
                    "total": {
                        "amount": 97.20,
                        "currency": "SAR",
                    },
                    "tax": {
                        "amount": 12.68,
                        "currency": "SAR",
                    },
                },
                "options": [
                    {
                        "name": "اللون",
                        "value": {
                            "id": 1348006107,
                            "name": "ذهبي",
                        },
                    },
                    {
                        "name": "الاسم",
                        "value": {
                            "name": "سارة",
                        },
                    },
                ],
                "files": [
                    {
                        "url": "https://example.com/customer-file.jpg",
                    },
                ],
            },
        ],
    }

    doc = _salla_order_to_doc(raw)
    item = doc["products"][0]

    assert item["order_item_id"] == "261445298"
    assert item["product_id"] == "1001"
    assert item["variant_id"] == "2001"
    assert item["sku"] == "AMS10660"
    assert item["barcode"] == "628000000001"
    assert item["quantity"] == 1.0
    assert item["price"] == 84.52
    assert item["total"] == 97.20
    assert item["tax"] == 12.68
    assert item["image_url"] == "https://example.com/item.jpg"
    assert item["options"] == [
        {"name": "اللون", "value": "ذهبي"},
        {"name": "الاسم", "value": "سارة"},
    ]
    assert item["custom_fields"] == [
        {"url": "https://example.com/customer-file.jpg"},
    ]


def test_unified_products_preserve_plural_customer_option_values():
    doc = _salla_order_to_doc({
        "id": 13070,
        "reference_id": "AMS13070-test",
        "date": "2026-08-27T12:00:00+03:00",
        "items": [{
            "id": 1,
            "product_id": 653374677,
            "sku": "AMS13070",
            "name": "أناقة طفلك في احتفالات الوطن",
            "quantity": 1,
            "options": [{
                "name": "نوع الطلب",
                "values": [{"name": "فستان", "value": ""}],
            }],
        }],
    })

    assert doc["products"][0]["options"] == [{
        "name": "نوع الطلب",
        "value": ["فستان"],
    }]
