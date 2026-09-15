from salla_orders_v3.normalizer import normalize_order_item, normalize_order_items


def test_normalizer_preserves_all_customer_value_shapes_without_raw_json():
    item = {
        "id": 7001,
        "product_id": 11,
        "parent_product_id": 10,
        "product_sku_id": 22,
        "sku": "BASE-SKU",
        "variant": {"id": 22, "sku": "VARIANT-SKU"},
        "name": "منتج مخصص",
        "quantity": 2,
        "options": [
            {"name": "المقاس", "value": "XL"},
            {"label": "اللون", "value": {"name": "أخضر"}},
            {"question": "طباعة؟", "answer": False},
            {"name": "عدد النجوم", "selected": 0},
            {"name": "الإضافات", "choice": [
                {"label": "شريط"}, {"value": "علبة"},
            ]},
        ],
        "custom_fields": {"الاسم المطلوب": {"text": "نورة"}},
        "customizations": [{"name": "نص الإهداء", "value": "مبارك"}],
        "personalization": [{"title": "الحروف", "option_value": "NA"}],
        "attachments": [{"name": "صورة العميل", "url": "https://files.test/a.png"}],
        "files": [{"label": "ملف الطباعة", "value": {"url": "https://files.test/b.pdf"}}],
    }

    normalized = normalize_order_item(item, order_number="3001", index=0)

    assert normalized["order_item_id"] == "salla:3001:7001"
    assert normalized["source_item_id"] == "7001"
    assert normalized["product_id"] == "11"
    assert normalized["parent_product_id"] == "10"
    assert normalized["variant_id"] == "22"
    assert normalized["sku"] == "VARIANT-SKU"
    assert normalized["quantity"] == 2
    assert normalized["raw_item"] == item

    values = {row["name"]: row["value"] for row in normalized["options"]}
    assert values == {
        "المقاس": "XL",
        "اللون": "أخضر",
        "طباعة؟": False,
        "عدد النجوم": 0,
        "الإضافات": "شريط / علبة",
    }

    custom = {row["name"]: row["value"] for row in normalized["custom_fields"]}
    assert custom == {
        "الاسم المطلوب": "نورة",
        "نص الإهداء": "مبارك",
        "الحروف": "NA",
        "صورة العميل": "https://files.test/a.png",
        "ملف الطباعة": "https://files.test/b.pdf",
    }
    assert all(isinstance(row["value"], (str, int, float, bool)) for row in (
        normalized["options"] + normalized["custom_fields"]
    ))


def test_same_sku_lines_keep_distinct_order_item_identities():
    first = normalize_order_item(
        {"id": 1, "sku": "SAME", "name": "أ", "options": {"المقاس": "S"}},
        order_number="4001",
        index=0,
    )
    second = normalize_order_item(
        {"id": 2, "sku": "SAME", "name": "أ", "options": {"المقاس": "M"}},
        order_number="4001",
        index=1,
    )

    assert first["sku"] == second["sku"] == "SAME"
    assert first["order_item_id"] != second["order_item_id"]
    assert first["options"][0]["value"] == "S"
    assert second["options"][0]["value"] == "M"


def test_missing_source_item_id_uses_full_line_signature_not_sku_alone():
    common = {"sku": "SAME", "name": "منتج", "quantity": 1}
    first = normalize_order_item(
        common | {"options": {"المقاس": "S"}},
        order_number="5001",
        index=0,
    )
    second = normalize_order_item(
        common | {"options": {"المقاس": "M"}},
        order_number="5001",
        index=1,
    )

    assert first["order_item_id"] != second["order_item_id"]


def test_generated_identity_is_stable_when_distinct_rows_are_reordered():
    small = {"sku": "SAME", "name": "منتج", "options": {"المقاس": "S"}}
    medium = {"sku": "SAME", "name": "منتج", "options": {"المقاس": "M"}}

    first_pass = normalize_order_items([small, medium], order_number="5002")
    second_pass = normalize_order_items([medium, small], order_number="5002")

    first_by_size = {row["options"][0]["value"]: row["order_item_id"] for row in first_pass}
    second_by_size = {row["options"][0]["value"]: row["order_item_id"] for row in second_pass}
    assert first_by_size == second_by_size


def test_zero_quantity_is_preserved_instead_of_becoming_one():
    normalized = normalize_order_item(
        {"id": 9, "sku": "ZERO", "quantity": 0},
        order_number="5003",
        index=0,
    )

    assert normalized["quantity"] == 0
