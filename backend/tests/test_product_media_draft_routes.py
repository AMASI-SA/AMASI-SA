import pytest

from product_media_draft_routes import (
    media_diff,
    normalize_media_rows,
    published_image_from_response,
    upload_token_from_row,
)


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


def test_normalize_media_rows_preserves_temporary_upload_identity():
    rows = normalize_media_rows([
        {
            "url": "https://mezan.example/api/products-v2/media-upload/file/random",
            "is_main": True,
            "source": "temporary_upload",
            "upload_token": "secret-token",
            "filename": "product.webp",
        }
    ])
    assert rows[0]["source"] == "temporary_upload"
    assert rows[0]["upload_token"] == "secret-token"
    assert rows[0]["filename"] == "product.webp"


def test_temporary_upload_requires_token():
    with pytest.raises(ValueError, match="temporary_upload_token_required"):
        normalize_media_rows([
            {
                "url": "https://mezan.example/temp.jpg",
                "is_main": True,
                "source": "temporary_upload",
            }
        ])


def test_recovers_upload_token_from_legacy_saved_draft_url():
    row = {
        "url": "https://mezansalla.com/api/products-v2/media-upload/file/legacy-secret-token",
        "source": "external_url",
    }
    assert upload_token_from_row(row) == "legacy-secret-token"


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


def test_salla_attach_response_becomes_persistent_cdn_image():
    row = {
        "id": None,
        "url": "https://mezan.example/temp.jpg",
        "alt": "مريول",
        "is_main": True,
        "sort": 3,
        "source": "temporary_upload",
        "upload_token": "secret",
    }
    response = {
        "data": {
            "id": 713368802,
            "image": {
                "original": {
                    "url": "https://cdn.salla.sa/store/new-image.webp",
                }
            },
            "sort": 3,
            "default": True,
            "alt_seo": "مريول مدرسي",
        }
    }
    published = published_image_from_response(response, row)
    assert published["id"] == "713368802"
    assert published["url"] == "https://cdn.salla.sa/store/new-image.webp"
    assert published["source"] == "salla"
    assert published["upload_token"] is None
    assert published["is_main"] is True
