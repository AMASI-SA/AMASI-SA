import pytest

from product_media_draft_routes import media_diff, normalize_media_rows


def test_normalize_media_rows_requires_one_main_and_reindexes_sort():
    rows = normalize_media_rows([
        {"id": "10", "url": "https://cdn.example.com/a.jpg", "alt": "A", "is_main": False, "sort": 99},
        {"id": "11", "url": "https://cdn.example.com/b.jpg", "alt": "B", "is_main": True, "sort": 1},
    ])
    assert [row["sort"] for row in rows] == [1, 2]
    assert rows[1]["is_main"] is True


def test_normalize_media_rows_rejects_duplicate_and_missing_main():
    with pytest.raises(ValueError, match="duplicate_image"):
        normalize_media_rows([
            {"url": "https://cdn.example.com/a.jpg", "is_main": True},
            {"url": "https://cdn.example.com/a.jpg?x=1", "is_main": False},
        ])
    with pytest.raises(ValueError, match="exactly_one_main_image_required"):
        normalize_media_rows([{"url": "https://cdn.example.com/a.jpg", "is_main": False}])


def test_media_diff_detects_add_remove_and_metadata_update():
    before = [
        {"id": "1", "url": "https://x/a.jpg", "alt": "old", "is_main": True, "sort": 1},
        {"id": "2", "url": "https://x/b.jpg", "alt": "", "is_main": False, "sort": 2},
    ]
    after = [
        {"id": "1", "url": "https://x/a.jpg", "alt": "new", "is_main": False, "sort": 2},
        {"id": None, "url": "https://x/c.jpg", "alt": "C", "is_main": True, "sort": 1},
    ]
    diff = media_diff(before, after)
    assert len(diff["added"]) == 1
    assert diff["removed"][0]["id"] == "2"
    assert len(diff["updated"]) == 1
