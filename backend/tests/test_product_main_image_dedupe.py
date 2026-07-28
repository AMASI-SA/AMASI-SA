from product_v2_details_routes import _dedupe_images


def test_main_image_is_not_inserted_when_images_list_already_has_a_main():
    rows = [
        {"id": "101", "url": "https://cdn.salla.sa/gallery/photo-a.jpg", "is_main": True, "sort": 0},
        {"id": "102", "url": "https://cdn.salla.sa/gallery/photo-b.jpg", "is_main": False, "sort": 1},
    ]

    result = _dedupe_images(
        rows,
        main_image="https://cdn.salla.sa/cover/generated-main-file.jpg",
        product_name="مريول مدرسي",
    )

    assert len(result) == 2
    assert [row["id"] for row in result] == ["101", "102"]
    assert sum(bool(row.get("is_main")) for row in result) == 1


def test_main_image_is_inserted_only_when_gallery_has_no_main_image():
    rows = [
        {"id": "102", "url": "https://cdn.salla.sa/gallery/photo-b.jpg", "is_main": False, "sort": 1},
    ]

    result = _dedupe_images(
        rows,
        main_image="https://cdn.salla.sa/cover/main.jpg",
        product_name="منتج",
    )

    assert len(result) == 2
    assert result[0]["id"] == "main"
    assert result[0]["is_main"] is True
