from product_category_variant_support import enrich_product_patch


def test_variant_ids_become_readable_option_labels():
    patch = {
        "options": [{"id": "10", "name": "اللون", "values": [{"id": "101", "name": "ذهبي"}, {"id": "102", "name": "فضي"}]}],
        "variants": [{"id": "9001", "name": None, "selections": ["101"]}],
        "categories": [],
    }
    raw = {
        "categories": [{"id": 55, "name": "اكسسوارات نسائية"}],
        "variants": [{"id": 9001, "values": [{"id": 101}]}],
        "google_product_category": "Apparel & Accessories > Jewelry",
    }
    result = enrich_product_patch(raw, patch)
    assert result["variants"][0]["display_name"] == "اللون: ذهبي"
    assert result["categories"][0]["name"] == "اكسسوارات نسائية"
    assert result["google_category"] == "Apparel & Accessories > Jewelry"


def test_numeric_variant_id_is_not_used_as_display_name():
    patch = {"options": [], "variants": [{"id": "1373493425", "name": None, "selections": []}]}
    result = enrich_product_patch({"variants": [{"id": "1373493425"}]}, patch)
    assert not result["variants"][0].get("display_name")
