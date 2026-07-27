import pytest

from product_category_publish_support import normalize_salla_status
from product_v2_details_routes import _dedupe_images


def test_product_images_are_deduplicated_by_id_and_cdn_filename():
    rows = [
        {"id": "1", "url": "https://cdn.salla.sa/path/pen.jpg?width=1200", "is_main": True, "sort": 0},
        {"id": "1", "url": "https://cdn.salla.sa/path/pen.jpg?width=400", "is_main": False, "sort": 1},
        {"id": "2", "url": "https://cdn.salla.sa/path/blue.jpg", "is_main": False, "sort": 2},
    ]
    result = _dedupe_images(rows, "https://cdn.salla.sa/other/pen.jpg?format=webp", "قلم")
    assert [row["id"] for row in result] == ["1", "2"]
    assert result[0]["is_main"] is True


def test_distinct_product_images_are_preserved():
    rows = [
        {"id": "1", "url": "https://cdn.salla.sa/path/black.jpg", "is_main": True, "sort": 0},
        {"id": "2", "url": "https://cdn.salla.sa/path/blue.jpg", "is_main": False, "sort": 1},
    ]
    assert len(_dedupe_images(rows)) == 2


@pytest.mark.parametrize(
    ("mezan", "salla"),
    [("active", "sale"), ("inactive", "hidden"), ("out_of_stock", "out")],
)
def test_mezan_product_status_maps_to_salla_status(mezan, salla):
    assert normalize_salla_status(mezan) == salla


def test_unknown_status_is_rejected_before_salla_call():
    with pytest.raises(ValueError):
        normalize_salla_status("unknown")
