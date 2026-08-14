from product_v2_sync_hotfix import _pagination, next_product_page


def test_pagination_supports_direct_and_meta_shapes():
    assert _pagination({"pagination": {"currentPage": 1}}) == {"currentPage": 1}
    assert _pagination({"meta": {"pagination": {"current_page": 2}}}) == {"current_page": 2}


def test_next_page_does_not_trust_total_pages_when_rows_remain():
    assert next_product_page(
        requested_page=1,
        row_count=60,
        pagination={"currentPage": 1, "totalPages": 20},
    ) == 2
    assert next_product_page(
        requested_page=20,
        row_count=20,
        pagination={"currentPage": 20, "totalPages": 20},
    ) == 21


def test_misleading_single_page_metadata_cannot_truncate_catalogue():
    assert next_product_page(
        requested_page=1,
        row_count=20,
        pagination={"currentPage": 1, "totalPages": 1},
    ) == 2


def test_default_fifteen_rows_is_not_treated_as_end_of_catalogue():
    assert next_product_page(
        requested_page=1,
        row_count=15,
        pagination={},
    ) == 2


def test_empty_page_ends_traversal():
    assert next_product_page(
        requested_page=2,
        row_count=0,
        pagination={},
    ) is None
