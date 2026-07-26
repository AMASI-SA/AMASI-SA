from datetime import datetime, timezone

from product_v2_routes import normalize_salla_product


def test_normalize_salla_product_builds_stable_v2_contract():
    now = datetime.now(timezone.utc)
    raw = {
        "id": 12345,
        "name": "سلسال بالاسم",
        "sku": "NAME-01",
        "barcode": "628000001",
        "price": {"amount": "149.00", "currency": "SAR"},
        "sale_price": {"amount": 129},
        "quantity": 17,
        "status": "sale",
        "main_image": {"url": "https://cdn.example.com/product.jpg"},
        "categories": [{"id": 8, "name": "السلاسل"}],
        "options": [{"id": 1}],
        "skus": [{"id": 1}, {"id": 2}],
    }

    product = normalize_salla_product(raw, user_id="owner-1", synced_at=now)

    assert product["user_id"] == "owner-1"
    assert product["mezan_product_id"] == "mpv2_12345"
    assert product["salla_product_id"] == "12345"
    assert product["name"] == "سلسال بالاسم"
    assert product["sku"] == "NAME-01"
    assert product["barcode"] == "628000001"
    assert product["price"] == 149.0
    assert product["sale_price"] == 129.0
    assert product["currency"] == "SAR"
    assert product["quantity"] == 17.0
    assert product["status"] == "active"
    assert product["main_image"] == "https://cdn.example.com/product.jpg"
    assert product["categories"] == [{"id": "8", "name": "السلاسل"}]
    assert product["options_count"] == 1
    assert product["variants_count"] == 2
    assert product["archived"] is False
    assert product["source"] == "salla"
    assert len(product["source_revision"]) == 64


def test_normalize_salla_product_rejects_missing_external_id():
    try:
        normalize_salla_product({"name": "بدون رقم"}, user_id="owner-1", synced_at=datetime.now(timezone.utc))
    except ValueError as exc:
        assert str(exc) == "missing_salla_product_id"
    else:
        raise AssertionError("missing id must be rejected")


def test_normalize_salla_product_is_independent_from_legacy_shape():
    product = normalize_salla_product(
        {
            "product_id": "p-9",
            "title": "منتج مستقل",
            "stock_quantity": "3",
            "availability": "hidden",
        },
        user_id="owner-2",
        synced_at=datetime.now(timezone.utc),
    )

    assert product["salla_product_id"] == "p-9"
    assert product["name"] == "منتج مستقل"
    assert product["quantity"] == 3.0
    assert product["status"] == "inactive"
    assert "legacy" not in product
