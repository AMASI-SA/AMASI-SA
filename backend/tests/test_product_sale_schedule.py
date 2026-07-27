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
