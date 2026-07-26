from product_v2_routes import _next_product_page


def test_next_product_page_uses_salla_pagination_metadata():
    assert _next_product_page(
        requested_page=1,
        row_count=60,
        pagination={"currentPage": 1, "totalPages": 20},
    ) == 2
    assert _next_product_page(
        requested_page=20,
        row_count=60,
        pagination={"currentPage": 20, "totalPages": 20},
    ) is None


def test_next_product_page_does_not_stop_on_sallas_default_15_rows_without_metadata():
    assert _next_product_page(
        requested_page=1,
        row_count=15,
        pagination={},
    ) == 2


def test_next_product_page_stops_on_empty_page():
    assert _next_product_page(
        requested_page=2,
        row_count=0,
        pagination={},
    ) is None
