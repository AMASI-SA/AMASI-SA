from dashboard_eligibility_filter import (
    ORDER_MIN_TOTAL_SAR,
    PRODUCT_MIN_UNIT_SALE_SAR,
    line_unit_sale,
    order_is_eligible,
    qualifying_piece_counts,
)


def test_order_threshold_is_inclusive():
    assert ORDER_MIN_TOTAL_SAR == 50.0
    assert not order_is_eligible({"total_amount": 49.99})
    assert order_is_eligible({"total_amount": 50})


def test_piece_threshold_is_inclusive_and_uses_line_total_per_unit():
    assert PRODUCT_MIN_UNIT_SALE_SAR == 25.0
    assert line_unit_sale({"quantity": 2, "total": 50, "price": 100}) == 25.0
    assert line_unit_sale({"quantity": 2, "total": 49.98, "price": 100}) == 24.99


def test_piece_count_excludes_low_value_orders_and_low_price_items():
    orders = [
        {
            "total_amount": 120,
            "products": [
                {"quantity": 3, "total": 90},
                {"quantity": 4, "total": 80},
            ],
        },
        {
            "total_amount": 49,
            "products": [
                {"quantity": 20, "total": 1000},
            ],
        },
    ]
    eligible, excluded = qualifying_piece_counts(orders)
    assert eligible == 3
    assert excluded == 4
