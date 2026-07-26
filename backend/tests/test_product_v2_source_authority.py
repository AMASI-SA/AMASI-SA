from datetime import timezone

from product_v2_source_authority import salla_created_at, unique_images


def test_salla_created_at_ignores_updated_at():
    created = salla_created_at({
        "created_at": "2026-07-01T10:00:00Z",
        "updated_at": "2026-07-26T20:00:00Z",
    })
    assert created is not None
    assert created.tzinfo is not None
    assert created.astimezone(timezone.utc).isoformat().startswith("2026-07-01T10:00:00")


def test_unique_images_deduplicates_same_id_and_cdn_path():
    rows = [
        {"id": "1", "url": "https://cdn.salla.sa/a/image.jpg?w=500", "is_main": True, "sort": 0},
        {"id": "1", "url": "https://cdn.salla.sa/a/image.jpg?w=900", "sort": 1},
        {"id": "2", "url": "https://cdn.salla.sa/b/second.jpg", "sort": 2},
        {"id": "3", "url": "https://cdn.salla.sa/b/second.jpg?quality=80", "sort": 3},
    ]
    result = unique_images(rows, main_url="https://cdn.salla.sa/a/image.jpg")
    assert [row["id"] for row in result] == ["1", "2"]
    assert result[0]["is_main"] is True
    assert result[1]["is_main"] is False
    assert [row["sort"] for row in result] == [0, 1]
