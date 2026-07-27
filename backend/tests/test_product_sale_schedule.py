from product_sale_schedule import normalize_product_prices


def test_nested_salla_price_and_sale_schedule_are_distinct():
    result = normalize_product_prices({
        "price": {"regular": 170, "sale": 100, "currency": "SAR"},
        "sale_period": {"start": "2026-07-01", "end": "2026-07-31"},
    })
    assert result["price"] == 170
    assert result["sale_price"] == 100
    assert result["sale_starts_at"] == "2026-07-01"
    assert result["sale_ends_at"] == "2026-07-31"


def test_scalar_base_price_does_not_become_sale_price():
    result = normalize_product_prices({"price": 170})
    assert result["price"] == 170
    assert result["sale_price"] is None


def test_salla_current_price_uses_top_level_regular_price_as_base():
    result = normalize_product_prices({
        "price": {"amount": 170, "currency": "SAR"},
        "regular_price": {"amount": 220, "currency": "SAR"},
        "sale_end": "2026-07-31",
    })
    assert result["price"] == 220
    assert result["sale_price"] == 170
    assert result["sale_ends_at"] == "2026-07-31"


def test_identical_price_and_sale_are_not_exposed_as_discount():
    result = normalize_product_prices({"price": 170, "sale_price": 170})
    assert result["price"] is None
    assert result["sale_price"] == 170


def test_discount_object_can_hold_original_and_current_prices():
    result = normalize_product_prices({
        "price": 170,
        "discount": {"original_price": 220, "price": 170, "end": "2026-08-10"},
    })
    assert result["price"] == 220
    assert result["sale_price"] == 170
    assert result["sale_ends_at"] == "2026-08-10"
