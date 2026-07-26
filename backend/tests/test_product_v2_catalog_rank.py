from product_v2_catalog_rank import catalog_rank


def test_catalog_rank_is_stable_across_pages():
    assert catalog_rank(page=1, index=0, per_page=60) == 0
    assert catalog_rank(page=1, index=59, per_page=60) == 59
    assert catalog_rank(page=2, index=0, per_page=60) == 60


def test_catalog_rank_never_uses_updated_time():
    first = catalog_rank(page=1, index=0, per_page=60)
    later = catalog_rank(page=2, index=0, per_page=60)
    assert first < later
