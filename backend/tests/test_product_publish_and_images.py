from product_category_publish_support import normalize_category_ids, normalize_product_status
from product_image_dedupe_support import dedupe_images


def test_salla_status_mapping():
    assert normalize_product_status("active") == "sale"
    assert normalize_product_status("inactive") == "hidden"
    assert normalize_product_status("out_of_stock") == "out"


def test_category_ids_are_integers():
    assert normalize_category_ids(["12", {"id": "13"}, 12]) == [12, 13]


def test_duplicate_images_removed_by_url_without_query():
    rows = [
        {"id": "1", "url": "https://cdn.example/a.jpg?v=1"},
        {"id": "2", "url": "https://cdn.example/a.jpg?v=2"},
        {"id": "3", "url": "https://cdn.example/b.jpg"},
    ]
    result = dedupe_images(rows)
    assert [row["id"] for row in result] == ["1", "3"]
