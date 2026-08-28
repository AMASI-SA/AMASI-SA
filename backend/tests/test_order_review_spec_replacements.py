from pathlib import Path
from types import SimpleNamespace

from order_review_spec_replacements import (
    ORDER_OVERRIDE_FIELD,
    canonical_spec_key,
    effective_spec_rows,
    extract_item_specs,
    materialize_defaults_into_state,
    replacement_override_map,
    split_legacy_replacement_text,
    supplier_file_spec_fields,
    supplier_file_spec_lines,
)


ROOT = Path(__file__).resolve().parents[2]


def item(*, order_item_id="order-1:item-1", size="54 انش"):
    return SimpleNamespace(
        order_item_id=order_item_id,
        order_id="order-1",
        order_number="1001",
        line_index=0,
        product_id="product-88",
        parent_product_id=None,
        variant_id=None,
        sku="AMS13031",
        name="عباية صيفية",
        color=None,
        size=size,
        material=None,
        options=[],
        custom_fields=[],
        source=SimpleNamespace(source_product_id="product-88"),
    )


def test_canonical_spec_key_matches_review_ui_aliases():
    assert canonical_spec_key("المقاس:") == "size"
    assert canonical_spec_key(" اللون ") == "color"
    assert canonical_spec_key("ملاحظات") == "ملاحظات"


def test_extract_item_specs_keeps_name_and_value_separate():
    rows = extract_item_specs(item())
    assert rows == [{
        "spec_key": "size",
        "name": "المقاس",
        "value": "54 انش",
    }]


def test_supplier_file_uses_review_snapshot_when_live_item_options_are_missing():
    sparse = item(size=None)
    state = {
        "specifications_snapshot": {
            "مقاس الطفل بالعمر": "8 سنوات",
            "الاسم المطلوب": "سارة",
        },
    }

    assert supplier_file_spec_fields(sparse, state) == [
        {
            "spec_key": "مقاس الطفل بالعمر",
            "name": "مقاس الطفل بالعمر",
            "value": "8 سنوات",
            "text": "مقاس الطفل بالعمر: 8 سنوات",
        },
        {
            "spec_key": "الاسم المطلوب",
            "name": "الاسم المطلوب",
            "value": "سارة",
            "text": "الاسم المطلوب: سارة",
        },
    ]


def test_live_option_wins_over_older_review_snapshot_value():
    current = item(size="10 سنوات")
    state = {"specifications_snapshot": {"المقاس": "8 سنوات"}}

    fields = supplier_file_spec_fields(current, state)
    assert fields[0]["value"] == "10 سنوات"


def test_sparse_live_item_uses_all_line_specific_review_sources():
    sparse = item(size=None)
    state = {
        "options_normalized": {
            "هل تريد إضافة اسم": "نعم",
            "الاسم": "ريم",
        },
        "options_raw": [{"label": "اللون", "selected": "أخضر"}],
        "custom_fields": [{"question": "المقاس", "answer": "8 سنوات"}],
    }

    fields = supplier_file_spec_fields(sparse, state)
    assert {row["name"]: row["value"] for row in fields} == {
        "هل تريد إضافة اسم": "نعم",
        "الاسم": "ريم",
        "اللون": "أخضر",
        "المقاس": "8 سنوات",
    }


def test_review_snapshot_precedes_legacy_state_sources_for_same_field():
    sparse = item(size=None)
    state = {
        "specifications_snapshot": {"اللون": "أخضر"},
        "options_normalized": {"اللون": "أحمر", "المقاس": "10 سنوات"},
    }

    fields = supplier_file_spec_fields(sparse, state)
    assert {row["name"]: row["value"] for row in fields} == {
        "اللون": "أخضر",
        "المقاس": "10 سنوات",
    }


def test_customer_options_are_empty_only_when_every_line_source_is_empty():
    sparse = item(size=None)
    assert supplier_file_spec_fields(sparse, {
        "specifications_snapshot": {},
        "options": [],
        "options_normalized": {},
        "options_raw": [],
        "custom_fields": [],
    }) == []


def test_name_and_value_can_be_replaced_independently_for_file_only():
    product = item()
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_name": "المقاس المطلوب",
            "replacement_value": "54 بوصة",
        }],
    }

    rows = effective_spec_rows(product, state, {})
    assert rows[0]["original_name"] == "المقاس"
    assert rows[0]["original_value"] == "54 انش"
    assert rows[0]["replacement_name"] == "المقاس المطلوب"
    assert rows[0]["replacement_value"] == "54 بوصة"
    assert rows[0]["file_name"] == "المقاس المطلوب"
    assert rows[0]["file_value"] == "54 بوصة"
    assert rows[0]["file_text"] == "المقاس المطلوب: 54 بوصة"
    assert product.size == "54 انش"
    assert supplier_file_spec_fields(product, state) == [{
        "spec_key": "size",
        "name": "المقاس المطلوب",
        "value": "54 بوصة",
        "text": "المقاس المطلوب: 54 بوصة",
    }]


def test_only_value_can_change_while_original_name_is_used():
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_name": None,
            "replacement_value": "54 بوصة",
        }],
    }
    row = effective_spec_rows(item(), state, {})[0]
    assert row["file_name"] == "المقاس"
    assert row["file_value"] == "54 بوصة"
    assert supplier_file_spec_lines(item(), state) == ["المقاس: 54 بوصة"]


def test_only_name_can_change_while_original_value_is_used():
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_name": "المقاس المطلوب",
            "replacement_value": None,
        }],
    }
    row = effective_spec_rows(item(), state, {})[0]
    assert row["file_name"] == "المقاس المطلوب"
    assert row["file_value"] == "54 انش"


def test_hidden_spec_stays_out_of_supplier_file_with_field_overrides():
    state = {
        "supplier_export_excluded_spec_keys": ["المقاس"],
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_name": "المقاس المطلوب",
            "replacement_value": "54 بوصة",
        }],
    }
    assert supplier_file_spec_fields(item(), state) == []
    assert supplier_file_spec_lines(item(), state) == []


def test_mapping_item_specs_include_normalized_options():
    assert extract_item_specs({
        "options": [],
        "options_normalized": {
            "المقاس": "8 سنوات",
            "اللون": "أخضر",
        },
    }) == [
        {
            "spec_key": "size",
            "name": "المقاس",
            "value": "8 سنوات",
        },
        {
            "spec_key": "color",
            "name": "اللون",
            "value": "أخضر",
        },
    ]


def test_order_override_has_priority_over_later_product_default():
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_name": None,
            "replacement_value": "قيمة الطلب القديم",
        }],
    }
    rows = effective_spec_rows(
        item(),
        state,
        {"size": {
            "replacement_name": "اسم افتراضي جديد",
            "replacement_value": "قيمة افتراضية جديدة",
        }},
    )
    assert rows[0]["replacement_name"] is None
    assert rows[0]["replacement_value"] == "قيمة الطلب القديم"
    assert rows[0]["replacement_source"] == "order"


def test_explicit_current_order_clear_suppresses_product_default():
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_name": None,
            "replacement_value": None,
        }],
    }
    rows = effective_spec_rows(
        item(),
        state,
        {"size": {
            "replacement_name": "المقاس المطلوب",
            "replacement_value": "54 بوصة",
        }},
    )
    assert rows[0]["replacement_name"] is None
    assert rows[0]["replacement_value"] is None
    assert rows[0]["replacement_source"] == "order_clear"
    assert rows[0]["file_name"] == "المقاس"
    assert rows[0]["file_value"] == "54 انش"


def test_future_default_is_snapshotted_once_into_each_order():
    product = item()
    default = {
        "size": {
            "replacement_name": "المقاس المطلوب",
            "replacement_value": "54 بوصة",
        }
    }
    first, changed = materialize_defaults_into_state(
        product,
        {"order_item_id": product.order_item_id},
        default,
    )
    assert changed is True
    assert replacement_override_map(first)["size"] == {
        "replacement_name": "المقاس المطلوب",
        "replacement_value": "54 بوصة",
        "replacement_text": None,
    }

    second, changed_again = materialize_defaults_into_state(
        product,
        first,
        {"size": {
            "replacement_name": "اسم تم تعديله لاحقًا",
            "replacement_value": "قيمة تم تعديلها لاحقًا",
        }},
    )
    assert changed_again is False
    assert replacement_override_map(second)["size"]["replacement_value"] == "54 بوصة"


def test_legacy_full_line_is_converted_to_separate_fields():
    converted = split_legacy_replacement_text(
        "المقاس 54 بوصة",
        original_name="المقاس",
        original_value="54 انش",
    )
    assert converted == {
        "replacement_name": None,
        "replacement_value": "54 بوصة",
    }

    legacy_state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_text": "المقاس 54 بوصة",
        }],
    }
    row = effective_spec_rows(item(), legacy_state, {})[0]
    assert row["replacement_name"] is None
    assert row["replacement_value"] == "54 بوصة"
    assert row["file_text"] == "المقاس: 54 بوصة"


def test_router_is_registered_in_order_engine():
    source = (ROOT / "backend/order_engine/__init__.py").read_text(encoding="utf-8")
    assert "make_order_review_spec_replacements_router" in source
    assert "make_order_review_spec_replacements_router(db, current_user)" in source
