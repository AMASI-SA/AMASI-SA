from datetime import datetime, timezone

from product_v2_details_routes import _details_patch, _normalize_options, _normalize_variants


def test_full_details_preserve_html_images_options_and_variants():
    raw = {
        "id": 13175352894,
        "name": "مريول مدرسي ابتدائي 2026",
        "description": "<h2>مقاسات المريول</h2><img src='https://cdn.example/size.jpg'><p>وصف عربي</p>",
        "price": {"amount": 170, "currency": "SAR"},
        "cost_price": 45,
        "sku": "AMS13028",
        "images": [
            {"id": 1, "url": "https://cdn.example/main.jpg", "is_main": True},
            {"id": 2, "url": "https://cdn.example/side.jpg"},
        ],
        "options": [{
            "id": "size",
            "name": "المقاس",
            "values": [{"id": "38", "name": "38"}, {"id": "40", "name": "40", "additional_price": 5}],
        }],
        "variants": [{
            "id": "v-38",
            "name": "مقاس 38",
            "sku": "AMS13028-38",
            "price": 170,
            "cost_price": 47,
            "quantity": 4,
            "options": [{"name": "المقاس", "value": "38"}],
        }],
    }
    doc = _details_patch(raw, user_id="owner-1")
    assert doc["description_html"].startswith("<h2>")
    assert len(doc["images"]) == 2
    assert doc["options"][0]["values"][1]["price"] == 5
    assert doc["variants"][0]["cost_price_from_salla"] == 47
    assert doc["cost_price_from_salla"] == 45
    assert doc["details_loaded"] is True
    assert isinstance(doc["details_synced_at"], datetime)


def test_option_and_variant_normalizers_tolerate_sparse_shapes():
    options = _normalize_options([{"name": "اللون", "values": ["ذهبي", {"label": "فضي"}]}])
    variants = _normalize_variants([{"id": 7, "sku": "A-7", "attributes": {"اللون": "ذهبي"}}])
    assert [row["name"] for row in options[0]["values"]] == ["ذهبي", "فضي"]
    assert variants[0]["id"] == "7"
    assert variants[0]["selections"][0] == {"name": "اللون", "value": "ذهبي"}
