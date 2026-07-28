from product_category_variant_support import _build_category_catalog, _flatten_categories, enrich_product_patch


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


def test_flat_salla_categories_build_full_parent_path():
    rows = _flatten_categories([
        {"id": 1, "name": "اكسسوارات نسائية", "parent_id": 0, "status": "active"},
        {"id": 2, "name": "سلاسل", "parent_id": 1, "status": "active"},
        {"id": 3, "name": "سلسال بالاسم", "parent_id": 2, "status": "active"},
    ])
    catalog = _build_category_catalog(rows)
    child = next(row for row in catalog if row["id"] == "3")
    assert child["path"] == "اكسسوارات نسائية ← سلاسل ← سلسال بالاسم"
    assert child["depth"] == 2
    assert child["is_hidden"] is False


def test_nested_sub_categories_are_included_and_hidden_is_marked():
    rows = _flatten_categories([
        {
            "id": 1,
            "name": "اكسسوارات نسائية",
            "status": "active",
            "sub_categories": [
                {"id": 2, "name": "سلسال بالاسم", "status": "hidden"},
            ],
        },
    ])
    catalog = _build_category_catalog(rows)
    child = next(row for row in catalog if row["id"] == "2")
    assert child["parent_id"] == "1"
    assert child["is_hidden"] is True
    assert child["status_label"] == "مخفي"
    assert child["path"].endswith("— مخفي")
